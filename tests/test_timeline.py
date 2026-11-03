"""Persistent history (merge semantics) and timeline series."""

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from ai_usage import timeline as tl
from ai_usage.history import UsageHistory, aggregate
from ai_usage.models import UsageBucket, UsageRecord
from ai_usage.providers.claude import ClaudeProvider
from tests.test_logs import assistant, line


def rec(day: date, out=10, project="~/p", key="-p", model="m", hour=12):
    ts = datetime(day.year, day.month, day.day, hour, 30, tzinfo=timezone.utc)
    return UsageRecord(ts, model, project, "s", 1, out, 100, 1000, project_key=key)


def bucket(day: date, out=10, project="~/p", requests=1):
    # Noon UTC: the same local date in any timezone between UTC-11 and UTC+11.
    return UsageBucket(datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc),
                       "m", project, 1, out, 100, 1000, requests)


class DataDir(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "data")})
        env.start()
        self.addCleanup(env.stop)


class HistoryTest(DataDir):
    D = date(2026, 10, 1)

    def totals(self, buckets):
        return sum(b.output_tokens for b in buckets), sum(b.requests for b in buckets)

    def test_aggregate(self):
        buckets, labels = aggregate([rec(self.D, 5), rec(self.D, 7), rec(self.D, 1, "~/q", "-q")])
        self.assertEqual(buckets["2026-10-01T12"], {"m|-p": [2, 12, 200, 2000, 2], "m|-q": [1, 1, 100, 1000, 1]})
        self.assertEqual(labels, {"-p": "~/p", "-q": "~/q"})

    def test_survives_deleted_transcripts(self):
        h = UsageHistory("claude")
        old, new = rec(self.D, 50), rec(self.D + timedelta(days=40), 7)
        self.assertEqual(self.totals(h.update([old, new])), (57, 2))
        # Claude Code deleted the old transcript: it's gone from the scan but not from history.
        self.assertEqual(self.totals(UsageHistory("claude").update([new])), (57, 2))
        # A streaming copy with a larger output for the same hour raises the counter.
        self.assertEqual(self.totals(UsageHistory("claude").update([rec(self.D + timedelta(days=40), 9)])), (59, 2))

    def test_label_change_does_not_double_count(self):
        h = UsageHistory("claude")
        h.update([rec(self.D, 5, project="/tmp/wt/sub", key="-tmp-wt")])
        buckets = h.update([rec(self.D, 5, project="/tmp/wt", key="-tmp-wt")])
        self.assertEqual([(b.project, b.output_tokens) for b in buckets], [("/tmp/wt", 5)])

    def test_corrupt_file_is_kept_aside(self):
        h = UsageHistory("claude")
        h.path.parent.mkdir(parents=True)
        h.path.write_text("{broken")
        self.assertEqual(self.totals(h.update([rec(self.D)])), (10, 1))
        self.assertEqual(len(list(h.path.parent.glob("claude-history.json.corrupt-*"))), 1)

    def test_file_has_numbers_only(self):
        UsageHistory("claude").update([rec(self.D)])
        data = json.loads(UsageHistory("claude").path.read_text())
        self.assertEqual(set(data), {"version", "labels", "buckets"})

    def test_provider_keeps_history_after_transcript_cleanup(self):
        config = self.tmp / "claude"
        session = config / "projects" / "-w" / "s.jsonl"
        session.parent.mkdir(parents=True)
        session.write_text(line(assistant("m1", "r1", 42, cwd="/w", ts="2026-09-01T10:00:00Z")))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(config)}):
            first = ClaudeProvider()
            first.usage_records()
            self.assertEqual([b.output_tokens for b in first.usage_history()], [42])
            session.unlink()
            later = ClaudeProvider()
            self.assertEqual(later.usage_records(), [])
            self.assertEqual([(b.project, b.output_tokens) for b in later.usage_history()], [("/w", 42)])


class PeriodTest(unittest.TestCase):
    def test_period_start_and_next(self):
        wed = date(2026, 10, 28)
        self.assertEqual(tl.period_start(wed, "week"), date(2026, 10, 26))  # Monday
        self.assertEqual(tl.period_start(wed, "month"), date(2026, 10, 1))
        self.assertEqual(tl.next_start(date(2026, 12, 1), "month"), date(2027, 1, 1))
        self.assertEqual(tl.next_start(date(2026, 10, 26), "week"), date(2026, 11, 2))

    def test_labels(self):
        self.assertEqual(tl.axis_label(date(2026, 10, 5), "day"), "Oct 5")
        self.assertEqual(tl.axis_label(date(2027, 1, 1), "month"), "Jan 2027")
        self.assertEqual(tl.axis_label(date(2026, 10, 1), "month"), "Oct")
        self.assertEqual(tl.full_label(date(2026, 10, 26), "week"), "Week of Oct 26 – Nov 1, 2026")
        self.assertEqual(tl.table_label(date(2026, 10, 26), "week"), "2026-10-26 … 11-01")
        self.assertEqual(tl.table_label(date(2026, 10, 1), "month"), "2026-10")


class BuildTest(unittest.TestCase):
    TODAY = date(2026, 11, 3)  # a Tuesday

    def test_daily_series_is_continuous(self):
        buckets = [bucket(date(2026, 10, 30), 5), bucket(date(2026, 11, 1), 7), bucket(self.TODAY, 3)]
        points = tl.build(buckets, "day", today=self.TODAY)
        self.assertEqual([p.start.day for p in points], [30, 31, 1, 2, 3])  # trimmed to first data
        self.assertEqual([p.value("output") for p in points], [5, 0, 7, 0, 3])
        self.assertEqual([p.partial for p in points], [False] * 4 + [True])

    def test_range_and_project_filter(self):
        buckets = [bucket(self.TODAY - timedelta(days=d), 1, project="~/a" if d % 2 else "~/b")
                   for d in range(100)]
        last30 = tl.build(buckets, "day", since=self.TODAY - timedelta(days=29), today=self.TODAY)
        self.assertEqual(len(last30), 30)
        self.assertEqual(sum(p.value("requests") for p in last30), 30)  # nothing from before the range
        only_a = tl.build(buckets, "day", project="~/a", since=self.TODAY - timedelta(days=3), today=self.TODAY)
        self.assertEqual([p.value("requests") for p in only_a], [1, 0, 1, 0])

    def test_range_cutting_a_week_marks_it_incomplete(self):
        buckets = [bucket(date(2026, 10, 26), 100), bucket(date(2026, 10, 29), 7), bucket(date(2026, 11, 2), 5)]
        points = tl.build(buckets, "week", since=date(2026, 10, 28), today=self.TODAY)  # "Last 7 days"
        self.assertEqual([(p.start, p.value("output"), p.incomplete, p.partial) for p in points],
                         [(date(2026, 10, 26), 7, True, False), (date(2026, 11, 2), 5, False, True)])

    def test_range_before_history_starts_at_history(self):
        buckets = [bucket(self.TODAY - timedelta(days=d)) for d in range(10)]
        points = tl.build(buckets, "day", since=self.TODAY - timedelta(days=89), today=self.TODAY)
        self.assertEqual(len(points), 10)  # days before the history are unknown, not zero
        self.assertEqual(tl.count_bars(buckets, "day", self.TODAY - timedelta(days=89), self.TODAY), 10)

    def test_count_bars_rules_out_too_fine_granularity(self):
        buckets = [bucket(self.TODAY - timedelta(days=d)) for d in range(0, 300, 5)]
        self.assertGreater(tl.count_bars(buckets, "day", None, self.TODAY), tl.MAX_BARS)
        self.assertLessEqual(tl.count_bars(buckets, "week", None, self.TODAY), tl.MAX_BARS)

    def test_hours_of_today(self):
        now = datetime(2026, 11, 3, 14, 20).astimezone()
        at = lambda h, day=3: UsageBucket(datetime(2026, 11, day, h).astimezone().astimezone(timezone.utc),
                                          "m", "~/p", 1, 5, 0, 0, 1)
        points = tl.build_hours([at(9), at(9), at(14), at(23, day=2)], now=now)
        self.assertEqual(len(points), 15)  # 00:00 .. 14:00
        self.assertEqual(points[9].value("output"), 10)
        self.assertTrue(points[-1].partial)
        self.assertEqual(sum(p.value("requests") for p in points), 3)  # yesterday 23:00 left out
        self.assertEqual(tl.axis_label(points[9].start, "hour"), "09:00")
        self.assertEqual(tl.table_label(points[9].start, "hour"), "2026-11-03 09:00")

    def test_months_across_year_end(self):
        buckets = [bucket(date(2026, 11, 20), 1), bucket(date(2027, 1, 5), 2)]
        points = tl.build(buckets, "month", today=date(2027, 2, 10))
        self.assertEqual([(p.start.month, p.value("output")) for p in points], [(11, 1), (12, 0), (1, 2), (2, 0)])

    def test_weeks(self):
        buckets = [bucket(date(2026, 10, 26), 1), bucket(date(2026, 11, 1), 2), bucket(date(2026, 11, 2), 4)]
        points = tl.build(buckets, "week", today=self.TODAY)
        self.assertEqual([(p.start, p.value("output")) for p in points],
                         [(date(2026, 10, 26), 3), (date(2026, 11, 2), 4)])

    def test_first_period_only_partly_covered_by_history(self):
        # History starts Sep 28: "September" is a lower bound, so it doesn't count in the average.
        buckets = [bucket(date(2026, 9, 28), 100), bucket(date(2026, 10, 10), 900)]
        points = tl.build(buckets, "month", today=date(2026, 11, 3))
        self.assertEqual([(p.start.month, p.incomplete, p.partial) for p in points],
                         [(9, True, False), (10, False, False), (11, False, True)])
        self.assertEqual(tl.average(points, "output"), 900)
        # Days and a week that starts on the first history day are complete.
        self.assertFalse(any(p.incomplete for p in tl.build(buckets, "day", today=date(2026, 11, 3))))
        self.assertFalse(tl.build(buckets, "week", today=date(2026, 11, 3))[0].incomplete)  # Sep 28 = Monday

    def test_empty(self):
        self.assertEqual(tl.build([], "day", today=self.TODAY), [])

    def test_metrics(self):
        (p,) = tl.build([bucket(self.TODAY, 10, requests=3)], "day", today=self.TODAY)
        self.assertEqual({m: p.value(m) for m in tl.METRICS},
                         {"output": 10, "total": 1111, "requests": 3, "input": 101})


class StatsTest(unittest.TestCase):
    def test_average_skips_partial_and_counts_zero_days(self):
        points = [tl.Point(date(2026, 11, d)) for d in (1, 2, 3)]
        points[0].totals.output, points[2].totals.output, points[2].partial = 30, 999, True
        self.assertEqual(tl.average(points, "output"), 15)
        self.assertIsNone(tl.average(points[2:], "output"))

    def test_trend(self):
        now = datetime(2026, 11, 3, 18, tzinfo=timezone.utc)
        buckets = [bucket(date(2026, 11, 2), 12), bucket(date(2026, 10, 30), 3),  # last 7 days
                   bucket(date(2026, 10, 25), 10), bucket(date(2026, 10, 1), 999)]  # previous 7 / older
        week_ago = now - timedelta(days=7)
        t = tl.trend(buckets, "output", week_ago, now=now)
        self.assertEqual((t.current, t.previous), (15, 10))
        self.assertAlmostEqual(t.change, 0.5)
        self.assertIsNone(tl.trend([], "output", week_ago, now=now).change)

    def test_trend_needs_history_covering_previous_window(self):
        now = datetime(2026, 11, 3, 18, tzinfo=timezone.utc)
        # History starts 10 days ago: the previous 7-day window is only partly covered.
        buckets = [bucket(date(2026, 10, 24), 1), bucket(date(2026, 11, 2), 50)]
        t = tl.trend(buckets, "output", now - timedelta(days=7), now=now)
        self.assertFalse(t.complete)
        self.assertIsNone(t.change)
        # Coverage is judged on all projects: a project that simply started later is fine.
        buckets.append(bucket(date(2026, 10, 1), 5, project="~/old"))
        t = tl.trend(buckets, "output", now - timedelta(days=7), project="~/p", now=now)
        self.assertTrue(t.complete)
        self.assertEqual((t.current, t.previous), (50, 1))


if __name__ == "__main__":
    unittest.main()
