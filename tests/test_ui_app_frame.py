"""QuotaFrame startup-size tests.

Constructing a frame needs a wx.App — conftest's shared `wx_app`, never a
local one: a same-named module fixture is a *distinct* fixture and would
build a second app in the process. The frame is never Show()n, so nothing
appears on screen. The expected size is asserted against literals
rather than the module's own constant — importing the constant would make
the assertion agree with any value frame.py happens to hold.
"""

import dataclasses
import sys

import pytest
import wx

from claude_usage.ui.app.frame import QuotaFrame
from claude_usage.ui.app.panels import QuotaPanel
from claude_usage.ui.app.presenter import BarView, QuotaView

# The window is a glanceable side panel, not a document window: it opens
# narrow enough to park beside real work.
EXPECTED_SIZE = (377, 216)


def make_view(bars=3, notices=("+50% weekly limits promo through Sep 13",)):
    return QuotaView(
        headline="66% used",
        age_text="as of 7m ago",
        stale=False,
        bars=tuple(
            BarView(
                label=f"Limit {index}",
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


def test_opens_at_the_narrow_default_size(frame):
    assert tuple(frame.GetSize()) == EXPECTED_SIZE


def test_rendering_a_view_never_widens_the_window(frame):
    # _fit_to_content grows height only; a view must not undo the narrow
    # opening width by pushing the window back out.
    frame.show_view(make_view())
    assert frame.GetSize().width == EXPECTED_SIZE[0]


FAILURE = "Refresh failed: claude not found"
# _NOTICE_HEIGHT in panels.py, as a literal for the same reason as EXPECTED_SIZE.
NOTICE_HEIGHT = 16


def make_message_view(detail=None):
    return QuotaView(
        headline="No data",
        age_text="no reading yet",
        stale=True,
        bars=(),
        notices=(),
        message="No quota data cached yet — click Refresh",
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


def test_a_failed_refresh_leaves_no_button_tooltip(frame):
    frame.end_refresh(FAILURE)
    assert frame._refresh_button.GetToolTip() is None


@pytest.mark.parametrize(
    "view",
    [make_view(), make_message_view(), make_message_view(detail="RuntimeError")],
    ids=["bars", "message", "message-with-detail"],
)
def test_the_failure_line_grows_the_minimum_height_by_one_line(frame, view):
    frame.show_view(view)
    without = frame.GetMinClientSize().height
    frame.end_refresh(FAILURE)
    frame.show_view(view)
    assert frame.GetMinClientSize().height - without == NOTICE_HEIGHT


def test_window_has_no_os_caption_but_stays_on_top_and_minimisable(frame):
    style = frame.GetWindowStyleFlag()
    assert not style & wx.CAPTION
    assert style & wx.STAY_ON_TOP
    assert style & wx.SYSTEM_MENU  # taskbar "Close window"
    assert not style & wx.FRAME_NO_TASKBAR
    assert not style & wx.FRAME_TOOL_WINDOW


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
    expected = parent.FromDIP(wx.Size(310, 63))
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
