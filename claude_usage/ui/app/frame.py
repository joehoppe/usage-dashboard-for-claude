"""wx.Frame composition root for the compact strip: wires QuotaPanel's drawn
buttons to refresh, help, minimise and close, and drives hover, drag and
resize from the mouse.

The strip is frameless but stays a real top-level window: it stays on top
and keeps its taskbar button and Dock icon, which is how a minimised strip
comes back. A right-click menu (Minimise, Close) replaces close-on-right-
click, so a stray click can't quit. On macOS, which won't miniaturise a
borderless window, minimise hides the strip and a Dock click restores it.

Never steals focus on refresh: a poll or refresh only repaints — it never
calls Raise()/SetFocus().
"""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Callable

import wx

from claude_usage.ui.app import strip_layout
from claude_usage.ui.app.help_dialog import HelpDialog
from claude_usage.ui.app.panels import QuotaPanel
from claude_usage.ui.app.presenter import QuotaView

# Frameless, but still a real top-level window. SYSTEM_MENU gives the
# Windows taskbar button its "Close window" item and lets the taskbar
# restore a minimised strip. No MINIMIZE_BOX: on MSW it makes wx add
# WS_CAPTION, bringing the title bar back; Iconize() works without it. No
# FRAME_TOOL_WINDOW or FRAME_NO_TASKBAR: the taskbar button and Dock icon
# are how a minimised strip comes back. FRAME_SHAPED allows the rounded
# outline.
_STYLE = wx.BORDER_NONE | wx.STAY_ON_TOP | wx.SYSTEM_MENU | wx.FRAME_SHAPED
_HOVER_POLL_MS = 100
_CORNER_RADIUS = 6  # matches the border QuotaPanel draws


class QuotaFrame(wx.Frame):
    def __init__(
        self,
        on_close: Callable[[], None],
        on_refresh: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(None, title="Usage Dashboard for Claude", style=_STYLE)
        # Opens at the default width and no height: the first show_view()
        # grows it to exactly what that view needs.
        self.SetSize(self.FromDIP(wx.Size(strip_layout.DEFAULT_WIDTH, 1)))
        self._on_close = on_close
        self._on_refresh = on_refresh
        # GUI-thread state only: the poller and the refresh worker never
        # touch it. Both are overlaid on every view shown.
        self._refresh_failure: str | None = None  # the latest refresh's footer line
        self._refreshing = False
        self._view: QuotaView | None = None  # the last view as delivered
        # Mouse gesture in progress: a pressed button, a drag or a resize.
        self._pressed: str | None = None
        self._gesture: str | None = None  # "drag" | "resize"
        self._gesture_origin = wx.Point()
        self._gesture_start = wx.Point()  # frame position (drag) or size (resize)
        self._shaped_size: tuple[int, int] | None = None  # size the shape was cut for
        self._reshaping = False

        self.panel = QuotaPanel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.panel, 1, wx.EXPAND)
        self.SetSizer(sizer)

        self.Bind(wx.EVT_CLOSE, self._handle_close)
        self.Bind(wx.EVT_SIZE, self._on_size)
        self.Bind(wx.EVT_DPI_CHANGED, self._on_dpi_changed)
        self.Bind(wx.EVT_CONTEXT_MENU, self._show_context_menu)
        self.panel.Bind(wx.EVT_CONTEXT_MENU, self._show_context_menu)
        self.panel.Bind(wx.EVT_LEFT_DOWN, self._on_left_down)
        self.panel.Bind(wx.EVT_LEFT_UP, self._on_left_up)
        self.panel.Bind(wx.EVT_MOTION, self._on_motion)
        self.panel.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self._on_capture_lost)

        # Hover is polled, not taken from enter/leave events: those fire
        # whenever the pointer crosses into a child window, so the hover
        # controls would flicker (spec §5.2).
        self._hover_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._poll_hover, self._hover_timer)
        self._hover_timer.Start(_HOVER_POLL_MS)

    def minimise(self) -> None:
        """Send the strip to the taskbar or Dock.

        macOS refuses to miniaturise a borderless window, so there the strip
        hides instead and a Dock-icon click (main._App.MacReopenApp) brings
        it back: out of the way, back with one click, either way.
        """
        self.Iconize(True)
        if sys.platform == "darwin" and not self.IsIconized():
            self.Hide()

    def restore(self) -> None:
        if self.IsIconized():
            self.Iconize(False)
        if not self.IsShown():
            self.ShowWithoutActivating()

    def context_menu(self) -> wx.Menu:
        """Right-click menu. It replaces close-on-right-click so a stray
        right-click can't quit the app."""
        menu = wx.Menu()
        minimise = menu.Append(wx.ID_ANY, "Minimise")
        close = menu.Append(wx.ID_ANY, "Close")
        menu.Bind(wx.EVT_MENU, lambda event: self.minimise(), minimise)
        menu.Bind(wx.EVT_MENU, lambda event: self.Close(), close)
        return menu

    def set_hovered(self, hovered: bool) -> None:
        self.panel.set_hovered(hovered)

    def begin_refresh(self) -> None:
        """The dimmed ↻ and "refreshing…" are the entire in-progress UI."""
        self._refreshing = True
        if self._view is not None:
            self.show_view(self._view)

    def end_refresh(self, failure: str | None) -> None:
        """Clear the in-progress state and record the outcome's footer line
        (None clears it). Renders nothing: deliver() calls show_view straight
        after, and that overlays the line.
        """
        self._refreshing = False
        self._refresh_failure = failure

    def show_view(self, view: QuotaView) -> None:
        self._view = view
        # Every view gets the last refresh's failure, poller views included:
        # only a later click can show the problem is fixed.
        view = dataclasses.replace(
            view, refresh_failure=self._refresh_failure, refreshing=self._refreshing
        )
        self._fit_to_content(view)
        self.panel.render(view)

    def _on_button(self, name: str) -> None:
        if name == "refresh":
            # A click while a run is in flight does nothing: no second run.
            if self._on_refresh is not None and not self._refreshing:
                self._on_refresh()
        elif name == "help":
            with HelpDialog(self) as dialog:
                dialog.ShowModal()
        elif name == "minimise":
            self.minimise()
        elif name == "close":
            self.Close()

    def _fit_to_content(self, view: QuotaView) -> None:
        """Grow the window when a view needs more room than it has, and hold
        that as the minimum. Limit count, label width and footer lines all
        vary per view, so a fixed size would clip.

        Never shrinks: a size the user chose deliberately must stick.
        """
        needed = self.panel.min_size(view)
        self.SetMinSize(needed)
        width, height = self.GetSize()
        if width < needed.width or height < needed.height:
            self.SetSize((max(width, needed.width), max(height, needed.height)))

    def _poll_hover(self, event: wx.TimerEvent) -> None:
        pointer_inside = self.GetScreenRect().Contains(wx.GetMousePosition())
        # Keep the controls up mid-gesture, even if the pointer outruns the strip.
        self.set_hovered(self.IsShown() and (pointer_inside or self._gesture is not None))

    def _on_left_down(self, event: wx.MouseEvent) -> None:
        target = self.panel.hit(event.GetPosition())
        if target is not None and target != "grip":
            self._pressed = target  # acts on release over the same button
            return
        self._gesture = "resize" if target == "grip" else "drag"
        self._gesture_origin = wx.GetMousePosition()
        self._gesture_start = wx.Point(
            *(self.GetSize() if target == "grip" else self.GetPosition())
        )
        self.panel.CaptureMouse()

    def _on_motion(self, event: wx.MouseEvent) -> None:
        event.Skip()  # the panel tracks hot buttons and tooltips too
        if self._gesture is None or not event.LeftIsDown():
            return
        delta = wx.GetMousePosition() - self._gesture_origin
        target = self._gesture_start + delta
        if self._gesture == "drag":
            self.Move(target)
        else:
            minimum = self.GetMinSize()
            self.SetSize((max(target.x, minimum.width), max(target.y, minimum.height)))

    def _on_left_up(self, event: wx.MouseEvent) -> None:
        pressed, self._pressed = self._pressed, None
        self._end_gesture()
        if pressed is not None and self.panel.hit(event.GetPosition()) == pressed:
            self._on_button(pressed)

    def _on_capture_lost(self, event: wx.MouseCaptureLostEvent) -> None:
        self._gesture = None

    def _end_gesture(self) -> None:
        self._gesture = None
        if self.panel.HasCapture():
            self.panel.ReleaseMouse()

    def _on_dpi_changed(self, event: wx.DPIChangedEvent) -> None:
        # Moved to a monitor with different scaling: new fonts, new minimum.
        event.Skip()
        self.panel.rescale()
        if self._view is not None:
            self.show_view(self._view)

    def _on_size(self, event: wx.SizeEvent) -> None:
        event.Skip()  # the sizer still lays out the panel
        # macOS answers SetShape with a size event from inside the call;
        # reshaping on that one too recurses until Python gives up.
        size = tuple(self.GetSize())
        if self._reshaping or size == self._shaped_size:
            return
        self._reshaping = True
        try:
            self._apply_rounded_shape()
        finally:
            self._reshaping = False
        self._shaped_size = size

    def _apply_rounded_shape(self) -> None:
        # Clip the window to the rounded border the panel draws. Square
        # corners are acceptable where SetShape fails (spec §4.1).
        width, height = self.GetSize()
        if width <= 0 or height <= 0:
            return
        mask = wx.Bitmap(width, height)
        dc = wx.MemoryDC(mask)
        dc.SetBackground(wx.BLACK_BRUSH)
        dc.Clear()
        dc.SetBrush(wx.WHITE_BRUSH)
        dc.SetPen(wx.WHITE_PEN)
        dc.DrawRoundedRectangle(0, 0, width, height, self.FromDIP(_CORNER_RADIUS))
        dc.SelectObject(wx.NullBitmap)
        self.SetShape(wx.Region(mask, wx.BLACK))

    def _show_context_menu(self, event: wx.ContextMenuEvent) -> None:
        menu = self.context_menu()
        self.PopupMenu(menu)
        menu.Destroy()

    def _handle_close(self, event: wx.CloseEvent) -> None:
        self._hover_timer.Stop()
        self._end_gesture()
        self._on_close()
        event.Skip()
