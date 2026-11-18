"""Tests for the pure body simulation."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.config import Settings
from core.model import (
    BodyState,
    advance,
    apply_action,
    cycle_view,
    parse_sleep_window,
    sleep_seconds_between,
)
from core.profile import Profile, detect_profile

TZ = ZoneInfo("Asia/Shanghai")


def make_profile(
    *,
    gender: str = "female",
    age: int | None = 25,
    adult: bool = True,
) -> Profile:
    """Build a profile without invoking persona detection."""
    return Profile(
        persona_id="test",
        name="test",
        gender=gender,
        age=age,
        adult=adult,
        cycle_length=28,
        period_length=5,
    )


def make_state(now: datetime) -> BodyState:
    """Build a coherent internal state at the supplied instant."""
    return BodyState(
        persona_id="test",
        updated_at=now.timestamp(),
        hunger=30,
        thirst=20,
        bladder=20,
        cleanliness=20,
        oral=20,
        fatigue=40,
        desire=20,
        stomach=70,
        glucose=80,
        hydration=80,
        urine=20,
        skin=20,
        sleep_debt=5,
        last_eat_at=now.timestamp() - 3 * 3600,
        last_drink_at=now.timestamp() - 2 * 3600,
    )


def test_advance_awake_increases_needs() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now - timedelta(hours=4))
    state.sleep_debt = 18
    settings = Settings(sleep_window="23:00-07:00")
    advance(state, now, make_profile(), settings)
    assert state.hunger > 30
    assert state.thirst > 20
    assert state.fatigue > 40
    assert state.stomach < 70
    assert state.hydration < 80
    assert state.sleep_debt > 5


def test_advance_sleep_recovers_sleep_debt() -> None:
    now = datetime(2026, 11, 18, 8, 0, tzinfo=TZ)
    state = make_state(now - timedelta(hours=8))
    state.sleep_debt = 20
    state.fatigue = 70
    settings = Settings(sleep_window="00:00-08:00")
    advance(state, now, make_profile(), settings)
    assert state.sleep_debt < 20
    assert state.fatigue < 70
    assert state.hunger > 30


def test_recent_activity_keeps_sleep_window_awake() -> None:
    now = datetime(2026, 11, 18, 3, 0, tzinfo=TZ)
    active = make_state(now - timedelta(minutes=10))
    active.sleep_debt = 24
    active.last_active_at = (now - timedelta(minutes=10)).timestamp()
    sleeping = make_state(now - timedelta(minutes=10))
    sleeping.sleep_debt = 24
    settings = Settings(sleep_window="00:00-08:00")
    advance(active, now, make_profile(), settings)
    advance(sleeping, now, make_profile(), settings)
    assert active.fatigue > sleeping.fatigue
    assert active.sleep_debt > sleeping.sleep_debt


def test_cycle_phase_and_hormones() -> None:
    settings = Settings()
    profile = make_profile()
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now)
    state.cycle_anchor = (now.date() - timedelta(days=1)).isoformat()
    view = cycle_view(state, now, profile, settings)
    assert view is not None
    assert view.day == 2
    assert view.phase == "menstrual"
    assert view.progesterone < 20

    state.cycle_anchor = (now.date() - timedelta(days=13)).isoformat()
    view = cycle_view(state, now, profile, settings)
    assert view is not None
    assert view.phase == "ovulation"
    assert view.estrogen > 80

    state.cycle_anchor = (now.date() - timedelta(days=21)).isoformat()
    view = cycle_view(state, now, profile, settings)
    assert view is not None
    assert view.phase == "luteal"
    assert view.progesterone > 50


def test_period_event_is_recorded_once() -> None:
    settings = Settings()
    profile = make_profile()
    start = datetime(2026, 11, 18, 8, 0, tzinfo=TZ)
    state = make_state(start)
    state.cycle_anchor = start.date().isoformat()
    advance(state, start + timedelta(minutes=1), profile, settings)
    assert any(event["kind"] == "period" for event in state.events)
    count = len(state.events)
    advance(state, start + timedelta(minutes=2), profile, settings)
    assert len(state.events) == count


def test_actions_change_internal_state() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now)
    state.stomach = 5
    state.glucose = 10
    state.hydration = 20
    state.urine = 80
    state.skin = 70
    state.oral = 70
    state.sleep_debt = 20
    state.hunger = 90
    state.thirst = 80
    state.bladder = 80
    profile = make_profile()
    settings = Settings()

    before_hunger = state.hunger
    result = apply_action(state, "eat", now, profile, settings)
    assert state.hunger < before_hunger
    assert state.stomach > 50
    assert "饥饿" in result.detail

    before_thirst = state.thirst
    apply_action(state, "drink", now, profile, settings)
    assert state.thirst < before_thirst
    assert state.urine > 80

    apply_action(state, "shower", now, profile, settings)
    assert state.cleanliness == 2

    apply_action(state, "brush", now, profile, settings)
    assert state.oral == 2

    apply_action(state, "toilet", now, profile, settings)
    assert state.bladder == 2

    before_debt = state.sleep_debt
    apply_action(state, "nap", now, profile, settings)
    assert state.sleep_debt < before_debt


def test_digestion_after_meal() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now)
    profile = make_profile()
    settings = Settings(sleep_window="23:00-07:00")
    apply_action(state, "eat", now, profile, settings)
    full_stomach = state.stomach
    hungry_after = state.hunger
    advance(state, now + timedelta(hours=6), profile, settings)
    assert state.stomach < full_stomach
    assert state.hunger > hungry_after


def test_intimacy_is_adult_only_and_sets_refractory() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now)
    settings = Settings()
    minor = make_profile(age=17, adult=False)
    with pytest.raises(ValueError):
        apply_action(state, "intimacy", now, minor, settings)

    male = make_profile(gender="male", age=25, adult=True)
    apply_action(state, "intimacy", now, male, settings)
    assert state.desire == 0
    assert state.refractory_until > now.timestamp()


def test_sleep_window_crosses_midnight() -> None:
    window = parse_sleep_window("23:00-07:00")
    assert window is not None
    start = datetime(2026, 11, 18, 22, 0, tzinfo=TZ)
    end = datetime(2026, 11, 19, 8, 0, tzinfo=TZ)
    seconds = sleep_seconds_between(start, end, window)
    assert seconds == pytest.approx(8 * 3600, abs=1)


def test_minor_profile_never_adult() -> None:
    settings = Settings(age=30)
    profile = detect_profile(
        "kid", "kid", "她今年17岁，是一个女孩子。", settings
    )
    assert profile.age == 17
    assert profile.adult is False
