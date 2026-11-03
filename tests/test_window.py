"""Statistics window behaviour (needs GTK 3 and a display; skipped otherwise)."""

import os
import unittest
from datetime import datetime, timedelta, timezone

from ai_usage import config
from ai_usage.models import UsageBucket

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    HAVE_GTK = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and Gtk.init_check()[0]
except (ImportError, ValueError):
    HAVE_GTK = False


def buckets():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    out = []
    for d in range(10):
        for project, n in (("~/a", 10), ("~/secret", 1000)):
            out.append(UsageBucket(now - timedelta(days=d), "m", project, 1, n, 0, 0, 1))
    return out


@unittest.skipUnless(HAVE_GTK, "needs GTK 3 and a display")
class TimelineWindowTest(unittest.TestCase):
    def setUp(self):
        from ai_usage.tray.window import PAGE_TIMELINE, StatsWindow

        config.save({})  # no hidden projects, no remembered timeline prefs
        self.win = StatsWindow("Claude", "test")
        self.addCleanup(self.win.destroy)
        self.win.update(None, [], "", buckets())
        self.page_timeline = PAGE_TIMELINE

    def combo_ids(self):
        model = self.win._project_combo.get_model()
        return [row[1] for row in model]

    def test_all_projects_shows_bars(self):
        bars = self.win._chart._bars
        self.assertGreater(len(bars), 0)
        self.assertEqual(bars[-1].value, 1010)  # both projects, metric output
        self.assertEqual(len(self.win._stores["timeline"]), len(bars))

    def test_project_filter(self):
        self.win._project_combo.set_active_id("~/a")
        self.assertEqual({b.value for b in self.win._chart._bars}, {10})

    def test_hidden_project_only_listed_in_reveal_mode(self):
        config.set_hidden("~/secret", True)
        self.win.update(None, [], "", buckets())
        self.assertNotIn("~/secret", self.combo_ids())
        self.assertEqual(self.win._chart._bars[-1].value, 1010)  # still counted in totals
        self.win._set_reveal(True)
        self.assertIn("~/secret", self.combo_ids())
        self.win._project_combo.set_active_id("~/secret")
        self.win._set_reveal(False)
        self.assertEqual(self.win._project_combo.get_active_id(), "@all")  # falls back

    def test_metric_and_granularity(self):
        self.win._metric_combo.set_active_id("requests")
        self.assertEqual(self.win._chart._bars[-1].value, 2)
        self.assertEqual(config.get_pref("timeline_metric", "output", ["output", "requests"]), "requests")

    def test_shared_period_drives_the_timeline(self):
        self.win._notebook.set_current_page(self.page_timeline)
        self.assertTrue(self.win._period_combo.get_sensitive())
        self.win._period_combo.set_active_id("7d")
        self.assertEqual(len(self.win._chart._bars), 7)
        self.win._period_combo.set_active_id("all")
        self.assertEqual(len(self.win._chart._bars), 10)
        self.win._period_combo.set_active_id("today")
        self.assertEqual(len(self.win._chart._bars), datetime.now().hour + 1)  # by hour
        self.assertFalse(any(b.get_sensitive() for b, _ in self.win._granularity_buttons.values()))
        self.assertEqual(config.get_pref("period", "7d", ["7d", "today"]), "today")  # remembered

    def test_too_fine_granularity_falls_back_without_losing_the_choice(self):
        long = [UsageBucket(datetime.now(timezone.utc) - timedelta(days=d), "m", "~/a", 1, 1, 0, 0, 1)
                for d in range(0, 300, 3)]
        self.win.update(None, [], "", long)
        self.win._period_combo.set_active_id("all")
        day, _ = self.win._granularity_buttons["day"]
        week, _ = self.win._granularity_buttons["week"]
        self.assertFalse(day.get_sensitive())
        self.assertTrue(week.get_active())
        self.assertEqual(self.win._granularity, "day")  # the saved choice is kept...
        self.win._period_combo.set_active_id("30d")
        self.assertTrue(day.get_active())  # ...and comes back for a shorter period


if __name__ == "__main__":
    unittest.main()
