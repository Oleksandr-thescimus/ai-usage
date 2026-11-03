"""Single-series column chart drawn with cairo (no extra dependencies).

Follows the dataviz mark specs: one colour for every bar (one series, so no legend; the
controls name the metric), columns at most 24px wide with a 2px gap, a 4px rounded cap and
a square base, hairline solid gridlines, recessive axis text, one selective direct label
(the peak), a labelled average reference line, and a per-column hover tooltip whose hit
area is the whole column band. The table under the chart carries every value.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, PangoCairo  # noqa: E402

# Categorical slot 1 (blue), validated with the dataviz palette checker against light
# (#fafafa) and dark (#2d2d2d) GTK surfaces.
BAR_LIGHT = (0x2A / 255, 0x78 / 255, 0xD6 / 255)
BAR_DARK = (0x39 / 255, 0x87 / 255, 0xE5 / 255)
MAX_BAR = 24
GAP = 2
RADIUS = 4
PARTIAL_ALPHA = 0.45  # the running period: lighter, since it isn't finished
MARGIN_TOP, MARGIN_BOTTOM, MARGIN_RIGHT = 20, 24, 8


@dataclass(frozen=True)
class Bar:
    value: float
    axis_label: str
    tooltip_markup: str
    partial: bool = False


def nice_ticks(vmax: float, count: int = 4) -> list[float]:
    """0 .. a round top value in round steps (1/2/2.5/5 × 10^n, at least 1)."""
    if vmax <= 0:
        return [0, 1]
    raw = vmax / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    step = max(step, 1)
    steps = math.ceil(vmax / step - 1e-9)
    return [i * step for i in range(steps + 1)]


def _luminance(rgba: Gdk.RGBA) -> float:
    return 0.2126 * rgba.red + 0.7152 * rgba.green + 0.0722 * rgba.blue


def _rect_overlap(a, b) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


class BarChart(Gtk.DrawingArea):
    def __init__(self, height: int = 240):
        super().__init__()
        self.set_size_request(-1, height)
        self.set_has_tooltip(True)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("draw", self._on_draw)
        self.connect("query-tooltip", self._on_query_tooltip)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("leave-notify-event", self._on_leave)
        self._bars: list[Bar] = []
        self._average: float | None = None
        self._average_label = ""
        self._format: Callable[[float], str] = lambda v: f"{v:,.0f}"
        self._hover: int | None = None
        self._geometry: tuple[float, float, int] | None = None  # plot left, band width, bars
        self.empty_text = "No usage yet"

    def set_data(self, bars: list[Bar], average: float | None, average_label: str,
                 value_format: Callable[[float], str]) -> None:
        self._bars = bars
        self._average = average
        self._average_label = average_label
        self._format = value_format
        if self._hover is not None and self._hover >= len(bars):
            self._hover = None
        self.queue_draw()

    # ----- hover ------------------------------------------------------------------

    def _hit(self, x: float) -> int | None:
        if self._geometry is None:
            return None
        left, band, count = self._geometry
        if band <= 0 or x < left or x >= left + band * count:
            return None
        return int((x - left) // band)

    def _on_motion(self, _widget, event) -> bool:
        index = self._hit(event.x)
        if index != self._hover:
            self._hover = index
            self.queue_draw()
        return False

    def _on_leave(self, *_args) -> bool:
        if self._hover is not None:
            self._hover = None
            self.queue_draw()
        return False

    def _on_query_tooltip(self, _widget, x, _y, _keyboard, tooltip) -> bool:
        index = self._hit(x)
        if index is None:
            return False
        tooltip.set_markup(self._bars[index].tooltip_markup)
        return True

    # ----- drawing ----------------------------------------------------------------

    def _layout(self, text: str, small: bool = True):
        layout = self.create_pango_layout("")
        escaped = GLib.markup_escape_text(text)
        layout.set_markup(f"<small>{escaped}</small>" if small else escaped, -1)
        width, height = layout.get_pixel_size()
        return layout, width, height

    def _on_draw(self, _widget, cr) -> bool:
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        style = self.get_style_context()
        fg = style.get_color(style.get_state())
        found, bg = style.lookup_color("theme_bg_color")
        if not found:
            bg = Gdk.RGBA(1 - fg.red, 1 - fg.green, 1 - fg.blue, 1)
        bar_rgb = BAR_DARK if _luminance(fg) > 0.5 else BAR_LIGHT

        def ink(alpha: float) -> None:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, alpha)

        def text(layout, x: float, y: float, alpha: float) -> None:
            ink(alpha)
            cr.move_to(x, y)
            PangoCairo.show_layout(cr, layout)

        bars = self._bars
        if not bars:
            self._geometry = None
            layout, w, h = self._layout(self.empty_text, small=False)
            text(layout, (width - w) / 2, (height - h) / 2, 0.6)
            return False

        ticks = nice_ticks(max(b.value for b in bars))
        top_value = ticks[-1]
        tick_layouts = [self._layout(self._format(t)) for t in ticks]
        left = max(w for _, w, _ in tick_layouts) + 10
        plot_w = max(1.0, width - left - MARGIN_RIGHT)
        plot_h = max(1.0, height - MARGIN_TOP - MARGIN_BOTTOM)
        baseline = MARGIN_TOP + plot_h

        def y_of(value: float) -> float:
            return MARGIN_TOP + plot_h * (1 - value / top_value)

        # Gridlines + y labels (hairline, solid, recessive).
        cr.set_line_width(1)
        for t, (layout, w, h) in zip(ticks, tick_layouts):
            y = round(y_of(t)) + 0.5
            ink(0.25 if t == 0 else 0.10)  # the baseline a step stronger than the grid
            cr.move_to(left, y)
            cr.line_to(left + plot_w, y)
            cr.stroke()
            text(layout, left - 6 - w, y - h / 2, 0.6)

        # Columns.
        band = plot_w / len(bars)
        bar_w = max(1.0, min(MAX_BAR, band - GAP))
        self._geometry = (left, band, len(bars))
        for i, b in enumerate(bars):
            if b.value <= 0:
                continue
            x = left + i * band + (band - bar_w) / 2
            y = y_of(b.value)
            h = baseline - y
            r = min(RADIUS, bar_w / 2, h)
            red, green, blue = bar_rgb
            if i == self._hover:  # lift the hovered column
                red, green, blue = (c + (1 - c) * 0.25 for c in (red, green, blue))
            cr.set_source_rgba(red, green, blue, PARTIAL_ALPHA if b.partial and i != self._hover else 1)
            cr.new_path()
            cr.move_to(x, baseline)
            cr.line_to(x, y + r)
            cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
            cr.line_to(x + bar_w - r, y)
            cr.arc(x + bar_w - r, y + r, r, 1.5 * math.pi, 2 * math.pi)
            cr.line_to(x + bar_w, baseline)
            cr.close_path()
            cr.fill()

        # X labels: as many as fit, always including the current (last) period.
        label_layouts = [self._layout(b.axis_label) for b in bars]
        widest = max(w for _, w, _ in label_layouts)
        step = max(1, math.ceil((widest + 12) / band))
        for i in range(len(bars) - 1, -1, -step):
            layout, w, h = label_layouts[i]
            cx = left + i * band + band / 2
            x = min(max(cx - w / 2, left), width - MARGIN_RIGHT - w)
            text(layout, x, baseline + 4, 0.6)

        # Average reference line, labelled at the right end.
        occupied = []
        if self._average is not None and self._average > 0:
            y = round(y_of(self._average)) + 0.5
            ink(0.55)
            cr.set_dash([4, 3])
            cr.move_to(left, y)
            cr.line_to(left + plot_w, y)
            cr.stroke()
            cr.set_dash([])
            layout, w, h = self._layout(self._average_label)
            ly = y - h - 2

            def clear(x0: float) -> bool:  # no column reaches into the label's box
                for i, b in enumerate(bars):
                    bx = left + i * band + (band - bar_w) / 2
                    if b.value > 0 and bx < x0 + w + 4 and bx + bar_w > x0 - 4 and y_of(b.value) < ly + h:
                        return False
                return True

            # Prefer the right end; slide left one column at a time until nothing is covered.
            candidates = (left + plot_w - w - 4 - k * band for k in range(len(bars)))
            x = next((c for c in candidates if c >= left + 4 and clear(c)), None)
            if x is None:  # every spot above the line is taken: sit just below it instead
                x, ly = left + plot_w - w - 4, y + 2
            cr.set_source_rgba(bg.red, bg.green, bg.blue, 0.85)
            cr.rectangle(x - 4, ly, w + 8, h)
            cr.fill()
            text(layout, x, ly, 0.85)
            occupied.append((x - 4, ly, w + 8, h))

        # One direct label: the peak value on its cap (skipped if it would collide).
        peak = max(range(len(bars)), key=lambda i: bars[i].value)
        layout, w, h = self._layout(self._format(bars[peak].value))
        cx = left + peak * band + band / 2
        x = min(max(cx - w / 2, left), width - MARGIN_RIGHT - w)
        y = y_of(bars[peak].value) - h - 2
        if not any(_rect_overlap((x, y, w, h), rect) for rect in occupied):
            text(layout, x, y, 0.9)
        return False
