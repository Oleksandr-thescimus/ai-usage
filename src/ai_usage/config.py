"""User settings in $XDG_CONFIG_HOME/ai-usage/config.json (currently: hidden projects).

Hidden projects are left out of the per-project lists (CLI `tokens --by project`, the
"By project" tab). Totals and the other breakdowns still count them. This keeps them off
the screen; it isn't access control, since this file and the transcripts are plain files.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

HIDDEN_KEY = "hidden_projects"


def config_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ai-usage" / "config.json"


def load() -> dict:
    try:
        data = json.loads(config_file().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict) -> None:
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def get_pref(name: str, default: str, allowed: Iterable[str]) -> str:
    """A string preference under "prefs", falling back to `default` if unset or invalid."""
    prefs = load().get("prefs")
    value = prefs.get(name) if isinstance(prefs, dict) else None
    return value if isinstance(value, str) and value in set(allowed) else default


def set_pref(name: str, value: str) -> None:
    data = load()
    prefs = data.get("prefs") if isinstance(data.get("prefs"), dict) else {}
    prefs[name] = value
    data["prefs"] = prefs
    save(data)


def hidden_projects() -> set[str]:
    value = load().get(HIDDEN_KEY)
    if not isinstance(value, list):
        return set()
    return {p for p in value if isinstance(p, str)}


def _store_hidden(projects: Iterable[str]) -> None:
    data = load()  # keep any other settings
    data[HIDDEN_KEY] = sorted(set(projects))
    save(data)


def set_hidden(project: str, hidden: bool) -> None:
    current = hidden_projects()
    if hidden:
        current.add(project)
    else:
        current.discard(project)
    _store_hidden(current)


def unhide_all() -> int:
    count = len(hidden_projects())
    _store_hidden([])
    return count
