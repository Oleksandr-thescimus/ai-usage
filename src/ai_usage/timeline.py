"""Timeline series (per hour / day / week / month) built from hourly usage buckets.

The range comes from the shared period selector ("Last 30 days", ...), so the chart and
the tables always cover the same span; the granularity only sets the bar size. "Today"
is shown by hour.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ai_usage.models import TokenTotals, UsageBucket

GRANULARITIES = {"day": "Day", "week": "Week", "month": "Month"}  # user-selectable
METRICS = {
    "output": "Output",
    "total": "Total",
    "requests": "Requests",
    "input": "Input + cache write",
}
UNIT = {"hour": "hour", "day": "day", "week": "week", "month": "month"}
MAX_BARS = 120  # more would make columns too thin to read or hover


@dataclass
class Point:
    start: date  # first day of the period (local); a datetime for hours
    totals: TokenTotals = field(default_factory=TokenTotals)
    partial: bool = False  # the current, still running period
    # Only part of this period is covered: the history starts inside it (e.g. Sep 28 for
    # "September") or the selected range cuts it (a week starting before "Last 7 days").
    # Its total is a lower bound, so it's drawn lighter and left out of the average.
    incomplete: bool = False

    def value(self, metric: str) -> float:
        return metric_value(self.totals, metric)


@dataclass(frozen=True)
class Trend:
    current: float
    previous: float
    complete: bool  # the history reaches back to the start of the previous window

    @property
    def change(self) -> float | None:
        """Relative change, e.g. 0.23 for +23%. None when there's nothing fair to compare
        to: no usage before, or the history doesn't cover the whole previous window
        (a full month vs a few days would show a meaningless +1000%)."""
        if not self.complete or self.previous <= 0:
            return None
        return self.current / self.previous - 1


def metric_value(t: TokenTotals, metric: str) -> float:
    if metric == "output":
        return t.output
    if metric == "total":
        return t.total
    if metric == "requests":
        return t.requests
    if metric == "input":
        return t.input + t.cache_write
    raise ValueError(f"unknown metric: {metric!r}")


def period_start(d: date, granularity: str) -> date:
    if granularity == "day":
        return d
    if granularity == "week":
        return d - timedelta(days=d.weekday())  # ISO weeks start on Monday
    if granularity == "month":
        return d.replace(day=1)
    raise ValueError(f"unknown granularity: {granularity!r}")


def next_start(d: date, granularity: str) -> date:
    if granularity == "day":
        return d + timedelta(days=1)
    if granularity == "week":
        return d + timedelta(days=7)
    if granularity == "month":
        return date(d.year + d.month // 12, d.month % 12 + 1, 1)
    raise ValueError(f"unknown granularity: {granularity!r}")


def local_day(bucket: UsageBucket) -> date:
    return bucket.hour.astimezone().date()


def history_start(buckets: Iterable[UsageBucket]) -> date | None:
    """First local day in the history (all projects: coverage of the history itself)."""
    return min((local_day(b) for b in buckets), default=None)


def _first_bar(since: date | None, first_history_day: date | None, first_data: date | None,
               granularity: str) -> date | None:
    # Days before the history starts are unknown, not zero: don't draw bars for them.
    if since is None:
        start = first_data
    elif first_history_day is None:
        start = None
    else:
        start = max(since, first_history_day)
    return None if start is None else period_start(start, granularity)


def count_bars(buckets: list[UsageBucket], granularity: str, since: date | None,
               today: date | None = None) -> int:
    """How many bars build() would draw (to rule out granularities that are too fine)."""
    today = today or datetime.now().astimezone().date()
    first = _first_bar(since, history_start(buckets), history_start(buckets), granularity)
    if first is None:
        return 0
    count, cursor, end = 0, first, period_start(today, granularity)
    while cursor <= end:
        count += 1
        cursor = next_start(cursor, granularity)
    return count


def build(
    buckets: Iterable[UsageBucket],
    granularity: str,
    project: str | None = None,
    since: date | None = None,
    today: date | None = None,
) -> list[Point]:
    """Continuous series (empty periods included) from `since` (None: the first data) to
    the current period. Data before `since` is left out, so sums match the tables."""
    buckets = list(buckets)
    today = today or datetime.now().astimezone().date()
    end = period_start(today, granularity)
    first_history_day = history_start(buckets)

    sums: dict[date, TokenTotals] = {}
    first_data: date | None = None
    for b in buckets:
        if project is not None and b.project != project:
            continue
        day = local_day(b)
        if since is not None and day < since:
            continue
        start = period_start(day, granularity)
        if start > end:
            continue  # clock skew: ignore the future
        first_data = start if first_data is None else min(first_data, start)
        sums.setdefault(start, TokenTotals()).add_bucket(b)

    cursor = _first_bar(since, first_history_day, first_data, granularity)
    if cursor is None or (since is None and not sums):
        return []
    covered_from = max(d for d in (since, first_history_day) if d is not None)
    points = []
    while cursor <= end:
        points.append(Point(cursor, sums.get(cursor, TokenTotals()), partial=cursor == end,
                            incomplete=cursor < covered_from))
        cursor = next_start(cursor, granularity)
    return points


def build_hours(buckets: Iterable[UsageBucket], project: str | None = None,
                now: datetime | None = None) -> list[Point]:
    """Today's local hours from midnight to the current hour."""
    now = (now or datetime.now()).astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sums: dict[int, TokenTotals] = {}
    for b in buckets:
        if project is not None and b.project != project:
            continue
        local = b.hour.astimezone(now.tzinfo)
        if local.date() == now.date() and local <= now:
            sums.setdefault(local.hour, TokenTotals()).add_bucket(b)
    return [
        Point(midnight.replace(hour=h), sums.get(h, TokenTotals()), partial=h == now.hour)
        for h in range(now.hour + 1)
    ]


def average(points: list[Point], metric: str) -> float | None:
    """Mean per period over complete periods (empty ones count as zero; the running
    period and partly covered ones are left out)."""
    complete = [p.value(metric) for p in points if not p.partial and not p.incomplete]
    return sum(complete) / len(complete) if complete else None


def trend(buckets: Iterable[UsageBucket], metric: str, since: datetime, project: str | None = None,
          now: datetime | None = None) -> Trend:
    """The selected range [since, now] vs the equally long span right before it (so
    "Today" compares with yesterday up to the same hour)."""
    now = now or datetime.now().astimezone()
    previous_start = since - (now - since)
    current, previous = TokenTotals(), TokenTotals()
    earliest = None
    for b in buckets:
        earliest = b.hour if earliest is None or b.hour < earliest else earliest
        if project is not None and b.project != project:
            continue
        if since <= b.hour <= now:
            current.add_bucket(b)
        elif previous_start <= b.hour < since:
            previous.add_bucket(b)
    complete = earliest is not None and earliest <= previous_start
    return Trend(metric_value(current, metric), metric_value(previous, metric), complete)


def axis_label(start: date, granularity: str) -> str:
    if granularity == "hour":
        return f"{start:%H}:00"
    if granularity == "month":
        return start.strftime("%b %Y") if start.month == 1 else start.strftime("%b")
    return start.strftime("%b %d").replace(" 0", " ")


def full_label(start: date, granularity: str) -> str:
    if granularity == "hour":
        return f"{start:%a, %b} {start.day} · {start:%H}:00–{(start.hour + 1) % 24:02d}:00"
    if granularity == "day":
        return start.strftime("%a, %b %d, %Y").replace(" 0", " ")
    if granularity == "week":
        end = next_start(start, "week") - timedelta(days=1)
        return f"Week of {start:%b %d} – {end:%b %d, %Y}".replace(" 0", " ")
    return start.strftime("%B %Y")


def table_label(start: date, granularity: str) -> str:
    """Sortable as text: '2026-11-03 14:00', '2026-11-03', '2026-10-27 … 11-02', '2026-10'."""
    if granularity == "hour":
        return f"{start:%Y-%m-%d %H}:00"
    if granularity == "day":
        return start.isoformat()
    if granularity == "week":
        end = next_start(start, "week") - timedelta(days=1)
        return f"{start.isoformat()} … {end:%m-%d}"
    return start.strftime("%Y-%m")
