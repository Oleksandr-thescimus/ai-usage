"""Where Claude Code keeps its config, credentials and transcripts."""

from __future__ import annotations

import os
from pathlib import Path


def config_dirs() -> list[Path]:
    """Existing Claude config dirs: $CLAUDE_CONFIG_DIR (comma-separated) or the defaults."""
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if env:
        candidates = [Path(p.strip()).expanduser() for p in env.split(",") if p.strip()]
    else:
        home = Path.home()
        candidates = [home / ".config" / "claude", home / ".claude"]
    seen: set[Path] = set()
    result = []
    for c in candidates:
        resolved = c.resolve()
        if resolved not in seen and c.is_dir():
            seen.add(resolved)
            result.append(c)
    return result


def projects_dirs() -> list[Path]:
    return [d / "projects" for d in config_dirs() if (d / "projects").is_dir()]


def credentials_file() -> Path | None:
    for d in config_dirs():
        f = d / ".credentials.json"
        if f.is_file():
            return f
    return None
