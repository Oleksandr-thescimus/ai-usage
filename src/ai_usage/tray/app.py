"""Top bar indicator: label with limit %, gauge icon, menu, background refresh."""

from __future__ import annotations

import fcntl
import os
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import GLib, Gtk  # noqa: E402

from ai_usage import autostart, fmt  # noqa: E402
from ai_usage.aggregate import in_period, totals  # noqa: E402
from ai_usage.backoff import Backoff  # noqa: E402
from ai_usage.models import LimitsSnapshot, UsageBucket, UsageRecord, severity_for  # noqa: E402
from ai_usage.providers.base import Provider  # noqa: E402
from ai_usage.tray.icons import ensure_icon  # noqa: E402

TOKENS_INTERVAL_SECONDS = 60
MIN_LIMITS_INTERVAL_SECONDS = 60
STALE_AFTER_SECONDS = 15 * 60
TRANSIENT_ERRORS = {"rate_limited", "network", "http", "parse"}
LABEL_GUIDE = "100% · 100%*"
ZERO_WIDTH_SPACE = "​"
# Re-send the label this long after the panel accepts the indicator (see _apply_label).
LABEL_REEMIT_DELAYS_MS = (1000, 3000, 6000)


class TrayApp:
    def __init__(self, provider: Provider, limits_interval: int):
        self.provider = provider
        self.backoff = Backoff(max(MIN_LIMITS_INTERVAL_SECONDS, limits_interval))
        self.snapshot: LimitsSnapshot | None = provider.cached_limits()
        self.records: list[UsageRecord] = []
        self.buckets: list[UsageBucket] = []
        self.records_loaded = False
        self.window = None

        # At most one background job per data source (guarded by the *_busy flags), so the
        # provider's scanner state is never touched concurrently and needs no locks.
        self._limits_timer: int | None = None
        self._tokens_timer: int | None = None
        self._limits_busy = False
        self._tokens_busy = False

        self.indicator = AppIndicator.Indicator.new(
            "ai-usage", "utilities-system-monitor-symbolic", AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_title(f"AI Usage — {provider.title}")
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._label = ""
        self._label_flip = False
        self.indicator.connect("connection-changed", self._on_connection_changed)
        self._build_menu()
        self.render()
        self.refresh_limits()
        self.refresh_tokens()

    # ----- menu -------------------------------------------------------------------

    def _item(self, label: str, callback=None) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=label)
        item.connect("activate", callback or self.open_window)
        return item

    def _build_menu(self) -> None:
        self.menu = Gtk.Menu()
        self.header_item = self._item(self.provider.title)
        self.menu.append(self.header_item)
        self.limit_items: list[Gtk.MenuItem] = []
        self.limits_end = Gtk.SeparatorMenuItem()  # limit rows are inserted before this
        self.menu.append(self.limits_end)
        self.today_item = self._item("Today: loading…")
        self.week_item = self._item("Last 7 days: loading…")
        self.menu.append(self.today_item)
        self.menu.append(self.week_item)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.menu.append(self._item("Statistics…"))
        self.menu.append(self._item("Refresh now", self.refresh_all))
        self.autostart_item = Gtk.CheckMenuItem(label="Start on login")
        self.autostart_item.set_active(autostart.is_enabled())
        self._autostart_handler = self.autostart_item.connect("toggled", self._on_autostart_toggled)
        self.menu.append(self.autostart_item)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.status_item = Gtk.MenuItem(label="Starting…")
        self.status_item.set_sensitive(False)
        self.menu.append(self.status_item)
        self.menu.append(self._item("Quit", self.quit))
        self.menu.show_all()
        self.indicator.set_menu(self.menu)

    def _set_limit_rows(self, labels: list[str]) -> None:
        if len(labels) != len(self.limit_items):
            for item in self.limit_items:
                self.menu.remove(item)
            self.limit_items = []
            position = self.menu.get_children().index(self.limits_end)
            for i in range(len(labels)):
                item = self._item("")
                self.menu.insert(item, position + i)
                item.show()
                self.limit_items.append(item)
        for item, label in zip(self.limit_items, labels):
            item.set_label(label)

    def _on_autostart_toggled(self, item: Gtk.CheckMenuItem) -> None:
        try:
            autostart.enable() if item.get_active() else autostart.disable()
        except OSError as e:
            print(f"ai-usage: autostart: {e}", file=sys.stderr)
        self.autostart_item.handler_block(self._autostart_handler)
        self.autostart_item.set_active(autostart.is_enabled())
        self.autostart_item.handler_unblock(self._autostart_handler)

    # ----- top bar label ----------------------------------------------------------

    def _apply_label(self, label: str) -> None:
        """Set the label so that the panel actually shows it.

        The GNOME AppIndicator extension doesn't read XAyatanaLabel when an indicator
        appears (the property isn't in its D-Bus interface description); it only reacts to
        the XAyatanaNewLabel signal. libayatana emits that signal only when the text changes,
        so a label set before the panel subscribed, or an unchanged label after a shell
        restart or screen unlock, never shows. Alternating an invisible zero-width space
        makes every call emit the signal.
        """
        self._label = label
        if not label:
            self.indicator.set_label("", "")
            return
        self._label_flip = not self._label_flip
        suffix = ZERO_WIDTH_SPACE if self._label_flip else ""
        self.indicator.set_label(label + suffix, LABEL_GUIDE)

    def _on_connection_changed(self, _indicator, connected: bool) -> None:
        if connected:
            # The extension subscribes to our signals a moment after registration.
            for delay in LABEL_REEMIT_DELAYS_MS:
                GLib.timeout_add(delay, self._reemit_label)

    def _reemit_label(self) -> bool:
        self._apply_label(self._label)
        return False  # one-shot

    # ----- rendering --------------------------------------------------------------

    def _status_text(self) -> str:
        s = self.snapshot
        if s is None:
            return "Fetching limits…"
        if s.error:
            data = f" (showing data from {fmt.age_text(s.fetched_at)})" if s.windows else ""
            return f"⚠ {s.error}{data}"
        if s.fetched_at:
            return f"Updated {s.fetched_at.astimezone():%H:%M}"
        return ""

    def render(self) -> None:
        s = self.snapshot
        windows = s.windows if s else []
        session = s.window("session") if s else None
        weekly = s.window("weekly") if s else None

        shown = [w for w in (session, weekly) if w is not None] or windows[:2]
        label = " · ".join(fmt.percent_text(w.percent) for w in shown)
        if label and s and s.fetched_at:
            age = (datetime.now(s.fetched_at.tzinfo) - s.fetched_at).total_seconds()
            if s.error and age > STALE_AFTER_SECONDS:
                label += "*"
        self._apply_label(label)

        gauge = session or (shown[0] if shown else None)
        severity = severity_for(s.max_percent) if windows else "unknown"
        theme_dir, icon_name = ensure_icon(gauge.percent if gauge else None, severity)
        self.indicator.set_icon_theme_path(theme_dir)
        self.indicator.set_icon_full(icon_name, label or "AI usage")

        plan = f" · {s.plan} plan" if s and s.plan else ""
        self.header_item.set_label(f"{self.provider.title}{plan}")
        self._set_limit_rows(
            [f"{w.label}: {fmt.percent_text(w.percent)} · resets {fmt.reset_text(w.resets_at)}" for w in windows]
        )
        if self.records_loaded:
            for item, period, title in ((self.today_item, "today", "Today"), (self.week_item, "7d", "Last 7 days")):
                t = totals(in_period(self.records, period))
                item.set_label(
                    f"{title}: {fmt.human_tokens(t.total)} tokens · {fmt.human_tokens(t.output)} out · {t.requests} req"
                )
        self.status_item.set_label(self._status_text())
        if self.window is not None and self.window.get_visible():
            self.window.update(self.snapshot, self.records, self._status_text(), self.buckets)

    # ----- refresh scheduling -----------------------------------------------------

    @staticmethod
    def _in_background(name: str, func, on_done) -> None:
        """Run func in a daemon thread (quitting never waits for a long log scan) and
        deliver (result, error) to on_done on the GTK main thread."""

        def worker() -> None:
            try:
                result, error = func(), None
            except Exception as e:  # reported on the main thread
                result, error = None, e
            GLib.idle_add(on_done, result, error)

        threading.Thread(target=worker, name=f"ai-usage-{name}", daemon=True).start()

    @staticmethod
    def _cancel(timer: int | None) -> None:
        if timer is not None:
            GLib.source_remove(timer)

    def refresh_all(self, *_args) -> None:
        self.refresh_limits()
        self.refresh_tokens()

    def refresh_limits(self, *_args) -> bool:
        self._cancel(self._limits_timer)
        self._limits_timer = None
        if not self._limits_busy:
            self._limits_busy = True
            self._in_background("limits", self.provider.fetch_limits, self._on_limits)
        return False  # one-shot GLib timeout

    def _on_limits(self, snapshot: LimitsSnapshot | None, error: Exception | None) -> bool:
        self._limits_busy = False
        if error is not None or snapshot is None:  # fetch_limits shouldn't raise; never let the tray die
            snapshot = self.snapshot or LimitsSnapshot(self.provider.name, None, [], None, stale=True)
            snapshot.error, snapshot.error_kind = f"Internal error: {error}", "internal"
        self.snapshot = snapshot
        if snapshot.error_kind in TRANSIENT_ERRORS:
            delay = self.backoff.failure(snapshot.retry_after)
        else:
            # Success, or a credentials problem: re-check at the normal pace, since
            # Claude Code refreshes the token file on its own.
            delay = self.backoff.success()
        self._limits_timer = GLib.timeout_add_seconds(int(delay), self.refresh_limits)
        self.render()
        return False

    def refresh_tokens(self, *_args) -> bool:
        self._cancel(self._tokens_timer)
        self._tokens_timer = None
        if not self._tokens_busy:
            self._tokens_busy = True
            self._in_background("logs", self._load_usage, self._on_tokens)
        return False

    def _load_usage(self) -> tuple[list[UsageRecord], list[UsageBucket]]:
        # Runs in the background thread: scanning also updates the persistent history.
        return self.provider.usage_records(), self.provider.usage_history()

    def _on_tokens(self, result, error: Exception | None) -> bool:
        self._tokens_busy = False
        if error is None and result is not None:
            self.records, self.buckets = result
            self.records_loaded = True
        else:
            print(f"ai-usage: scanning logs failed: {error}", file=sys.stderr)
        self._tokens_timer = GLib.timeout_add_seconds(TOKENS_INTERVAL_SECONDS, self.refresh_tokens)
        self.render()  # also refreshes the "resets in …" texts
        return False

    # ----- window / lifecycle -----------------------------------------------------

    def open_window(self, *_args) -> None:
        if self.window is None:
            from ai_usage.tray.window import StatsWindow

            self.window = StatsWindow(self.provider.title, self.provider.usage_scope)
        self.window.update(self.snapshot, self.records, self._status_text(), self.buckets)
        self.window.show_all()
        self.window.present()

    def quit(self, *_args) -> None:
        Gtk.main_quit()


def _acquire_single_instance_lock():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or str(Path.home() / ".cache")
    path = Path(runtime) / "ai-usage-tray.lock"
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh  # keep open for the process lifetime


def run(provider: Provider, limits_interval: int = 180) -> int:
    lock = _acquire_single_instance_lock()
    if lock is None:
        print("ai-usage tray is already running.", file=sys.stderr)
        return 1
    app = TrayApp(provider, limits_interval)
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (app.quit(), False)[1])
    Gtk.main()
    lock.close()
    return 0
