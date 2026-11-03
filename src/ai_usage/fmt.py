"""Human-readable formatting helpers (shared by CLI and tray)."""

from __future__ import annotations

from datetime import datetime


def human_tokens(n: int) -> str:
    """1234 -> '1.2k', 1_234_567 -> '1.2M'. Values below 1000 stay exact."""
    sign = "-" if n < 0 else ""
    n = abs(n)
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if n >= divisor:
            value = n / divisor
            text = f"{value:.1f}" if value < 100 else f"{value:.0f}"
            return f"{sign}{text.removesuffix('.0')}{suffix}"
    return f"{sign}{n}"


def human_duration(seconds: float) -> str:
    """Compact duration: '45s', '12m', '2h 05m', '3d 4h'."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, _ = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def reset_text(resets_at: datetime | None, now: datetime | None = None) -> str:
    """'today 19:10 (in 1h 58m)', 'Thu 21:59 (in 2d 4h)'."""
    if resets_at is None:
        return "reset time unknown"
    now = (now or datetime.now().astimezone()).astimezone()
    local = resets_at.astimezone(now.tzinfo)
    delta = (local - now).total_seconds()
    if local.date() == now.date():
        day = "today"
    elif (local.date() - now.date()).days == 1:
        day = "tomorrow"
    elif 0 < (local.date() - now.date()).days < 7:
        day = local.strftime("%a")
    else:
        day = local.strftime("%b %d")
    when = f"{day} {local:%H:%M}"
    if delta <= 0:
        return f"{when} (now)"
    return f"{when} (in {human_duration(delta)})"


def age_text(then: datetime | None, now: datetime | None = None) -> str:
    if then is None:
        return "never"
    now = now or datetime.now().astimezone()
    return f"{human_duration((now - then).total_seconds())} ago"


def bar(percent: float, width: int = 20) -> str:
    filled = round(max(0.0, min(percent, 100.0)) / 100 * width)
    return "█" * filled + "░" * (width - filled)


def percent_text(percent: float) -> str:
    return f"{percent:.0f}%"


def table(headers: list[str], rows: list[list[str]], align: str | None = None) -> str:
    """Plain text table. `align` is one char per column: 'l' or 'r'."""
    align = align or "l" + "r" * (len(headers) - 1)
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(cells: list[str]) -> str:
        parts = [c.ljust(w) if a == "l" else c.rjust(w) for c, w, a in zip(cells, widths, align)]
        return "  ".join(parts).rstrip()

    out = [line(headers), line(["─" * w for w in widths])]
    out += [line(r) for r in rows]
    return "\n".join(out)
