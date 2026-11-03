"""Polling interval with exponential backoff on failures."""

from __future__ import annotations

MAX_BACKOFF_SECONDS = 30 * 60


class Backoff:
    def __init__(self, base: float, maximum: float = MAX_BACKOFF_SECONDS):
        self.base = base
        self.maximum = max(maximum, base)
        self.failures = 0

    def success(self) -> float:
        self.failures = 0
        return self.base

    def failure(self, retry_after: float | None = None) -> float:
        """Next delay after a failure: base * 2^n (capped), at least Retry-After."""
        self.failures += 1
        delay = min(self.base * (2 ** (self.failures - 1)), self.maximum)
        if retry_after is not None:
            delay = max(delay, min(retry_after, self.maximum))
        return delay
