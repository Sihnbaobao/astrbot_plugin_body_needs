"""Infer the active persona's body profile from its prompt.

AstrBot personas are free-form text. Most of them state gender and age in the
first sentence, so a small heuristic is enough; the plugin config can always
override the result. Age detection is deliberately conservative: a stated age
below 18 disables every adult-only feature even if the config says otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Settings

_FEMALE_WORDS = (
    ("少女", 3),
    ("女孩", 3),
    ("女生", 3),
    ("女性", 3),
    ("女孩子", 3),
    ("姐姐", 2),
    ("妹妹", 2),
    ("妈妈", 2),
    ("阿姨", 2),
    ("姑娘", 2),
    ("小姐", 2),
    ("她", 0.5),
)
_MALE_WORDS = (
    ("少年", 3),
    ("男孩", 3),
    ("男生", 3),
    ("男性", 3),
    ("男孩子", 3),
    ("哥哥", 2),
    ("弟弟", 2),
    ("爸爸", 2),
    ("叔叔", 2),
    ("先生", 2),
    ("他", 0.5),
)
_AGE_PATTERNS = (
    re.compile(r"(?<!\d)(\d{1,2})\s*(?:岁|歲)"),
    re.compile(r"(?:年龄|年齡|age)\s*[:：=]?\s*(\d{1,2})", re.IGNORECASE),
)
_GENDER_HINTS = (
    (re.compile(r"(?:性别|gender)\s*[:：=]?\s*(?:女|female)", re.IGNORECASE), "female"),
    (re.compile(r"(?:性别|gender)\s*[:：=]?\s*(?:男|male)", re.IGNORECASE), "male"),
)


@dataclass(frozen=True)
class Profile:
    """A resolved body profile for one persona.

    Attributes:
        persona_id: Stable persona identifier used as the state key.
        name: Display name for commands and the plugin page.
        gender: One of female, male, none.
        age: Detected or configured age, or None when unknown.
        adult: Whether adult-only physiology is allowed.
        cycle_length: Cycle length in days from settings.
        period_length: Menstrual days from settings.
    """

    persona_id: str
    name: str
    gender: str
    age: int | None
    adult: bool
    cycle_length: int
    period_length: int


def gender_label(gender: str) -> str:
    """Return a short Chinese label for a gender value."""
    return {"female": "女", "male": "男"}.get(gender, "未知")


def detect_age(text: str) -> int | None:
    """Extract the first plausible age from a persona prompt.

    Args:
        text: The persona system prompt.

    Returns:
        A year value, or None when the prompt does not state one.
    """
    if not text:
        return None
    for pattern in _AGE_PATTERNS:
        for match in pattern.finditer(text):
            value = int(match.group(1))
            if 1 <= value <= 120:
                return value
    return None


def detect_gender(text: str) -> str:
    """Score gendered words in a persona prompt.

    Explicit "gender: female/male" hints outweigh pronouns, so a male persona
    that merely describes a female friend is not misclassified.

    Args:
        text: The persona system prompt.

    Returns:
        female, male, or none when the prompt is ambiguous.
    """
    if not text:
        return "none"
    female = 0.0
    male = 0.0
    for word, weight in _FEMALE_WORDS:
        female += text.count(word) * weight
    for word, weight in _MALE_WORDS:
        male += text.count(word) * weight
    for pattern, gender in _GENDER_HINTS:
        if pattern.search(text):
            if gender == "female":
                female += 8
            else:
                male += 8
    if female == male:
        return "none"
    return "female" if female > male else "male"


def detect_profile(
    persona_id: str,
    name: str,
    prompt: str,
    settings: Settings,
) -> Profile:
    """Resolve the body profile for one persona.

    Args:
        persona_id: Persona identifier used as the state key.
        name: Display name, usually the same as the persona id.
        prompt: The persona system prompt.
        settings: Plugin settings with optional overrides.

    Returns:
        A profile whose adult flag already accounts for the age gate.
    """
    gender = settings.gender
    if gender == "auto":
        gender = detect_gender(prompt)

    age = detect_age(prompt)
    if age is None and settings.age > 0:
        age = settings.age

    # A stated age always wins over the config, so a config value cannot turn a
    # canonically underage persona into an adult one.
    adult = bool(age is not None and age >= 18 and gender in ("female", "male"))

    return Profile(
        persona_id=persona_id,
        name=name or persona_id,
        gender=gender,
        age=age,
        adult=adult,
        cycle_length=settings.cycle_length,
        period_length=settings.period_length,
    )
