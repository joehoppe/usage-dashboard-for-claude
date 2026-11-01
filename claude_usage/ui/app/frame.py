"""wx.Frame composition root for the window: a top row (headline, age text,
Refresh/help buttons), then bar rows and footer via QuotaPanel. STAY_ON_TOP,
resizable, never steals focus on refresh (refresh only repaints — it never
calls Raise()/SetFocus()).
"""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Callable

import wx

from claude_usage.ui.app import theme
from claude_usage.ui.app.help_dialog import HelpDialog
from claude_usage.ui.app.panels import QuotaPanel
from claude_usage.ui.app.presenter import QuotaView
from claude_usage.ui.app.refresh import HELP_TOOLTIP

_MIN_WIDTH = 240
# Opening size of the window. Narrow on purpose: this is a glanceable side
# panel meant to park beside real work, not a document window. The height is
# only a starting guess — _fit_to_content() grows it to whatever the current
# view needs (see below).
_DEFAULT_SIZE = (377, 216)
_BUTTON_MARGIN = 8
# Frameless, but still a real top-level window. SYSTEM_MENU gives the
# Windows taskbar button its "Close window" item and lets the taskbar
# restore a minimised strip. No MINIMIZE_BOX: on MSW it makes wx add
# WS_CAPTION, bringing the title bar back; Iconize() works without it. No
# FRAME_TOOL_WINDOW or FRAME_NO_TASKBAR: the taskbar button and Dock icon
# are how a minimised strip comes back. FRAME_SHAPED allows the rounded
# outline.
_STYLE = wx.BORDER_NONE | wx.STAY_ON_TOP | wx.SYSTEM_MENU | wx.FRAME_SHAPED
# Extra horizontal breathing room for the exact-fit "?" button.
_HELP_BUTTON_PADDING = 6


class QuotaFrame(wx.Frame):
    def __init__(
        self,
        on_close: Callable[[], None],
        on_refresh: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            None,
            title="Usage Dashboard for Claude",
            size=_DEFAULT_SIZE,
            style=_STYLE,
        )
        self.SetBackgroundColour(wx.Colour(*theme.BACKGROUND))
        self._on_close = on_close
        # The latest refresh's footer line, overlaid on every view shown.
        # GUI-thread state only: the poller and the refresh worker never touch it.
        self._refresh_failure: str | None = None
        self.panel = QuotaPanel(self)
        self._refresh_button: wx.Button | None = None
        self._help_button: wx.Button | None = None
        self._headline_text = wx.StaticText(self, label="")
        self._headline_text.SetFont(self._headline_text.GetFont().Bold())
        self._headline_text.SetForegroundColour(wx.Colour(*theme.TEXT_PRIMARY))
        self._age_text = wx.StaticText(self, label="")
        self._age_text.SetForegroundColour(wx.Colour(*theme.TEXT_SECONDARY))
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(self._headline_text, 0, wx.ALIGN_CENTER_VERTICAL)
        row.Add(
            self._age_text,
            0,
            wx.LEFT | wx.ALIGN_CENTER_VERTICAL,
            _BUTTON_MARGIN,
        )
        row.AddStretchSpacer(1)
        sizer = wx.BoxSizer(wx.VERTICAL)
        if on_refresh is not None:
            self._refresh_button = wx.Button(self, label="Refresh")
            self._refresh_button.Bind(wx.EVT_BUTTON, lambda event: on_refresh())
            # Pre-compute the size of both labels so a label change never
            # clips: min size is the max of both dimensions.
            refresh_size = self._refresh_button.GetBestSize()
            self._refresh_button.SetLabel("Refreshing…")
            refreshing_size = self._refresh_button.GetBestSize()
            self._refresh_button.SetLabel("Refresh")
            max_width = max(refresh_size.width, refreshing_size.width)
            max_height = max(refresh_size.height, refreshing_size.height)
            self._refresh_button.SetMinSize(wx.Size(max_width, max_height))
            # The "?" warns that refreshing spends quota (tooltip for hover,
            # dialog for click). It must be visible before the first click,
            # so it gets a button of its own.
            self._help_button = wx.Button(self, label="?", style=wx.BU_EXACTFIT)
            # BU_EXACTFIT hugs the "?" glyph too tightly to read as a button,
            # so widen it a little without touching the exact-fit height.
            help_size = self._help_button.GetBestSize()
            self._help_button.SetMinSize(
                wx.Size(help_size.width + _HELP_BUTTON_PADDING, help_size.height)
            )
            self._help_button.SetToolTip(HELP_TOOLTIP)
            self._help_button.Bind(wx.EVT_BUTTON, self._show_refresh_help)
            row.Add(self._refresh_button, 0, wx.ALIGN_CENTER_VERTICAL)
            row.Add(
                self._help_button,
                0,
                wx.LEFT | wx.ALIGN_CENTER_VERTICAL,
                _BUTTON_MARGIN,
            )
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, _BUTTON_MARGIN)
        sizer.Add(self.panel, 1, wx.EXPAND)
        self.SetSizer(sizer)
        self.Bind(wx.EVT_CLOSE, self._handle_close)
        self.Bind(wx.EVT_CONTEXT_MENU, self._show_context_menu)
        self.panel.Bind(wx.EVT_CONTEXT_MENU, self._show_context_menu)

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

    def _show_context_menu(self, event: wx.ContextMenuEvent) -> None:
        menu = self.context_menu()
        self.PopupMenu(menu)
        menu.Destroy()

    def begin_refresh(self) -> None:
        """The disabled button is the entire in-progress UI (design §6)."""
        if self._refresh_button is None:
            return
        self._refresh_button.Disable()
        self._refresh_button.SetLabel("Refreshing…")

    def end_refresh(self, failure: str | None) -> None:
        """Restore the button and record the outcome's footer line (None
        clears it). Renders nothing: deliver() calls show_view straight
        after, and that overlays the line.
        """
        self._refresh_failure = failure
        if self._refresh_button is None:
            return
        self._refresh_button.SetLabel("Refresh")
        self._refresh_button.Enable()

    def show_view(self, view: QuotaView) -> None:
        # Every view gets the last refresh's failure, poller views included:
        # only a later click can show the problem is fixed.
        view = dataclasses.replace(view, refresh_failure=self._refresh_failure)
        self._headline_text.SetLabel(view.headline)
        self._age_text.SetLabel(view.age_text)
        self.Layout()  # label widths changed; re-place the top row
        self._fit_to_content(view)
        self.panel.render(view)

    def _fit_to_content(self, view: QuotaView) -> None:
        """Grow the window when a view needs more room than it has, and hold
        that as the minimum. The constructor's height is only a starting
        guess — bar count varies per view, and the window chrome eats height
        the frame size does not account for, so a fixed height clips.

        Never shrinks: a size the user chose deliberately must stick.
        """
        needed = self.panel.content_height(view) + self._top_row_height()
        width, height = self.GetClientSize()
        self.SetMinClientSize((_MIN_WIDTH, needed))
        if height < needed:
            self.SetClientSize((width, needed))

    def _show_refresh_help(self, event: wx.CommandEvent) -> None:
        with HelpDialog(self) as dialog:
            dialog.ShowModal()

    def _top_row_height(self) -> int:
        # Without this the top row clips: content_height() covers only
        # QuotaPanel's drawing (design §6).
        heights = [
            self._headline_text.GetBestSize().height,
            self._age_text.GetBestSize().height,
        ]
        if self._refresh_button is not None:
            heights.append(self._refresh_button.GetMinSize().height)
        if self._help_button is not None:
            heights.append(self._help_button.GetMinSize().height)
        return max(heights) + 2 * _BUTTON_MARGIN

    def _handle_close(self, event: wx.CloseEvent) -> None:
        self._on_close()
        event.Skip()
