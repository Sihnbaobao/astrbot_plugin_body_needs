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
    """Build a state with a known cycle day."""
    state = BodyState(
        persona_id="test",
        updated_at=now.timestamp(),
        satiety=38,
        hydration=78,
        bladder=38,
        cleanliness=70,
        oral_health=28,
        energy=42,
        libido=60,
        stomach=38,
        glucose=55,
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


def test_private_block_shows_cold_and_pain() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.illness = "cold"
    state.illness_severity = 55.0
    state.pain = 35.0
    state.pain_site = "头"
    block = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "感冒：" in block
    assert "疼：头" in block


def test_group_chat_hides_self_release() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.events.append(
        {
            "at": (now - timedelta(hours=1)).isoformat(),
            "kind": "self_release",
            "text": "自慰了一次，身体松下来",
        }
    )
    group = build_prompt_block(
        state, now, make_profile(), Settings(group_detail="full"), private=False
    )
    private = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "自慰" not in group
    assert "自慰" in private


def test_loaded_body_adds_irritation_hint() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.satiety = 5.0
    state.hydration = 5.0
    state.sleep_debt = 18.0
    state.bowel = 90.0
    block = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "耐心" in block


def test_private_block_contains_cycle_and_intimate_lines() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    block = build_prompt_block(
        make_state(now), now, make_profile(), Settings(), private=True
    )
    assert "【身体状态｜14:32】" in block
    assert "经期：第 2 天" in block
    assert "性欲" in block
    assert "body_action" in block


def test_profile_without_resolved_gender_has_no_intimate_state() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.libido = 0
    block = build_prompt_block(
        state,
        now,
        make_profile(gender_resolved=False),
        Settings(),
        private=True,
    )
    assert "经期：第 2 天" in block
    assert "性欲" not in block
    assert "梦遗" not in block


def test_group_basic_hides_cycle() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    block = build_prompt_block(
        make_state(now), now, make_profile(), Settings(), private=False
    )
    assert "周期" not in block
    assert "经期：" not in block
    assert "性欲" not in block
    assert "胃：" in block


def test_group_full_shows_cycle_but_not_intimate() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    settings = Settings(group_detail="full")
    block = build_prompt_block(
        make_state(now), now, make_profile(), settings, private=False
    )
    assert "经期：第 2 天" in block
    assert "性欲" not in block
    assert "可以提非性相关" in block
    assert "不必提经期" not in block


def test_group_basic_hides_pregnancy_and_lactation_events() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.events = [
        {
            "at": (now - timedelta(minutes=30)).isoformat(),
            "kind": "conception",
            "text": "怀孕了",
        },
        {
            "at": (now - timedelta(hours=2)).isoformat(),
            "kind": "prenatal",
            "text": "产检：第一次产检，听到了胎心，单胎",
        },
        {
            "at": (now - timedelta(hours=4)).isoformat(),
            "kind": "self_care",
            "text": "喂了奶，胸口松下来",
        },
    ]
    basic = build_prompt_block(state, now, make_profile(), Settings(), private=False)
    assert "怀孕" not in basic
    assert "产检" not in basic
    assert "喂了奶" not in basic

    full = build_prompt_block(
        state, now, make_profile(), Settings(group_detail="full"), private=False
    )
    assert "怀孕" in full
    assert "产检" in full
    assert "喂了奶" not in full

    private = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "喂了奶" in private


def test_private_wetness_line_is_filtered() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.wetness = 78.0
    state.libido = 80.0
    private = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "湿" in private
    group = build_prompt_block(state, now, make_profile(), Settings(), private=False)
    assert "湿" not in group


def test_group_basic_wording_stays_restricted() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    block = build_prompt_block(
        make_state(now), now, make_profile(), Settings(), private=False
    )
    assert "不必提经期" in block


def test_status_card_and_web_payload() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    profile = make_profile()
    text = build_status_text(state, now, profile, Settings(), private=True)
    assert "test · 身体" in text
    assert "经期：" in text
    state.care = [{"t": now.timestamp() - 600, "kind": "drink"}]
    payload = build_web_state(state, now, profile, Settings())
    assert payload["care"][0]["label"] == "喝水"
    assert payload["now_minutes"] == now.hour * 60 + now.minute
    assert payload["gender"] == "female"
    assert payload["cycle"]["phase"] == "menstrual"
    assert payload["cycle"]["estrogen"] > 0
    assert any(detail["key"] == "stomach" for detail in payload["details"])
    assert payload["intimacy"]["count"] == 0
    assert payload["intimacy"]["inseminations"] == 0
    assert "wetness" in payload["intimacy"]
    meters = {meter["key"]: meter for meter in payload["meters"]}
    # Fullness and hydration point the other way from the internal need value.
    assert meters["satiety"]["value"] == pytest.approx(state.satiety, abs=0.1)
    assert meters["satiety"]["name"] == "饱食度"
    assert meters["hydration"]["name"] == "水分"
    assert meters["hydration"]["value"] == pytest.approx(state.hydration, abs=0.1)
    # Pressure meters keep "full is strong".
    assert meters["bladder"]["value"] == pytest.approx(state.bladder, abs=0.1)
    assert meters["libido"]["value"] == pytest.approx(state.libido, abs=0.1)
    assert "饱食度" in text
    assert "不想吃" not in text


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
    assert "性欲" not in text


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


def test_meter_labels_follow_reserve_direction() -> None:
    from core.prompt import clean_label, energy_label, oral_label, satiety_label

    assert satiety_label(92) == "很饱"
    assert satiety_label(8) == "饿得难受"
    assert clean_label(92) == "刚洗过"
    assert clean_label(8) == "浑身难受"
    assert oral_label(92) == "刚刷过"
    assert oral_label(72) == "清爽"
    assert oral_label(45) == "还行"
    assert oral_label(20) == "有点口气"
    assert oral_label(8) == "嘴里发黏"
    assert energy_label(92) == "很有精神"
    assert energy_label(8) == "快撑不住了"


def test_display_meters_is_the_only_conversion_point() -> None:
    from core.prompt import display_meters

    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    meters = {
        meter["key"]: meter
        for meter in display_meters(state, make_profile(), Settings())
    }
    for key in ("satiety", "hydration", "energy", "cleanliness", "oral_health"):
        assert meters[key]["value"] == pytest.approx(getattr(state, key), abs=0.1)
    # Pressure meters keep the internal value.
    assert meters["bladder"]["value"] == pytest.approx(state.bladder, abs=0.1)
    assert meters["libido"]["value"] == pytest.approx(state.libido, abs=0.1)


def test_web_state_omits_hygiene_when_disabled() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    payload = build_web_state(
        make_state(now), now, make_profile(), Settings(enable_hygiene=False)
    )
    keys = {meter["key"] for meter in payload["meters"]}
    assert "cleanliness" not in keys
    assert "oral" not in keys


def test_private_block_includes_mucus_and_organs() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.cycle_anchor = (now.date() - timedelta(days=12)).isoformat()
    state.hydration = 35.0
    state.uterine_congestion = 45.0
    private = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "分泌物：" in private
    assert "宫颈" in private
    assert "器官：" in private
    assert "肾脏" in private
    assert "卵巢" in private
    group = build_prompt_block(
        state, now, make_profile(), Settings(group_detail="full"), private=False
    )
    assert "器官：" not in group
    assert "宫颈" not in group


def test_private_scent_lines_cover_scalp_breath_and_afterglow() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    state = make_state(now)
    state.scalp_scent = 55.0
    state.breath_scent = 55.0
    state.intimate_scent = 55.0
    state.last_self_at = now.timestamp() - 2 * 3600
    block = build_prompt_block(
        state, now, make_profile(age=25), Settings(), private=True
    )
    assert "头发：" in block
    assert "口气：" in block
    assert "事后身上" in block


def test_intimate_scent_follows_the_act_not_the_meter() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    state = make_state(now)
    # A recent act is enough on its own: the meter can be low or zero.
    state.intimate_scent = 0.0
    state.last_intimacy_at = now.timestamp() - 20 * 60
    block = build_prompt_block(
        state, now, make_profile(age=25), Settings(), private=True
    )
    assert "事后身上" in block

    # A wash is what removes it, not a value threshold.
    state.last_shower_at = now.timestamp()
    state.intimate_scent = 0.0
    washed = build_prompt_block(
        state, now, make_profile(age=25), Settings(), private=True
    )
    assert "事后身上" not in washed

    # And it fades out once the act is old enough.
    state.last_shower_at = 0.0
    state.last_intimacy_at = now.timestamp() - 40 * 3600
    stale = build_prompt_block(
        state, now, make_profile(age=25), Settings(), private=True
    )
    assert "事后身上" not in stale


def test_age_never_hides_intimate_prompt_or_web_block() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    state = make_state(now)
    state.libido = 90.0
    state.wetness = 85.0
    state.intimate_scent = 70.0
    state.last_intimacy_at = now.timestamp() - 2 * 3600
    profile = make_profile(age=16)
    block = build_prompt_block(state, now, profile, Settings(), private=True)
    assert "性欲" in block
    assert "事后身上" in block
    payload = build_web_state(state, now, profile, Settings())
    assert payload["can_intimacy"] is True
    assert payload["intimacy"] is not None
    assert any(row["key"] == "intimate" for row in payload["scents"])


def test_web_state_exposes_mucus_organs_and_scent_sources() -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    state = make_state(now)
    state.cycle_anchor = (now.date() - timedelta(days=12)).isoformat()
    state.kidney_load = 42.0
    state.liver_load = 31.0
    state.scalp_scent = 22.0
    state.breath_scent = 18.0
    payload = build_web_state(state, now, make_profile(), Settings())
    cycle = payload["cycle"]
    assert cycle["mucus_texture"] in {
        "dry",
        "sticky",
        "creamy",
        "watery",
        "egg_white",
    }
    assert cycle["cervix_position"] in {"low", "mid", "high"}
    assert "pelvic_pressure" in cycle
    organs = {row["key"]: row for row in payload["organs"]}
    assert set(organs) == {
        "bladder",
        "kidney",
        "liver",
        "uterus",
        "ovaries",
        "rectum",
        "urethra",
        "anus",
    }
    anatomy = payload["anatomy"]
    labels = [row["label"] for row in anatomy["rows"]]
    assert labels == [
        "大阴唇",
        "小阴唇",
        "阴蒂",
        "尿道口",
        "阴道口",
        "会阴",
        "阴道",
        "阴毛",
        "乳晕",
        "乳头",
    ]
    assert all(row["detail"] for row in anatomy["rows"])
    mouth = payload["mouth"]
    assert set(mouth) == {
        "plaque",
        "gum",
        "tongue_body",
        "tongue",
        "saliva",
        "throat",
    }
    assert mouth["saliva"]["level"]
    assert all(row["detail"] for row in mouth.values())
    assert all(row["detail"] for row in payload["organs"])
    assert all(row["detail"] for row in payload["scents"])
    scents = {row["key"]: row for row in payload["scents"]}
    assert {
        "scent",
        "scalp",
        "breath",
        "foot",
        "underarm",
        "vulva",
        "anus",
        "nipple",
        "period",
        "intimate",
    } <= set(scents)
    assert all(row["detail"] for row in payload["scents"])


def test_scent_is_a_separate_meter() -> None:
    from core.prompt import display_meters, scent_label

    now = datetime(2026, 11, 21, 20, 0, tzinfo=TZ)
    profile = make_profile(age=24)
    state = make_state(now)
    state.scent = 62.0
    meters = {item["key"]: item for item in display_meters(state, profile, Settings())}
    assert "scent" in meters
    assert meters["scent"]["level"] == scent_label(62.0)
    assert meters["scent"]["tone"] == "warn"


def test_private_prompt_describes_vulva_vagina_and_the_lower_tract() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    state = make_state(now)
    state.cycle_anchor = (now.date() - timedelta(days=6)).isoformat()
    block = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "外阴：大阴唇" in block
    assert "小阴唇" in block
    assert "阴道：" in block
    assert "会阴" in block
    # Quiet by default: the organ line does not nag about a normal rectum.
    assert "肛门：" not in block


def test_organ_line_reports_a_notable_rectum_urethra_and_anus() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    state = make_state(now)
    state.rectum = 55.0
    state.urethra_irritation = 40.0
    state.anus_irritation = 35.0
    block = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "直肠：" in block
    assert "尿道：" in block
    assert "肛门：" in block


def test_private_prompt_describes_hair_areola_and_mouth_detail() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    state = make_state(now)
    state.plaque = 55.0
    state.gum_irritation = 30.0
    state.tongue_coat = 40.0
    block = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "阴毛：" in block
    assert "乳晕：" in block
    assert "乳头：" in block
    assert "口腔：牙齿" in block
    assert "舌苔" in block


def test_private_scent_lines_cover_the_new_sources() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    state = make_state(now)
    state.underarm_scent = 60.0
    state.vulva_scent = 55.0
    state.anus_scent = 50.0
    state.nipple_scent = 45.0
    block = build_prompt_block(state, now, make_profile(), Settings(), private=True)
    assert "腋下：" in block
    assert "小穴：" in block
    assert "肛门：" in block
    assert "胸前：" in block


def test_anatomy_rows_carry_appearance_detail() -> None:
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    payload = build_web_state(make_state(now), now, make_profile(), Settings())
    rows = {row["key"]: row for row in payload["anatomy"]["rows"]}
    assert set(rows) == {
        "labia_majora",
        "labia_minora",
        "clitoris",
        "urethra_opening",
        "vagina_opening",
        "perineum",
        "vagina",
        "pubic_hair",
        "areola",
        "nipple",
    }
    assert (
        "个体" in rows["labia_majora"]["detail"]
        or "专辑" in rows["labia_majora"]["detail"]
    )
    assert all(row["text"] for row in rows.values())
