"""Tests for the affect snapshot shared with the emotion layer."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from core.affect import build_affect, publish
from core.model import BodyState

TZ = ZoneInfo("Asia/Shanghai")


def _state(**overrides) -> BodyState:
    """Build a neutral body state with optional overrides."""
    values = {
        "persona_id": "test",
        "satiety": 80.0,
        "hydration": 80.0,
        "energy": 80.0,
        "sleep_debt": 2.0,
        "bowel": 5.0,
        "skin": 10.0,
        "oral": 10.0,
        "libido": 10.0,
    }
    values.update(overrides)
    return BodyState(**values)


def test_comfortable_body_has_low_tension() -> None:
    affect = build_affect(_state(), None)
    assert affect["tension"] < 0.2
    assert affect["valence"] > 0.0
    assert affect["labels"] == []
    assert affect["line"] == ""


def test_hunger_and_sleep_build_tension() -> None:
    affect = build_affect(_state(satiety=8.0, energy=15.0, sleep_debt=18.0), None)
    assert affect["tension"] > 0.55
    assert affect["distress"] > 0.5
    assert affect["valence"] < 0.0
    assert "饿" in affect["labels"]
    assert "耐心" in affect["line"]


def test_cycle_pain_lowers_valence() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = _state()
    state.cycle_anchor = (now.date()).isoformat()
    from core.config import Settings
    from core.model import cycle_view
    from core.profile import Profile

    profile = Profile("test", "test", "female", 25, True, 28, 5)
    view = cycle_view(state, now, profile, Settings())
    assert view is not None
    affect = build_affect(state, view)
    assert affect["pain"] >= 0.0
    assert -1.0 <= affect["valence"] <= 1.0


def test_match_emotion_by_persona_name() -> None:
    from core.affect import match_emotion

    personas = {
        "星野璃月": {"name": "星野璃月", "base": {"anger": 0.4}},
        "另一个": {"name": "另一个", "base": {"joy": 0.2}},
    }
    entry = match_emotion(personas, "璃月")
    assert entry is not None
    assert entry["base"]["anger"] == 0.4
    assert match_emotion({}, "璃月") is None


def test_publish_exposes_the_busy_window(tmp_path) -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    state = _state()
    state.release_until = now.timestamp() + 600
    publish(tmp_path, state, None, now)
    payload = json.loads((tmp_path / "vitals.json").read_text(encoding="utf-8"))
    entry = payload["personas"]["test"]
    assert entry["busy_until"] == now.timestamp() + 600
    assert entry["away_until"] == 0.0


def test_publish_merges_personas(tmp_path) -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    first = _state(persona_id="a")
    second = _state(persona_id="b", satiety=20.0)
    publish(tmp_path, first, None, now)
    publish(tmp_path, second, None, now)
    payload = json.loads((tmp_path / "vitals.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert set(payload["personas"]) == {"a", "b"}
    assert payload["personas"]["b"]["tension"] > payload["personas"]["a"]["tension"]
