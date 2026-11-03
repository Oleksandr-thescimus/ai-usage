"""Ring-gauge tray icons, generated as small SVG files on demand."""

from __future__ import annotations

import math
from pathlib import Path

from ai_usage.cache import cache_dir

COLORS = {
    "normal": "#ffffff",
    "warning": "#f5c211",
    "critical": "#ff5c5c",
    "unknown": "#9a9996",
}
SIZE = 22
RADIUS = 8
STROKE = 3


def gauge_svg(percent: float | None, severity: str) -> str:
    color = COLORS.get(severity, COLORS["unknown"])
    c = SIZE / 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" viewBox="0 0 {SIZE} {SIZE}">',
        f'<circle cx="{c}" cy="{c}" r="{RADIUS}" fill="none" stroke="#ffffff" stroke-opacity="0.3" stroke-width="{STROKE}"/>',
    ]
    fraction = 0.0 if percent is None else max(0.0, min(percent, 100.0)) / 100
    if fraction >= 0.999:
        parts.append(f'<circle cx="{c}" cy="{c}" r="{RADIUS}" fill="none" stroke="{color}" stroke-width="{STROKE}"/>')
    elif fraction > 0:
        angle = 2 * math.pi * fraction
        x = c + RADIUS * math.sin(angle)
        y = c - RADIUS * math.cos(angle)
        large = 1 if fraction > 0.5 else 0
        parts.append(
            f'<path d="M {c} {c - RADIUS} A {RADIUS} {RADIUS} 0 {large} 1 {x:.2f} {y:.2f}" '
            f'fill="none" stroke="{color}" stroke-width="{STROKE}" stroke-linecap="round"/>'
        )
    if percent is None:
        parts.append(f'<circle cx="{c}" cy="{c}" r="1.6" fill="{color}"/>')
    parts.append("</svg>")
    return "".join(parts)


def icon_dir() -> Path:
    return cache_dir() / "icons"


def ensure_icon(percent: float | None, severity: str) -> tuple[str, str]:
    """Return (theme_dir, icon_name) for a gauge rounded to 5% steps."""
    bucket = "na" if percent is None else str(int(round(max(0.0, min(percent, 100.0)) / 5) * 5))
    name = f"ai-usage-{severity}-{bucket}"
    directory = icon_dir()
    path = directory / f"{name}.svg"
    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        rounded = None if percent is None else float(bucket)
        path.write_text(gauge_svg(rounded, severity))
    return str(directory), name
