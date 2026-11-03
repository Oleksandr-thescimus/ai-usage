"""Provider-neutral data types shared by the CLI and the tray."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

WARNING_PERCENT = 80.0
CRITICAL_PERCENT = 95.0


def severity_for(percent: float | None) -> str:
    if percent is None:
        return "unknown"
    if percent >= CRITICAL_PERCENT:
        return "critical"
    if percent >= WARNING_PERCENT:
        return "warning"
    return "normal"


@dataclass(frozen=True)
class LimitWindow:
    key: str  # stable id, e.g. "session", "weekly_all", "seven_day_opus"
    group: str  # "session" | "weekly" | other
    label: str  # human label, e.g. "Session (5h)"
    percent: float  # 0..100 (may exceed 100 if the API says so)
    resets_at: datetime | None  # timezone-aware

    @property
    def severity(self) -> str:
        return severity_for(self.percent)


@dataclass
class LimitsSnapshot:
    provider: str
    plan: str | None
    windows: list[LimitWindow]
    fetched_at: datetime | None  # when the windows were fetched from the API
    stale: bool = False  # windows come from the on-disk cache, not a fresh fetch
    error: str | None = None  # human-readable reason the fresh fetch failed
    error_kind: str | None = None  # machine-readable, see LimitsError.kind
    retry_after: float | None = None  # seconds, from a 429 Retry-After header

    def window(self, group: str) -> LimitWindow | None:
        """First window of a group, preferring the 'all models' weekly window."""
        candidates = [w for w in self.windows if w.group == group]
        for w in candidates:
            if w.key in ("weekly_all", "seven_day", "session", "five_hour"):
                return w
        return candidates[0] if candidates else None

    @property
    def max_percent(self) -> float | None:
        return max((w.percent for w in self.windows), default=None)


@dataclass(frozen=True)
class UsageRecord:
    timestamp: datetime  # timezone-aware (UTC)
    model: str
    project: str
    session_id: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int


@dataclass
class TokenTotals:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    requests: int = 0
    sessions: set[str] = field(default_factory=set, repr=False)

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read

    def add(self, rec: UsageRecord) -> None:
        self.input += rec.input_tokens
        self.output += rec.output_tokens
        self.cache_write += rec.cache_creation_tokens
        self.cache_read += rec.cache_read_tokens
        self.requests += 1
        self.sessions.add(rec.session_id)

    def as_dict(self) -> dict:
        return {
            "input": self.input,
            "output": self.output,
            "cache_write": self.cache_write,
            "cache_read": self.cache_read,
            "total": self.total,
            "requests": self.requests,
            "sessions": len(self.sessions),
        }
