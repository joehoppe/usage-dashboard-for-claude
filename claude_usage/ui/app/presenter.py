"""Pure QuotaSnapshot + Config -> QuotaView. panels.py places strings and
draws rectangles; it computes nothing — every display decision is made here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from claude_usage.application.ports import Config
from claude_usage.domain.quota import (
    LimitReading,
    QuotaSnapshot,
    QuotaUnavailable,
    time_remaining,
)
from claude_usage.ui.shared.format import coarse, coarse_pair, label_for

_SEVERITY = {"normal": "normal", "warning": "warning"}

# Fixed display order; unknown kinds sort after these, keeping input order.
_KIND_ORDER = {"session": 0, "weekly_all": 1, "weekly_scoped": 2}

_NO_DATA_MESSAGES = {
    QuotaUnavailable.NO_FILE: "Claude Code data not found",
    QuotaUnavailable.NO_QUOTA_KEY: "No quota data cached yet — click ↻",
}

_READ_ERROR_MESSAGE = "Couldn't read quota data"


@dataclass(frozen=True)
class BarView:
    # The trailing comments are a column: each field against a worked example.
    # fmt: off
    label: str               # "Weekly Fable"
    percent: int
    used: int                # percent clamped to 0–100, the number shown (SPEC §7.2)
    severity: str            # "normal" | "warning" | "critical"
    active: bool
    resets_text: str | None  # "resets 1d 4h"; None when stale
    # fmt: on


@dataclass(frozen=True)
class QuotaView:
    # The trailing comments are a column: each field against a worked example.
    # fmt: off
    headline: str              # "66% used"
    age_text: str              # "7m ago", or "data 12d old" when stale
    stale: bool
    bars: tuple[BarView, ...]  # fixed kind order: session, weekly, weekly fable
    notices: tuple[str, ...]
    message: str | None        # set only when there are no bars to show
    message_detail: str | None
    critical: bool = False     # a bar is critical and the data is fresh
    refresh_failure: str | None = None  # "Refresh failed: timed out"; set only by QuotaFrame
    refreshing: bool = False   # a refresh is in flight; set only by QuotaFrame
    # fmt: on


def present(snapshot: QuotaSnapshot, config: Config) -> QuotaView:
    notices = list(config.warnings)
    quota = snapshot.quota

    if quota is None:
        message = _NO_DATA_MESSAGES.get(snapshot.unavailable, _READ_ERROR_MESSAGE)
        message_detail = (
            snapshot.detail if snapshot.unavailable is QuotaUnavailable.READ_ERROR else None
        )
        return QuotaView(
            headline="No data",
            age_text="no reading yet",
            stale=True,
            bars=(),
            notices=tuple(notices),
            message=message,
            message_detail=message_detail,
        )

    if snapshot.unavailable is QuotaUnavailable.READ_ERROR and snapshot.detail:
        notices.append(snapshot.detail)
    notices.extend(quota.promo_notices)

    binding = quota.binding()
    headline = f"{_used(binding.percent)}% used" if binding else "No limits reported"

    bars = tuple(
        _bar_view(limit, snapshot.captured_at, snapshot.is_stale)
        for limit in sorted(
            quota.limits,
            key=lambda limit: _KIND_ORDER.get(limit.kind, len(_KIND_ORDER)),
        )
    )

    age = coarse(quota.age(snapshot.captured_at))
    age_text = f"data {age} old" if snapshot.is_stale else f"{age} ago"

    return QuotaView(
        headline=headline,
        age_text=age_text,
        stale=snapshot.is_stale,
        bars=bars,
        notices=tuple(notices),
        message=None,
        message_detail=None,
        # An old severity is not an alarm: stale data never turns the border red.
        critical=not snapshot.is_stale and any(bar.severity == "critical" for bar in bars),
    )


def present_error(exc: Exception) -> QuotaView:
    """Fallback view when the poller loop itself raises — it must never die
    silently, which would freeze the display on a stale reading."""
    return QuotaView(
        headline="No data",
        age_text="no reading yet",
        stale=True,
        bars=(),
        notices=(),
        message=_READ_ERROR_MESSAGE,
        message_detail=type(exc).__name__,
    )


def _used(percent: int) -> int:
    return max(0, min(100, percent))


def _bar_view(limit: LimitReading, now: datetime, stale: bool) -> BarView:
    # A stale reading's reset times may already have passed, so show none.
    resets_in = None if stale else time_remaining(limit.resets_at, now)
    return BarView(
        label=label_for(limit),
        percent=limit.percent,
        used=_used(limit.percent),
        severity=_SEVERITY.get(limit.severity, "critical"),
        active=limit.is_active,
        resets_text=f"resets {coarse_pair(resets_in)}" if resets_in is not None else None,
    )
