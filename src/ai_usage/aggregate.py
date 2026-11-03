"""Time periods and grouping of usage records."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta

from ai_usage.models import TokenTotals, UsageRecord

PERIODS = {
    "today": "Today",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "all": "All time",
}

GROUPINGS = ("model", "project", "day")


def local_now() -> datetime:
    return datetime.now().astimezone()


def period_start(period: str, now: datetime | None = None) -> datetime | None:
    """Start of a period in local time; calendar days, so '7d' = today + 6 previous days."""
    if period == "all":
        return None
    days = {"today": 1, "7d": 7, "30d": 30}.get(period)
    if days is None:
        raise ValueError(f"unknown period: {period!r} (expected one of {', '.join(PERIODS)})")
    now = (now or local_now()).astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=days - 1)


def in_period(records: Iterable[UsageRecord], period: str, now: datetime | None = None) -> list[UsageRecord]:
    start = period_start(period, now)
    if start is None:
        return list(records)
    return [r for r in records if r.timestamp >= start]


def totals(records: Iterable[UsageRecord]) -> TokenTotals:
    t = TokenTotals()
    for r in records:
        t.add(r)
    return t


def _key_func(by: str) -> Callable[[UsageRecord], str]:
    if by == "model":
        return lambda r: r.model
    if by == "project":
        return lambda r: r.project
    if by == "day":
        return lambda r: r.timestamp.astimezone().date().isoformat()
    raise ValueError(f"unknown grouping: {by!r} (expected one of {', '.join(GROUPINGS)})")


def group_by(records: Iterable[UsageRecord], by: str) -> list[tuple[str, TokenTotals]]:
    """Groups sorted by total tokens (desc); days sorted newest first."""
    key = _key_func(by)
    groups: dict[str, TokenTotals] = {}
    for r in records:
        groups.setdefault(key(r), TokenTotals()).add(r)
    if by == "day":
        return sorted(groups.items(), key=lambda kv: kv[0], reverse=True)
    return sorted(groups.items(), key=lambda kv: kv[1].total, reverse=True)
