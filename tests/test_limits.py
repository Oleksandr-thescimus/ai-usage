import io
import json
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from ai_usage.providers.claude.limits import LimitsError, fetch_usage_json, parse_usage

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "oauth_usage.json").read_text())


class ParseUsageTest(unittest.TestCase):
    def test_real_response_uses_limits_array(self):
        windows = parse_usage(FIXTURE)
        self.assertEqual([w.key for w in windows], ["session", "weekly_all"])
        self.assertEqual([w.group for w in windows], ["session", "weekly"])
        self.assertEqual(windows[0].label, "Session (5h)")
        self.assertEqual(windows[0].percent, 42)
        self.assertEqual(windows[0].resets_at.tzinfo is not None, True)

    def test_flat_keys_fallback(self):
        data = {k: v for k, v in FIXTURE.items() if k != "limits"}
        data["seven_day_opus"] = {"utilization": 81.5, "resets_at": "2026-01-19T20:00:00Z"}
        windows = parse_usage(data)
        self.assertEqual([w.key for w in windows], ["session", "weekly_all", "weekly_opus"])
        self.assertEqual(windows[2].percent, 81.5)
        self.assertEqual(windows[2].severity, "warning")
        self.assertEqual(windows[2].resets_at, datetime(2026, 1, 19, 20, tzinfo=timezone.utc))

    def test_garbage_limits_array_falls_back_to_flat(self):
        data = dict(FIXTURE, limits=[None, {"kind": 1}, {"kind": "x", "percent": "5"}])
        self.assertEqual([w.key for w in parse_usage(data)], ["session", "weekly_all"])

    def test_unknown_kind_and_scope(self):
        data = {"limits": [{"kind": "weekly_fancy_model", "group": "weekly", "percent": 12, "scope": "Fable"}]}
        (w,) = parse_usage(data)
        self.assertEqual(w.label, "Weekly fancy model — Fable")
        self.assertEqual(w.key, "weekly_fancy_model:Fable")
        self.assertIsNone(w.resets_at)

    def test_epoch_reset_time(self):
        data = {"five_hour": {"utilization": 1, "resets_at": 1_800_000_000}}
        (w,) = parse_usage(data)
        self.assertEqual(w.resets_at, datetime.fromtimestamp(1_800_000_000, tz=timezone.utc))

    def test_nothing_known(self):
        self.assertEqual(parse_usage({"tangelo": None, "limits": []}), [])


def _http_error(code, headers=None):
    return urllib.error.HTTPError("https://x", code, "err", headers or {}, io.BytesIO(b""))


class FetchErrorsTest(unittest.TestCase):
    def _fetch_with(self, exc):
        with mock.patch("urllib.request.urlopen", side_effect=exc):
            with self.assertRaises(LimitsError) as ctx:
                fetch_usage_json("secret-token")
        return ctx.exception

    def test_401_is_auth(self):
        self.assertEqual(self._fetch_with(_http_error(401)).kind, "auth")

    def test_429_with_retry_after(self):
        err = self._fetch_with(_http_error(429, {"Retry-After": "120"}))
        self.assertEqual((err.kind, err.retry_after), ("rate_limited", 120.0))

    def test_500_is_http(self):
        self.assertEqual(self._fetch_with(_http_error(500)).kind, "http")

    def test_network(self):
        self.assertEqual(self._fetch_with(urllib.error.URLError("no route")).kind, "network")

    def test_token_not_in_error_message(self):
        for exc in (_http_error(401), _http_error(500), urllib.error.URLError("x")):
            self.assertNotIn("secret-token", str(self._fetch_with(exc)))

    def test_sends_beta_header(self):
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"limits": []}')
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.assertEqual(fetch_usage_json("tok"), {"limits": []})
        request = urlopen.call_args[0][0]
        self.assertEqual(request.get_header("Anthropic-beta"), "oauth-2025-04-20")
        self.assertEqual(request.get_header("Authorization"), "Bearer tok")


if __name__ == "__main__":
    unittest.main()
