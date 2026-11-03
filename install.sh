#!/usr/bin/env bash
# Install ai-usage for the current user (runs from this checkout, no pip needed).
#   ./install.sh              install ~/.local/bin/ai-usage + app launcher
#   ./install.sh --uninstall  remove them (and the autostart entry)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/ai-usage.desktop"
BIN="$BIN_DIR/ai-usage"
LAUNCHER="$APPS_DIR/ai-usage.desktop"
PYTHON="${PYTHON:-/usr/bin/python3}"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$BIN" "$LAUNCHER" "$AUTOSTART"
    echo "Removed $BIN, $LAUNCHER and the autostart entry (if any)."
    echo "Cached data stays in ${XDG_CACHE_HOME:-$HOME/.cache}/ai-usage (safe to delete)."
    exit 0
fi

if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "Python 3.10+ is required ($PYTHON)." >&2
    exit 1
fi
if ! "$PYTHON" -c "import gi; gi.require_version('Gtk', '3.0'); gi.require_version('AyatanaAppIndicator3', '0.1'); from gi.repository import Gtk, AyatanaAppIndicator3" 2>/dev/null; then
    echo "Warning: the tray needs GTK 3 + AyatanaAppIndicator3. The CLI works without them." >&2
    echo "  sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1" >&2
fi

mkdir -p "$BIN_DIR" "$APPS_DIR"
cat > "$BIN" <<EOF
#!/bin/sh
PYTHONPATH="$ROOT/src\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PYTHON" -m ai_usage "\$@"
EOF
chmod +x "$BIN"

cat > "$LAUNCHER" <<EOF
[Desktop Entry]
Type=Application
Name=AI Usage
Comment=Claude usage limits and token stats in the top bar
Exec=$BIN tray
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;Monitor;
EOF

echo "Installed: $BIN"
echo "Launcher:  $LAUNCHER"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "Note: $BIN_DIR is not in PATH; add it or call $BIN directly." ;;
esac
# Refresh an existing autostart entry so it points at the installed wrapper.
if [[ -f "$AUTOSTART" ]]; then
    "$BIN" autostart enable >/dev/null && echo "Autostart entry updated."
fi
echo "Run 'ai-usage' for a summary, 'ai-usage tray &' for the top bar, 'ai-usage autostart enable' to start on login."
