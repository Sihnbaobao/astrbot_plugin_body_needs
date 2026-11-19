"""Infer the active persona's body profile from its prompt.

AstrBot personas are free-form text. Most of them state gender and age in the
first sentence, so a small heuristic is enough; the plugin config can always
override the result.

Age is informational only. It never gates a feature: it feeds the age curves of
the male physiology (libido rate and nocturnal-emission odds).
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
_GENDER_HINTS = (
    (re.compile(r"(?:性别|gender)\s*[:：=]?\s*(?:女|female)", re.IGNORECASE), "female"),
    (re.compile(r"(?:性别|gender)\s*[:：=]?\s*(?:男|male)", re.IGNORECASE), "male"),
)
_AGE_PATTERNS = (
    re.compile(r"(?<!\d)(\d{1,5})\s*(?:岁|歲)"),
    re.compile(r"(?:年龄|年齡|age)\s*[:：=]?\s*(\d{1,5})", re.IGNORECASE),
)
# Lifespan of one ordinary human year, used to map fantasy ages onto the human
# curves: an elf of 300 in a 1200-year species is physiologically about 20.
HUMAN_LIFESPAN = 80
# Ages at or below this stay literal: a prompt saying "精灵少女，17岁" means a
# seventeen-year-old, not a toddler of the same species. Only ages that no
# human could have are mapped onto the species lifespan.
PHYSIO_SCALE_ABOVE = 100
_RACE_HINTS = (
    (("神族", "神明", "半神", "deity", "goddess"), "神族", 8000),
    (("龙族", "龙人", "dragon"), "龙族", 5000),
    (("恶魔", "魅魔", "魔女", "demon", "succubus"), "恶魔", 3000),
    (("吸血鬼", "vampire"), "吸血鬼", 2000),
    (("精灵", "elf"), "精灵", 1200),
    (("天使", "angel"), "天使", 1000),
    (("矮人", "dwarf"), "矮人", 250),
)


@dataclass(frozen=True)
class Profile:
    """A resolved body profile for one persona.

    Attributes:
        persona_id: Stable persona identifier used as the state key.
        name: Display name for commands and the plugin page.
        gender: One of female, male, none.
        age: Detected or configured age, or None when unknown. Only used by
            the age curves; it never enables or disables a feature.
        adult: Whether adult-only physiology is allowed.
        cycle_length: Cycle length in days from settings.
        period_length: Menstrual days from settings.
        race: Detected species label, for display only.
        lifespan: Species lifespan in years; the physiology curves are driven
            by the human-equivalent age, not the raw one.
        pcos: Whether the persona is written with polycystic ovary syndrome,
            which makes ovulation rare and cycles long.
        libido_habit: Explicit release tendency for this persona, usually
            judged from the persona prompt; None keeps the stable random
            baseline from the constitution.
    """

    persona_id: str
    name: str
    gender: str
    age: int | None
    adult: bool
    cycle_length: int
    period_length: int
    race: str = "人类"
    lifespan: int = HUMAN_LIFESPAN
    pcos: bool = False
    libido_habit: float | None = None

    @property
    def physio_age(self) -> float | None:
        """Return the human-equivalent age used by every age curve.

        Ages up to PHYSIO_SCALE_ABOVE are taken at face value so a written
        number keeps its meaning; only longer spans are compressed onto the
        species lifespan.
        """
        if self.age is None:
            return None
        if self.age <= PHYSIO_SCALE_ABOVE or self.lifespan <= HUMAN_LIFESPAN:
            return float(self.age)
        return self.age * HUMAN_LIFESPAN / max(1, self.lifespan)


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
            if 1 <= value <= 100000:
                return value
    return None


_PCOS_WORDS = ("多囊", "pcos", "多囊卵巢")


def detect_pcos(text: str) -> bool:
    """Return whether the persona states a polycystic ovary condition."""
    lowered = (text or "").lower()
    return any(word in lowered for word in _PCOS_WORDS)


def detect_race(text: str) -> tuple[str, int]:
    """Guess the species and its lifespan from a persona prompt.

    Args:
        text: The persona system prompt.

    Returns:
        A (label, lifespan) pair; humans when nothing matches.
    """
    lowered = (text or "").lower()
    for words, label, span in _RACE_HINTS:
        if any(word.lower() in lowered for word in words):
            return label, span
    return "人类", HUMAN_LIFESPAN


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
        A resolved profile.
    """
    gender = settings.gender
    if gender == "auto":
        gender = detect_gender(prompt)

    age = detect_age(prompt)
    if age is None and settings.age > 0:
        age = settings.age

    race, lifespan = detect_race(prompt)
    if settings.lifespan > 0:
        race, lifespan = "自定义", settings.lifespan
    pcos = detect_pcos(prompt)

    # Age no longer participates in this decision: any persona whose gender can
    # be resolved has the physiology, and ENABLE_INTIMATE plus the private-chat
    # check decide what actually reaches the model.
    adult = gender in ("female", "male")

    return Profile(
        persona_id=persona_id,
        name=name or persona_id,
        gender=gender,
        age=age,
        adult=adult,
        cycle_length=settings.cycle_length,
        period_length=settings.period_length,
        race=race,
        lifespan=lifespan,
        pcos=pcos,
    )
