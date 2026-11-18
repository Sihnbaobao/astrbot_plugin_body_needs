"""Tests for persona detection and JSON persistence."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.config import Settings
from core.profile import detect_gender, detect_profile
from core.store import Store, new_state

TZ = ZoneInfo("Asia/Shanghai")


def test_detect_gender_from_persona_prompt() -> None:
    assert detect_gender("17岁的无口少女，女同性恋") == "female"
    assert detect_gender("25岁的男性，冷静的医生") == "male"
    assert detect_gender("一个普通的助手") == "none"


def test_detect_profile_does_not_gate_on_stated_age() -> None:
    settings = Settings(age=30)
    profile = detect_profile(
        "kid", "kid", "她今年17岁，是一个女孩子。", settings
    )
    assert profile.gender == "female"
    assert profile.adult is True


def test_detect_profile_uses_config_gender() -> None:
    settings = Settings(gender="male", age=30)
    profile = detect_profile("doc", "doc", "一个冷静的医生。", settings)
    assert profile.gender == "male"
    assert profile.adult is True


def test_store_round_trip(tmp_path) -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    store = Store(tmp_path)
    state = new_state("璃月/测试", now)
    state.hunger = 73.5
    store.save(state)
    loaded = store.load("璃月/测试", now)
    assert loaded.persona_id == "璃月/测试"
    assert loaded.hunger == 73.5
    assert store.path_for("璃月/测试").exists()


def test_store_migrates_old_single_meter_state(tmp_path) -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    store = Store(tmp_path)
    path = store.path_for("old")
    path.write_text(
        json.dumps(
            {
                "persona_id": "old",
                "updated_at": now.timestamp(),
                "hunger": 70,
                "thirst": 60,
                "bladder": 50,
                "cleanliness": 40,
                "oral": 30,
                "fatigue": 20,
                "desire": 0,
            }
        ),
        encoding="utf-8",
    )
    loaded = store.load("old", now)
    assert loaded.stomach == pytest.approx(30)
    assert loaded.glucose == pytest.approx(51)
    assert loaded.hydration == pytest.approx(40)
    assert loaded.urine == pytest.approx(50)
    assert loaded.skin == pytest.approx(40)


def test_store_repairs_corrupt_file(tmp_path) -> None:
    now = datetime(2026, 11, 18, 14, 32, tzinfo=TZ)
    store = Store(tmp_path)
    path = store.path_for("broken")
    path.write_text("{not json", encoding="utf-8")
    loaded = store.load("broken", now)
    assert loaded.persona_id == "broken"
    assert not path.exists()
    assert list(tmp_path.glob("*.corrupt-*"))
