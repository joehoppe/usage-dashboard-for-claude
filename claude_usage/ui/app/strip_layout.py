"""Pure geometry for the compact strip, in DIPs, with no wx.

Places what the presenter decided: every string and colour choice is made in
presenter.py, so this module only measures and positions. The panel scales
the result with FromDIP at the wx edge.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from claude_usage.ui.app.presenter import QuotaView

Measure = Callable[[str], int]  # text width in DIPs at the strip's 11 px font

BORDER = 1
PAD_TOP, PAD_RIGHT, PAD_BOTTOM, PAD_LEFT = 5, 8, 6, 14  # left holds the dot
HEADER_H = 16
ROW_H = 14
FOOTER_GAP = 2
BUTTON = 13
HEADER_GAP = 5
COL_GAP = 4
LABEL_MIN = 84
BAR_MIN = 60
PERCENT_W = 26
RESET_MIN = 68
DEFAULT_WIDTH = 310
GRIP = 6
GRIP_INSET = 2  # clear of the 6 px rounded corner, which clips the window
DOT = 4

_ACTIVE_MARK = " ●"


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


@dataclass(frozen=True)
class RowLayout:
    label: Rect
    bar: Rect
    percent: Rect
    reset: Rect


@dataclass(frozen=True)
class StripLayout:
    # fmt: off
    width: int
    height: int
    dot: Rect
    headline_x: int
    header_y: int
    buttons: dict[str, Rect]    # left to right: "minimise", "close" (hover only), "refresh", "help"
    rows: tuple[RowLayout, ...]
    message_ys: tuple[int, ...]
    footer_ys: tuple[int, ...]  # notices, then refresh_failure
    footer_width: int           # footer text truncates to this
    grip: Rect | None           # hover only
    # fmt: on


def min_size(view: QuotaView, measure: Measure) -> tuple[int, int]:
    """The smallest strip that shows the view without clipping. Footer and
    message text never widen it: they truncate instead."""
    label_w, reset_w = _column_widths(view, measure)
    width = (
        2 * BORDER
        + PAD_LEFT
        + label_w
        + COL_GAP
        + BAR_MIN
        + COL_GAP
        + PERCENT_W
        + COL_GAP
        + reset_w
        + PAD_RIGHT
    )
    footer_count = len(footer_lines(view))
    height = (
        2 * BORDER
        + PAD_TOP
        + HEADER_H
        + ROW_H * (len(view.bars) + len(_message_lines(view)))
        + (FOOTER_GAP + ROW_H * footer_count if footer_count else 0)
        + PAD_BOTTOM
    )
    return max(DEFAULT_WIDTH, width), height


def layout(view: QuotaView, size: tuple[int, int], hovered: bool, measure: Measure) -> StripLayout:
    width, height = size
    left = BORDER + PAD_LEFT
    right = width - BORDER - PAD_RIGHT
    header_y = BORDER + PAD_TOP

    # Right-aligned from "?": the window controls sit left of the data
    # controls, so ↻ and ? never move when hover reveals – and ×.
    keys = ["minimise", "close", "refresh", "help"] if hovered else ["refresh", "help"]
    button_y = header_y + (HEADER_H - BUTTON) // 2
    buttons = {}
    for index, key in enumerate(keys):
        from_right = len(keys) - index
        x = right - from_right * BUTTON - (from_right - 1) * COL_GAP
        buttons[key] = Rect(x, button_y, BUTTON, BUTTON)

    label_w, reset_w = _column_widths(view, measure)
    reset_x = right - reset_w
    percent_x = reset_x - COL_GAP - PERCENT_W
    bar_x = left + label_w + COL_GAP
    bar_w = max(0, percent_x - COL_GAP - bar_x)
    y = header_y + HEADER_H
    rows = []
    for _ in view.bars:
        rows.append(
            RowLayout(
                label=Rect(left, y, label_w, ROW_H),
                bar=Rect(bar_x, y, bar_w, ROW_H),
                percent=Rect(percent_x, y, PERCENT_W, ROW_H),
                reset=Rect(reset_x, y, reset_w, ROW_H),
            )
        )
        y += ROW_H

    message_ys = []
    for _ in _message_lines(view):
        message_ys.append(y)
        y += ROW_H

    footer_ys = []
    if footer_lines(view):
        y += FOOTER_GAP
        for _ in footer_lines(view):
            footer_ys.append(y)
            y += ROW_H

    return StripLayout(
        width=width,
        height=height,
        dot=Rect(BORDER + (PAD_LEFT - DOT) // 2, header_y + (HEADER_H - DOT) // 2, DOT, DOT),
        headline_x=left,
        header_y=header_y,
        buttons=buttons,
        rows=tuple(rows),
        message_ys=tuple(message_ys),
        footer_ys=tuple(footer_ys),
        footer_width=right - left,
        grip=_grip(width, height) if hovered else None,
    )


def _grip(width: int, height: int) -> Rect:
    inset = BORDER + GRIP_INSET
    return Rect(width - inset - GRIP, height - inset - GRIP, GRIP, GRIP)


def hit(strip: StripLayout, x: int, y: int) -> str | None:
    for key, rect in strip.buttons.items():
        if rect.contains(x, y):
            return key
    if strip.grip is not None and strip.grip.contains(x, y):
        return "grip"
    return None


def _column_widths(view: QuotaView, measure: Measure) -> tuple[int, int]:
    labels = [bar.label + (_ACTIVE_MARK if bar.active else "") for bar in view.bars]
    resets = [bar.resets_text for bar in view.bars if bar.resets_text]
    label_w = max([LABEL_MIN, *map(measure, labels)])
    reset_w = max([RESET_MIN, *map(measure, resets)])
    return label_w, reset_w


def _message_lines(view: QuotaView) -> list[str]:
    return [line for line in (view.message, view.message_detail) if line] if view.message else []


def footer_lines(view: QuotaView) -> list[str]:
    """Notices, then the refresh failure last."""
    return [*view.notices, *([view.refresh_failure] if view.refresh_failure else [])]
