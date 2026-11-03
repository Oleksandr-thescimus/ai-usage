"""Incremental scanner for Claude Code transcripts (~/.claude/projects/**/*.jsonl).

Usage is grouped by transcript directory (projects/<encoded launch dir>/), i.e. by the
directory `claude` was started in, not by the shifting `cwd` of each message. The label
is the `cwd` whose encoding equals the directory name.

Each API response is written several times while it streams (same message id and
request id); later copies carry the final output_tokens, so we keep the copy with
the largest output. Files are read from a remembered byte offset and only complete
lines are consumed, so a line being written right now is picked up on the next scan.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from ai_usage.models import UsageRecord

SKIP_MODELS = {"<synthetic>"}


_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


def encode_project_dir(cwd: str) -> str:
    """How Claude Code names transcript dirs: every non-alphanumeric char becomes '-'."""
    return _NON_ALNUM.sub("-", cwd)


@dataclass
class _FileState:
    inode: int
    offset: int
    project_key: str


def _int(value) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    return 0


def _parse_timestamp(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def display_project(cwd: str, home: str | None = None) -> str:
    home = home if home is not None else str(Path.home())
    if home and (cwd == home or cwd.startswith(home + "/")):
        return "~" + cwd[len(home):]
    return cwd


def parse_line(line: bytes, project: str = "unknown") -> tuple[tuple[str, str], UsageRecord, str] | None:
    """Return (dedup_key, record, cwd) for an assistant line with usage, else None."""
    # Cheap byte prefilter: most lines (user messages, request blobs) have no usage.
    if b'"usage"' not in line or b'"assistant"' not in line:
        return None
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("type") != "assistant":
        return None
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    model = message.get("model") if isinstance(message.get("model"), str) else "unknown"
    if model in SKIP_MODELS:
        return None
    timestamp = _parse_timestamp(data.get("timestamp"))
    if timestamp is None:
        return None

    message_id = message.get("id") if isinstance(message.get("id"), str) else ""
    request_id = data.get("requestId") if isinstance(data.get("requestId"), str) else ""
    if not message_id and not request_id:
        uuid = data.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            return None
        message_id = uuid
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else ""
    session = data.get("sessionId") if isinstance(data.get("sessionId"), str) else ""

    record = UsageRecord(
        timestamp=timestamp,
        model=model,
        project=project,
        session_id=session,
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        cache_creation_tokens=_int(usage.get("cache_creation_input_tokens")),
        cache_read_tokens=_int(usage.get("cache_read_input_tokens")),
    )
    return (message_id, request_id), record, cwd


class LogScanner:
    def __init__(self, roots: list[Path]):
        self.roots = roots
        self._files: dict[Path, _FileState] = {}
        self._records: dict[tuple[str, str], UsageRecord] = {}
        self._exact_cwd: dict[str, str] = {}  # project key -> cwd that encodes to it
        self._shortest_cwd: dict[str, str] = {}  # project key -> fallback label

    @staticmethod
    def _project_key(root: Path, path: Path) -> str:
        # projects/<encoded-cwd>/<session>.jsonl or .../<session>/subagents/agent-*.jsonl
        try:
            return path.relative_to(root).parts[0]
        except (ValueError, IndexError):
            return "unknown"

    def _note_cwd(self, key: str, cwd: str) -> None:
        if not cwd or key in self._exact_cwd:
            return
        if encode_project_dir(cwd) == key:
            self._exact_cwd[key] = cwd
        elif key not in self._shortest_cwd or len(cwd) < len(self._shortest_cwd[key]):
            self._shortest_cwd[key] = cwd

    def project_label(self, key: str) -> str:
        cwd = self._exact_cwd.get(key) or self._shortest_cwd.get(key)
        return display_project(cwd) if cwd else key

    def _add(self, key: tuple[str, str], record: UsageRecord) -> None:
        current = self._records.get(key)
        if current is None or record.output_tokens > current.output_tokens:
            self._records[key] = record

    def _read_file(self, path: Path, state: _FileState) -> None:
        with open(path, "rb") as fh:
            fh.seek(state.offset)
            for line in fh:
                if not line.endswith(b"\n"):
                    break  # incomplete line still being written; retry next scan
                state.offset += len(line)
                parsed = parse_line(line, state.project_key)
                if parsed is not None:
                    key, record, cwd = parsed
                    self._note_cwd(state.project_key, cwd)
                    self._add(key, record)

    def scan(self) -> list[UsageRecord]:
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*.jsonl"):
                try:
                    st = path.stat()
                except OSError:
                    continue
                state = self._files.get(path)
                if state is None or state.inode != st.st_ino or st.st_size < state.offset:
                    # New, replaced or truncated file: read from the start. Records are
                    # keyed by message id, so re-reading never double counts.
                    state = _FileState(st.st_ino, 0, self._project_key(root, path))
                    self._files[path] = state
                if st.st_size == state.offset:
                    continue
                try:
                    self._read_file(path, state)
                except OSError:
                    continue
        labels: dict[str, str] = {}
        result = []
        for record in self._records.values():
            label = labels.get(record.project)
            if label is None:
                label = labels[record.project] = self.project_label(record.project)
            result.append(record if label == record.project else replace(record, project=label))
        return result
