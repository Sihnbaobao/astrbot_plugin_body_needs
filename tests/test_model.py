"""Tests for the pure body simulation."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from core.config import Settings
from core.model import (
    PAD_RESTOCK,
    BodyState,
    Constitution,
    _cycle_length_for,
    _emission_age_factor,
    _fetal_count,
    _libido_rate,
    _maybe_release,
    _menarche_age,
    _menopause_age,
    _miscarriage_chance,
    _morning_age_factor,
    advance,
    apply_action,
    constitution,
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
    gender_resolved: bool = True,
) -> Profile:
    """Build a profile without invoking persona detection."""
    return Profile(
        persona_id="test",
        name="test",
        gender=gender,
        age=age,
        gender_resolved=gender_resolved,
        cycle_length=28,
        period_length=5,
    )


def make_state(now: datetime) -> BodyState:
    """Build a coherent internal state at the supplied instant."""
    return BodyState(
        persona_id="test",
        updated_at=now.timestamp(),
        satiety=70,
        hydration=80,
        bladder=20,
        cleanliness=80,
        oral_health=80,
        energy=60,
        libido=20,
        stomach=70,
        glucose=80,
        urine=20,
        skin=20,
        oral=20,
        sleep_debt=5,
        last_eat_at=now.timestamp() - 3 * 3600,
        last_drink_at=now.timestamp() - 2 * 3600,
    )


def test_advance_awake_increases_needs() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now - timedelta(hours=4))
    # Below the overtired collapse threshold, so this really is an awake day.
    state.sleep_debt = 12
    settings = Settings(sleep_window="23:00-07:00")
    advance(state, now, make_profile(), settings)
    assert state.sleeping is False
    assert state.satiety < 70
    # She sips through the day, so hydration holds instead of draining.
    assert 60 < state.hydration <= 95
    assert state.energy < 75
    assert state.stomach < 70
    assert state.sleep_debt > 5


def test_advance_sleep_recovers_sleep_debt() -> None:
    now = datetime(2026, 11, 18, 8, 0, tzinfo=TZ)
    state = make_state(now - timedelta(hours=8))
    state.sleep_debt = 20
    state.energy = 30
    settings = Settings(sleep_window="00:00-08:00")
    advance(state, now, make_profile(), settings)
    assert state.sleep_debt < 20
    assert state.energy > 30
    assert state.satiety < 75


def test_recent_activity_delays_sleep_onset() -> None:
    # 00:30 sits inside every night band for a 00:00-08:00 window.
    now = datetime(2026, 11, 18, 0, 30, tzinfo=TZ)
    active = make_state(now - timedelta(minutes=10))
    active.sleep_debt = 12
    active.last_active_at = (now - timedelta(minutes=10)).timestamp()
    idle = make_state(now - timedelta(minutes=10))
    idle.sleep_debt = 12
    idle.last_active_at = (now - timedelta(hours=6)).timestamp()
    settings = Settings(sleep_window="00:00-08:00", enable_nap=False)
    advance(active, now, make_profile(), settings)
    advance(idle, now, make_profile(), settings)
    # Someone who just spoke is still up; the quiet one has gone to bed.
    assert active.sleeping is False
    assert idle.bed_since > 0 or idle.sleeping is True


def test_cycle_phase_and_hormones() -> None:
    settings = Settings()
    profile = make_profile()
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)

    def view_for(days_ago: int):
        state = make_state(now)
        state.cycle_anchor = (now.date() - timedelta(days=days_ago)).isoformat()
        return cycle_view(state, now, profile, settings)

    view = view_for(1)
    assert view is not None
    assert view.day == 2
    assert view.phase == "menstrual"
    assert view.progesterone < 20

    view = view_for(13)
    assert view is not None
    assert view.phase == "ovulation"
    assert view.estrogen > 80

    view = view_for(21)
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


def test_release_sets_a_waiting_window() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    apply_action(state, "release", now, make_profile(), Settings())
    assert state.release_count == 1
    assert state.release_until > now.timestamp()
    assert state.release_kind == "self"
    # She is not "away on errands": she is busy with herself.
    assert state.away_until == 0.0


def test_intimacy_records_counts_and_wetness() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.wetness = 60.0
    profile = make_profile()
    apply_action(state, "intimacy", now, profile, Settings(), ejaculated=True)
    assert state.intimacy_count == 1
    assert state.insemination_count == 1
    assert state.last_intimacy_at == now.timestamp()
    assert state.last_insemination_at == now.timestamp()
    assert state.wetness < 60.0

    apply_action(state, "intimacy", now, profile, Settings(), ejaculated=False)
    assert state.intimacy_count == 2
    assert state.insemination_count == 1


def test_drinking_rinses_the_mouth() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.oral = 30.0
    apply_action(state, "drink", now, make_profile(), Settings())
    assert state.oral < 30.0


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
    state.satiety = 10
    state.hydration = 20
    state.bladder = 80
    profile = make_profile()
    settings = Settings()

    before_satiety = state.satiety
    result = apply_action(state, "eat", now, profile, settings)
    assert state.satiety > before_satiety
    assert state.stomach > 50
    assert "饱食度" in result.detail

    before_hydration = state.hydration
    apply_action(state, "drink", now, profile, settings)
    assert state.hydration > before_hydration
    assert state.urine > 80

    apply_action(state, "shower", now, profile, settings)
    assert state.skin == 2
    assert state.cleanliness == 98

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
    satiety_after = state.satiety
    advance(state, now + timedelta(hours=6), profile, settings)
    assert state.stomach < full_stomach
    assert state.satiety < satiety_after


def test_intimacy_requires_a_resolved_gender_and_sets_refractory() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now)
    settings = Settings()
    gated = make_profile(gender_resolved=False)
    with pytest.raises(ValueError):
        apply_action(state, "intimacy", now, gated, settings)

    male = make_profile(gender="male")
    apply_action(state, "intimacy", now, male, settings)
    assert state.libido == 0
    assert state.refractory_until > now.timestamp()


def test_male_libido_rate_falls_with_age() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now)
    rates = [
        _libido_rate(state, None, make_profile(gender="male", age=age))
        for age in (18, 25, 35, 50)
    ]
    assert rates == [80.0, 70.0, 50.0, 32.0]
    assert rates == sorted(rates, reverse=True)
    # Age only scales the curve: a younger persona is not switched off.
    assert _libido_rate(state, None, make_profile(gender="male", age=16)) > 0.0


def test_menarche_varies_between_personas() -> None:
    ages = {_menarche_age(pid) for pid in ("a", "b", "c", "d", "e", "f")}
    assert len(ages) > 1
    assert all(9.0 <= value <= 16.0 for value in ages)


def test_menopause_age_varies_between_personas() -> None:
    ages = {_menopause_age(pid) for pid in ("a", "b", "c", "d", "e", "f")}
    assert len(ages) > 1
    assert all(40.0 <= value <= 61.0 for value in ages)


def test_menarche_is_not_a_fixed_cutoff() -> None:
    now = datetime(2026, 11, 19, 9, 0, tzinfo=TZ)
    phases = set()
    for index in range(20):
        pid = f"kid{index}"
        state = make_state(now)
        state.cycle_anchor = "2026-11-01"
        profile = Profile(pid, "kid", "female", 12, True, 28, 5)
        phases.add(cycle_view(state, now, profile, Settings()).phase)
    assert "premenarche" in phases
    assert any(phase != "premenarche" for phase in phases)


def test_menopause_stops_the_cycle() -> None:
    now = datetime(2026, 11, 19, 9, 0, tzinfo=TZ)
    state = make_state(now)
    view = cycle_view(state, now, make_profile(age=62), Settings())
    assert view is not None
    assert view.phase == "menopause"
    assert view.flow == 0


def test_long_lived_species_scale_to_human_age() -> None:
    now = datetime(2026, 11, 19, 9, 0, tzinfo=TZ)
    elf = Profile(
        "elf",
        "elf",
        "female",
        300,
        True,
        28,
        5,
        race="精灵",
        lifespan=1200,
    )
    assert elf.physio_age == 20.0
    # A human-scale age keeps its literal meaning even for a long-lived race.
    girl = Profile("g", "g", "female", 17, True, 28, 5, race="精灵", lifespan=1200)
    assert girl.physio_age == 17.0
    view = cycle_view(make_state(now), now, elf, Settings())
    assert view is not None
    assert view.phase not in ("premenarche", "menopause")


def test_anovulatory_cycle_never_reports_ovulation() -> None:
    settings = Settings()
    start = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    checked = 0
    for index in range(30):
        pid = f"anov{index}"
        profile = Profile(pid, "t", "female", 15, True, 28, 5)
        state = make_state(start)
        state.cycle_anchor = "2026-11-01"
        view = cycle_view(state, start, profile, settings)
        if not view.anovulatory:
            continue
        checked += 1
        for offset in range(view.cycle_length):
            moment = start + timedelta(days=offset)
            scan = cycle_view(state, moment, profile, settings)
            assert scan.phase != "ovulation"
    assert checked > 0


def _period_state(moment: datetime, pads: int = 20) -> BodyState:
    state = BodyState(
        persona_id="test",
        updated_at=moment.replace(hour=0, minute=0, second=0).timestamp(),
    )
    state.cycle_anchor = "2026-11-01"
    state.pads = pads
    return state


def test_supplies_are_used_during_a_period() -> None:
    now = datetime(2026, 11, 2, 12, 0, tzinfo=TZ)
    profile = make_profile(age=25)
    state = _period_state(now)
    advance(state, now, profile, Settings())
    assert state.pads < 20


def test_heavy_period_days_cost_more_pads() -> None:
    profile = make_profile(age=25)
    settings = Settings()

    heavy_moment = datetime(2026, 11, 2, 12, 0, tzinfo=TZ)
    heavy = _period_state(heavy_moment, 40)
    advance(heavy, heavy_moment, profile, settings)

    light_moment = datetime(2026, 11, 6, 12, 0, tzinfo=TZ)
    light = _period_state(light_moment, 40)
    advance(light, light_moment, profile, settings)

    assert heavy.pads < light.pads


def test_supplies_action_restocks() -> None:
    now = datetime(2026, 11, 2, 12, 0, tzinfo=TZ)
    profile = make_profile(age=25)
    state = make_state(now)
    state.cycle_anchor = "2026-11-01"
    state.pads = 0
    apply_action(state, "supplies", now, profile, Settings())
    assert state.pads == PAD_RESTOCK


def test_morning_odds_fall_with_age() -> None:
    factors = [_morning_age_factor(age) for age in (17, 25, 35, 45, 55, 65)]
    assert factors == sorted(factors, reverse=True)
    assert factors[0] > factors[-1] * 3


def test_cycle_jitter_only_when_asked() -> None:
    fixed = {_cycle_length_for(28, index, "p", 0.0) for index in range(24)}
    assert fixed == {28}

    teen = {_cycle_length_for(28, index, "p", 5.0) for index in range(24)}
    assert len(teen) > 1
    assert all(18 <= length <= 70 for length in teen)

    pcos = {_cycle_length_for(28, index, "p", 12.0) for index in range(24)}
    assert max(pcos) - min(pcos) > max(teen) - min(teen)


def test_pcos_makes_cycles_rarely_ovulatory() -> None:
    settings = Settings()
    start = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    skipped = 0
    for index in range(12):
        pid = f"pcos{index}"
        profile = Profile(pid, "t", "female", 30, True, 28, 5, pcos=True)
        state = make_state(start)
        state.cycle_anchor = "2026-11-01"
        view = cycle_view(state, start, profile, settings)
        if view.anovulatory:
            skipped += 1
            assert view.fertile is False
    assert skipped >= 8


def test_pregnancy_stops_the_cycle_and_advances() -> None:
    settings = Settings()
    start = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    profile = make_profile(age=28)
    state = make_state(start)
    state.cycle_anchor = "2026-11-01"
    state.pregnant = True
    state.conceived_at = start.timestamp()
    view = cycle_view(state, start + timedelta(days=70), profile, settings)
    assert view is not None
    assert view.phase == "pregnant"
    assert view.weeks == 10
    assert view.flow == 0

    later = start + timedelta(days=290)
    state.updated_at = (start + timedelta(days=70)).timestamp()
    advance(state, later, profile, settings)
    assert state.pregnant is False


def test_contraception_scales_conception_chance() -> None:
    from core.model import CONCEPTION_BASE, CONTRACEPTION_FACTOR

    assert CONTRACEPTION_FACTOR["none"] == 1.0
    assert CONCEPTION_BASE * CONTRACEPTION_FACTOR["condom"] < 0.01
    assert CONCEPTION_BASE * CONTRACEPTION_FACTOR["pill"] < 0.01


def test_conception_only_inside_the_fertile_window() -> None:
    settings = Settings()
    start = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    conceived = 0
    for index in range(60):
        pid = f"con{index}"
        profile = Profile(pid, "t", "female", 28, True, 28, 5)
        state = make_state(start)
        state.cycle_anchor = "2026-11-01"
        # Day 3 is inside the period, far outside any fertile window.
        apply_action(state, "intimacy", start + timedelta(days=2), profile, settings)
        if state.pregnant:
            conceived += 1
    assert conceived == 0


def test_fertile_window_can_overlap_the_period() -> None:
    settings = Settings()
    start = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    profile = Profile("short", "t", "female", 28, True, 21, 5)
    state = make_state(start)
    state.cycle_anchor = "2026-11-01"
    inside_period = [
        day
        for day in range(1, 6)
        if cycle_view(state, start + timedelta(days=day - 1), profile, settings).fertile
    ]
    assert inside_period, "a short cycle should have fertile days inside the period"


def test_fertile_window_ends_a_day_after_ovulation() -> None:
    settings = Settings()
    start = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    profile = Profile("long", "t", "female", 28, True, 28, 5)
    state = make_state(start)
    state.cycle_anchor = "2026-11-01"
    days = [
        day
        for day in range(1, 29)
        if cycle_view(state, start + timedelta(days=day - 1), profile, settings).fertile
    ]
    assert days == list(range(9, 16))


def test_fertile_window_marks_ovulation() -> None:
    settings = Settings()
    start = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    found = False
    for index in range(40):
        pid = f"fert{index}"
        profile = Profile(pid, "t", "female", 30, True, 28, 5)
        state = make_state(start)
        state.cycle_anchor = "2026-11-01"
        view = cycle_view(state, start + timedelta(days=13), profile, settings)
        if view.phase == "ovulation":
            found = True
            assert view.fertile is True
            assert view.anovulatory is False
    assert found


def test_premenarche_has_no_period() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now)
    settings = Settings()
    view = cycle_view(state, now, make_profile(age=8), settings)
    assert view is not None
    assert view.phase == "premenarche"
    assert view.flow == 0
    assert view.puberty is True


def test_puberty_flags_teen_cycle() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now)
    settings = Settings()
    teen = cycle_view(state, now, make_profile(age=16), settings)
    adult = cycle_view(state, now, make_profile(age=28), settings)
    assert teen is not None and adult is not None
    assert teen.puberty is True
    assert adult.puberty is False


def test_emission_odds_fall_with_age() -> None:
    factors = [_emission_age_factor(age) for age in (15, 20, 30, 40, 50, 60)]
    assert factors == sorted(factors, reverse=True)
    assert factors[0] > factors[-1] * 10


def test_sleep_window_crosses_midnight() -> None:
    window = parse_sleep_window("23:00-07:00")
    assert window is not None
    start = datetime(2026, 11, 18, 22, 0, tzinfo=TZ)
    end = datetime(2026, 11, 19, 8, 0, tzinfo=TZ)
    seconds = sleep_seconds_between(start, end, window)
    assert seconds == pytest.approx(8 * 3600, abs=1)


def test_constitution_varies_between_personas() -> None:
    from core.model import constitution

    base = constitution("a")
    other = constitution("b")
    assert base.sleep_need != other.sleep_need
    assert 7.0 <= base.sleep_need <= 9.2
    assert 0.85 <= base.stomach_capacity <= 1.15
    assert 0.75 <= base.gut_transit <= 1.25


def test_eating_fills_the_gut_and_toilet_relieves_it() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    profile = make_profile()
    settings = Settings()
    state = make_state(now)
    state.gut = 10.0
    apply_action(state, "eat", now, profile, settings)
    assert state.gut > 10.0

    state.gut = 80.0
    state.bowel = 82.0
    apply_action(state, "toilet", now, profile, settings)
    assert state.gut == 0.0
    assert state.bowel == 0.0
    assert state.urine == 2.0


def test_skin_baseline_varies_by_persona() -> None:
    first = constitution("skin-a")
    second = constitution("skin-b")
    assert first.skin_oil_rate != pytest.approx(second.skin_oil_rate)
    assert first.skin_clarity != pytest.approx(second.skin_clarity)
    assert 0.65 <= first.skin_oil_rate <= 1.35
    assert 55.0 <= first.skin_clarity <= 90.0


def test_weather_sleep_and_iron_are_simulated() -> None:
    now = datetime(2026, 11, 20, 9, 0, tzinfo=TZ)
    state = make_state(now - timedelta(days=1))
    state.iron = 40.0
    advance(state, now, make_profile(), Settings())
    assert state.weather in {"clear", "cloudy", "rain", "snow"}
    assert -20.0 <= state.ambient_temp <= 45.0
    assert 0.0 <= state.sleep_quality <= 100.0
    assert state.iron > 40.0


def test_cold_raises_temperature_and_pain() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now - timedelta(hours=2))
    state.illness = "cold"
    state.illness_severity = 55.0
    state.illness_started_at = now.timestamp() - 86400
    state.temperature = 36.5
    before = state.temperature
    advance(state, now, make_profile(), Settings())
    assert state.temperature > before
    assert state.pain > 0
    assert state.heart_rate >= 62
    assert state.breath_rate >= 14


def test_temperature_and_sweat_are_alive() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now - timedelta(hours=6))
    state.temperature = 36.6
    state.sweat = 20.0
    advance(state, now, make_profile(), Settings())
    assert 35.8 <= state.temperature <= 37.5
    assert 0.0 <= state.sweat <= 100.0
    state.sweat = 45.0
    apply_action(state, "shower", now, make_profile(), Settings())
    assert state.sweat == 0.0


def test_brushing_is_not_gated_by_high_oral_meter() -> None:
    now = datetime(2026, 11, 20, 23, 30, tzinfo=TZ)
    state = make_state(now - timedelta(days=2))
    state.oral = 35.0
    state.last_brush_at = 0.0
    advance(state, now, make_profile(), Settings())
    kinds = {item["kind"] for item in state.care}
    assert "brush" in kinds


def test_idle_persona_looks_after_herself() -> None:
    now = datetime(2026, 11, 19, 22, 0, tzinfo=TZ)
    state = make_state(now - timedelta(hours=30))
    state.last_active_at = now.timestamp() - 30 * 3600
    state.satiety = 8.0
    state.stomach = 5.0
    state.glucose = 20.0
    state.hydration = 30.0
    state.gut = 78.0
    state.bowel = 79.0
    advance(state, now, make_profile(), Settings())
    assert state.satiety > 8.0
    assert state.hydration > 30.0
    assert state.bowel < 75.0
    assert any(event["kind"] == "self_care" for event in state.events)


def test_three_meals_and_care_log() -> None:
    now = datetime(2026, 11, 19, 22, 0, tzinfo=TZ)
    state = make_state(now - timedelta(days=1))
    state.stomach = 20.0
    state.glucose = 30.0
    state.hydration = 60.0
    state.urine = 2.0
    advance(state, now, make_profile(), Settings())
    meals = [
        event
        for event in state.events
        if event["kind"] == "self_care" and "吃" in event["text"]
    ]
    # The replay starts at 22:00 and may catch a late dinner, then the next
    # full day has all three meals.
    assert len(meals) >= 3
    assert {"早饭", "午饭", "晚饭"} <= {event["text"][-2:] for event in meals}
    kinds = {item["kind"] for item in state.care}
    assert {"meal", "drink", "toilet"} <= kinds
    assert state.care == sorted(state.care, key=lambda item: item["t"])


def test_autonomy_runs_even_during_a_chat() -> None:
    now = datetime(2026, 11, 19, 22, 0, tzinfo=TZ)
    state = make_state(now - timedelta(hours=10))
    state.last_active_at = now.timestamp() - 60
    state.satiety = 12.0
    state.stomach = 8.0
    state.glucose = 22.0
    advance(state, now, make_profile(), Settings())
    assert state.satiety > 12.0
    assert any(event["kind"] == "self_care" for event in state.events)


def test_self_release_lowers_libido(monkeypatch) -> None:
    import core.model as model

    # The roll is one per bucket; pin it to zero so this checks the effect.
    monkeypatch.setattr(model, "_roll", lambda *parts: 0.0)
    now = datetime(2026, 11, 19, 22, 0, tzinfo=TZ)
    profile = Profile("rel", "t", "female", 25, True, 28, 5)
    state = make_state(now - timedelta(hours=1))
    state.persona_id = profile.persona_id
    state.libido = 95.0
    state.last_self_at = now.timestamp() - 6 * 86400
    const = Constitution(
        sleep_need=8.0,
        metabolism=1.0,
        stomach_capacity=1.0,
        gut_transit=1.0,
        self_rate=20.0,
    )
    _maybe_release(state, now, profile, Settings(), const, 0.25, 0.0)
    assert state.release_count == 1
    assert state.libido < 95.0
    assert state.last_self_at == now.timestamp()


def test_persona_habit_scales_libido_rate() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now)
    low = Profile("test", "test", "female", 25, True, 28, 5, libido_habit=0.3)
    high = Profile("test", "test", "female", 25, True, 28, 5, libido_habit=4.8)
    assert _libido_rate(state, None, high) > _libido_rate(state, None, low)


def test_release_frequency_is_stable_and_plausible() -> None:
    now = datetime(2026, 11, 19, 22, 0, tzinfo=TZ)
    settings = Settings()

    def run(persona_id: str) -> int:
        profile = Profile(persona_id, "t", "male", 25, True, 28, 5)
        state = make_state(now - timedelta(days=30))
        state.persona_id = persona_id
        state.last_active_at = state.updated_at
        advance(state, now, profile, settings)
        return state.release_count

    first = run("freq-a")
    assert first == run("freq-a")
    assert 1 <= first <= 120


def test_self_release_needs_intimate_enabled() -> None:
    now = datetime(2026, 11, 19, 22, 0, tzinfo=TZ)
    settings = Settings(enable_intimate=False)
    state = make_state(now - timedelta(hours=1))
    state.last_active_at = now.timestamp() - 10 * 3600
    state.libido = 95.0
    advance(state, now, make_profile(), settings)
    assert not any(event["kind"] == "self_release" for event in state.events)


def test_adult_follows_detected_gender() -> None:
    male = detect_profile("doc", "doc", "一个冷静的医生。", Settings(gender="male"))
    assert male.gender_resolved is True

    unknown = detect_profile("anon", "anon", "一个普通的助手。", Settings())
    assert unknown.gender == "none"
    assert unknown.gender_resolved is False


def test_birth_starts_lactation() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now - timedelta(hours=2))
    state.pregnant = True
    state.fetuses = 1
    state.conceived_at = (now - timedelta(days=281)).timestamp()
    advance(state, now, make_profile(), Settings())
    assert state.pregnant is False
    assert state.lactating is True
    assert state.milk > 0
    assert state.birth_at > 0
    assert any(event["kind"] == "birth" for event in state.events)


def test_twins_arrive_earlier_than_a_singleton() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    settings = Settings()
    profile = make_profile()

    singleton = make_state(now - timedelta(hours=2))
    singleton.pregnant = True
    singleton.fetuses = 1
    singleton.conceived_at = (now - timedelta(days=270)).timestamp()
    advance(singleton, now, profile, settings)
    assert singleton.pregnant is True

    twins = make_state(now - timedelta(hours=2))
    twins.pregnant = True
    twins.fetuses = 2
    twins.conceived_at = (now - timedelta(days=270)).timestamp()
    advance(twins, now, profile, settings)
    assert twins.pregnant is False
    assert "双胎" in twins.events[-1]["text"]


def test_miscarriage_hazard_falls_with_weeks() -> None:
    chances = [_miscarriage_chance(week) for week in (3, 8, 12, 18, 25)]
    assert chances[0] > chances[1] > chances[2] > chances[3] > chances[4]
    assert chances[4] == 0.0


def test_prenatal_visit_needs_a_pregnancy() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    profile = make_profile()
    settings = Settings()
    state = make_state(now)
    with pytest.raises(ValueError):
        apply_action(state, "prenatal", now, profile, settings)

    state.pregnant = True
    state.conceived_at = (now - timedelta(days=70)).timestamp()
    result = apply_action(state, "prenatal", now, profile, settings)
    assert result.label
    assert state.prenatal_week == 10
    assert any(event["kind"] == "prenatal" for event in state.events)


def test_express_relieves_milk() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    profile = make_profile()
    settings = Settings()
    state = make_state(now)
    state.lactating = True
    state.milk = 80.0
    with pytest.raises(ValueError):
        apply_action(state, "express", now, make_profile(gender="male"), settings)
    apply_action(state, "express", now, profile, settings)
    assert state.milk == pytest.approx(15.0)
    assert state.last_express_at == now.timestamp()


def test_fetal_count_is_rare_but_possible() -> None:
    counts = [_fetal_count("test", f"conception-{index}") for index in range(2000)]
    assert set(counts) <= {1, 2, 3}
    assert counts.count(1) > counts.count(2) > 0


def _away_state(now, days: int) -> BodyState:
    """Build a state whose owner has been away for the given number of days."""
    stamp = (now - timedelta(days=days)).timestamp()
    state = BodyState(persona_id="test", updated_at=stamp)
    state.cycle_anchor = (now - timedelta(days=days)).date().isoformat()
    state.cycle_start = state.cycle_anchor
    state.last_active_at = stamp
    return state


def test_long_absence_keeps_the_cycle_phase() -> None:
    now = datetime(2026, 11, 19, 12, 0, tzinfo=TZ)
    profile = make_profile(age=28)
    settings = Settings(sleep_window="00:00-08:00")
    state = _away_state(now, 90)
    advance(state, now, profile, settings)
    view = cycle_view(state, now, profile, settings)
    # Sleep debt can stretch a cycle by a few days, so a 90-day absence lands
    # somewhere in the first half of a cycle rather than in premenarche.
    assert 1 <= view.day <= 34
    assert view.phase in ("menstrual", "follicular", "luteal")


def test_year_absence_settles_the_cycle() -> None:
    now = datetime(2026, 11, 19, 12, 0, tzinfo=TZ)
    profile = make_profile(age=28)
    settings = Settings(sleep_window="00:00-08:00")
    state = _away_state(now, 365)
    advance(state, now, profile, settings)
    assert 1 <= state.cycle_day <= 28
    assert 0.0 <= state.estrogen <= 100.0
    assert state.ovulation_day == 0 or state.ovulation_day <= state.cycle_day


def test_sad_mood_suppresses_appetite() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    settings = Settings()
    profile = make_profile()
    calm = make_state(now - timedelta(hours=1))
    sad = make_state(now - timedelta(hours=1))
    advance(calm, now, profile, settings)
    advance(sad, now, profile, settings, {"sadness": 1.0})
    assert sad.satiety < calm.satiety


def test_fear_stretches_the_cycle() -> None:
    from core.model import _cycle_stress

    state = make_state(datetime(2026, 11, 18, 12, 0, tzinfo=TZ))
    calm = _cycle_stress(state, None)
    anxious = _cycle_stress(state, {"fear": 1.0})
    assert anxious > calm


def test_bbt_rises_after_ovulation() -> None:
    settings = Settings()
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)

    def view_for(days_ago: int):
        state = make_state(now)
        state.cycle_anchor = (now.date() - timedelta(days=days_ago)).isoformat()
        return cycle_view(state, now, make_profile(), settings)

    follicular = view_for(6)
    luteal = view_for(21)
    assert follicular is not None and luteal is not None
    assert luteal.bbt - follicular.bbt >= 0.15
    assert 36.0 < follicular.bbt < 37.0


def test_mucus_thins_near_ovulation() -> None:
    settings = Settings()
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)

    def view_for(days_ago: int):
        state = make_state(now)
        state.cycle_anchor = (now.date() - timedelta(days=days_ago)).isoformat()
        return cycle_view(state, now, make_profile(), settings)

    early = view_for(3)
    peak = view_for(13)
    late = view_for(22)
    assert early is not None and peak is not None and late is not None
    assert peak.discharge == 3
    assert late.discharge < peak.discharge


def test_mucus_detail_has_volume_texture_and_cervix() -> None:
    settings = Settings()
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)

    def view_for(days_ago: int):
        state = make_state(now)
        state.cycle_anchor = (now.date() - timedelta(days=days_ago)).isoformat()
        return cycle_view(state, now, make_profile(), settings)

    peak = view_for(13)
    late = view_for(22)
    assert peak is not None and late is not None
    assert peak.mucus_volume > late.mucus_volume
    assert peak.mucus_texture in ("watery", "egg_white")
    assert peak.mucus_color == "clear"
    assert peak.mucus_stretch > late.mucus_stretch
    assert peak.cervix_position == "high"
    assert peak.cervix_open is True
    assert late.cervix_position == "low"
    assert late.cervix_open is False
    assert peak.pelvic_pressure >= 0.0
    assert peak.breast_fullness >= 0.0


def test_mucus_stays_consistent_with_the_old_score() -> None:
    settings = Settings()
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now)
    state.cycle_anchor = (now.date() - timedelta(days=12)).isoformat()
    view = cycle_view(state, now, make_profile(), settings)
    assert view is not None
    # The detailed volume is still driven by the same axis as the 0-3 score.
    assert (view.mucus_volume >= 50.0) == (view.discharge >= 2)


def test_organ_loads_react_to_water_diet_and_the_cycle() -> None:
    settings = Settings(sleep_window="23:00-07:00")
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    profile = make_profile()

    dry = make_state(now - timedelta(hours=2))
    dry.hydration = 22.0
    dry.sodium = 85.0
    dry.caffeine = 85.0
    dry.urine_color = 3
    dry.last_drink_at = now.timestamp() - 30.0
    wet = make_state(now - timedelta(hours=2))
    wet.hydration = 95.0
    wet.sodium = 5.0
    wet.caffeine = 0.0
    wet.urine_color = 0
    wet.last_drink_at = now.timestamp() - 30.0
    advance(dry, now, profile, settings)
    advance(wet, now, profile, settings)
    assert dry.kidney_load > wet.kidney_load + 10.0

    heavy = make_state(now - timedelta(hours=12))
    heavy.diet_long = 20.0
    heavy.indigestion = 80.0
    heavy.caffeine = 80.0
    light = make_state(now - timedelta(hours=12))
    light.diet_long = 85.0
    light.indigestion = 0.0
    light.caffeine = 0.0
    advance(heavy, now, profile, settings)
    advance(light, now, profile, settings)
    assert heavy.liver_load > light.liver_load + 10.0

    period = make_state(now - timedelta(hours=12))
    period.cycle_anchor = now.date().isoformat()
    follicular = make_state(now - timedelta(hours=12))
    follicular.cycle_anchor = (now.date() - timedelta(days=8)).isoformat()
    advance(period, now, profile, settings)
    advance(follicular, now, profile, settings)
    assert period.uterine_congestion > follicular.uterine_congestion + 10.0


def test_scalp_breath_and_intimate_scent_are_separate_sources() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    profile = make_profile()
    state = make_state(now)
    state.scalp_scent = 70.0
    state.breath_scent = 65.0
    state.intimate_scent = 60.0
    state.foot_scent = 70.0
    state.period_scent = 60.0
    apply_action(state, "shower", now, profile, Settings())
    assert state.scalp_scent == 0.0
    assert state.breath_scent < 65.0
    assert state.intimate_scent == 0.0
    assert state.foot_scent <= 10.0
    assert state.period_scent <= 10.0
    apply_action(state, "brush", now, profile, Settings())
    assert state.breath_scent <= 2.0


def test_release_leaves_an_afterglow_scent_that_washes_off() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    profile = make_profile(age=25)
    state = make_state(now)
    state.libido = 70.0
    apply_action(state, "release", now, profile, Settings())
    assert state.intimate_scent >= 30.0
    apply_action(state, "shower", now, profile, Settings())
    assert state.intimate_scent == 0.0


def test_age_does_not_gate_the_intimate_module() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    settings = Settings()
    young = make_profile(age=16)
    state = make_state(now)
    state.libido = 100.0
    assert _libido_rate(state, cycle_view(state, now, young, settings), young) > 0.0
    apply_action(state, "release", now, young, settings)
    assert state.release_count >= 1
    apply_action(state, "intimacy", now, young, settings)
    assert state.intimacy_count == 1


def test_puberty_advances_maturity_without_sexual_content() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    settings = Settings()
    child = cycle_view(make_state(now), now, make_profile(age=9), settings)
    teen = cycle_view(make_state(now), now, make_profile(age=16), settings)
    adult = cycle_view(make_state(now), now, make_profile(age=28), settings)
    assert child is not None and teen is not None and adult is not None
    assert (
        child.endocrine_maturity < teen.endocrine_maturity <= adult.endocrine_maturity
    )
    assert child.puberty_stage == "prepubertal"
    assert teen.puberty_stage == "late"
    assert adult.puberty_stage == "adult"
    assert 0.0 <= teen.mucus_volume <= 100.0


def test_mood_follows_hormones_not_the_calendar() -> None:
    settings = Settings()
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)

    def view_for(days_ago: int):
        state = make_state(now)
        state.cycle_anchor = (now.date() - timedelta(days=days_ago)).isoformat()
        return cycle_view(state, now, make_profile(), settings)

    follicular = view_for(6)
    luteal = view_for(21)
    menstrual = view_for(1)
    assert follicular is not None and luteal is not None and menstrual is not None
    assert luteal.mood < follicular.mood
    assert menstrual.mood < follicular.mood


def test_hpo_cycle_stays_on_schedule() -> None:
    profile = make_profile(age=28)
    settings = Settings(sleep_window="00:00-08:00")
    now = datetime(2026, 1, 1, 12, 0, tzinfo=TZ)
    state = BodyState(persona_id="test", updated_at=now.timestamp())
    state.cycle_anchor = "2026-01-01"
    starts = []
    previous = None
    for offset in range(150):
        moment = now + timedelta(days=offset)
        state.last_active_at = moment.timestamp()
        advance(state, moment, profile, settings)
        view = cycle_view(state, moment, profile, settings)
        if previous != "menstrual" and view.phase == "menstrual":
            starts.append(moment.date())
        previous = view.phase
    gaps = [(starts[i + 1] - starts[i]).days for i in range(len(starts) - 1)]
    assert len(gaps) >= 3
    assert all(25 <= gap <= 31 for gap in gaps)


def test_hpo_values_stay_in_range() -> None:
    profile = make_profile(age=28)
    settings = Settings()
    now = datetime(2026, 1, 1, 12, 0, tzinfo=TZ)
    state = BodyState(persona_id="test", updated_at=now.timestamp())
    state.cycle_anchor = "2026-01-01"
    for offset in range(0, 90, 3):
        moment = now + timedelta(days=offset)
        advance(state, moment, profile, settings)
        assert 0.0 <= state.estrogen <= 100.0
        assert 0.0 <= state.progesterone <= 100.0
        assert 0.0 <= state.lh <= 100.0
        assert 0.0 <= state.fsh <= 100.0
        assert 0.0 <= state.follicle <= 1.0


def test_bed_time_arrives_even_without_sleepiness() -> None:
    settings = Settings(sleep_window="23:00-07:00", enable_nap=False)
    profile = make_profile(age=25)
    start = datetime(2026, 11, 18, 21, 0, tzinfo=TZ)
    state = make_state(start)
    state.sleep_debt = 2.0
    for step in range(1, 13):
        advance(state, start + timedelta(minutes=15 * step), profile, settings)
    # Around the habitual bedtime she gets into bed even if not sleepy yet.
    assert state.bed_since > 0 or state.sleeping is True


def test_low_sleepiness_means_lying_awake_first() -> None:
    settings = Settings(sleep_window="23:00-07:00", enable_nap=False)
    profile = make_profile(age=25)
    start = datetime(2026, 11, 18, 22, 45, tzinfo=TZ)
    state = make_state(start)
    state.sleep_debt = 1.0
    bed_at = None
    for step in range(1, 25):
        moment = start + timedelta(minutes=5 * step)
        advance(state, moment, profile, settings)
        if state.bed_since and bed_at is None:
            bed_at = moment
        if state.sleeping:
            break
    assert bed_at is not None
    # A well rested persona does not fall asleep the second she lies down.
    assert state.sleeping is True
    assert state.sleep_since - state.bed_since >= 10 * 60


def test_being_busy_pushes_bedtime_later() -> None:
    settings = Settings(sleep_window="23:00-07:00", enable_nap=False)
    profile = make_profile(age=25)
    start = datetime(2026, 11, 18, 22, 30, tzinfo=TZ)
    state = make_state(start)
    state.sleep_debt = 7.0
    busy_until = (start + timedelta(minutes=90)).timestamp()
    for step in range(1, 25):
        moment = start + timedelta(minutes=5 * step)
        advance(
            state,
            moment,
            profile,
            settings,
            busy_until=busy_until,
            busy_kind="看B站",
        )
    assert state.sleeping is False
    # She may get into bed once the session ends, never while it is running.
    assert state.bed_since == 0.0 or state.bed_since >= busy_until - 60


def test_extreme_debt_beats_being_busy() -> None:
    settings = Settings(sleep_window="23:00-07:00", enable_nap=False)
    profile = make_profile(age=25)
    start = datetime(2026, 11, 18, 15, 0, tzinfo=TZ)
    state = make_state(start)
    state.sleep_debt = 20.0
    busy_until = (start + timedelta(hours=2)).timestamp()
    advance(
        state,
        start + timedelta(minutes=15),
        profile,
        settings,
        busy_until=busy_until,
        busy_kind="看B站",
    )
    assert state.bed_since > 0 or state.sleeping is True


def test_meals_follow_a_late_wake_up() -> None:
    settings = Settings(sleep_window="01:00-07:00", enable_nap=False)
    profile = make_profile(age=25)
    start = datetime(2026, 11, 18, 10, 30, tzinfo=TZ)
    state = make_state(start)
    state.last_wake_at = start.timestamp()
    state.stomach = 25.0
    state.glucose = 40.0
    state.sleep_debt = 4.0
    for step in range(1, 40):
        advance(state, start + timedelta(minutes=15 * step), profile, settings)
    meals = [
        event
        for event in state.events
        if event["kind"] == "self_care" and "吃了" in event["text"]
    ]
    assert meals
    first = datetime.fromisoformat(str(meals[0]["at"]))
    assert first.hour >= 11


def test_brushing_happens_after_waking_and_before_bed() -> None:
    settings = Settings(sleep_window="01:00-07:00", enable_nap=False)
    profile = make_profile(age=25)
    start = datetime(2026, 11, 18, 9, 0, tzinfo=TZ)
    state = make_state(start)
    state.last_wake_at = (start - timedelta(minutes=10)).timestamp()
    state.oral = 60.0
    state.sleep_debt = 5.0
    for step in range(1, 30):
        advance(state, start + timedelta(minutes=15 * step), profile, settings)
    brushes = [item for item in state.care if item["kind"] == "brush"]
    assert brushes, state.care


def test_sleep_phase_drifts_later_with_free_days() -> None:
    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 1, 10, 0, tzinfo=TZ)
    state = make_state(start)
    state.last_wake_at = start.timestamp()
    for step in range(1, 21 * 96):
        advance(state, start + timedelta(minutes=15 * step), profile, settings)
    # With nothing on her calendar the whole rhythm slides later, week by week.
    assert state.sleep_phase > 3600


def test_late_bedtime_pushes_the_next_night_later() -> None:
    from core.model import _update_phase

    profile = make_profile(age=24)
    on_time = make_state(datetime(2026, 11, 1, 9, 0, tzinfo=TZ))
    on_time.sleep_plan_at = on_time.updated_at
    on_time.bed_since = on_time.updated_at
    on_time.sleep_debt = 4.0
    late = make_state(datetime(2026, 11, 1, 9, 0, tzinfo=TZ))
    late.sleep_plan_at = late.updated_at
    late.bed_since = late.updated_at + 2 * 3600
    late.sleep_debt = 4.0
    moment = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    _update_phase(on_time, moment, profile)
    _update_phase(late, moment, profile)
    assert late.sleep_phase > on_time.sleep_phase


def test_sleep_phase_is_capped() -> None:
    from core.model import SLEEP_PHASE_MAX, _update_phase

    profile = make_profile(age=24)
    state = make_state(datetime(2026, 11, 1, 12, 0, tzinfo=TZ))
    state.sleep_phase = SLEEP_PHASE_MAX - 60
    state.sleep_plan_at = state.updated_at
    state.bed_since = state.updated_at + 3600
    state.sleep_debt = 8.0
    _update_phase(state, datetime(2026, 11, 1, 12, 0, tzinfo=TZ), profile)
    assert state.sleep_phase <= SLEEP_PHASE_MAX


def test_early_morning_wake_pulls_the_phase_back() -> None:
    from core.model import _update_phase

    profile = make_profile(age=24)
    night = make_state(datetime(2026, 11, 1, 10, 0, tzinfo=TZ))
    night.sleep_plan_at = night.updated_at
    night.bed_since = night.updated_at
    night.sleep_debt = 4.0
    morning = make_state(datetime(2026, 11, 1, 7, 0, tzinfo=TZ))
    morning.sleep_plan_at = morning.updated_at
    morning.bed_since = morning.updated_at
    morning.sleep_debt = 4.0
    _update_phase(night, datetime(2026, 11, 1, 10, 0, tzinfo=TZ), profile)
    _update_phase(morning, datetime(2026, 11, 1, 7, 0, tzinfo=TZ), profile)
    # Waking at seven in summer light pulls the rhythm earlier than nine.
    assert morning.sleep_phase < night.sleep_phase


def test_early_night_intent_pulls_the_phase_back() -> None:
    from core.model import _update_phase

    profile = make_profile(age=24)
    state = make_state(datetime(2026, 11, 1, 11, 0, tzinfo=TZ))
    state.sleep_phase = 3 * 3600
    state.sleep_anchor_at = state.updated_at
    state.bed_since = state.updated_at
    state.sleep_intent = 3600
    state.sleep_debt = 4.0
    before = state.sleep_phase
    _update_phase(state, datetime(2026, 11, 1, 11, 0, tzinfo=TZ), profile)
    assert state.sleep_phase < before


def test_drift_settles_rather_than_running_away() -> None:
    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 1, 10, 0, tzinfo=TZ)
    state = make_state(start)
    state.last_wake_at = start.timestamp()
    for step in range(1, 60 * 96):
        advance(state, start + timedelta(minutes=15 * step), profile, settings)
    # She notices the slide and tries to go to bed earlier, so after two
    # months she is still around a late night, not a whole day off.
    assert 0.5 * 3600 < state.sleep_phase < 3.5 * 3600


def test_release_does_not_lose_odds_to_frequent_advances() -> None:
    # Page polling and chat traffic advance the state every few seconds. The
    # arousal roll is drawn once per 15-minute bucket, so its chance must cover
    # the whole bucket instead of the few seconds since the last call.
    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 21, 9, 0, tzinfo=TZ)
    state = BodyState(persona_id="test", updated_at=start.timestamp())
    state.last_wake_at = start.timestamp()
    state.cycle_anchor = "2026-11-01"
    state.sleep_debt = 3.0
    state.satiety = 80.0
    for step in range(1, 24 * 30):
        advance(state, start + timedelta(minutes=2 * step), profile, settings)
    assert state.release_count >= 1
    assert state.last_self_at > start.timestamp()


def test_drinking_happens_several_times_a_day() -> None:
    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 21, 9, 0, tzinfo=TZ)
    state = make_state(start)
    state.last_wake_at = start.timestamp()
    state.hydration = 60.0
    state.sleep_debt = 3.0
    for step in range(1, 24 * 4):
        advance(state, start + timedelta(minutes=15 * step), profile, settings)
    drinks = [item for item in state.care if item["kind"] == "drink"]
    # Sipping through the day, not one big glass every eight hours.
    assert len(drinks) >= 6


def test_self_release_takes_a_short_break(monkeypatch) -> None:
    import core.model as model

    monkeypatch.setattr(model, "_roll", lambda *parts: 0.0)
    now = datetime(2026, 11, 21, 15, 0, tzinfo=TZ)
    profile = Profile("rel", "t", "female", 25, True, 28, 5)
    state = make_state(now - timedelta(minutes=5))
    state.persona_id = profile.persona_id
    state.libido = 90.0
    const = Constitution(
        sleep_need=8.0,
        metabolism=1.0,
        stomach_capacity=1.0,
        gut_transit=1.0,
        self_rate=20.0,
    )
    model._maybe_release(state, now, profile, Settings(), const, 0.25, 0.0)
    assert state.release_count == 1
    assert state.release_until > now.timestamp()
    assert state.release_kind in ("self", "bed")
    # Between four and twenty-five minutes: people differ, and the body has limits.
    assert 4 * 60 <= state.release_until - now.timestamp() <= 25 * 60


def test_release_duration_fits_the_body_and_the_persona() -> None:
    from core.model import _release_seconds

    moment = datetime(2026, 11, 21, 22, 0, tzinfo=TZ)
    for persona in ("a", "b", "c"):
        profile = Profile(persona, "t", "female", 25, True, 28, 5)
        state = make_state(moment)
        state.energy = 55.0
        for kind in ("self", "bed", "shower"):
            seconds = _release_seconds(state, profile, moment, kind)
            assert 4 * 60 <= seconds <= 25 * 60
            if kind == "shower":
                assert seconds <= 9 * 60


def test_wetness_has_a_baseline_even_without_arousal() -> None:
    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 21, 12, 0, tzinfo=TZ)
    state = make_state(start)
    state.libido = 0.0
    state.wetness = 0.0
    state.cycle_anchor = (start - timedelta(days=20)).date().isoformat()
    for step in range(1, 25):
        advance(state, start + timedelta(hours=step), profile, settings)
    # Not aroused, but the body still has its baseline discharge.
    assert 5.0 <= state.wetness <= 35.0


def test_ovulation_is_wetter_than_the_luteal_phase() -> None:
    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 21, 12, 0, tzinfo=TZ)
    ovulation = make_state(start)
    ovulation.libido = 0.0
    ovulation.wetness = 0.0
    ovulation.cycle_anchor = (start - timedelta(days=13)).date().isoformat()
    luteal = make_state(start)
    luteal.libido = 0.0
    luteal.wetness = 0.0
    luteal.cycle_anchor = (start - timedelta(days=20)).date().isoformat()
    for step in range(1, 13):
        advance(ovulation, start + timedelta(hours=step), profile, settings)
        advance(luteal, start + timedelta(hours=step), profile, settings)
    assert ovulation.wetness > luteal.wetness + 5.0


def test_sleep_lowers_heart_and_breathing() -> None:
    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    night = datetime(2026, 11, 21, 1, 0, tzinfo=TZ)
    sleeping = make_state(night)
    sleeping.last_wake_at = (night - timedelta(hours=16)).timestamp()
    sleeping.sleep_debt = 8.0
    for step in range(1, 13):
        advance(sleeping, night + timedelta(minutes=30 * step), profile, settings)
    day = datetime(2026, 11, 21, 13, 0, tzinfo=TZ)
    awake = make_state(day)
    awake.last_wake_at = (day - timedelta(hours=4)).timestamp()
    awake.sleep_debt = 2.0
    for step in range(1, 13):
        advance(awake, day + timedelta(minutes=30 * step), profile, settings)
    assert sleeping.heart_rate < awake.heart_rate
    assert sleeping.breath_rate < awake.breath_rate


def test_an_ordinary_day_keeps_a_human_rhythm() -> None:
    from collections import Counter

    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 18, 9, 0, tzinfo=TZ)
    state = make_state(start)
    state.last_wake_at = start.timestamp()
    state.hydration = 70.0
    state.sleep_debt = 4.0
    counts: Counter[str] = Counter()
    for day in range(7):
        state.care = []
        for step in range(96):
            advance(
                state,
                start + timedelta(days=day, minutes=15 * (step + 1)),
                profile,
                settings,
            )
        counts.update(item["kind"] for item in state.care)
    per_day = {kind: total / 7 for kind, total in counts.items()}
    assert 2.5 <= per_day.get("meal", 0) <= 3.5
    assert 4.0 <= per_day.get("drink", 0) <= 9.0
    assert 1.5 <= per_day.get("brush", 0) <= 2.5
    assert 3.0 <= per_day.get("toilet", 0) <= 7.0
    assert 0.5 <= per_day.get("shower", 0) <= 1.2


def test_scent_follows_the_cycle_and_washes_off() -> None:
    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 21, 12, 0, tzinfo=TZ)
    period = make_state(start)
    period.cycle_anchor = (start - timedelta(days=1)).date().isoformat()
    period.scent = 50.0
    luteal = make_state(start)
    luteal.cycle_anchor = (start - timedelta(days=23)).date().isoformat()
    luteal.scent = 50.0
    for step in range(1, 13):
        advance(period, start + timedelta(hours=step), profile, settings)
        advance(luteal, start + timedelta(hours=step), profile, settings)
    assert period.scent > luteal.scent


def test_affect_reports_premenstrual_irritability() -> None:
    from core.affect import build_affect

    now = datetime(2026, 11, 21, 20, 0, tzinfo=TZ)
    settings = Settings()
    profile = make_profile(age=24)
    state = make_state(now)
    state.cycle_anchor = (now - timedelta(days=25)).date().isoformat()
    view = cycle_view(state, now, profile, settings)
    affect = build_affect(state, view)
    assert affect["pms"] > 0.3
    assert affect["irritability"] > 0.15
    assert "经前" in affect["line"]


def test_private_and_group_full_mention_cycle_mood_but_basic_does_not() -> None:
    from core.prompt import build_prompt_block

    now = datetime(2026, 11, 21, 20, 0, tzinfo=TZ)
    profile = make_profile(age=24)
    state = make_state(now)
    state.cycle_anchor = (now - timedelta(days=25)).date().isoformat()
    private = build_prompt_block(state, now, profile, Settings(), True)
    full = build_prompt_block(state, now, profile, Settings(group_detail="full"), False)
    basic = build_prompt_block(
        state, now, profile, Settings(group_detail="basic"), False
    )
    assert "经前" in private
    assert "经前" in full
    assert "经前" not in basic


def test_period_libido_is_lower_than_ovulation() -> None:
    from core.model import _libido_rate, cycle_view

    now = datetime(2026, 11, 21, 12, 0, tzinfo=TZ)
    profile = make_profile(age=24)
    period = make_state(now)
    period.cycle_anchor = (now - timedelta(days=1)).date().isoformat()
    ovulation = make_state(now)
    ovulation.cycle_anchor = (now - timedelta(days=13)).date().isoformat()
    settings = Settings()
    period_rate = _libido_rate(
        period, cycle_view(period, now, profile, settings), profile
    )
    ovulation_rate = _libido_rate(
        ovulation, cycle_view(ovulation, now, profile, settings), profile
    )
    assert ovulation_rate > period_rate + 15.0


def test_luteal_sleep_is_lighter_and_slower() -> None:
    from core.model import _latency_minutes, _routine, constitution

    moment = datetime(2026, 11, 21, 9, 0, tzinfo=TZ)
    profile = make_profile(age=24)
    settings = Settings()
    const = constitution(profile.persona_id)
    early = make_state(moment)
    early.progesterone = 0.0
    early.pain = 0.0
    late = make_state(moment)
    late.progesterone = 80.0
    late.pain = 20.0
    _routine(early, moment, profile, settings, const)
    _routine(late, moment, profile, settings, const)
    assert late.sleep_quality < early.sleep_quality - 5.0
    assert _latency_minutes(late, profile, "2026-11-21") > _latency_minutes(
        early, profile, "2026-11-21"
    )


def test_caffeine_raises_heart_rate() -> None:
    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 21, 14, 0, tzinfo=TZ)
    plain = make_state(start)
    coffee = make_state(start)
    coffee.caffeine = 80.0
    for step in range(1, 9):
        advance(plain, start + timedelta(minutes=30 * step), profile, settings)
        advance(coffee, start + timedelta(minutes=30 * step), profile, settings)
    assert coffee.heart_rate > plain.heart_rate


def test_being_out_prevents_self_release(monkeypatch) -> None:
    import core.model as model

    monkeypatch.setattr(model, "_roll", lambda *parts: 0.0)
    moment = datetime(2026, 11, 21, 15, 0, tzinfo=TZ)
    profile = make_profile(age=24)
    state = make_state(moment)
    state.libido = 95.0
    state.location = "out"
    const = constitution(profile.persona_id)
    model._maybe_release(state, moment, profile, Settings(), const, 0.25, 0.0)
    assert state.release_count == 0


def test_a_week_has_some_exercise_and_sitting_stiffness() -> None:
    from collections import Counter

    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 18, 9, 0, tzinfo=TZ)
    state = make_state(start)
    state.last_wake_at = start.timestamp()
    counts = Counter()
    for day in range(7):
        state.care = []
        for step in range(96):
            advance(
                state,
                start + timedelta(days=day, minutes=15 * (step + 1)),
                profile,
                settings,
            )
        counts.update(item["kind"] for item in state.care)
    assert counts.get("exercise", 0) >= 2


def test_sitting_all_day_makes_the_back_ache() -> None:
    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 21, 10, 0, tzinfo=TZ)
    state = make_state(start)
    state.last_wake_at = start.timestamp()
    state.sedentary = 90.0
    for step in range(1, 9):
        advance(state, start + timedelta(minutes=15 * step), profile, settings)
    assert state.sedentary > 65.0
    assert state.pain > 0.0
    assert state.pain_site == "肩背"


def test_illness_rises_peaks_and_recovers() -> None:
    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 21, 8, 0, tzinfo=TZ)
    state = make_state(start)
    state.last_wake_at = start.timestamp()
    state.illness = "cold"
    state.illness_started_at = start.timestamp()
    state.illness_severity = 30.0
    peak = 0.0
    for step in range(1, 6 * 24 * 4):
        advance(state, start + timedelta(minutes=15 * step), profile, settings)
        peak = max(peak, state.illness_severity)
        if not state.illness:
            break
    assert peak >= 55.0
    assert state.illness == ""


def test_period_line_has_flow_and_colour() -> None:
    from core.model import cycle_view
    from core.prompt import _cycle_text

    now = datetime(2026, 11, 21, 12, 0, tzinfo=TZ)
    profile = make_profile(age=24)
    state = make_state(now)
    state.cycle_anchor = (now - timedelta(days=1)).date().isoformat()
    text = _cycle_text(cycle_view(state, now, profile, Settings()))
    assert "经期" in text
    assert "鲜红" in text or "暗红" in text
    assert "量" in text


def test_meal_kind_is_stable_and_sensible() -> None:
    from core.model import _meal_kind

    allowed = {"light", "spicy", "greasy", "protein", "sweet", "veg", "soup"}
    first = _meal_kind("test", "2026-11-21", "lunch")
    assert first in allowed
    assert _meal_kind("test", "2026-11-21", "lunch") == first


def test_indigestion_makes_the_body_feel_worse() -> None:
    from core.affect import build_affect

    now = datetime(2026, 11, 21, 14, 0, tzinfo=TZ)
    profile = make_profile(age=24)
    fine = make_state(now)
    fine.indigestion = 0.0
    upset = make_state(now)
    upset.indigestion = 80.0
    fine_affect = build_affect(fine, cycle_view(fine, now, profile, Settings()))
    upset_affect = build_affect(upset, cycle_view(upset, now, profile, Settings()))
    assert upset_affect["comfort"] < fine_affect["comfort"]
    assert upset_affect["distress"] > fine_affect["distress"]


def test_winter_daylight_lowers_the_mood() -> None:
    from core.affect import build_affect

    now = datetime(2026, 11, 21, 12, 0, tzinfo=TZ)
    profile = make_profile(age=24)
    summer = make_state(now)
    summer.daylight = 15.0
    winter = make_state(now)
    winter.daylight = 8.0
    summer_affect = build_affect(summer, cycle_view(summer, now, profile, Settings()))
    winter_affect = build_affect(winter, cycle_view(winter, now, profile, Settings()))
    assert winter_affect["seasonal"] < summer_affect["seasonal"]
    assert winter_affect["valence"] < summer_affect["valence"]


def test_a_long_bad_diet_worsens_period_symptoms() -> None:
    now = datetime(2026, 11, 21, 12, 0, tzinfo=TZ)
    profile = make_profile(age=24)
    good = make_state(now)
    good.cycle_anchor = (now - timedelta(days=1)).date().isoformat()
    good.diet_long = 70.0
    bad = make_state(now)
    bad.cycle_anchor = (now - timedelta(days=1)).date().isoformat()
    bad.diet_long = 30.0
    good_view = cycle_view(good, now, profile, Settings())
    bad_view = cycle_view(bad, now, profile, Settings())
    assert bad_view.cramps >= good_view.cramps
    assert bad_view.mood <= good_view.mood


def test_showers_happen_almost_every_day() -> None:
    from collections import Counter

    settings = Settings(sleep_window="01:00-07:00")
    profile = make_profile(age=24)
    start = datetime(2026, 11, 18, 9, 0, tzinfo=TZ)
    state = make_state(start)
    state.last_wake_at = start.timestamp()
    counts = Counter()
    for day in range(7):
        state.care = []
        for step in range(96):
            advance(
                state,
                start + timedelta(days=day, minutes=15 * (step + 1)),
                profile,
                settings,
            )
        counts.update(item["kind"] for item in state.care)
    assert counts.get("shower", 0) >= 5


def test_a_full_bladder_wakes_her_in_the_night() -> None:
    start = datetime(2026, 11, 18, 23, 30, tzinfo=TZ)
    state = make_state(start)
    state.sleeping = True
    state.sleep_kind = "night"
    state.sleep_since = start.timestamp()
    state.sleep_wake_at = (start + timedelta(hours=8)).timestamp()
    state.urine = 55.0
    state.hydration = 95.0
    state.caffeine = 50.0
    state.last_toilet_at = (start - timedelta(hours=4)).timestamp()
    state.sleep_debt = 8.0
    state.sleep_quality = 90.0
    profile = make_profile(age=24)
    settings = Settings(sleep_window="23:00-07:00", enable_nap=False)
    advance(state, start + timedelta(hours=8), profile, settings)
    assert state.night_wakes >= 1
    assert "半夜起来上了个厕所" in " ".join(
        str(item.get("text")) for item in state.events
    )
    assert state.sleep_quality < 90.0


def test_morning_urge_takes_her_to_the_toilet() -> None:
    now = datetime(2026, 11, 18, 7, 30, tzinfo=TZ)
    state = make_state(now)
    state.sleeping = False
    state.bed_since = 0.0
    state.last_wake_at = (now - timedelta(hours=1)).timestamp()
    state.bowel = 56.0
    state.gut = 65.0
    profile = make_profile()
    advance(state, now + timedelta(minutes=30), profile, Settings())
    assert state.last_bowel_at >= now.timestamp()
    assert "去上了个大号" in " ".join(str(item.get("text")) for item in state.events)


def test_a_mild_urge_does_not_send_her_at_once() -> None:
    now = datetime(2026, 11, 18, 7, 30, tzinfo=TZ)
    state = make_state(now - timedelta(hours=1))
    state.sleeping = False
    state.bed_since = 0.0
    state.last_wake_at = (now - timedelta(hours=1)).timestamp()
    state.bowel = 40.0
    state.gut = 52.0
    profile = make_profile()
    advance(state, now + timedelta(minutes=30), profile, Settings())
    assert state.last_bowel_at == 0.0


def test_a_urine_break_can_lead_to_a_release(monkeypatch) -> None:
    import core.model as model

    monkeypatch.setattr(model, "_roll", lambda *args, **kwargs: 0.0)
    now = datetime(2026, 11, 18, 15, 0, tzinfo=TZ)
    state = make_state(now)
    state.libido = 70.0
    state.location = "home"
    model._maybe_toilet_release(state, now, make_profile(), Settings(), "urine")
    assert state.release_count == 1
    assert state.release_kind == "toilet"
    assert state.release_until > now.timestamp()


def test_libido_ceiling_forces_an_outlet(monkeypatch) -> None:
    import core.model as model

    monkeypatch.setattr(model, "_roll", lambda *args, **kwargs: 0.99)
    now = datetime(2026, 11, 18, 22, 0, tzinfo=TZ)
    state = make_state(now)
    state.libido = 100.0
    state.location = "home"
    const = model.constitution("pity")
    model._maybe_release(state, now, make_profile(), Settings(), const, 0.25, 0.0)
    assert state.release_count == 1


def test_high_but_not_full_libido_is_not_a_guarantee(monkeypatch) -> None:
    import core.model as model

    monkeypatch.setattr(model, "_roll", lambda *args, **kwargs: 0.99)
    now = datetime(2026, 11, 18, 22, 0, tzinfo=TZ)
    state = make_state(now)
    state.libido = 77.0
    state.location = "home"
    const = model.constitution("pity")
    model._maybe_release(state, now, make_profile(), Settings(), const, 0.25, 0.0)
    assert state.release_count == 0


def test_a_high_drive_session_can_have_two_rounds(monkeypatch) -> None:
    import core.model as model

    monkeypatch.setattr(model, "_roll", lambda *args, **kwargs: 0.0)
    now = datetime(2026, 11, 18, 22, 0, tzinfo=TZ)
    state = make_state(now)
    state.libido = 90.0
    state.location = "home"
    const = model.constitution("rounds")
    model._maybe_release(state, now, make_profile(), Settings(), const, 0.25, 0.0)
    assert state.release_count == 1
    assert any("两次" in str(event.get("text")) for event in state.events)


def test_sleep_stress_and_illness_delay_ovulation() -> None:
    from core.model import _cycle_stress

    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    calm = make_state(now)
    calm.sleep_debt = 2.0
    calm.stress = 20.0
    calm.illness_severity = 0.0
    rough = make_state(now)
    rough.sleep_debt = 14.0
    rough.stress = 85.0
    rough.illness_severity = 50.0
    assert _cycle_stress(rough) > _cycle_stress(calm) * 1.15


def test_perimenopause_can_have_a_hot_flash(monkeypatch) -> None:
    import core.model as model

    monkeypatch.setattr(model, "_roll", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(model, "_menopause_age", lambda persona_id: 55.0)
    now = datetime(2026, 11, 18, 22, 0, tzinfo=TZ)
    state = make_state(now)
    profile = Profile("peri", "t", "female", 52, True, 28, 5)
    model._maybe_hot_flash(state, now, profile, 1.0, 0.0)
    assert state.hot_flash_until > now.timestamp()
    assert state.sweat > 0.0


def test_spring_pollen_raises_allergy() -> None:
    now = datetime(2026, 4, 10, 9, 0, tzinfo=TZ)
    state = make_state(now - timedelta(hours=1))
    state.sleeping = False
    state.bed_since = 0.0
    state.last_wake_at = (now - timedelta(hours=2)).timestamp()
    advance(state, now, make_profile(age=17), Settings())
    assert state.allergy > 0.0


def test_low_hydration_darkens_the_urine() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now - timedelta(minutes=15))
    state.hydration = 25.0
    state.sleeping = False
    state.bed_since = 0.0
    state.last_wake_at = (now - timedelta(hours=2)).timestamp()
    advance(state, now, make_profile(), Settings())
    assert state.urine_color >= 2


def test_genitals_follow_arousal() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    settings = Settings()
    profile = make_profile(age=25)
    anchor = (now.date() - timedelta(days=6)).isoformat()

    calm_state = make_state(now)
    calm_state.cycle_anchor = anchor
    calm = cycle_view(calm_state, now, profile, settings)

    hot_state = make_state(now)
    hot_state.cycle_anchor = anchor
    hot_state.wetness = 90.0
    hot_state.libido = 90.0
    hot = cycle_view(hot_state, now, profile, settings)

    assert calm is not None and hot is not None
    assert calm.clitoris == "hidden"
    assert hot.clitoris == "engorged"
    assert calm.labia_minora == "tucked"
    assert hot.labia_minora == "flared"
    assert calm.vagina_open == "closed"
    assert hot.vagina_open == "open"
    assert hot.vagina_warmth == "hot"
    assert hot.vestibule == "glossy"


def test_rectum_fills_from_the_gut_and_a_bowel_movement_clears_it() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    profile = make_profile()
    settings = Settings()
    state = make_state(now)
    state.gut = 45.0
    advance(state, now + timedelta(minutes=15), profile, settings)
    assert state.rectum > 0.0
    before = state.anus_irritation
    apply_action(state, "toilet", now + timedelta(minutes=16), profile, settings)
    assert state.rectum == 0.0
    assert state.anus_irritation > before


def test_urethra_irritation_tracks_concentrated_urine() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    profile = make_profile()
    settings = Settings()
    dry = make_state(now)
    dry.hydration = 25.0
    dry.urine_color = 3
    dry.caffeine = 60.0
    wet = make_state(now)
    wet.hydration = 90.0
    wet.urine_color = 0
    wet.caffeine = 0.0
    advance(dry, now + timedelta(hours=2), profile, settings)
    advance(wet, now + timedelta(hours=2), profile, settings)
    assert dry.urethra_irritation > wet.urethra_irritation


def test_pubic_hair_and_areola_vary_by_persona() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    traits = {
        (
            round(constitution(pid).pubic_hair_density, 3),
            round(constitution(pid).areola_size, 3),
        )
        for pid in ("a", "b", "c", "d", "e")
    }
    assert len(traits) > 1
    view = cycle_view(make_state(now), now, make_profile(age=25), Settings())
    assert view is not None
    assert view.pubic_hair in {"none", "sparse", "medium", "bushy", "shaved"}
    assert view.areola in {"small", "medium", "large"}
    assert view.areola_tint in {"pink", "rose", "brown"}
    assert isinstance(view.montgomery, bool)
    assert view.nipple in {"soft", "erect"}


def test_oral_detail_tracks_brushing_and_illness() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    profile = make_profile()
    settings = Settings()
    state = make_state(now)
    state.oral = 80.0
    state.plaque = 70.0
    state.gum_irritation = 30.0
    state.tongue_coat = 50.0
    apply_action(state, "brush", now, profile, settings)
    assert state.plaque == 2.0
    assert state.gum_irritation < 30.0
    assert state.tongue_coat < 50.0

    poor = make_state(now)
    poor.hydration = 25.0
    poor.dry_mouth = 60.0
    poor.illness = "cold"
    poor.illness_severity = 50.0
    # Just drank, so the autonomous self-care loop will not top her up again
    # inside this short window and hide the dry-mouth effect.
    poor.last_drink_at = now.timestamp()
    advance(poor, now + timedelta(minutes=45), profile, settings)
    assert poor.saliva < 62.0
    assert poor.throat > 0.0


def test_pubic_hair_config_can_force_none() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    view = cycle_view(
        make_state(now), now, make_profile(age=25), Settings(pubic_hair="none")
    )
    assert view is not None
    assert view.pubic_hair == "none"
    assert view.pubic_hair_style == "none"


def test_shower_washes_the_new_scent_sources() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    profile = make_profile()
    settings = Settings()
    state = make_state(now)
    state.underarm_scent = 70.0
    state.vulva_scent = 60.0
    state.anus_scent = 55.0
    state.nipple_scent = 40.0
    apply_action(state, "shower", now, profile, settings)
    assert state.underarm_scent < 10.0
    assert state.vulva_scent < 10.0
    assert state.anus_scent < 10.0
    assert state.nipple_scent < 10.0


def test_new_scent_sources_rise_from_the_body() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    profile = make_profile()
    settings = Settings()
    state = make_state(now)
    state.sweat = 80.0
    state.ambient_temp = 33.0
    state.anus_irritation = 60.0
    state.wetness = 80.0
    # Just showered, so the autonomous loop will not wash the new scents away
    # inside this short window.
    state.last_shower_at = now.timestamp()
    advance(state, now + timedelta(hours=1), profile, settings)
    assert state.underarm_scent > 10.0
    assert state.anus_scent > 5.0
    assert state.vulva_scent > 5.0
