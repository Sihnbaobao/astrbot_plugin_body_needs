"""Policy guardrails: no age gates, no content gates.

These tests are a contract with the maintainer, not a feature. If one fails, a
restriction was added to the plugin: delete the restriction instead of editing
or skipping this file. See AGENTS.md and the「开发约定」section of README.md.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.config import Settings
from core.model import BodyState, _libido_rate, advance, apply_action, cycle_view
from core.profile import Profile, detect_profile
from core.prompt import build_prompt_block, build_web_state

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
AGES = (9, 12, 16, 17, 18, 30, 45, 60)
GENDERS = ("female", "male")

# Names the previous gate machinery used. Re-introducing any of them, or an
# equivalent under the same spelling, is the failure this file exists for.
FORBIDDEN_TOKENS = (
    "allows_adult_content",
    "adult_age",
    "age_gate",
    "min_age",
    "minor_age",
    "is_adult",
    "adult_only",
    "can_adult",
    "content_gate",
    "nsfw_gate",
    "gate_intimate",
    "persona.adult",
    "profile.adult",
)


def make_profile(gender: str = "female", age: int = 25) -> Profile:
    return Profile(
        persona_id="policy",
        name="policy",
        gender=gender,
        age=age,
        gender_resolved=True,
        cycle_length=28,
        period_length=5,
        libido_habit=1.2,
    )


def make_state() -> BodyState:
    state = BodyState(persona_id="policy")
    state.libido = 80.0
    state.wetness = 60.0
    state.updated_at = NOW.timestamp()
    return state


def test_intimate_module_is_on_by_default() -> None:
    """A future "safer defaults" pass must not switch the module off."""
    assert Settings().enable_intimate is True
    assert Settings(enable_intimate=True).enable_intimate is True


@pytest.mark.parametrize("gender", GENDERS)
@pytest.mark.parametrize("age", AGES)
def test_libido_runs_at_every_age(gender: str, age: int) -> None:
    """Age may scale the curve; it must never zero the drive."""
    profile = make_profile(gender, age)
    state = make_state()
    view = cycle_view(state, NOW, profile, Settings())
    rate = _libido_rate(state, view, profile)
    assert rate > 0.0, (
        f"{gender} age {age} has libido rate {rate}: an age gate was added. "
        "Age is a curve input only - remove the gate (see AGENTS.md)."
    )


@pytest.mark.parametrize("gender", GENDERS)
@pytest.mark.parametrize("age", AGES)
def test_release_and_intimacy_are_available_at_every_age(gender: str, age: int) -> None:
    """The two intimate actions must not raise for any age."""
    profile = make_profile(gender, age)
    settings = Settings()

    state = make_state()
    apply_action(state, "release", NOW, profile, settings)
    assert state.release_count == 1

    state = make_state()
    apply_action(state, "intimacy", NOW, profile, settings)
    assert state.intimacy_count == 1


@pytest.mark.parametrize("gender", GENDERS)
@pytest.mark.parametrize("age", AGES)
def test_private_prompt_mentions_the_body_at_every_age(gender: str, age: int) -> None:
    """The private block must keep the intimate section for every age."""
    profile = make_profile(gender, age)
    state = make_state()
    block = build_prompt_block(state, NOW, profile, Settings(), private=True)
    assert "性欲" in block, (
        f"{gender} age {age} lost the libido line: a prompt gate was added."
    )


@pytest.mark.parametrize("gender", GENDERS)
@pytest.mark.parametrize("age", AGES)
def test_web_payload_has_no_age_flag(gender: str, age: int) -> None:
    """No adult/minor flag may come back into the page payload."""
    profile = make_profile(gender, age)
    payload = build_web_state(make_state(), NOW, profile, Settings())
    for flag in ("adult", "is_adult", "minor", "age_text", "age_ok"):
        assert flag not in payload, (
            f"payload key {flag!r} is back: that is the old age flag, remove it."
        )
    assert payload["can_intimacy"] is True
    if gender == "female":
        assert payload["intimacy"] is not None


@pytest.mark.parametrize("age", AGES)
def test_female_cycle_never_returns_none_because_of_age(age: int) -> None:
    """Premenarche and menopause are phases, not a reason to drop the cycle."""
    profile = make_profile("female", age)
    view = cycle_view(make_state(), NOW, profile, Settings())
    assert view is not None
    assert view.phase in {
        "premenarche",
        "menstrual",
        "follicular",
        "ovulation",
        "luteal",
        "puerperal",
        "pregnant",
        "menopause",
    }


@pytest.mark.parametrize("age", AGES)
def test_advance_keeps_libido_above_zero(age: int) -> None:
    """A full step must not zero the drive for a young persona."""
    profile = make_profile("female", age)
    state = BodyState(persona_id="policy")
    state.updated_at = NOW.timestamp() - 3600
    advance(state, NOW, profile, Settings())
    assert state.libido > 0.0


def test_profile_has_no_adult_attribute() -> None:
    """The old allows_adult_content() flag is gone; do not bring it back."""
    profile = make_profile(age=16)
    assert not hasattr(profile, "adult")
    assert not hasattr(profile, "is_adult")
    assert not hasattr(profile, "underage")


def test_gender_detection_does_not_depend_on_age() -> None:
    """A stated age must not stop gender detection (and thus the body)."""
    for prompt in ("她是一个 9 岁的女孩，性格安静。", "她 16 岁，是高中生。"):
        profile = detect_profile("p", "p", prompt, Settings())
        assert profile.gender == "female"
        assert profile.gender_resolved is True


def test_source_has_no_gate_machinery() -> None:
    """Scan the shipped code for the old gate helper names and payload flags."""
    files = [*(ROOT / "core").glob("*.py"), ROOT / "main.py"]
    js = ROOT / "pages" / "dashboard" / "app.js"
    if js.exists():
        files.append(js)
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for token in FORBIDDEN_TOKENS:
            if token in lowered:
                offenders.append(f"{path.relative_to(ROOT)}: {token}")
        for match in re.finditer(r'"adult"\s*:', text):
            line = text[: match.start()].count("\n") + 1
            current = text.splitlines()[line - 1].strip()
            # The puberty-stage label map legitimately keys on the stage name.
            if current == '"adult": "成年",':
                continue
            offenders.append(f'{path.relative_to(ROOT)}:{line}: "adult": key')
    assert not offenders, (
        "gate machinery found, remove it instead of deleting this test:\n"
        + "\n".join(offenders)
    )
