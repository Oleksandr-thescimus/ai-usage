"""Persistent usage history, so the timeline outlives Claude Code's transcript cleanup.

Stored in $XDG_DATA_HOME/ai-usage/<provider>-history.json: usage summed per
(UTC hour, model, project key). Numbers only: no prompts, no message ids.

Merging takes the per-counter maximum of the stored and the freshly scanned values.
While transcripts exist, each counter only grows (new responses, final output_tokens).
When Claude Code deletes old transcripts, the scanned values drop, and the maximum keeps
what was seen before. UTC hours (not local days) keep the history correct if the
timezone changes; the timeline converts them to local days.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from ai_usage.models import UsageBucket, UsageRecord

VERSION = 1
HOUR_FORMAT = "%Y-%m-%dT%H"
SEP = "|"  # model and project key never contain it (keys are [A-Za-z0-9-])

Counters = list[int]  # [input, output, cache_write, cache_read, requests]


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "ai-usage"


def aggregate(records: Iterable[UsageRecord]) -> tuple[dict[str, dict[str, Counters]], dict[str, str]]:
    """Records -> ({hour: {"model|key": counters}}, {key: label})."""
    buckets: dict[str, dict[str, Counters]] = {}
    labels: dict[str, str] = {}
    for r in records:
        key = r.project_key or r.project
        labels[key] = r.project
        hour = r.timestamp.astimezone(timezone.utc).strftime(HOUR_FORMAT)
        c = buckets.setdefault(hour, {}).setdefault(f"{r.model}{SEP}{key}", [0, 0, 0, 0, 0])
        c[0] += r.input_tokens
        c[1] += r.output_tokens
        c[2] += r.cache_creation_tokens
        c[3] += r.cache_read_tokens
        c[4] += 1
    return buckets, labels


def merge_max(stored: dict[str, dict[str, Counters]], fresh: dict[str, dict[str, Counters]]) -> bool:
    """Fold `fresh` into `stored` (per-counter max). Returns True if anything changed."""
    changed = False
    for hour, entries in fresh.items():
        target = stored.setdefault(hour, {})
        for key, counters in entries.items():
            old = target.get(key)
            if old is None:
                target[key] = list(counters)
                changed = True
                continue
            merged = [max(a, b) for a, b in zip(old, counters)]
            if merged != old:
                target[key] = merged
                changed = True
    return changed


class UsageHistory:
    def __init__(self, provider: str):
        self.path = data_dir() / f"{provider}-history.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {"version": VERSION, "labels": {}, "buckets": {}}
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("buckets"), dict):
            # Don't silently overwrite history we can't read: keep it aside for recovery.
            try:
                self.path.rename(self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}"))
            except OSError:
                pass
            return {"version": VERSION, "labels": {}, "buckets": {}}
        data.setdefault("labels", {})
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def update(self, records: Iterable[UsageRecord]) -> list[UsageBucket]:
        """Merge the scanned records into the file (re-read first: another process may
        have written it) and return the full history."""
        data = self._load()
        fresh, labels = aggregate(records)
        changed = merge_max(data["buckets"], fresh)
        for key, label in labels.items():
            if data["labels"].get(key) != label:
                data["labels"][key] = label
                changed = True
        if changed:
            try:
                self._save(data)
            except OSError:
                pass  # still return the merged view; next update retries the write
        return to_buckets(data)


def to_buckets(data: dict) -> list[UsageBucket]:
    labels = data.get("labels", {})
    result = []
    for hour, entries in data.get("buckets", {}).items():
        try:
            start = datetime.strptime(hour, HOUR_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        for key, c in entries.items():
            model, _, project_key = key.partition(SEP)
            if not isinstance(c, list) or len(c) != 5:
                continue
            result.append(UsageBucket(start, model, labels.get(project_key, project_key), *map(int, c)))
    return result
