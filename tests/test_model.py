"""Tests for the pure body simulation."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.config import Settings
from core.model import (
    PAD_RESTOCK,
    BodyState,
    _cycle_length_for,
    _fetal_count,
    _miscarriage_chance,
    _libido_rate,
    _emission_age_factor,
    _menarche_age,
    _menopause_age,
    _morning_age_factor,
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
    state.sleep_debt = 18
    settings = Settings(sleep_window="23:00-07:00")
    advance(state, now, make_profile(), settings)
    assert state.satiety < 70
    assert state.hydration < 80
    assert state.energy < 60
    assert state.stomach < 70
    assert state.hydration < 80
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
    assert active.energy < sleeping.energy
    assert active.sleep_debt > sleeping.sleep_debt


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


def test_intimacy_is_adult_only_and_sets_refractory() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = make_state(now)
    settings = Settings()
    gated = make_profile(adult=False)
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
        for age in (16, 25, 35, 50)
    ]
    assert rates == [70.0, 60.0, 45.0, 28.0]
    assert rates == sorted(rates, reverse=True)


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


def test_self_release_lowers_libido() -> None:
    now = datetime(2026, 11, 19, 22, 0, tzinfo=TZ)
    settings = Settings()
    hits = 0
    for index in range(16):
        profile = Profile(f"rel{index}", "t", "female", 25, True, 28, 5)
        state = make_state(now - timedelta(hours=1))
        state.persona_id = profile.persona_id
        state.last_active_at = now.timestamp() - 10 * 3600
        state.libido = 95.0
        state.last_self_at = now.timestamp() - 6 * 86400
        advance(state, now, profile, settings)
        if any(event["kind"] == "self_release" for event in state.events):
            hits += 1
            assert state.libido < 95.0
            assert now.timestamp() - 3600 <= state.last_self_at <= now.timestamp()
    assert hits > 0


def test_release_frequency_follows_persona_trait() -> None:
    now = datetime(2026, 11, 19, 22, 0, tzinfo=TZ)
    settings = Settings()

    def run(trait: float) -> int:
        profile = Profile("freq", "t", "male", 25, True, 28, 5, libido_trait=trait)
        state = make_state(now - timedelta(days=30))
        state.persona_id = profile.persona_id
        state.last_active_at = state.updated_at
        advance(state, now, profile, settings)
        return state.release_count

    low, normal, high = run(0.6), run(1.0), run(1.5)
    assert low < normal < high
    assert normal >= 10


def test_libido_trait_reads_persona_prompt() -> None:
    from core.profile import detect_libido_trait

    assert detect_libido_trait("她性欲很强，需求很大。") == 1.8
    assert detect_libido_trait("她性冷淡，对这件事没兴趣。") == 0.35
    assert detect_libido_trait("一个普通的医生。") == 1.0


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
    assert male.adult is True

    unknown = detect_profile("anon", "anon", "一个普通的助手。", Settings())
    assert unknown.gender == "none"
    assert unknown.adult is False


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
    # 90 days is three cycles plus six days: around day seven, not a luteal day.
    assert 5 <= view.day <= 10
    assert view.phase in ("menstrual", "follicular")


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
