"""Start-on-login via an XDG autostart desktop entry."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path

import ai_usage

FILE_NAME = "ai-usage.desktop"


def autostart_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / FILE_NAME


def launch_command() -> str:
    """Absolute command that starts the tray (autostart sessions may lack ~/.local/bin in PATH)."""
    installed = shutil.which("ai-usage")
    if installed:
        return f"{shlex.quote(installed)} tray"
    src_root = Path(ai_usage.__file__).resolve().parent.parent
    return f"env PYTHONPATH={shlex.quote(str(src_root))} {shlex.quote(sys.executable)} -m ai_usage tray"


def desktop_entry(exec_line: str) -> str:
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=AI Usage",
            "Comment=Claude usage limits and token stats in the top bar",
            f"Exec={exec_line}",
            "Icon=utilities-system-monitor",
            "Terminal=false",
            "X-GNOME-Autostart-enabled=true",
            "X-GNOME-Autostart-Delay=5",
            "",
        ]
    )


def is_enabled() -> bool:
    return autostart_file().is_file()


def enable() -> Path:
    path = autostart_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(desktop_entry(launch_command()))
    return path


def disable() -> bool:
    path = autostart_file()
    if path.is_file():
        path.unlink()
        return True
    return False
