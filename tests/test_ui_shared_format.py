from datetime import timedelta

from claude_usage.domain.quota import LimitReading
from claude_usage.ui.shared.format import coarse, coarse_pair, label_for


def make_limit(**overrides):
    defaults = {
        "kind": "session",
        "group": "session",
        "percent": 25,
        "severity": "normal",
        "is_active": False,
        "resets_at": None,
        "scope_model": None,
    }
    defaults.update(overrides)
    return LimitReading(**defaults)


def test_known_kind_labels():
    assert label_for(make_limit(kind="session")) == "Session (5hr)"
    assert label_for(make_limit(kind="weekly_all")) == "Weekly (7 day)"
    assert label_for(make_limit(kind="weekly_scoped", scope_model="Fable")) == "Weekly Fable"


def test_unknown_kind_falls_back_without_crashing():
    assert label_for(make_limit(kind="monthly_all")) == "Monthly All"
    assert (
        label_for(make_limit(kind="monthly_scoped", scope_model="Sonnet"))
        == "Monthly Scoped Sonnet"
    )


def test_coarse_units():
    assert coarse(timedelta(seconds=30)) == "<1m"
    assert coarse(timedelta(minutes=12)) == "12m"
    assert coarse(timedelta(hours=2)) == "2h"
    assert coarse(timedelta(days=1, hours=18)) == "1d"


def test_coarse_pair_units():
    assert coarse_pair(timedelta(seconds=30)) == "<1m"
    assert coarse_pair(timedelta(minutes=13)) == "13m"
    assert coarse_pair(timedelta(hours=3, minutes=40)) == "3h"  # under a day: hours only
    assert coarse_pair(timedelta(days=1, hours=4, minutes=59)) == "1d 4h"
    assert coarse_pair(timedelta(days=2)) == "2d"  # a zero second unit is dropped
    assert coarse_pair(timedelta(days=6, hours=23)) == "6d 23h"
