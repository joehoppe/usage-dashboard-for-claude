from claude_usage.ui.app.presenter import BarView, QuotaView
from claude_usage.ui.app.strip_layout import hit, layout, min_size


def measure(text):
    return len(text) * 6


def make_view(bars=3, notices=(), failure=None, message=None, detail=None, labels=None):
    labels = list(labels or ())
    return QuotaView(
        headline="66% used",
        age_text="7m ago",
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
        message=message,
        message_detail=detail,
        refresh_failure=failure,
    )


def test_three_limits_default_to_310_by_63():
    assert min_size(make_view(bars=3), measure) == (310, 63)


def test_each_footer_line_adds_12_after_a_2px_gap():
    assert min_size(make_view(notices=("a",)), measure)[1] == 63 + 2 + 12
    view = make_view(notices=("a",), failure="Refresh failed: timed out")
    assert min_size(view, measure)[1] == 63 + 2 + 24


def test_message_view_with_detail_is_two_lines():
    view = make_view(bars=0, message="Couldn't read quota data", detail="OSError")
    assert min_size(view, measure)[1] == 27 + 24


def test_no_limits_is_header_only():
    # Review Focus 5.
    assert min_size(make_view(bars=0), measure) == (310, 27)


def test_long_label_widens_the_strip_and_keeps_the_bar_at_60():
    # Review Focus 1.
    view = make_view(labels=["Weekly Claude Opus 4.1 Preview"])
    width, _ = min_size(view, measure)
    strip = layout(view, (width, 63), hovered=False, measure=measure)
    assert width > 310
    assert strip.rows[0].bar.w >= 60
    assert strip.rows[0].label.x + strip.rows[0].label.w <= strip.rows[0].bar.x


def test_long_footer_text_never_widens():
    # Review Focus 2.
    view = make_view(notices=("x" * 400,))
    assert min_size(view, measure)[0] == 310
    assert layout(view, (310, 77), False, measure).footer_width <= 310 - 2 - 14 - 8


def test_bars_stretch_with_width_other_columns_do_not():
    narrow = layout(make_view(), (310, 63), False, measure).rows[0]
    wide = layout(make_view(), (400, 63), False, measure).rows[0]
    assert wide.bar.w - narrow.bar.w == 90
    assert (wide.label.w, wide.percent.w, wide.reset.w) == (
        narrow.label.w,
        narrow.percent.w,
        narrow.reset.w,
    )


def test_window_controls_and_grip_only_when_hovered():
    idle = layout(make_view(), (310, 63), False, measure)
    hover = layout(make_view(), (310, 63), True, measure)
    assert list(idle.buttons) == ["refresh", "help"] and idle.grip is None
    assert list(hover.buttons) == ["minimise", "close", "refresh", "help"]
    assert hover.grip is not None
    # ↻ and ? do not move when hover controls appear (spec §4.2).
    assert idle.buttons["refresh"] == hover.buttons["refresh"]


def test_hit_finds_buttons_and_grip():
    strip = layout(make_view(), (310, 63), True, measure)
    help_rect = strip.buttons["help"]
    assert hit(strip, help_rect.x + 1, help_rect.y + 1) == "help"
    assert hit(strip, strip.grip.x + 1, strip.grip.y + 1) == "grip"
    assert hit(strip, 20, 40) is None
