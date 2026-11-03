"""Statistics window: limit bars, token tables by model / project, and a timeline.

One period selector (Today ... All time) scopes every tab. The Timeline tab charts one
metric for that period per hour (Today) / day / week / month from the persistent history
(so it reaches past Claude Code's transcript cleanup), with an average line, a comparison
with the previous period of the same length, a project filter, and a table of every value.

Projects can be hidden from the "By project" tab (right click → Hide project). Totals and
the other tabs still count them. Ctrl+Shift+H toggles a reveal mode that shows hidden
projects (marked, and unhidable from the right-click menu or "Unhide all"); the mode
switches off whenever the window is closed.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from ai_usage import config, fmt  # noqa: E402
from ai_usage import timeline as tl  # noqa: E402
from ai_usage.aggregate import PERIODS, group_by, in_period, period_start, totals  # noqa: E402
from ai_usage.models import LimitsSnapshot, UsageBucket, UsageRecord  # noqa: E402
from ai_usage.tray.chart import Bar, BarChart  # noqa: E402

CSS = b"""
progressbar.warning progress { background-color: #e5a50a; border-color: #e5a50a; }
progressbar.critical progress { background-color: #e01b24; border-color: #e01b24; }
.dim-label { opacity: 0.7; }
"""

NUMERIC_COLUMNS = ["Input", "Output", "Cache write", "Cache read", "Total", "Requests"]
HIDDEN_COLUMN = len(NUMERIC_COLUMNS) + 1  # bool: row is a hidden project (reveal mode only)
TABLE_GROUPINGS = ("model", "project")
TAB_TITLES = {"model": "By model", "project": "By project", "timeline": "Timeline"}
PAGE_PROJECT, PAGE_TIMELINE = 1, 2
# How the summary names the span the selected period is compared with.
PREVIOUS_SPAN = {
    "today": "yesterday by this time",
    "7d": "the 7 days before",
    "30d": "the 30 days before",
    "90d": "the 90 days before",
    "12m": "the 12 months before",
}
ALL_PROJECTS = "@all"  # combo id; project labels start with "~", "/" or "-"
REVEAL_MODIFIERS = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK


class StatsWindow(Gtk.Window):
    def __init__(self, title: str, usage_scope: str):
        super().__init__(title=f"AI Usage — {title}")
        self.set_default_size(860, 580)
        self.set_icon_name("utilities-system-monitor")
        self.connect("delete-event", self._on_delete)
        self.connect("key-press-event", self._on_key_press)
        self._snapshot: LimitsSnapshot | None = None
        self._records: list[UsageRecord] = []
        self._buckets: list[UsageBucket] = []
        self._period = config.get_pref("period", "7d", PERIODS)
        self._granularity = config.get_pref("timeline_granularity", "day", tl.GRANULARITIES)
        self._metric = config.get_pref("timeline_metric", "output", tl.METRICS)
        self._project_choices: tuple[str, ...] = ()
        self._hidden: set[str] = set()
        self._reveal = False

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_border_width(16)
        self.add(root)

        root.pack_start(self._heading("Plan limits"), False, False, 0)
        self._limits_grid = Gtk.Grid(column_spacing=16, row_spacing=8)
        root.pack_start(self._limits_grid, False, False, 0)
        self._status = Gtk.Label(xalign=0)
        self._status.get_style_context().add_class("dim-label")
        self._status.set_line_wrap(True)
        root.pack_start(self._status, False, False, 0)

        root.pack_start(Gtk.Separator(), False, False, 4)

        header = Gtk.Box(spacing=12)
        header.pack_start(self._heading(f"Tokens — {usage_scope}"), False, False, 0)
        self._period_combo = Gtk.ComboBoxText()
        for key, label in PERIODS.items():
            self._period_combo.append(key, label)
        self._period_combo.set_active_id(self._period)
        self._period_combo.connect("changed", self._on_period_changed)
        header.pack_end(self._period_combo, False, False, 0)
        root.pack_start(header, False, False, 0)

        root.pack_start(self._make_reveal_bar(), False, False, 0)

        self._stores: dict[str, Gtk.ListStore] = {}
        self._notebook = Gtk.Notebook()
        for by in TABLE_GROUPINGS:
            self._notebook.append_page(self._make_table(by), Gtk.Label(label=TAB_TITLES[by]))
        self._notebook.append_page(self._make_timeline(), Gtk.Label(label=TAB_TITLES["timeline"]))
        root.pack_start(self._notebook, True, True, 0)

        self._totals = Gtk.Label(xalign=0, selectable=True)
        root.pack_start(self._totals, False, False, 0)

    @staticmethod
    def _heading(text: str) -> Gtk.Label:
        label = Gtk.Label(xalign=0)
        label.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
        return label

    def _make_reveal_bar(self) -> Gtk.Widget:
        self._reveal_bar = Gtk.Box(spacing=12)
        self._reveal_bar.set_no_show_all(True)  # only shown in reveal mode
        self._reveal_label = Gtk.Label(xalign=0)
        self._reveal_bar.pack_start(self._reveal_label, True, True, 0)
        self._unhide_all_button = Gtk.Button(label="Unhide all")
        self._unhide_all_button.connect("clicked", self._on_unhide_all)
        self._reveal_bar.pack_end(self._unhide_all_button, False, False, 0)
        self._reveal_label.show()
        self._unhide_all_button.show()
        return self._reveal_bar

    def _make_timeline(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(8)

        # Filters: one row, above everything they scope.
        controls = Gtk.Box(spacing=12)
        granularity = Gtk.Box()
        granularity.get_style_context().add_class("linked")
        group = None
        self._granularity_buttons: dict[str, tuple[Gtk.RadioButton, int]] = {}
        for key, label in tl.GRANULARITIES.items():
            button = Gtk.RadioButton.new_with_label_from_widget(group, label)
            button.set_mode(False)  # render as a toggle button
            button.set_active(key == self._granularity)
            handler = button.connect("toggled", self._on_granularity, key)
            self._granularity_buttons[key] = (button, handler)
            granularity.pack_start(button, False, False, 0)
            group = group or button
        controls.pack_start(granularity, False, False, 0)

        self._metric_combo = Gtk.ComboBoxText()
        for key, label in tl.METRICS.items():
            self._metric_combo.append(key, label)
        self._metric_combo.set_active_id(self._metric)
        self._metric_combo.connect("changed", self._on_metric_changed)
        controls.pack_start(self._metric_combo, False, False, 0)

        self._project_combo = Gtk.ComboBoxText()
        self._project_combo.append(ALL_PROJECTS, "All projects")
        self._project_combo.set_active_id(ALL_PROJECTS)
        self._project_changed_handler = self._project_combo.connect("changed", lambda *_: self._fill_timeline())
        controls.pack_start(self._project_combo, False, False, 0)
        box.pack_start(controls, False, False, 0)

        self._chart = BarChart()
        box.pack_start(self._chart, False, True, 0)
        self._timeline_summary = Gtk.Label(xalign=0, selectable=True)
        self._timeline_summary.set_line_wrap(True)
        box.pack_start(self._timeline_summary, False, False, 0)
        box.pack_start(self._make_table("timeline", "Period"), True, True, 0)
        return box

    def _make_table(self, by: str, title: str | None = None) -> Gtk.Widget:
        store = Gtk.ListStore(str, *([GObject.TYPE_INT64] * len(NUMERIC_COLUMNS)), bool)
        self._stores[by] = store
        tree = Gtk.TreeView(model=store)
        tree.set_enable_search(True)
        tree.set_search_column(0)
        if by == "project":
            tree.connect("button-press-event", self._on_project_click)

        name_cell = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.START)
        name_col = Gtk.TreeViewColumn(title or by.capitalize(), name_cell)
        name_col.set_cell_data_func(name_cell, self._format_name)
        name_col.set_expand(True)
        name_col.set_resizable(True)
        name_col.set_sort_column_id(0)
        tree.append_column(name_col)

        for i, title in enumerate(NUMERIC_COLUMNS, start=1):
            cell = Gtk.CellRendererText(xalign=1.0)
            col = Gtk.TreeViewColumn(title, cell)
            col.set_cell_data_func(cell, self._format_cell, i)
            col.set_sort_column_id(i)
            col.set_alignment(1.0)
            tree.append_column(col)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(tree)
        return scroll

    @staticmethod
    def _format_name(column, cell, model, it, _data) -> None:
        name, hidden = model.get_value(it, 0), model.get_value(it, HIDDEN_COLUMN)
        cell.set_property("text", f"{name}  (hidden)" if hidden else name)
        cell.set_property("style", Pango.Style.ITALIC if hidden else Pango.Style.NORMAL)
        cell.set_property("sensitive", not hidden)

    @staticmethod
    def _format_cell(column, cell, model, it, index) -> None:
        value = model.get_value(it, index)
        cell.set_property("text", str(value) if index == len(NUMERIC_COLUMNS) else fmt.human_tokens(value))

    # ----- events -----------------------------------------------------------------

    def _on_delete(self, *_args) -> bool:
        self.hide()
        if self._reveal:
            self._set_reveal(False)  # closing always hides the hidden projects again
        return True  # keep the window for next time

    def _on_key_press(self, _widget, event: Gdk.EventKey) -> bool:
        if (
            Gdk.keyval_to_lower(event.keyval) == Gdk.KEY_h
            and event.state & REVEAL_MODIFIERS == REVEAL_MODIFIERS
        ):
            self._set_reveal(not self._reveal)
            return True
        return False

    def _set_reveal(self, reveal: bool) -> None:
        self._reveal = reveal
        if reveal:
            self._reveal_bar.show()
            self._notebook.set_current_page(PAGE_PROJECT)
        else:
            self._reveal_bar.hide()
        self._fill_tables()
        self._fill_timeline()

    def _on_granularity(self, button: Gtk.RadioButton, key: str) -> None:
        if not button.get_active():
            return
        self._granularity = key
        self._save_pref("timeline_granularity", key)
        self._fill_timeline()

    def _on_metric_changed(self, combo: Gtk.ComboBoxText) -> None:
        self._metric = combo.get_active_id() or "output"
        self._save_pref("timeline_metric", self._metric)
        self._fill_timeline()

    def _save_pref(self, name: str, value: str) -> None:
        try:
            config.set_pref(name, value)
        except OSError:
            pass  # a preference that isn't remembered is not worth an error

    def _on_period_changed(self, combo: Gtk.ComboBoxText) -> None:
        self._period = combo.get_active_id() or "7d"
        self._save_pref("period", self._period)
        self._fill_tables()
        self._fill_timeline()

    def _on_project_click(self, tree: Gtk.TreeView, event: Gdk.EventButton) -> bool:
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button != Gdk.BUTTON_SECONDARY:
            return False
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if hit is None:
            return False
        path = hit[0]
        tree.get_selection().select_path(path)
        model = tree.get_model()
        name, hidden = model[path][0], model[path][HIDDEN_COLUMN]

        menu = Gtk.Menu()
        item = Gtk.MenuItem(label="Unhide project" if hidden else "Hide project")
        item.connect("activate", lambda *_: self._set_project_hidden(name, not hidden))
        menu.append(item)
        menu.show_all()
        menu.attach_to_widget(tree, None)
        menu.popup_at_pointer(event)
        return True

    def _set_project_hidden(self, name: str, hidden: bool) -> None:
        try:
            config.set_hidden(name, hidden)
        except OSError as e:
            self._status.set_text(f"Couldn't save settings: {e}")
        self._fill_tables()
        self._fill_timeline()

    def _on_unhide_all(self, _button) -> None:
        try:
            config.unhide_all()
        except OSError as e:
            self._status.set_text(f"Couldn't save settings: {e}")
        self._fill_tables()
        self._fill_timeline()

    # ----- data -------------------------------------------------------------------

    def update(self, snapshot: LimitsSnapshot | None, records: list[UsageRecord], status: str,
               buckets: list[UsageBucket] | None = None) -> None:
        self._snapshot = snapshot
        self._records = records
        if buckets is not None:
            self._buckets = buckets
        self._status.set_text(status)
        self._fill_limits()
        self._fill_tables()
        self._fill_timeline()

    def _fill_limits(self) -> None:
        for child in self._limits_grid.get_children():
            self._limits_grid.remove(child)
        windows = self._snapshot.windows if self._snapshot else []
        if not windows:
            self._limits_grid.attach(Gtk.Label(label="No limit data yet", xalign=0), 0, 0, 1, 1)
        for row, w in enumerate(windows):
            self._limits_grid.attach(Gtk.Label(label=w.label, xalign=0), 0, row, 1, 1)
            progress = Gtk.ProgressBar(fraction=max(0.0, min(w.percent, 100.0)) / 100, show_text=True)
            progress.set_text(fmt.percent_text(w.percent))
            progress.set_hexpand(True)
            progress.set_valign(Gtk.Align.CENTER)
            if w.severity in ("warning", "critical"):
                progress.get_style_context().add_class(w.severity)
            self._limits_grid.attach(progress, 1, row, 1, 1)
            reset = Gtk.Label(label=f"resets {fmt.reset_text(w.resets_at)}", xalign=0)
            reset.get_style_context().add_class("dim-label")
            self._limits_grid.attach(reset, 2, row, 1, 1)
        self._limits_grid.show_all()

    def _fill_tables(self) -> None:
        self._hidden = config.hidden_projects()  # re-read: the CLI may have changed it
        records = in_period(self._records, self._period)
        for by in TABLE_GROUPINGS:
            store = self._stores[by]
            store.clear()
            for name, t in group_by(records, by):
                hidden = by == "project" and name in self._hidden
                if hidden and not self._reveal:
                    continue
                store.append([name, t.input, t.output, t.cache_write, t.cache_read, t.total, t.requests, hidden])
        count = len(self._hidden)
        self._reveal_label.set_text(
            f"Showing {count} hidden project(s) · right click a project to unhide it · Ctrl+Shift+H to stop"
            if count
            else "No hidden projects · Ctrl+Shift+H to close this bar"
        )
        self._unhide_all_button.set_sensitive(count > 0)
        t = totals(records)
        self._totals.set_text(
            f"Total: {fmt.human_tokens(t.total)} tokens · input {fmt.human_tokens(t.input)} · "
            f"output {fmt.human_tokens(t.output)} · cache write {fmt.human_tokens(t.cache_write)} · "
            f"cache read {fmt.human_tokens(t.cache_read)} · {t.requests} requests · "
            f"{len(t.sessions)} sessions"
        )

    # ----- timeline ---------------------------------------------------------------

    def _sync_project_choices(self) -> str | None:
        """Refresh the project filter (hidden projects only in reveal mode); keep the
        selection when it's still available. Returns the selected project or None."""
        sums: dict[str, int] = {}
        for b in self._buckets:
            sums[b.project] = sums.get(b.project, 0) + b.output_tokens
        names = tuple(sorted((p for p in sums if self._reveal or p not in self._hidden),
                             key=lambda p: (-sums[p], p)))
        current = self._project_combo.get_active_id() or ALL_PROJECTS
        if names != self._project_choices:
            self._project_choices = names
            self._project_combo.handler_block(self._project_changed_handler)
            self._project_combo.remove_all()
            self._project_combo.append(ALL_PROJECTS, "All projects")
            for name in names:
                suffix = "  (hidden)" if name in self._hidden else ""
                self._project_combo.append(name, name + suffix)
            self._project_combo.set_active_id(current if current in names else ALL_PROJECTS)
            self._project_combo.handler_unblock(self._project_changed_handler)
        active = self._project_combo.get_active_id()
        return None if active in (None, ALL_PROJECTS) else active

    def _sync_granularity(self, since) -> str:
        """The bar size to draw: "hour" for Today; otherwise the user's choice unless the
        period would need too many bars (then the next coarser one). Updates the buttons
        without overwriting the saved choice, so a shorter period brings it back."""
        hourly = self._period == "today"
        allowed = [] if hourly else [
            g for g in tl.GRANULARITIES if tl.count_bars(self._buckets, g, since) <= tl.MAX_BARS
        ] or ["month"]
        effective = "hour" if hourly else (self._granularity if self._granularity in allowed else allowed[0])
        for key, (button, handler) in self._granularity_buttons.items():
            enabled = key in allowed
            button.set_sensitive(enabled)
            if hourly:
                button.set_tooltip_text("Today is shown by hour")
            elif not enabled:
                button.set_tooltip_text(f"Too many {key}s for “{PERIODS[self._period]}”; pick a shorter period")
            else:
                button.set_tooltip_text(None)
            if key == effective and not button.get_active():
                for other, other_handler in self._granularity_buttons.values():
                    other.handler_block(other_handler)
                button.set_active(True)
                for other, other_handler in self._granularity_buttons.values():
                    other.handler_unblock(other_handler)
        return effective

    def _fill_timeline(self) -> None:
        project = self._sync_project_choices()
        metric = self._metric
        range_start = period_start(self._period)  # local datetime, None for "All time"
        since = range_start.date() if range_start else None
        granularity = self._sync_granularity(since)
        if granularity == "hour":
            points = tl.build_hours(self._buckets, project)
        else:
            points = tl.build(self._buckets, granularity, project, since)
        metric_name = tl.METRICS[metric]
        unit = tl.UNIT[granularity]

        def value_text(v: float) -> str:
            return f"{int(v):,}" if metric == "requests" else fmt.human_tokens(int(v))

        def short_date(d) -> str:
            return f"{d:%b} {d.day}"

        history_start = tl.history_start(self._buckets)

        def partial_note(p: tl.Point) -> str:
            if history_start is not None and p.start < history_start:
                return f"history starts {short_date(history_start)}"
            return f"period starts {short_date(since)}" if since else ""

        bars = []
        for p in points:
            t = p.totals
            when = tl.full_label(p.start, granularity) + (" · so far" if p.partial else "")
            if p.incomplete:
                when += f" · {partial_note(p)}"
            bars.append(Bar(
                value=p.value(metric),
                axis_label=tl.axis_label(p.start, granularity),
                tooltip_markup=(
                    f"<b>{GLib.markup_escape_text(value_text(p.value(metric)))}</b>  "
                    f"{GLib.markup_escape_text(metric_name)}\n"
                    f"{GLib.markup_escape_text(when)}\n"
                    f"<small>output {fmt.human_tokens(t.output)} · total {fmt.human_tokens(t.total)} · "
                    f"{t.requests:,} requests</small>"
                ),
                partial=p.partial or p.incomplete,
            ))
        avg = tl.average(points, metric)
        self._chart.empty_text = "No usage recorded yet"
        self._chart.set_data(bars, avg, f"avg {value_text(avg)}/{unit}" if avg else "", value_text)

        parts = []
        if avg is not None:
            parts.append(f"Average {value_text(avg)} {metric_name.lower()} per {unit}")
        if range_start is not None:  # "All time" has nothing before it to compare with
            trend = tl.trend(self._buckets, metric, range_start, project)
            label, before = PERIODS[self._period], PREVIOUS_SPAN[self._period]
            if trend.complete:
                comparison = f"{label}: {value_text(trend.current)} vs {before}: {value_text(trend.previous)}"
            else:
                comparison = f"{label}: {value_text(trend.current)} · not enough history yet to compare with {before}"
            if trend.change is not None:
                arrow = "▲" if trend.change >= 0 else "▼"
                comparison += f" ({arrow} {abs(trend.change):.0%})"
            parts.append(comparison)
        self._timeline_summary.set_text(" · ".join(parts) if points else "")

        store = self._stores["timeline"]
        store.clear()
        for p in reversed(points):
            t = p.totals
            name = tl.table_label(p.start, granularity) + ("  (so far)" if p.partial else "")
            if p.incomplete:
                name += f"  ({partial_note(p)})"
            store.append([name, t.input, t.output, t.cache_write, t.cache_read, t.total, t.requests, False])
