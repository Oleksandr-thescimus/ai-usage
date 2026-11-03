"""Claude provider: plan limits from the OAuth usage API + tokens from Claude Code logs."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_usage import cache
from ai_usage.models import LimitsSnapshot, UsageRecord
from ai_usage.providers.claude import paths
from ai_usage.providers.claude.credentials import load_credentials
from ai_usage.providers.claude.limits import LimitsError, fetch_usage_json, parse_usage
from ai_usage.providers.claude.logs import LogScanner


class ClaudeProvider:
    name = "claude"
    title = "Claude"
    usage_scope = "Claude Code, this machine"

    def __init__(self) -> None:
        self._scanner = LogScanner(paths.projects_dirs())

    def fetch_limits(self) -> LimitsSnapshot:
        creds = load_credentials(paths.credentials_file())
        plan = creds.subscription_type if creds else None
        try:
            if creds is None:
                raise LimitsError("no_credentials", "No Claude Code login found — run `claude` and log in")
            if creds.is_expired():
                raise LimitsError("expired", "Token expired — run `claude` to refresh it")
            windows = parse_usage(fetch_usage_json(creds.access_token))
            if not windows:
                raise LimitsError("parse", "Usage API returned no known limit windows")
        except LimitsError as e:
            return self._fallback(plan, e)
        snapshot = LimitsSnapshot(self.name, plan, windows, fetched_at=datetime.now(timezone.utc))
        try:
            cache.save_limits(snapshot)
        except OSError:
            pass  # a read-only cache dir must not break the display
        return snapshot

    def _fallback(self, plan: str | None, error: LimitsError) -> LimitsSnapshot:
        cached = cache.load_limits(self.name)
        snapshot = cached or LimitsSnapshot(self.name, plan, [], fetched_at=None, stale=True)
        snapshot.plan = plan or snapshot.plan
        snapshot.error = str(error)
        snapshot.error_kind = error.kind
        snapshot.retry_after = error.retry_after
        return snapshot

    def cached_limits(self) -> LimitsSnapshot | None:
        return cache.load_limits(self.name)

    def usage_records(self) -> list[UsageRecord]:
        return self._scanner.scan()
