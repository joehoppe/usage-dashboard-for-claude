"""Forced dark palette for the compact strip, identical on every platform —
never inherit the OS widget colors (dark on macOS dark-mode, light on
Windows). Severity fills are the only colour signal: everything else is a
neutral grey, so colour always means what Claude's JSON reported. Plain RGB
tuples, no wx, so contrast rules are testable headless; the UI converts to
wx.Colour at the edge.
"""

from __future__ import annotations

BACKGROUND = (17, 20, 24)
BORDER = (43, 49, 59)
BORDER_STALE = (90, 74, 42)  # wins over BORDER_CRITICAL
BORDER_CRITICAL = (90, 42, 46)
TEXT_PRIMARY = (230, 232, 235)
TEXT_SECONDARY = (154, 163, 173)
TEXT_FAINT = (125, 133, 144)  # stale numbers: dimmed, still AA
TRACK = (38, 43, 51)
STALE_DOT = (107, 114, 128)
HATCH_STRIPE = (91, 99, 112)  # stale bars: diagonal stripes on HATCH_GROUND
HATCH_GROUND = (42, 47, 56)
BUTTON_FILL = (28, 33, 40)
BUTTON_BORDER = (58, 65, 76)
BUTTON_GLYPH = (201, 209, 217)
BUTTON_HOVER_BORDER = (107, 114, 128)
GRIP = (91, 99, 112)

SEVERITY_FILLS = {
    "normal": (63, 191, 159),
    "warning": (245, 165, 36),
    "critical": (242, 107, 107),
}
