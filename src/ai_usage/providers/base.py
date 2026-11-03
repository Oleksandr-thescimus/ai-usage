"""Interface every AI provider implements."""

from __future__ import annotations

from typing import Protocol

from ai_usage.models import LimitsSnapshot, UsageRecord


class Provider(Protocol):
    name: str  # machine id, e.g. "claude"
    title: str  # display name, e.g. "Claude"
    usage_scope: str  # what the token stats cover, e.g. "Claude Code, this machine"

    def fetch_limits(self) -> LimitsSnapshot:
        """Fresh limits, or the cached ones marked stale with an error. Never raises."""
        ...

    def cached_limits(self) -> LimitsSnapshot | None:
        """Last known limits from disk (no network), for instant display at startup."""
        ...

    def usage_records(self) -> list[UsageRecord]:
        """All known usage records; repeated calls only read new data. Not thread-safe."""
        ...
