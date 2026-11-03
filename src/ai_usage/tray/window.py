"""Statistics window: limit bars + token tables by model / project / day."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk, Pango  # noqa: E402

from ai_usage import fmt  # noqa: E402
from ai_usage.aggregate import GROUPINGS, PERIODS, group_by, in_period, totals  # noqa: E402
from ai_usage.models import LimitsSnapshot, UsageRecord  # noqa: E402

CSS = b"""
progressbar.warning progress { background-color: #e5a50a; border-color: #e5a50a; }
progressbar.critical progress { background-color: #e01b24; border-color: #e01b24; }
.dim-label { opacity: 0.7; }
"""

NUMERIC_COLUMNS = ["Input", "Output", "Cache write", "Cache read", "Total", "Requests"]
TAB_TITLES = {"model": "By model", "project": "By project", "day": "By day"}


class StatsWindow(Gtk.Window):
    def __init__(self, title: str, usage_scope: str):
        super().__init__(title=f"AI Usage — {title}")
        self.set_default_size(860, 580)
        self.set_icon_name("utilities-system-monitor")
        self.connect("delete-event", self._on_delete)
        self._snapshot: LimitsSnapshot | None = None
        self._records: list[UsageRecord] = []
        self._period = "7d"

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

        self._stores: dict[str, Gtk.ListStore] = {}
        notebook = Gtk.Notebook()
        for by in GROUPINGS:
            notebook.append_page(self._make_table(by), Gtk.Label(label=TAB_TITLES[by]))
        root.pack_start(notebook, True, True, 0)

        self._totals = Gtk.Label(xalign=0, selectable=True)
        root.pack_start(self._totals, False, False, 0)

    @staticmethod
    def _heading(text: str) -> Gtk.Label:
        label = Gtk.Label(xalign=0)
        label.set_markup(f"<b>{GObject.markup_escape_text(text)}</b>")
        return label

    def _make_table(self, by: str) -> Gtk.Widget:
        store = Gtk.ListStore(str, *([GObject.TYPE_INT64] * len(NUMERIC_COLUMNS)))
        self._stores[by] = store
        tree = Gtk.TreeView(model=store)
        tree.set_enable_search(True)
        tree.set_search_column(0)

        name_cell = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.START)
        name_col = Gtk.TreeViewColumn(by.capitalize(), name_cell, text=0)
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
    def _format_cell(column, cell, model, it, index) -> None:
        value = model.get_value(it, index)
        cell.set_property("text", str(value) if index == len(NUMERIC_COLUMNS) else fmt.human_tokens(value))

    def _on_delete(self, *_args) -> bool:
        self.hide()
        return True  # keep the window for next time

    def _on_period_changed(self, combo: Gtk.ComboBoxText) -> None:
        self._period = combo.get_active_id() or "7d"
        self._fill_tables()

    def update(self, snapshot: LimitsSnapshot | None, records: list[UsageRecord], status: str) -> None:
        self._snapshot = snapshot
        self._records = records
        self._status.set_text(status)
        self._fill_limits()
        self._fill_tables()

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
        records = in_period(self._records, self._period)
        for by, store in self._stores.items():
            store.clear()
            for name, t in group_by(records, by):
                store.append([name, t.input, t.output, t.cache_write, t.cache_read, t.total, t.requests])
        t = totals(records)
        self._totals.set_text(
            f"Total: {fmt.human_tokens(t.total)} tokens · input {fmt.human_tokens(t.input)} · "
            f"output {fmt.human_tokens(t.output)} · cache write {fmt.human_tokens(t.cache_write)} · "
            f"cache read {fmt.human_tokens(t.cache_read)} · {t.requests} requests · "
            f"{len(t.sessions)} sessions"
        )
