"""Aggregation, formatting, backoff, cache, credentials, icons."""

import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from ai_usage import cache, fmt
from ai_usage.aggregate import group_by, in_period, period_start, totals
from ai_usage.backoff import Backoff
from ai_usage.models import LimitsSnapshot, LimitWindow, UsageRecord
from ai_usage.providers.claude.credentials import load_credentials
from ai_usage.tray.icons import gauge_svg

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 11, 3, 17, 30, tzinfo=TZ)


def rec(ts, model="opus", project="p", out=10):
    return UsageRecord(ts, model, project, "s", 1, out, 100, 1000)


class AggregateTest(unittest.TestCase):
    def test_period_start(self):
        self.assertEqual(period_start("today", NOW), datetime(2026, 11, 3, tzinfo=TZ))
        self.assertEqual(period_start("7d", NOW), datetime(2026, 10, 28, tzinfo=TZ))
        self.assertEqual(period_start("90d", NOW), datetime(2026, 8, 6, tzinfo=TZ))
        self.assertEqual(period_start("12m", NOW), datetime(2025, 12, 1, tzinfo=TZ))  # 12 whole months
        self.assertIsNone(period_start("all", NOW))
        with self.assertRaises(ValueError):
            period_start("year", NOW)

    def test_in_period_and_totals(self):
        records = [
            rec(datetime(2026, 11, 3, 0, 5, tzinfo=TZ)),
            rec(datetime(2026, 11, 2, 23, 59, tzinfo=TZ)),
            rec(datetime(2026, 10, 1, tzinfo=TZ)),
        ]
        self.assertEqual(len(in_period(records, "today", NOW)), 1)
        self.assertEqual(len(in_period(records, "7d", NOW)), 2)
        t = totals(records)
        self.assertEqual((t.input, t.output, t.cache_write, t.cache_read, t.requests), (3, 30, 300, 3000, 3))
        self.assertEqual(t.total, 3333)

    def test_group_by(self):
        records = [
            rec(datetime(2026, 11, 1, tzinfo=TZ), model="a", out=1),
            rec(datetime(2026, 11, 3, tzinfo=TZ), model="b", out=500),
            rec(datetime(2026, 11, 3, 5, tzinfo=TZ), model="b", out=500),
        ]
        self.assertEqual([k for k, _ in group_by(records, "model")], ["b", "a"])
        self.assertEqual([k for k, _ in group_by(records, "day")], ["2026-11-03", "2026-11-01"])


class FmtTest(unittest.TestCase):
    def test_human_tokens(self):
        cases = {0: "0", 999: "999", 1000: "1k", 1234: "1.2k", 450_000: "450k", 1_250_000: "1.2M",
                 150_000_000: "150M", 2_500_000_000: "2.5B"}
        for n, expected in cases.items():
            self.assertEqual(fmt.human_tokens(n), expected, n)

    def test_duration(self):
        self.assertEqual(fmt.human_duration(42), "42s")
        self.assertEqual(fmt.human_duration(12 * 60), "12m")
        self.assertEqual(fmt.human_duration(2 * 3600 + 5 * 60), "2h 05m")
        self.assertEqual(fmt.human_duration(3 * 86400 + 4 * 3600), "3d 4h")

    def test_reset_text(self):
        self.assertEqual(fmt.reset_text(NOW + timedelta(hours=1, minutes=40), NOW), "today 19:10 (in 1h 40m)")
        self.assertEqual(fmt.reset_text(datetime(2026, 11, 5, 19, 59, tzinfo=timezone.utc), NOW),
                         "Thu 21:59 (in 2d 4h)")
        self.assertEqual(fmt.reset_text(NOW - timedelta(minutes=1), NOW), "today 17:29 (now)")
        self.assertEqual(fmt.reset_text(None, NOW), "reset time unknown")

    def test_bar(self):
        self.assertEqual(fmt.bar(50, 10), "█████░░░░░")
        self.assertEqual(fmt.bar(150, 4), "████")


class BackoffTest(unittest.TestCase):
    def test_doubles_and_caps(self):
        b = Backoff(180, maximum=1000)
        self.assertEqual([b.failure() for _ in range(4)], [180, 360, 720, 1000])
        self.assertEqual(b.success(), 180)
        self.assertEqual(b.failure(), 180)

    def test_retry_after(self):
        self.assertEqual(Backoff(60).failure(retry_after=600), 600)


class CacheTest(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
            self.assertIsNone(cache.load_limits("claude"))
            snap = LimitsSnapshot("claude", "pro", [LimitWindow("session", "session", "S", 42.0, NOW)], NOW)
            cache.save_limits(snap)
            loaded = cache.load_limits("claude")
            self.assertTrue(loaded.stale)
            self.assertEqual((loaded.plan, loaded.windows, loaded.fetched_at), ("pro", snap.windows, NOW))


class CredentialsTest(unittest.TestCase):
    def _write(self, data) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write(data if isinstance(data, str) else json.dumps(data))
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def test_valid(self):
        path = self._write({"claudeAiOauth": {"accessToken": "sk-secret", "expiresAt": 1790000000000,
                                              "subscriptionType": "pro"}})
        creds = load_credentials(path)
        self.assertEqual(creds.subscription_type, "pro")
        self.assertEqual(creds.expires_at, datetime.fromtimestamp(1790000000, tz=timezone.utc))
        self.assertNotIn("sk-secret", repr(creds))
        self.assertTrue(creds.is_expired(datetime(2027, 1, 1, tzinfo=timezone.utc)))
        self.assertFalse(creds.is_expired(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    def test_invalid(self):
        self.assertIsNone(load_credentials(None))
        self.assertIsNone(load_credentials(Path("/nonexistent/creds.json")))
        self.assertIsNone(load_credentials(self._write("{broken")))
        self.assertIsNone(load_credentials(self._write({"claudeAiOauth": {"accessToken": ""}})))


class IconTest(unittest.TestCase):
    def test_valid_svg(self):
        for percent in (None, 0, 3, 50, 51, 99.99, 100, 140):
            ET.fromstring(gauge_svg(percent, "normal"))


if __name__ == "__main__":
    unittest.main()
