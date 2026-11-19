"""Tests for prompt filtering and page payloads."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.config import Settings
from core.model import BodyState
from core.profile import Profile
from core.prompt import build_prompt_block, build_status_text, build_web_state

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
    """Build a state with a known cycle day."""
    state = BodyState(
        persona_id="test",
        updated_at=now.timestamp(),
        hunger=62,
        thirst=22,
        bladder=38,
        cleanliness=30,
        oral=72,
        fatigue=58,
        desire=60,
        stomach=38,
        glucose=55,
        hydration=78,
        urine=38,
        skin=30,
        sleep_debt=8,
        last_eat_at=now.timestamp() - 5 * 3600,
        last_drink_at=now.timestamp() - 1 * 3600,
    )
    state.cycle_anchor = (now.date() - timedelta(days=1)).isoformat()
    return state


def test_prompt_keeps_numbers_out_of_dialogue() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    block = build_prompt_block(
        make_state(now), now, make_profile(), Settings(), private=True
    )
    assert "/100" not in block


def test_bowel_pressure_appears_when_it_matters() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.gut = 70.0
    state.bowel = 68.0
    block = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "排便：" in block


def test_group_chat_hides_self_release() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.events.append(
        {
            "at": (now - timedelta(hours=1)).isoformat(),
            "kind": "self_release",
            "text": "自己解决了一次，身体松下来",
        }
    )
    group = build_prompt_block(
        state, now, make_profile(), Settings(group_detail="full"), private=False
    )
    private = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "自己解决" not in group
    assert "自己解决" in private


def test_loaded_body_adds_irritation_hint() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.hunger = 95.0
    state.thirst = 95.0
    state.sleep_debt = 18.0
    state.bowel = 90.0
    block = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "耐心" in block


def test_private_block_contains_cycle_and_adult_lines() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    block = build_prompt_block(
        make_state(now), now, make_profile(), Settings(), private=True
    )
    assert "【身体状态｜14:32】" in block
    assert "经期：第 2 天" in block
    assert "欲望" in block
    assert "body_action" in block


def test_profile_without_adult_flag_has_no_adult_state() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.desire = 0
    block = build_prompt_block(
        state,
        now,
        make_profile(adult=False),
        Settings(),
        private=True,
    )
    assert "经期：第 2 天" in block
    assert "欲望" not in block
    assert "梦遗" not in block


def test_group_basic_hides_cycle() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    block = build_prompt_block(
        make_state(now), now, make_profile(), Settings(), private=False
    )
    assert "周期" not in block
    assert "经期：" not in block
    assert "欲望" not in block
    assert "胃：" in block


def test_group_full_shows_cycle_but_not_adult() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    settings = Settings(group_detail="full")
    block = build_prompt_block(
        make_state(now), now, make_profile(), settings, private=False
    )
    assert "经期：第 2 天" in block
    assert "欲望" not in block


def test_status_card_and_web_payload() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    profile = make_profile()
    text = build_status_text(state, now, profile, Settings(), private=True)
    assert "test · 身体" in text
    assert "经期：" in text
    payload = build_web_state(state, now, profile, Settings())
    assert payload["gender"] == "female"
    assert payload["cycle"]["phase"] == "menstrual"
    assert payload["cycle"]["estrogen"] > 0
    assert any(detail["key"] == "stomach" for detail in payload["details"])
    meters = {meter["key"]: meter for meter in payload["meters"]}
    # Fullness and hydration point the other way from the internal need value.
    assert meters["satiety"]["value"] == pytest.approx(100 - state.hunger, abs=0.1)
    assert meters["hydration"]["value"] == pytest.approx(100 - state.thirst, abs=0.1)
    # Pressure meters keep "full is strong".
    assert meters["bladder"]["value"] == pytest.approx(state.bladder, abs=0.1)
    assert meters["desire"]["value"] == pytest.approx(state.desire, abs=0.1)
    assert "饱食度" in text


def test_status_card_hides_male_events_when_intimate_off() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.last_release_at = now.timestamp() - 5 * 86400
    text = build_status_text(
        state,
        now,
        make_profile(gender="male"),
        Settings(enable_intimate=False),
        private=True,
    )
    assert "梦遗" not in text
    assert "欲望" not in text


def test_status_card_has_no_age_line() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    text = build_status_text(
        make_state(now),
        now,
        make_profile(),
        Settings(),
        private=True,
    )
    assert "岁" not in text
    assert "未成年" not in text


def test_display_meters_is_the_only_conversion_point() -> None:
    from core.prompt import display_meters

    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    meters = {
        meter["key"]: meter
        for meter in display_meters(state, make_profile(), Settings())
    }
    for key, field in (
        ("satiety", "hunger"),
        ("hydration", "thirst"),
        ("energy", "fatigue"),
        ("cleanliness", "cleanliness"),
        ("oral", "oral"),
    ):
        assert meters[key]["value"] == pytest.approx(
            100 - getattr(state, field), abs=0.1
        )
    # Pressure meters keep the internal value.
    assert meters["bladder"]["value"] == pytest.approx(state.bladder, abs=0.1)
    assert meters["desire"]["value"] == pytest.approx(state.desire, abs=0.1)


def test_web_state_omits_hygiene_when_disabled() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    payload = build_web_state(
        make_state(now), now, make_profile(), Settings(enable_hygiene=False)
    )
    keys = {meter["key"] for meter in payload["meters"]}
    assert "cleanliness" not in keys
    assert "oral" not in keys
