"""Paints a QuotaView as the compact strip: header, limit rows, footer lines.

Places strings and shapes; computes nothing — every display decision was
already made by presenter.present(), and every position comes from
strip_layout (in DIPs, scaled here with FromDIP). Colours come from theme.py
and are painted explicitly so the strip looks the same on every platform
instead of inheriting OS widget colours.

The buttons are drawn, not native: a native button can't be 13 px, round
and dark on both platforms. Their glyphs are vector strokes, except the
bold "?", because the system monospace font may have no "↻".
"""

from __future__ import annotations

import wx

from claude_usage.ui.app import strip_layout, theme
from claude_usage.ui.app.presenter import BarView, QuotaView
from claude_usage.ui.app.refresh import HELP_TOOLTIP
from claude_usage.ui.app.strip_layout import Rect, StripLayout

_BACKGROUND = wx.Colour(*theme.BACKGROUND)
_TEXT_PRIMARY = wx.Colour(*theme.TEXT_PRIMARY)
_TEXT_SECONDARY = wx.Colour(*theme.TEXT_SECONDARY)
_TRACK = wx.Colour(*theme.TRACK)
_HATCH_STRIPE = wx.Colour(*theme.HATCH_STRIPE)
_HATCH_GROUND = wx.Colour(*theme.HATCH_GROUND)
_GRIP = wx.Colour(*theme.GRIP)
_WARNING = wx.Colour(*theme.SEVERITY_FILLS["warning"])  # stale age, refresh failure
_SEVERITY = {severity: wx.Colour(*rgb) for severity, rgb in theme.SEVERITY_FILLS.items()}
_BORDER_RADIUS = 6
_TRACK_H = 4
_TRACK_RADIUS = 2
_HATCH_PERIOD = 4
_HATCH_STRIPE_W = 2
_DISABLED_ALPHA = 102  # 40%: ↻ while a refresh runs
_FONT_PX = 11
_FOOTER_FONT_PX = 10

_TOOLTIPS = {
    "help": HELP_TOOLTIP,
    "refresh": "Refresh (runs claude -p /usage)",
    "minimise": "Minimise",
    "close": "Close",
}


class QuotaPanel(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)  # we paint every pixel
        self._view: QuotaView | None = None
        self.hovered = False
        self._hot: str | None = None  # button under the pointer, drawn brighter
        self._tooltip: str | None = None
        self.rescale()
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_MOTION, self._on_motion)
        self.Bind(wx.EVT_LEAVE_WINDOW, self._on_leave)

    def rescale(self) -> None:
        """Rebuild the pixel-sized fonts for the current DPI. The frame calls
        this when the strip moves to a monitor with different scaling;
        geometry is rescaled on every paint, fonts are not."""
        self._font = self._make_font(_FONT_PX)
        self._bold = self._make_font(_FONT_PX, wx.FONTWEIGHT_BOLD)
        self._footer_font = self._make_font(_FOOTER_FONT_PX)
        self.Refresh()

    def render(self, view: QuotaView) -> None:
        self._view = view
        self.Refresh()

    def set_hovered(self, hovered: bool) -> None:
        if hovered != self.hovered:
            self.hovered = hovered
            self.Refresh()

    def min_size(self, view: QuotaView) -> wx.Size:
        width, height = strip_layout.min_size(view, self._client_measure())
        return self.FromDIP(wx.Size(width, height))

    def hit(self, pos: wx.Point) -> str | None:
        strip = self._layout(self._client_measure())
        if strip is None:
            return None
        dip = self.ToDIP(pos)
        return strip_layout.hit(strip, dip.x, dip.y)

    def _make_font(self, px: int, weight: int = wx.FONTWEIGHT_NORMAL) -> wx.Font:
        # Pixel sizes, not points: point sizes drift between platforms.
        return wx.Font(
            wx.Size(0, self.FromDIP(px)),
            wx.FONTFAMILY_TELETYPE,
            wx.FONTSTYLE_NORMAL,
            weight,
        )

    def _client_measure(self) -> strip_layout.Measure:
        dc = wx.ClientDC(self)
        dc.SetFont(self._font)
        return lambda text: self.ToDIP(dc.GetTextExtent(text).width)

    def _layout(self, measure: strip_layout.Measure) -> StripLayout | None:
        if self._view is None:
            return None
        size = self.ToDIP(self.GetClientSize())
        return strip_layout.layout(self._view, (size.width, size.height), self.hovered, measure)

    def _px(self, rect: Rect) -> wx.Rect:
        return wx.Rect(
            self.FromDIP(rect.x), self.FromDIP(rect.y), self.FromDIP(rect.w), self.FromDIP(rect.h)
        )

    # -- mouse -------------------------------------------------------------

    def _on_motion(self, event: wx.MouseEvent) -> None:
        pos = event.GetPosition()
        hot = self.hit(pos)
        if hot != self._hot:
            self._hot = hot
            self.Refresh()
        tip = _TOOLTIPS.get(hot) if hot else self._footer_text_at(pos)
        if tip != self._tooltip:
            self._tooltip = tip
            if tip is None:
                self.UnsetToolTip()
            else:
                self.SetToolTip(tip)
        event.Skip()  # the frame drags and resizes from the same events

    def _on_leave(self, event: wx.MouseEvent) -> None:
        if self._hot is not None:
            self._hot = None
            self.Refresh()
        event.Skip()

    def _footer_text_at(self, pos: wx.Point) -> str | None:
        """The full text of the footer line under the pointer: the line
        itself may be ellipsised."""
        strip = self._layout(self._client_measure())
        if strip is None:
            return None
        y = self.ToDIP(pos).y
        for line_y, text in zip(
            strip.footer_ys, strip_layout.footer_lines(self._view), strict=True
        ):
            if line_y <= y < line_y + strip_layout.ROW_H:
                return text
        return None

    # -- painting ----------------------------------------------------------

    def _on_paint(self, event: wx.PaintEvent) -> None:
        # BufferedPaintDC is a MemoryDC, which GCDC accepts; AutoBufferedPaintDC
        # is not, on MSW.
        paint_dc = wx.BufferedPaintDC(self)
        self._paint(paint_dc)

    def _paint(self, target: wx.DC) -> None:
        target.SetBackground(wx.Brush(_BACKGROUND))
        target.Clear()
        dc = wx.GCDC(target)  # anti-aliased circles and alpha
        dc.SetFont(self._font)
        strip = self._layout(lambda text: self.ToDIP(dc.GetTextExtent(text).width))
        if strip is None:
            return
        view = self._view
        self._draw_frame(dc, view, strip)
        self._draw_header(dc, view, strip)
        for bar, row in zip(view.bars, strip.rows, strict=True):
            self._draw_row(dc, bar, row, stale=view.stale)
        self._draw_messages(dc, view, strip)
        self._draw_footer(dc, view, strip)
        if strip.grip is not None:
            self._draw_grip(dc, strip.grip)

    def _draw_frame(self, dc: wx.DC, view: QuotaView, strip: StripLayout) -> None:
        # Stale wins over critical: an old severity is not an alarm.
        if view.stale:
            border = theme.BORDER_STALE
        elif view.critical:
            border = theme.BORDER_CRITICAL
        else:
            border = theme.BORDER
        width, height = self.GetClientSize()
        dc.SetPen(wx.Pen(wx.Colour(*border), self.FromDIP(strip_layout.BORDER)))
        dc.SetBrush(wx.Brush(_BACKGROUND))
        dc.DrawRoundedRectangle(0, 0, width, height, self.FromDIP(_BORDER_RADIUS))

    def _draw_header(self, dc: wx.DC, view: QuotaView, strip: StripLayout) -> None:
        dot = self._px(strip.dot)
        dot_color = theme.STALE_DOT if view.stale else theme.SEVERITY_FILLS["normal"]
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.SetBrush(wx.Brush(wx.Colour(*dot_color)))
        dc.DrawEllipse(dot)

        top = self.FromDIP(strip.header_y)
        line_h = self.FromDIP(strip_layout.HEADER_H)
        gap = self.FromDIP(strip_layout.HEADER_GAP)
        x = self.FromDIP(strip.headline_x)
        dc.SetFont(self._bold)
        x = self._text(
            dc, view.headline, x, top, line_h, _TEXT_SECONDARY if view.stale else _TEXT_PRIMARY
        )
        dc.SetFont(self._font)
        x = self._text(
            dc, view.age_text, x + gap, top, line_h, _WARNING if view.stale else _TEXT_SECONDARY
        )
        if view.refreshing:
            self._text(dc, "refreshing…", x + gap, top, line_h, _TEXT_SECONDARY)

        for key, rect in strip.buttons.items():
            self._draw_button(
                dc, key, self._px(rect), disabled=key == "refresh" and view.refreshing
            )

    def _draw_button(self, dc: wx.DC, key: str, rect: wx.Rect, *, disabled: bool) -> None:
        hot = key == self._hot and not disabled
        fill = theme.BUTTON_BORDER if hot else theme.BUTTON_FILL
        ring = theme.BUTTON_HOVER_BORDER if hot else theme.BUTTON_BORDER
        glyph_rgb = (255, 255, 255) if hot else theme.BUTTON_GLYPH
        alpha = _DISABLED_ALPHA if disabled else wx.ALPHA_OPAQUE
        dc.SetPen(wx.Pen(wx.Colour(*ring, alpha), 1))
        dc.SetBrush(wx.Brush(wx.Colour(*fill, alpha)))
        dc.DrawEllipse(rect)

        glyph = wx.Colour(*glyph_rgb, alpha)
        cx, cy = rect.x + rect.width // 2, rect.y + rect.height // 2
        arm = max(2, rect.width // 5)
        dc.SetPen(wx.Pen(glyph, max(1, self.FromDIP(1))))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        if key == "minimise":
            dc.DrawLine(cx - arm, cy, cx + arm + 1, cy)
        elif key == "close":
            dc.DrawLine(cx - arm, cy - arm, cx + arm + 1, cy + arm + 1)
            dc.DrawLine(cx - arm, cy + arm, cx + arm + 1, cy - arm - 1)
        elif key == "refresh":
            radius = arm + 1
            # Open circle, gap at the top right, arrowhead on the open end.
            dc.DrawEllipticArc(cx - radius, cy - radius, 2 * radius, 2 * radius, 60, 360)
            tip_x, tip_y = cx + radius // 2, cy - radius
            head = max(2, radius // 2 + 1)
            dc.SetBrush(wx.Brush(glyph))
            dc.DrawPolygon(
                [
                    wx.Point(tip_x + head, tip_y),
                    wx.Point(tip_x - 1, tip_y - head),
                    wx.Point(tip_x - 1, tip_y + head),
                ]
            )
        else:
            dc.SetFont(self._bold)
            width, height = dc.GetTextExtent("?")
            dc.SetTextForeground(glyph)
            dc.DrawText("?", cx - width // 2, cy - height // 2)
            dc.SetFont(self._font)

    def _draw_row(
        self, dc: wx.DC, bar: BarView, row: strip_layout.RowLayout, *, stale: bool
    ) -> None:
        label = self._px(row.label)
        x = self._text(dc, bar.label, label.x, label.y, label.height, _TEXT_SECONDARY)
        if bar.active:
            self._text(dc, " ●", x, label.y, label.height, _TEXT_PRIMARY)

        area = self._px(row.bar)
        track_h = self.FromDIP(_TRACK_H)
        track = wx.Rect(area.x, area.y + (area.height - track_h) // 2, area.width, track_h)
        radius = self.FromDIP(_TRACK_RADIUS)
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.SetBrush(wx.Brush(_TRACK))
        dc.DrawRoundedRectangle(track, radius)
        fill = wx.Rect(track.x, track.y, track.width * bar.used // 100, track.height)
        severity = _SEVERITY.get(bar.severity, _SEVERITY["critical"])
        if fill.width > 0:
            if stale:
                self._draw_hatch(dc, fill)
            else:
                dc.SetBrush(wx.Brush(severity))
                dc.DrawRoundedRectangle(fill, radius)

        if stale:
            percent_color = _TEXT_SECONDARY
        elif bar.severity == "normal":
            percent_color = _TEXT_PRIMARY
        else:
            percent_color = severity
        # Bold: thin 11 px strokes never reach their full color once smoothed.
        dc.SetFont(self._bold)
        self._text_right(dc, f"{bar.used}%", self._px(row.percent), percent_color)
        dc.SetFont(self._font)
        if bar.resets_text:
            self._text_right(dc, bar.resets_text, self._px(row.reset), _TEXT_SECONDARY)

    def _draw_hatch(self, dc: wx.DC, rect: wx.Rect) -> None:
        """135° stripes on a darker ground: the fill keeps its last-known
        width but reads as "not live"."""
        dc.SetBrush(wx.Brush(_HATCH_GROUND))
        dc.DrawRectangle(rect)
        dc.SetClippingRegion(rect)
        dc.SetPen(wx.Pen(_HATCH_STRIPE, self.FromDIP(_HATCH_STRIPE_W)))
        period = self.FromDIP(_HATCH_PERIOD)
        bottom = rect.y + rect.height
        for x in range(rect.x - rect.height, rect.x + rect.width + period, period):
            dc.DrawLine(x, bottom, x + rect.height, rect.y)
        dc.DestroyClippingRegion()

    def _draw_messages(self, dc: wx.DC, view: QuotaView, strip: StripLayout) -> None:
        lines = [(view.message, _TEXT_PRIMARY), (view.message_detail, _TEXT_SECONDARY)]
        width = self.FromDIP(strip.footer_width)
        x = self.FromDIP(strip.headline_x)
        for y, (text, color) in zip(strip.message_ys, lines, strict=False):
            self._line(dc, text, x, self.FromDIP(y), width, color)

    def _draw_footer(self, dc: wx.DC, view: QuotaView, strip: StripLayout) -> None:
        dc.SetFont(self._footer_font)
        width = self.FromDIP(strip.footer_width)
        x = self.FromDIP(strip.headline_x)
        # Notices dim, then the refresh failure last in warning amber rather
        # than critical red, so it can't be mistaken for a quota alarm.
        colors = [_TEXT_SECONDARY] * len(view.notices) + [_WARNING]
        for y, text, color in zip(
            strip.footer_ys, strip_layout.footer_lines(view), colors, strict=False
        ):
            self._line(dc, text, x, self.FromDIP(y), width, color)
        dc.SetFont(self._font)

    def _draw_grip(self, dc: wx.DC, grip: Rect) -> None:
        rect = self._px(grip)
        right, bottom = rect.x + rect.width, rect.y + rect.height
        dc.SetPen(wx.Pen(_GRIP, 1))
        for inset in (0, rect.width // 2):
            dc.DrawLine(rect.x + inset, bottom, right, rect.y + inset)

    # -- text helpers ------------------------------------------------------

    def _text(self, dc: wx.DC, text: str, x: int, top: int, line_h: int, color: wx.Colour) -> int:
        """Draw text vertically centred in a line; return the x after it."""
        width, height = dc.GetTextExtent(text)
        dc.SetTextForeground(color)
        dc.DrawText(text, x, top + (line_h - height) // 2)
        return x + width

    def _text_right(self, dc: wx.DC, text: str, rect: wx.Rect, color: wx.Colour) -> None:
        width, _ = dc.GetTextExtent(text)
        self._text(dc, text, rect.x + rect.width - width, rect.y, rect.height, color)

    def _line(self, dc: wx.DC, text: str, x: int, top: int, width: int, color: wx.Colour) -> None:
        text = wx.Control.Ellipsize(text, dc, wx.ELLIPSIZE_END, width)
        self._text(dc, text, x, top, self.FromDIP(strip_layout.ROW_H), color)
