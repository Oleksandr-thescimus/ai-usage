"""Last-known limits on disk, so the UI has something to show when offline."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from ai_usage.models import LimitsSnapshot, LimitWindow


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ai-usage"


def _limits_path(provider: str) -> Path:
    return cache_dir() / f"{provider}-limits.json"


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def save_limits(snapshot: LimitsSnapshot) -> None:
    """Atomically store a fresh snapshot (contains no secrets)."""
    data = {
        "provider": snapshot.provider,
        "plan": snapshot.plan,
        "fetched_at": snapshot.fetched_at.isoformat() if snapshot.fetched_at else None,
        "windows": [
            {
                "key": w.key,
                "group": w.group,
                "label": w.label,
                "percent": w.percent,
                "resets_at": w.resets_at.isoformat() if w.resets_at else None,
            }
            for w in snapshot.windows
        ],
    }
    path = _limits_path(snapshot.provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_limits(provider: str) -> LimitsSnapshot | None:
    try:
        data = json.loads(_limits_path(provider).read_text())
        windows = [
            LimitWindow(
                key=str(w["key"]),
                group=str(w.get("group") or "other"),
                label=str(w.get("label") or w["key"]),
                percent=float(w["percent"]),
                resets_at=_dt(w.get("resets_at")),
            )
            for w in data.get("windows", [])
        ]
        return LimitsSnapshot(
            provider=provider,
            plan=data.get("plan"),
            windows=windows,
            fetched_at=_dt(data.get("fetched_at")),
            stale=True,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
