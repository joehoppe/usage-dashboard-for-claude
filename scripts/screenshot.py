"""Save PNG screenshots of the window at its real opening size, one per
(view, refresh failure) pair, for the GUI check AGENTS.md requires after any
layout, sizing, theming or label change.

    python scripts/screenshot.py OUT_DIR

Each shot gets a fresh QuotaFrame: _fit_to_content never shrinks, so reusing
one frame would carry a taller view's height into the next shot. The capture
copies the frame's screen area, so the window must be visible and
unobscured. On macOS the terminal running this needs Screen Recording
permission; without it the PNGs come out blank, which the script now reports
on stderr and in its exit status (1 if any shot failed).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import wx

from claude_usage.infrastructure.claude_cli import RefreshOutcome
from claude_usage.ui.app.frame import QuotaFrame
from claude_usage.ui.app.main import _enable_dark_titlebar
from claude_usage.ui.app.presenter import BarView, QuotaView
from claude_usage.ui.app.refresh import outcome_notice

# Long enough for the window server to map and paint a new frame.
_SETTLE_MS = 500

_FAILURES = (RefreshOutcome.NOT_FOUND, RefreshOutcome.TIMED_OUT, RefreshOutcome.FAILED)


def _bar(label: str, percent: int, severity: str, active: bool) -> BarView:
    return BarView(
        label=label,
        percent=percent,
        used=percent,
        severity=severity,
        active=active,
        resets_text="resets in 3h",
    )


_BARS_VIEW = QuotaView(
    headline="66% used",
    age_text="as of 7m ago",
    stale=False,
    bars=(
        _bar("Session", 25, "normal", True),
        _bar("Weekly", 39, "normal", False),
        _bar("Weekly Fable", 66, "warning", False),
    ),
    notices=("+50% weekly limits promo through Sep 13",),
    message=None,
    message_detail=None,
)

_MESSAGE_VIEW = QuotaView(
    headline="No data",
    age_text="no reading yet",
    stale=True,
    bars=(),
    notices=(),
    message="No quota data cached yet — click Refresh",
    message_detail=None,
)


def _shots() -> list[tuple[str, QuotaView, str | None]]:
    return [
        (f"{mode}-{outcome.value}.png", view, outcome_notice(outcome))
        for mode, view in (("bars", _BARS_VIEW), ("message", _MESSAGE_VIEW))
        for outcome in _FAILURES
    ]


def _capture(frame: wx.Frame, path: Path) -> str | None:
    """Save the frame's screen area. Returns None on success, else why it failed."""
    width, height = frame.GetClientSize()
    origin = frame.ClientToScreen(wx.Point(0, 0))
    bitmap = wx.Bitmap(width, height)
    memory = wx.MemoryDC(bitmap)
    copied = memory.Blit(0, 0, width, height, wx.ScreenDC(), origin.x, origin.y)
    memory.SelectObject(wx.NullBitmap)
    if not copied:
        return "Blit from the screen failed"
    if not bitmap.SaveFile(str(path), wx.BITMAP_TYPE_PNG):
        return "could not save the PNG"
    data = bytes(bitmap.ConvertToImage().GetData())
    if data[3:] == data[:-3]:  # every pixel is the same RGB triple
        return "capture is a single colour; likely missing macOS Screen Recording permission"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Screenshot the window's failure states.")
    parser.add_argument("out_dir", type=Path, help="folder to write the PNGs into")
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    app = wx.App()
    _enable_dark_titlebar(app)  # match the real window's chrome
    pending = _shots()
    failures: list[str] = []

    def next_shot() -> None:
        if not pending:
            app.ExitMainLoop()
            return
        name, view, failure = pending.pop(0)
        frame = QuotaFrame(on_close=lambda: None, on_refresh=lambda: None)
        frame.end_refresh(failure)  # the same path deliver() takes
        frame.show_view(view)
        frame.Show()

        def take() -> None:
            path = args.out_dir / name
            problem = _capture(frame, path)
            print(f"{path}  window={tuple(frame.GetSize())}")
            if problem:
                failures.append(name)
                print(f"FAILED {name}: {problem}", file=sys.stderr)
            # The next frame exists before this one goes, so the main loop
            # never sees zero top-level windows and exits early.
            next_shot()
            frame.Destroy()

        wx.CallLater(_SETTLE_MS, take)

    next_shot()
    app.MainLoop()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
