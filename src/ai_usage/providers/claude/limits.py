"""Claude plan limits from the (undocumented) OAuth usage endpoint used by `/usage`."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from ai_usage import __version__
from ai_usage.models import LimitWindow

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"
TIMEOUT_SECONDS = 10

# Known `limits[].kind` values -> labels. Unknown kinds get a humanized label.
KIND_LABELS = {
    "session": "Session (5h)",
    "weekly_all": "Weekly (all models)",
    "weekly_opus": "Weekly (Opus)",
    "weekly_sonnet": "Weekly (Sonnet)",
}

# Flat top-level keys (older shape) -> (key, group, label).
FLAT_WINDOWS = {
    "five_hour": ("session", "session", "Session (5h)"),
    "seven_day": ("weekly_all", "weekly", "Weekly (all models)"),
    "seven_day_opus": ("weekly_opus", "weekly", "Weekly (Opus)"),
    "seven_day_sonnet": ("weekly_sonnet", "weekly", "Weekly (Sonnet)"),
}


class LimitsError(Exception):
    """kind: no_credentials | expired | auth | rate_limited | network | http | parse"""

    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


def fetch_usage_json(access_token: str, timeout: float = TIMEOUT_SECONDS) -> dict:
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": BETA_HEADER,
            "Accept": "application/json",
            "User-Agent": f"ai-usage/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise LimitsError("auth", "Not authorized — run `claude` to log in again") from None
        if e.code == 429:
            raise LimitsError("rate_limited", "Rate limited by the usage API", _retry_after(e)) from None
        raise LimitsError("http", f"Usage API returned HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise LimitsError("network", f"Network error: {reason}") from None
    except ValueError:
        raise LimitsError("parse", "Usage API returned invalid JSON") from None
    if not isinstance(data, dict):
        raise LimitsError("parse", "Unexpected usage API response")
    return data


def _retry_after(error: urllib.error.HTTPError) -> float | None:
    value = error.headers.get("Retry-After") if error.headers else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _parse_time(value) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _humanize(kind: str) -> str:
    return kind.replace("_", " ").strip().capitalize() or "Limit"


def _from_limits_array(items: list) -> list[LimitWindow]:
    windows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        percent = _number(item.get("percent"))
        if not isinstance(kind, str) or percent is None:
            continue
        group = item.get("group") if isinstance(item.get("group"), str) else "other"
        label = KIND_LABELS.get(kind, _humanize(kind))
        scope = item.get("scope")
        if isinstance(scope, str) and scope:
            label = f"{label} — {scope}"
        windows.append(
            LimitWindow(
                key=kind if not isinstance(scope, str) or not scope else f"{kind}:{scope}",
                group=group,
                label=label,
                percent=percent,
                resets_at=_parse_time(item.get("resets_at")),
            )
        )
    return windows


def _from_flat_keys(data: dict) -> list[LimitWindow]:
    windows = []
    for field_name, (key, group, label) in FLAT_WINDOWS.items():
        entry = data.get(field_name)
        if not isinstance(entry, dict):
            continue
        percent = _number(entry.get("utilization"))
        if percent is None:
            continue
        windows.append(LimitWindow(key, group, label, percent, _parse_time(entry.get("resets_at"))))
    return windows


def parse_usage(data: dict) -> list[LimitWindow]:
    """Prefer the structured `limits[]` array; fall back to the flat keys."""
    items = data.get("limits")
    if isinstance(items, list):
        windows = _from_limits_array(items)
        if windows:
            return windows
    return _from_flat_keys(data)
