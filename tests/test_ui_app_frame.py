"""QuotaFrame and QuotaPanel tests.

Constructing a frame needs a wx.App — conftest's shared `wx_app`, never a
local one: a same-named module fixture is a *distinct* fixture and would
build a second app in the process. The frame is never Show()n, so nothing
appears on screen. Sizes are asserted against literals rather than
strip_layout's constants — importing them would make the assertions agree
with any value the module happens to hold.
"""

import dataclasses
import sys
import traceback

import pytest
import wx

from claude_usage.ui.app.frame import QuotaFrame
from claude_usage.ui.app.panels import QuotaPanel
from claude_usage.ui.app.presenter import BarView, QuotaView


def make_view(bars=3, notices=("+50% weekly limits promo through Sep 13",), labels=None):
    labels = list(labels or ())
    return QuotaView(
        headline="66% used",
        age_text="as of 7m ago",
        stale=False,
        bars=tuple(
            BarView(
                label=labels[index] if index < len(labels) else f"Limit {index}",
                percent=10,
                used=10,
                severity="normal",
                active=index == 0,
                resets_text=None,
            )
            for index in range(bars)
        ),
        notices=notices,
        message=None,
        message_detail=None,
    )


@pytest.fixture
def frame(wx_app):
    frame = QuotaFrame(on_close=lambda: None, on_refresh=lambda: None)
    yield frame
    frame.Destroy()


FAILURE = "Refresh failed: claude not found"
NOTICE_HEIGHT = 14  # ROW_H in strip_layout.py, as a literal (see module docstring)
FOOTER_GAP = 2  # opens the footer when the failure is its first line


def make_message_view(detail=None):
    return QuotaView(
        headline="No data",
        age_text="no reading yet",
        stale=True,
        bars=(),
        notices=(),
        message="No quota data cached yet — click ↻",
        message_detail=detail,
    )


def test_a_poll_after_a_failed_refresh_keeps_the_failure(frame):
    # A poll only re-reads ~/.claude.json and never re-runs claude, so it
    # cannot know the problem is fixed (spec §3 "Lifetime").
    frame.end_refresh(FAILURE)
    frame.show_view(make_view())  # a poller view: refresh_failure is None
    assert frame.panel._view.refresh_failure == FAILURE


def test_a_successful_refresh_clears_the_failure_for_later_polls(frame):
    frame.end_refresh(FAILURE)
    frame.show_view(make_view())
    frame.end_refresh(None)
    frame.show_view(make_view())
    frame.show_view(make_view())  # a later poll must not bring it back
    assert frame.panel._view.refresh_failure is None


@pytest.mark.parametrize(
    ("view", "grows_by"),
    [
        (make_view(), NOTICE_HEIGHT),
        # No notices: the failure opens the footer, so the gap comes too.
        (make_message_view(), FOOTER_GAP + NOTICE_HEIGHT),
        (make_message_view(detail="RuntimeError"), FOOTER_GAP + NOTICE_HEIGHT),
    ],
    ids=["bars", "message", "message-with-detail"],
)
def test_the_failure_line_grows_the_minimum_height_by_one_line(frame, view, grows_by):
    frame.show_view(view)
    without = frame.GetMinSize().height
    frame.end_refresh(FAILURE)
    frame.show_view(view)
    assert frame.GetMinSize().height - without == frame.FromDIP(grows_by)


def test_three_limits_open_at_310_by_71(frame):
    frame.show_view(make_view(bars=3, notices=()))
    assert tuple(frame.GetSize()) == tuple(frame.FromDIP(wx.Size(310, 71)))


def test_grows_to_fit_but_never_shrinks(frame):
    frame.show_view(make_view(notices=("a", "b")))
    tall = frame.GetSize().height
    frame.show_view(make_view(notices=()))
    assert frame.GetSize().height == tall


def test_long_label_widens_the_frame(frame):
    frame.show_view(make_view(labels=["Weekly Claude Opus 4.1 Preview"]))
    assert frame.GetSize().width > frame.FromDIP(310)


def test_refresh_marks_the_view_refreshing_until_it_ends(frame):
    frame.show_view(make_view())
    frame.begin_refresh()
    assert frame.panel._view.refreshing is True
    frame.end_refresh(None)
    frame.show_view(make_view())
    assert frame.panel._view.refreshing is False


def test_refresh_click_while_refreshing_does_nothing(wx_app):
    # Review Focus 3: a second click must not start a second run.
    calls = []
    frame = QuotaFrame(on_close=lambda: None, on_refresh=lambda: calls.append(1))
    frame.show_view(make_view())
    frame._on_button("refresh")
    frame.begin_refresh()
    frame._on_button("refresh")
    assert calls == [1]
    frame.Destroy()


def test_hover_reaches_the_panel(frame):
    frame.set_hovered(True)
    assert frame.panel.hovered is True


def test_window_has_no_os_caption_but_stays_on_top_and_minimisable(frame):
    style = frame.GetWindowStyleFlag()
    assert not style & wx.CAPTION
    assert style & wx.STAY_ON_TOP
    assert not style & wx.FRAME_NO_TASKBAR
    assert not style & wx.FRAME_TOOL_WINDOW


@pytest.mark.skipif(sys.platform != "win32", reason="the taskbar menu is Windows-only")
def test_windows_taskbar_button_offers_close(frame):
    assert frame.GetWindowStyleFlag() & wx.SYSTEM_MENU


def test_native_chrome_takes_none_of_the_window(frame):
    # macOS turns SYSTEM_MENU into a title bar that eats the strip's height
    # while wx still reports no CAPTION, so compare the sizes themselves.
    frame.show_view(make_view())
    assert tuple(frame.GetClientSize()) == tuple(frame.GetSize())


def test_context_menu_offers_minimise_then_close(frame):
    menu = frame.context_menu()
    labels = [item.GetItemLabelText() for item in menu.GetMenuItems()]
    assert labels == ["Minimise", "Close"]
    menu.Destroy()


@pytest.mark.skipif(sys.platform != "win32", reason="checks the native Win32 style")
def test_native_window_has_no_title_bar(frame):
    # wx reports no CAPTION flag even when MSW adds WS_CAPTION behind its
    # back (MINIMIZE_BOX does), so ask Windows itself.
    import ctypes

    ws_caption = 0x00C00000
    style = ctypes.windll.user32.GetWindowLongW(frame.GetHandle(), -16)
    assert not style & ws_caption


def test_panel_min_size_matches_the_layout(wx_app):
    parent = wx.Frame(None)
    panel = QuotaPanel(parent)
    expected = parent.FromDIP(wx.Size(310, 71))
    assert tuple(panel.min_size(make_view(bars=3, notices=()))) == tuple(expected)
    parent.Destroy()


def test_panel_hit_maps_through_dips(wx_app):
    parent = wx.Frame(None, size=(400, 200))
    panel = QuotaPanel(parent)
    panel.SetSize((400, 200))
    panel.render(make_view(bars=3, notices=()))
    panel.set_hovered(True)
    assert panel.hit(panel.FromDIP(wx.Point(20, 40))) is None
    parent.Destroy()


@pytest.mark.parametrize(
    "view",
    [
        make_view(),
        dataclasses.replace(make_view(), stale=True, refreshing=True),
        dataclasses.replace(make_view(), critical=True, refresh_failure=FAILURE),
        make_message_view(detail="RuntimeError"),
        make_view(bars=0, notices=()),
    ],
    ids=["fresh", "stale-refreshing", "critical-failure", "message", "no-limits"],
)
def test_panel_paints_every_view_shape(wx_app, view):
    # A paint error inside EVT_PAINT is only printed, never raised, so paint
    # into a bitmap directly to catch it.
    parent = wx.Frame(None)
    panel = QuotaPanel(parent)
    panel.SetSize(panel.min_size(view))
    panel.render(view)
    panel.set_hovered(True)
    bitmap = wx.Bitmap(*panel.GetClientSize())
    dc = wx.MemoryDC(bitmap)
    panel._paint(dc)
    dc.SelectObject(wx.NullBitmap)
    parent.Destroy()


def test_the_live_paint_handler_raises_nothing(wx_app, monkeypatch):
    # The bitmap test above skips _on_paint's own DC; a wrong DC type there
    # fails every real paint while every other test stays green. wx reports
    # handler exceptions through sys.excepthook, so collect them.
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc_info: errors.append(exc_info))
    frame = QuotaFrame(on_close=lambda: None)
    frame.show_view(make_view())
    frame.Show()
    frame.panel.Refresh()
    frame.panel.Update()
    wx.Yield()
    frame.Destroy()
    # A RecursionError's repr says nothing; name the frames that repeated.
    assert errors == [], "".join(traceback.format_tb(errors[0][2])[-6:])


class _DpiEvent:
    def Skip(self):
        pass


def test_a_size_event_raised_while_reshaping_does_not_reshape_again(frame, monkeypatch):
    # macOS answers SetShape with a size event from inside the call; reshaping
    # on that one too recursed until Python gave up.
    shapes = []

    def set_shape(region):
        shapes.append(region)
        frame._on_size(_DpiEvent())
        return True

    monkeypatch.setattr(frame, "SetShape", set_shape)
    frame.SetSize((400, 120))
    frame._on_size(_DpiEvent())
    assert len(shapes) == 1


def test_only_a_new_size_reshapes_the_window(frame, monkeypatch):
    shapes = []
    monkeypatch.setattr(frame, "SetShape", lambda region: shapes.append(region) or True)
    frame.SetSize((400, 120))
    frame._on_size(_DpiEvent())
    frame._on_size(_DpiEvent())
    assert len(shapes) == 1
    frame.SetSize((420, 120))
    frame._on_size(_DpiEvent())
    assert len(shapes) == 2


def test_a_dpi_change_rebuilds_fonts_and_refits(frame, monkeypatch):
    # Moving between monitors with different scaling: the panel's pixel-sized
    # fonts must follow, and the window must grow to the new minimum.
    frame.show_view(make_view())
    old_font = frame.panel._font
    bigger = wx.Size(frame.GetSize().width + 40, frame.GetSize().height + 30)
    monkeypatch.setattr(frame.panel, "min_size", lambda view: bigger)
    frame._on_dpi_changed(_DpiEvent())
    assert frame.panel._font is not old_font
    assert tuple(frame.GetSize()) == tuple(bigger)


def paint_and_record(view, monkeypatch):
    """Paint into a bitmap; return each drawn text with its color and boldness."""
    drawn = {}
    draw = QuotaPanel._text

    def record(self, dc, text, x, top, line_h, color):
        bold = dc.GetFont().GetWeight() >= wx.FONTWEIGHT_BOLD
        drawn[text] = ((color.Red(), color.Green(), color.Blue()), bold)
        return draw(self, dc, text, x, top, line_h, color)

    monkeypatch.setattr(QuotaPanel, "_text", record)
    parent = wx.Frame(None)
    panel = QuotaPanel(parent)
    panel.SetSize(panel.min_size(view))
    panel.render(view)
    bitmap = wx.Bitmap(*panel.GetClientSize())
    dc = wx.MemoryDC(bitmap)
    panel._paint(dc)
    dc.SelectObject(wx.NullBitmap)
    parent.Destroy()
    return drawn


LABEL_GRAY = (190, 197, 206)  # theme.TEXT_SECONDARY, as a literal
NEAR_WHITE = (230, 232, 235)  # theme.TEXT_PRIMARY, as a literal


def test_percentages_are_bold_and_near_white_when_fresh(wx_app, monkeypatch):
    drawn = paint_and_record(make_view(notices=()), monkeypatch)
    assert drawn["10%"] == (NEAR_WHITE, True)


def test_stale_percentages_and_headline_use_the_label_gray(wx_app, monkeypatch):
    view = dataclasses.replace(make_view(notices=()), stale=True)
    drawn = paint_and_record(view, monkeypatch)
    assert drawn["10%"] == (LABEL_GRAY, True)
    assert drawn["66% used"] == (LABEL_GRAY, True)
