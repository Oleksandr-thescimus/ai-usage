"""Statistics window: limit bars + token tables by model / project / day.

Projects can be hidden from the "By project" tab (right click → Hide project). Totals and
the other tabs still count them. Ctrl+Shift+H toggles a reveal mode that shows hidden
projects (marked, and unhidable from the right-click menu or "Unhide all"); the mode
switches off whenever the window is closed.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GObject, Gtk, Pango  # noqa: E402

from ai_usage import config, fmt  # noqa: E402
from ai_usage.aggregate import GROUPINGS, PERIODS, group_by, in_period, totals  # noqa: E402
from ai_usage.models import LimitsSnapshot, UsageRecord  # noqa: E402

CSS = b"""
progressbar.warning progress { background-color: #e5a50a; border-color: #e5a50a; }
progressbar.critical progress { background-color: #e01b24; border-color: #e01b24; }
.dim-label { opacity: 0.7; }
"""

NUMERIC_COLUMNS = ["Input", "Output", "Cache write", "Cache read", "Total", "Requests"]
HIDDEN_COLUMN = len(NUMERIC_COLUMNS) + 1  # bool: row is a hidden project (reveal mode only)
TAB_TITLES = {"model": "By model", "project": "By project", "day": "By day"}
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
        self._period = "7d"
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
        for by in GROUPINGS:
            self._notebook.append_page(self._make_table(by), Gtk.Label(label=TAB_TITLES[by]))
        root.pack_start(self._notebook, True, True, 0)

        self._totals = Gtk.Label(xalign=0, selectable=True)
        root.pack_start(self._totals, False, False, 0)

    @staticmethod
    def _heading(text: str) -> Gtk.Label:
        label = Gtk.Label(xalign=0)
        label.set_markup(f"<b>{GObject.markup_escape_text(text)}</b>")
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

    def _make_table(self, by: str) -> Gtk.Widget:
        store = Gtk.ListStore(str, *([GObject.TYPE_INT64] * len(NUMERIC_COLUMNS)), bool)
        self._stores[by] = store
        tree = Gtk.TreeView(model=store)
        tree.set_enable_search(True)
        tree.set_search_column(0)
        if by == "project":
            tree.connect("button-press-event", self._on_project_click)

        name_cell = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.START)
        name_col = Gtk.TreeViewColumn(by.capitalize(), name_cell)
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
            self._notebook.set_current_page(GROUPINGS.index("project"))
        else:
            self._reveal_bar.hide()
        self._fill_tables()

    def _on_period_changed(self, combo: Gtk.ComboBoxText) -> None:
        self._period = combo.get_active_id() or "7d"
        self._fill_tables()

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

    def _on_unhide_all(self, _button) -> None:
        try:
            config.unhide_all()
        except OSError as e:
            self._status.set_text(f"Couldn't save settings: {e}")
        self._fill_tables()

    # ----- data -------------------------------------------------------------------

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
        self._hidden = config.hidden_projects()  # re-read: the CLI may have changed it
        records = in_period(self._records, self._period)
        for by, store in self._stores.items():
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
