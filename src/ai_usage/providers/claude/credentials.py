"""Read-only access to Claude Code's OAuth credentials.

We never refresh or write the token: refresh tokens rotate, and refreshing behind
Claude Code's back could log it out. Claude Code refreshes the token while it runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

EXPIRY_SKEW_SECONDS = 60


@dataclass(frozen=True)
class Credentials:
    access_token: str = field(repr=False)
    expires_at: datetime | None
    subscription_type: str | None
    rate_limit_tier: str | None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        return (self.expires_at - now).total_seconds() < EXPIRY_SKEW_SECONDS


def load_credentials(path: Path | None) -> Credentials | None:
    """Parse the credentials file; None if missing, unreadable or without a token."""
    if path is None:
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        return None
    expires_at = None
    raw = oauth.get("expiresAt")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        # Milliseconds since epoch (seconds if the value is suspiciously small).
        seconds = raw / 1000 if raw > 10**11 else raw
        expires_at = datetime.fromtimestamp(seconds, tz=timezone.utc)
    sub = oauth.get("subscriptionType")
    tier = oauth.get("rateLimitTier")
    return Credentials(
        access_token=token,
        expires_at=expires_at,
        subscription_type=sub if isinstance(sub, str) else None,
        rate_limit_tier=tier if isinstance(tier, str) else None,
    )
