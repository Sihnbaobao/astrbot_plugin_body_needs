"""Physiology v2: digestion, hydration, sleep debt, cycle hormones.

The model is still intentionally simpler than a medical simulation, but the
primary meters are now derived from slower internal state instead of being
constant linear ramps:

- stomach fullness and glucose drive satiety
- hydration drives urine production and the hydration meter
- urine volume drives bladder pressure
- sleep debt and circadian alertness drive energy
- skin and oral dirtiness drive cleanliness
- a simplified estrogen/progesterone curve drives cycle symptoms and libido

All time integration is piecewise: 15-minute steps for the first day, hourly
steps afterwards. That keeps a 60-day absence cheap while letting digestion and
circadian changes happen at a human-ish resolution.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import Any

from . import hpo
from .config import Settings
from .profile import Profile

# Digestion / metabolism
STOMACH_DECAY_AWAKE = 0.32
STOMACH_DECAY_SLEEP = 0.18
GLUCOSE_USE_AWAKE = 1.0
GLUCOSE_USE_SLEEP = 0.55
GLUCOSE_RELEASE = 0.35
HYDRATION_USE_AWAKE = 1.1
HYDRATION_USE_SLEEP = 0.55
URINE_BASE_AWAKE = 4.0
URINE_BASE_SLEEP = 2.0
SKIN_AWAKE = 1.1
SKIN_SLEEP = 0.55
SKIN_PERIOD_EXTRA = 0.8
SKIN_PUBERTY_EXTRA = 0.15
PUBERTY_END = 18
# Menarche is roughly normal around 12.5 years; the clamp keeps the tails at
# the real extremes rather than letting the distribution run wild.
MENARCHE_MEAN = 12.5
MENARCHE_SD = 1.1
MENARCHE_MIN = 9.0
MENARCHE_MAX = 16.0
# Natural menopause is roughly normal around 51, and the years before it are
# a transition rather than a switch.
MENOPAUSE_MEAN = 51.0
MENOPAUSE_SD = 4.0
MENOPAUSE_MIN = 40.0
MENOPAUSE_MAX = 61.0
PERIMENOPAUSE_YEARS = 4.0
LUTEAL_DAYS = 14
CONCEPTION_BASE = 0.18
PREGNANCY_DAYS = 280
TWIN_CHANCE = 0.012
TRIPLET_CHANCE = 0.0008
LACTATION_DAYS = 365
MILK_RATE = 4.5
PREGNANCY_CHECKS = (
    (12, "第一次产检，听到了胎心"),
    (20, "大排畸，一切正常"),
    (28, "产检，胎位和发育都正常"),
    (36, "产检，孩子已经准备入盆"),
)
CONTRACEPTION_FACTOR = {"none": 1.0, "condom": 0.02, "pill": 0.01}
PAD_INTERVAL = {3: 2.0, 2: 4.0, 1: 6.0}
PAD_WAKING_HOURS = 16.0
PAD_RESTOCK = 20
ORAL_AWAKE = 1.6
ORAL_SLEEP = 0.8
SLEEP_DEBT_AWAKE = 0.55
SLEEP_DEBT_SLEEP = 1.2
# Bowel load: meals leave residue, the gut passes a little on its own, and
# pressure builds until it is answered. 0-100 like the other internal meters.
GUT_RESIDUE_MEAL = 14.0
GUT_RESIDUE_SNACK = 6.0
GUT_BASE = 0.30
GUT_PASSAGE = 0.35
GUT_URGE_START = 25.0
GUT_URGE_GAIN = 1.5
# A persona only looks after herself once nobody has interacted for a while.
# A long absence still has to leave the cycle in the right place, so the
# simulation window is a year; beyond 60 days it takes coarser steps.
MAX_SIM_DAYS = 365
LONG_GAP_DAYS = 60

# Circadian anchor points. Hour is local wall clock; value is 0-100 alertness.
_ALERT_POINTS = (
    (0.0, 40.0),
    (3.0, 28.0),
    (6.0, 45.0),
    (9.0, 85.0),
    (11.0, 95.0),
    (14.0, 62.0),
    (16.0, 88.0),
    (19.0, 76.0),
    (22.0, 50.0),
    (24.0, 40.0),
)
# Appetite bias around meal times. 1.0 is neutral.
_APPETITE_POINTS = (
    (0.0, 0.80),
    (6.0, 0.90),
    (8.0, 1.16),
    (12.0, 1.20),
    (14.0, 1.00),
    (18.0, 1.16),
    (21.0, 1.00),
    (24.0, 0.80),
)

_WINDOW_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """Clamp a value into its allowed range."""
    return max(low, min(high, float(value)))


def _number(value: Any, default: float = 0.0) -> float:
    """Coerce a stored JSON value to float without raising."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _roll(*parts: str) -> float:
    """Return a stable pseudo-random float in [0, 1) for the given parts."""
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 2**32


@dataclass(frozen=True)
class Constitution:
    """Per-persona physical baseline, drawn once from the persona id.

    Two personas should not share the same stomach, sleep need or appetite for
    release any more than two people do. The values are stable for one persona
    and never depend on age, so they only shape the curves.
    """

    sleep_need: float
    metabolism: float
    stomach_capacity: float
    gut_transit: float
    self_rate: float


def constitution(persona_id: str) -> Constitution:
    """Return the stable physical baseline for one persona.

    Args:
        persona_id: Stable persona identifier used as the seed.

    Returns:
        A constitution with small, believable individual variation.
    """
    return Constitution(
        sleep_need=7.0 + 2.2 * _roll(persona_id, "body", "sleep"),
        metabolism=0.9 + 0.2 * _roll(persona_id, "body", "metabolism"),
        stomach_capacity=0.85 + 0.3 * _roll(persona_id, "body", "stomach"),
        gut_transit=0.75 + 0.5 * _roll(persona_id, "body", "gut"),
        # Log-uniform 0.3-4.8: most personas sit near once a day, a few at
        # either end; stable for the persona like sleep need.
        self_rate=0.3 * (16.0 ** _roll(persona_id, "body", "release")),
    )


def _parse_ts(value: Any) -> float:
    """Parse an ISO timestamp from stored events, returning 0 on failure."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _interp(points: tuple[tuple[float, float], ...], hour: float) -> float:
    """Interpolate a daily curve at a fractional local hour."""
    hour = hour % 24.0
    for (left_hour, left_value), (right_hour, right_value) in zip(points, points[1:]):
        if left_hour <= hour <= right_hour:
            span = right_hour - left_hour
            if span <= 0:
                return right_value
            ratio = (hour - left_hour) / span
            return left_value + (right_value - left_value) * ratio
    return points[-1][1]


def circadian_alertness(moment: datetime) -> float:
    """Return circadian alertness (0-100) for a local datetime."""
    return _clamp(_interp(_ALERT_POINTS, moment.hour + moment.minute / 60.0))


def appetite_level(moment: datetime) -> float:
    """Return the time-of-day appetite bias (about 0.8-1.2)."""
    return _interp(_APPETITE_POINTS, moment.hour + moment.minute / 60.0)


@dataclass
class BodyState:
    """Everything persisted for one persona's body.

    Primary meters are 0-100 and are what the prompt and page show. Internal
    values are slower signals that drive them:

    - satiety is derived from stomach fullness and glucose
    - hydration is stored directly
    - bladder is urine volume
    - cleanliness is skin dirtiness
    - energy is derived from sleep debt and circadian alertness
    - libido is a tension meter that only matters while ENABLE_INTIMATE is on
    """

    persona_id: str
    updated_at: float = 0.0
    last_active_at: float = 0.0
    satiety: float = 80.0
    hydration: float = 80.0
    bladder: float = 18.0
    cleanliness: float = 82.0
    oral_health: float = 80.0
    energy: float = 78.0
    libido: float = 10.0
    release_count: int = 0
    stomach: float = 72.0
    glucose: float = 78.0
    urine: float = 18.0
    skin: float = 18.0
    oral: float = 20.0
    gut: float = 8.0
    bowel: float = 0.0
    sleep_debt: float = 2.0
    cycle_anchor: str = ""
    cycle_length: int = 28
    period_length: int = 5
    cycle_phase: str = ""
    cycle_start: str = ""
    cycle_day: int = 0
    estrogen: float = 0.0
    progesterone: float = 0.0
    lh: float = 0.0
    fsh: float = 0.0
    follicle: float = 0.0
    ovulation_day: int = 0
    anovulatory: bool = False
    pregnant: bool = False
    conceived_at: float = 0.0
    fetuses: int = 0
    prenatal_week: int = 0
    birth_at: float = 0.0
    lactating: bool = False
    milk: float = 0.0
    last_express_at: float = 0.0
    pads: int = 12
    pad_hours: float = 0.0
    last_pad_day: str = ""
    last_supply_at: float = 0.0
    last_eat_at: float = 0.0
    last_drink_at: float = 0.0
    last_shower_at: float = 0.0
    last_brush_at: float = 0.0
    last_toilet_at: float = 0.0
    last_bowel_at: float = 0.0
    last_nap_at: float = 0.0
    last_release_at: float = 0.0
    last_self_at: float = 0.0
    intimacy_count: int = 0
    insemination_count: int = 0
    last_intimacy_at: float = 0.0
    last_insemination_at: float = 0.0
    wetness: float = 0.0
    away_until: float = 0.0
    away_reason: str = ""
    refractory_until: float = 0.0
    last_meal_kind: str = ""
    last_meal_size: float = 0.0
    last_drink_ml: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)
    care: list[dict[str, Any]] = field(default_factory=list)
    flags: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain JSON-compatible data."""
        return {
            "persona_id": self.persona_id,
            "updated_at": self.updated_at,
            "last_active_at": self.last_active_at,
            "satiety": self.satiety,
            "hydration": self.hydration,
            "bladder": self.bladder,
            "cleanliness": self.cleanliness,
            "oral_health": self.oral_health,
            "energy": self.energy,
            "libido": self.libido,
            "release_count": self.release_count,
            "oral": self.oral,
            "stomach": self.stomach,
            "glucose": self.glucose,
            "urine": self.urine,
            "skin": self.skin,
            "gut": self.gut,
            "bowel": self.bowel,
            "sleep_debt": self.sleep_debt,
            "cycle_anchor": self.cycle_anchor,
            "cycle_length": self.cycle_length,
            "period_length": self.period_length,
            "cycle_phase": self.cycle_phase,
            "cycle_start": self.cycle_start,
            "cycle_day": self.cycle_day,
            "estrogen": self.estrogen,
            "progesterone": self.progesterone,
            "lh": self.lh,
            "fsh": self.fsh,
            "follicle": self.follicle,
            "ovulation_day": self.ovulation_day,
            "anovulatory": self.anovulatory,
            "pregnant": self.pregnant,
            "conceived_at": self.conceived_at,
            "fetuses": self.fetuses,
            "prenatal_week": self.prenatal_week,
            "birth_at": self.birth_at,
            "lactating": self.lactating,
            "milk": self.milk,
            "last_express_at": self.last_express_at,
            "pads": self.pads,
            "pad_hours": self.pad_hours,
            "last_pad_day": self.last_pad_day,
            "last_supply_at": self.last_supply_at,
            "last_eat_at": self.last_eat_at,
            "last_drink_at": self.last_drink_at,
            "last_shower_at": self.last_shower_at,
            "last_brush_at": self.last_brush_at,
            "last_toilet_at": self.last_toilet_at,
            "last_bowel_at": self.last_bowel_at,
            "last_nap_at": self.last_nap_at,
            "last_release_at": self.last_release_at,
            "last_self_at": self.last_self_at,
            "intimacy_count": self.intimacy_count,
            "insemination_count": self.insemination_count,
            "last_intimacy_at": self.last_intimacy_at,
            "last_insemination_at": self.last_insemination_at,
            "wetness": self.wetness,
            "away_until": self.away_until,
            "away_reason": self.away_reason,
            "refractory_until": self.refractory_until,
            "last_meal_kind": self.last_meal_kind,
            "last_meal_size": self.last_meal_size,
            "last_drink_ml": self.last_drink_ml,
            "events": list(self.events),
            "care": list(self.care),
            "flags": dict(self.flags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BodyState:
        """Rebuild a state from JSON, migrating old single-meter files."""
        state = cls(persona_id=str(data.get("persona_id") or ""))
        state.updated_at = _number(data.get("updated_at"))
        state.last_active_at = _number(data.get("last_active_at"))
        has_internal = "stomach" in data
        legacy = "satiety" not in data
        for key in (
            "satiety",
            "hydration",
            "bladder",
            "cleanliness",
            "oral_health",
            "energy",
            "libido",
            "stomach",
            "glucose",
            "urine",
            "skin",
            "oral",
            "gut",
            "bowel",
            "sleep_debt",
            "last_eat_at",
            "last_drink_at",
            "last_shower_at",
            "last_brush_at",
            "last_toilet_at",
            "last_bowel_at",
            "last_nap_at",
            "last_release_at",
            "last_self_at",
            "refractory_until",
            "last_meal_size",
            "last_drink_ml",
        ):
            if key in data:
                setattr(state, key, _number(data.get(key), getattr(state, key)))
        if "libido" not in data and "desire" in data:
            # 0.8.1 and older stored the same meter under the broader name.
            state.libido = _number(data.get("desire"), 10.0)
        state.release_count = max(0, int(_number(data.get("release_count"), 0)))

        if legacy:
            # Old files stored needs: hunger, thirst, fatigue and dirt. Convert
            # them once, so an existing persona keeps the same body.
            if "hunger" in data:
                state.satiety = 100.0 - _number(data.get("hunger"), 20.0)
            if "fatigue" in data:
                state.energy = 100.0 - _number(data.get("fatigue"), 22.0)
            if "cleanliness" in data:
                state.cleanliness = 100.0 - _number(data.get("cleanliness"), 18.0)
            if "oral" in data:
                state.oral_health = 100.0 - _number(data.get("oral"), 20.0)

        for key in (
            "satiety",
            "hydration",
            "bladder",
            "cleanliness",
            "oral_health",
            "energy",
            "libido",
            "stomach",
            "glucose",
            "urine",
            "skin",
            "oral",
            "gut",
        ):
            setattr(state, key, _clamp(getattr(state, key)))
        state.bowel = _clamp(state.bowel)
        state.sleep_debt = _clamp(state.sleep_debt, 0.0, 24.0)

        if not has_internal:
            # Old files only had the seven displayed meters. Reconstruct a
            # plausible digestive/hydration/sleep baseline from them and from
            # the last meal/drink timestamps when those exist.
            reference = state.updated_at or 0.0
            if state.last_eat_at and reference:
                meal_hours = max(0.0, (reference - state.last_eat_at) / 3600.0)
                state.stomach = _clamp(20.0 + 55.0 * math.exp(-0.32 * meal_hours))
                state.glucose = _clamp(50.0 + 35.0 * math.exp(-0.18 * meal_hours))
            else:
                state.stomach = _clamp(state.satiety)
                state.glucose = _clamp(30.0 + state.satiety * 0.7)
            if state.last_drink_at and reference:
                drink_hours = max(0.0, (reference - state.last_drink_at) / 3600.0)
                state.hydration = _clamp(40.0 + 60.0 * math.exp(-0.12 * drink_hours))
            elif "thirst" in data:
                state.hydration = _clamp(100.0 - _number(data.get("thirst"), 20.0))
            state.urine = _clamp(state.bladder)
            state.skin = _clamp(100.0 - state.cleanliness)
            state.oral = _clamp(100.0 - state.oral_health)
            state.sleep_debt = _clamp((100.0 - state.energy) / 4.0, 0.0, 24.0)
            state.last_meal_kind = "meal"
            state.last_meal_size = 45.0

        state.cycle_anchor = str(data.get("cycle_anchor") or "")
        state.cycle_length = int(_number(data.get("cycle_length"), 28))
        state.period_length = int(_number(data.get("period_length"), 5))
        state.cycle_phase = str(data.get("cycle_phase") or "")
        state.cycle_start = str(data.get("cycle_start") or "")
        state.cycle_day = max(0, int(_number(data.get("cycle_day"), 0)))
        state.estrogen = _clamp(_number(data.get("estrogen"), 0.0))
        state.progesterone = _clamp(_number(data.get("progesterone"), 0.0))
        state.lh = _clamp(_number(data.get("lh"), 0.0))
        state.fsh = _clamp(_number(data.get("fsh"), 0.0))
        state.follicle = _clamp(_number(data.get("follicle"), 0.0), 0.0, 1.0)
        state.ovulation_day = max(0, int(_number(data.get("ovulation_day"), 0)))
        state.anovulatory = bool(data.get("anovulatory"))
        state.pregnant = bool(data.get("pregnant"))
        state.conceived_at = _number(data.get("conceived_at"), 0.0)
        state.fetuses = max(0, min(3, int(_number(data.get("fetuses"), 0))))
        state.prenatal_week = max(0, int(_number(data.get("prenatal_week"), 0)))
        state.birth_at = _number(data.get("birth_at"), 0.0)
        state.lactating = bool(data.get("lactating"))
        state.milk = _clamp(_number(data.get("milk"), 0.0))
        state.last_express_at = _number(data.get("last_express_at"), 0.0)
        state.pads = max(0, int(_number(data.get("pads"), 12)))
        state.intimacy_count = max(0, int(_number(data.get("intimacy_count"), 0)))
        state.insemination_count = max(
            0, int(_number(data.get("insemination_count"), 0))
        )
        state.last_intimacy_at = _number(data.get("last_intimacy_at"), 0.0)
        state.last_insemination_at = _number(data.get("last_insemination_at"), 0.0)
        state.wetness = _clamp(_number(data.get("wetness"), 0.0))
        state.away_until = _number(data.get("away_until"), 0.0)
        state.away_reason = str(data.get("away_reason") or "")
        state.pad_hours = max(0.0, _number(data.get("pad_hours"), 0.0))
        state.last_pad_day = str(data.get("last_pad_day") or "")
        state.last_supply_at = _number(data.get("last_supply_at"), 0.0)
        state.last_meal_kind = str(data.get("last_meal_kind") or "")
        events = data.get("events")
        if isinstance(events, list):
            state.events = [
                item for item in events if isinstance(item, dict) and item.get("text")
            ][:100]
        care = data.get("care")
        if isinstance(care, list):
            state.care = [
                {"t": _number(item.get("t")), "kind": str(item.get("kind"))}
                for item in care
                if isinstance(item, dict) and item.get("kind")
            ][-120:]
        # Seed any action the timeline is missing, so old files and files from
        # before a new care kind was added still show their last occurrence.
        for kind, attr in (
            ("meal", "last_eat_at"),
            ("drink", "last_drink_at"),
            ("toilet", "last_toilet_at"),
            ("shower", "last_shower_at"),
            ("brush", "last_brush_at"),
        ):
            stamp = getattr(state, attr)
            if not stamp:
                continue
            if any(
                item["kind"] == kind and abs(item["t"] - stamp) < 120
                for item in state.care
            ):
                continue
            state.care.append({"t": stamp, "kind": kind})
        state.care.sort(key=lambda item: item["t"])
        flags = data.get("flags")
        if isinstance(flags, dict):
            state.flags = {
                str(key): _number(value)
                for key, value in flags.items()
                if isinstance(key, str)
            }
        return state


@dataclass(frozen=True)
class CycleView:
    """Derived view of one persona's current menstrual position."""

    cycle_start: date
    day: int
    phase: str
    flow: int
    cramps: int
    mood: int
    appetite: int
    days_to_next: int
    note: str
    estrogen: float
    progesterone: float
    lh: float
    puberty: bool = False
    cycle_length: int = 28
    anovulatory: bool = False
    fertile: bool = False
    weeks: int = 0
    breast: int = 0
    headache: int = 0
    backache: int = 0
    discharge: int = 0
    bbt: float = 36.3


@dataclass(frozen=True)
class ActionResult:
    """Outcome of a deliberate body action."""

    action: str
    label: str
    detail: str
    before: dict[str, float]
    after: dict[str, float]


ACTION_LABELS = {
    "eat": "吃了一顿饭",
    "snack": "吃了点零食",
    "drink": "喝了水",
    "shower": "洗了澡",
    "brush": "刷了牙",
    "toilet": "去了厕所",
    "nap": "小睡了一会儿",
    "supplies": "补了卫生用品",
    "intimacy": "做完爱",
    "release": "自己解决了一次",
    "prenatal": "去做了产检",
    "express": "喂了奶",
}
METER_TEXT = {
    "satiety": "饱食度",
    "hydration": "水分",
    "bladder": "膀胱压力",
    "cleanliness": "清洁",
    "oral_health": "口腔",
    "energy": "精力",
    "libido": "性欲",
}


def parse_sleep_window(text: str) -> tuple[time, time] | None:
    """Parse a sleep window such as 00:00-08:00."""
    match = _WINDOW_RE.fullmatch((text or "").strip())
    if not match:
        return None
    start_hour, start_minute, end_hour, end_minute = map(int, match.groups())
    if start_hour > 24 or end_hour > 24:
        return None
    if start_minute > 59 or end_minute > 59:
        return None
    return time(start_hour % 24, start_minute), time(end_hour % 24, end_minute)


def sleep_seconds_between(
    start: datetime,
    end: datetime,
    window: tuple[time, time],
) -> float:
    """Count seconds inside the daily sleep window between two instants."""
    if end <= start:
        return 0.0
    if (end - start).days > 60:
        start = end - timedelta(days=60)
    start_time, end_time = window
    total = 0.0
    current = start.date() - timedelta(days=1)
    last = end.date()
    while current <= last:
        begin = datetime.combine(current, start_time, tzinfo=start.tzinfo)
        finish = datetime.combine(current, end_time, tzinfo=start.tzinfo)
        if end_time <= start_time:
            finish += timedelta(days=1)
        overlap_start = max(start, begin)
        overlap_end = min(end, finish)
        if overlap_end > overlap_start:
            total += (overlap_end - overlap_start).total_seconds()
        current += timedelta(days=1)
    return min(total, (end - start).total_seconds())


def _is_pubertal(age: float | None) -> bool:
    """Return whether a human-equivalent age sits inside the puberty band."""
    return age is not None and age < PUBERTY_END


def _normal_draw(*parts: str) -> float:
    """Return a stable standard-normal deviate for the given parts."""
    u1 = max(1e-9, _roll(*parts, "u1"))
    u2 = _roll(*parts, "u2")
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _menarche_age(persona_id: str) -> float:
    """Return the age of first menstruation for one persona.

    Drawn from a normal distribution around 12.5 with a standard deviation of
    about one year, so most personas start between 11 and 14 while a few start
    at ten or even earlier, which is a normal spread rather than an exception.
    """
    value = MENARCHE_MEAN + _normal_draw(persona_id, "menarche") * MENARCHE_SD
    return min(MENARCHE_MAX, max(MENARCHE_MIN, value))


def _menopause_age(persona_id: str) -> float:
    """Return the age at which one persona reaches menopause.

    Normal around 51 with a standard deviation of four years, so a persona can
    stop anywhere between the early forties and the late fifties.
    """
    value = MENOPAUSE_MEAN + _normal_draw(persona_id, "menopause") * MENOPAUSE_SD
    return min(MENOPAUSE_MAX, max(MENOPAUSE_MIN, value))


def _is_perimenopausal(age: float | None, menopause_at: float) -> bool:
    """Return whether an age sits in the transition before menopause."""
    if age is None:
        return False
    return menopause_at - PERIMENOPAUSE_YEARS <= age < menopause_at


def _anovulatory_chance(
    age: float | None,
    pcos: bool = False,
    perimenopausal: bool = False,
) -> float:
    """Return how often a cycle skips ovulation."""
    if pcos:
        return 0.85
    if age is None:
        return 0.08
    if age < 16:
        return 0.55
    if age < 20:
        return 0.25
    if perimenopausal:
        return 0.35
    if age < 45:
        return 0.08
    return 0.35


def _cycle_length_for(
    base: int,
    index: int,
    persona_id: str,
    span: float,
    period: int = 5,
    stress: float = 1.0,
) -> int:
    """Return the length of one cycle, jittered while the body is still growing.

    Adult cycles are treated as steady. During puberty the gap between periods
    is visibly irregular, so each cycle drifts around the configured length.

    Args:
        base: Configured cycle length in days.
        index: Zero-based cycle number counted from the anchor.
        persona_id: Seed, so one persona keeps a stable sequence.
        span: Half-width of the drift in days; zero keeps every cycle equal.
        period: Bleeding days at the start of the cycle.
        stress: Follicular stretch factor; illness or sleep debt delays
            ovulation and therefore lengthens the whole cycle.

    Returns:
        A cycle length in days.
    """
    length = max(1, int(base))
    if span <= 0 and stress <= 1.0:
        return length
    follicular = max(6.0, float(length - period - LUTEAL_DAYS)) * max(1.0, stress)
    if span > 0:
        follicular += (_roll(persona_id, "cycle-length", str(index)) * 2.0 - 1.0) * span
    total = period + follicular + LUTEAL_DAYS
    return max(18, min(120, int(round(total))))


def cycle_position(
    anchor: date,
    today: date,
    cycle_length: int,
    *,
    persona_id: str = "",
    jitter: float = 0.0,
    period: int = 5,
    stress: float = 1.0,
) -> tuple[int, date, int]:
    """Return the cycle day, the current cycle's start and its length.

    Args:
        anchor: Day one of a known cycle.
        today: The date to place.
        cycle_length: Configured length in days.
        persona_id: Seed used when lengths are jittered.
        jitter: Half-width of the per-cycle drift in days.
        period: Bleeding days at the start of the cycle.
        stress: Follicular stretch factor.

    Returns:
        A (day, start, length) tuple, with a 1-based day.
    """
    length = max(1, int(cycle_length))
    if (jitter <= 0 and stress <= 1.0) or today < anchor:
        offset = max(0, (today - anchor).days)
        index = offset // length
        start = anchor + timedelta(days=index * length)
        return (today - start).days + 1, start, length

    cursor = anchor
    for index in range(600):
        span = _cycle_length_for(length, index, persona_id, jitter, period, stress)
        following = cursor + timedelta(days=span)
        if today < following:
            return (today - cursor).days + 1, cursor, span
        cursor = following
    return 1, cursor, length


def _ensure_anchor(
    state: BodyState,
    now: datetime,
    profile: Profile,
    configured_start: str = "",
) -> date:
    """Return the cycle anchor, seeding a sensible one on first use."""
    anchor: date | None = None
    if state.cycle_anchor:
        try:
            anchor = date.fromisoformat(state.cycle_anchor)
        except ValueError:
            anchor = None
    if anchor is None and configured_start:
        try:
            anchor = date.fromisoformat(configured_start)
        except ValueError:
            anchor = None
        if anchor is not None:
            state.cycle_anchor = anchor.isoformat()
    if anchor is None:
        length = max(1, profile.cycle_length)
        period = max(1, min(profile.period_length, length - 1))
        span = max(1, length - period)
        offset = period + int(_roll(profile.persona_id, "cycle-anchor") * span)
        anchor = now.date() - timedelta(days=min(offset, length + 30))
        state.cycle_anchor = anchor.isoformat()
    if anchor > now.date():
        anchor = now.date() - timedelta(days=max(1, profile.period_length))
        state.cycle_anchor = anchor.isoformat()
    return anchor


def _cycle_span(
    profile: Profile,
    pubertal: bool,
    perimenopausal: bool = False,
) -> float:
    """Return the per-cycle drift half-width for this persona."""
    span = 5.0 if pubertal else 0.0
    if perimenopausal:
        span = max(span, 9.0)
    if profile.pcos:
        span = max(span, 12.0)
    return span


def _mood_effect(mood: dict[str, Any] | None) -> tuple[float, float, float]:
    """Turn an emotion snapshot into body effects.

    Args:
        mood: Emotion values from the persona plugin, or None.

    Returns:
        An (appetite pressure delta, libido factor, follicular stress) tuple.
    """
    if not mood:
        return 0.0, 1.0, 0.0

    def read(key: str) -> float:
        try:
            return max(0.0, min(1.0, float(mood.get(key, 0.0))))
        except (TypeError, ValueError):
            return 0.0

    sadness = read("sadness")
    anger = read("anger")
    fear = read("fear")
    joy = read("joy")
    appetite = 10.0 * sadness + 5.0 * anger - 4.0 * joy
    libido = max(0.3, min(1.2, 1.0 - 0.45 * sadness - 0.2 * anger + 0.1 * joy))
    stress = min(0.2, 0.12 * fear + 0.04 * anger)
    return appetite, libido, stress


def _cycle_stress(state: BodyState, mood: dict[str, Any] | None = None) -> float:
    """Return the follicular stretch factor; sleep debt and stress delay it."""
    stress = 1.0 + min(0.35, max(0.0, state.sleep_debt - 6.0) / 24.0 * 0.6)
    return stress * (1.0 + _mood_effect(mood)[2])


def _period_days(profile: Profile) -> int:
    """Return the bleeding length in days, clamped below the cycle."""
    length = max(1, int(profile.cycle_length))
    return max(1, min(profile.period_length, length - 1))


def _restart_cycle(state: BodyState, now: datetime, profile: Profile) -> None:
    """Begin a new cycle today and roll whether it will ovulate."""
    state.cycle_start = now.date().isoformat()
    state.cycle_day = 1
    state.ovulation_day = 0
    state.estrogen = 12.0
    state.progesterone = 5.0
    state.lh = 10.0
    state.fsh = 34.0
    state.follicle = 0.0
    age = profile.physio_age
    state.anovulatory = _roll(
        profile.persona_id, now.date().isoformat(), "ovulation"
    ) < _anovulatory_chance(
        age, profile.pcos, _is_perimenopausal(age, _menopause_age(profile.persona_id))
    )


def _seed_cycle(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
    mood: dict[str, Any] | None = None,
) -> None:
    """Fill the hormone state from the calendar model on first use.

    Later calls keep the integrated values, so a persona's cycle stops
    following the calendar once the simulation has stepped past the seed.
    """
    if state.cycle_start and state.estrogen > 0.0:
        return
    age = profile.physio_age
    pubertal = _is_pubertal(age)
    span = _cycle_span(
        profile, pubertal, _is_perimenopausal(age, _menopause_age(profile.persona_id))
    )
    stress = _cycle_stress(state, mood)
    anchor = _ensure_anchor(state, now, profile, settings.cycle_start)
    day, start, length = cycle_position(
        anchor,
        now.date(),
        profile.cycle_length,
        persona_id=profile.persona_id,
        jitter=span,
        period=profile.period_length,
        stress=stress,
    )
    length = max(1, length)
    period = max(1, min(profile.period_length, length - 1))
    state.cycle_start = start.isoformat()
    state.cycle_day = day
    state.anovulatory = _roll(
        profile.persona_id, start.isoformat(), "ovulation"
    ) < _anovulatory_chance(
        age, profile.pcos, _is_perimenopausal(age, _menopause_age(profile.persona_id))
    )
    axis, ovulated_day = hpo.seed(day, length, period, state.anovulatory)
    state.estrogen = axis.estrogen
    state.progesterone = axis.progesterone
    state.lh = axis.lh
    state.fsh = axis.fsh
    state.follicle = axis.follicle
    state.ovulation_day = ovulated_day if not state.anovulatory else 0


def _step_cycle(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
    hours: float,
    mood: dict[str, Any] | None = None,
) -> None:
    """Advance the HPO axis by one chunk of time.

    FSH grows the follicle, estradiol feeds back on FSH and drives the LH
    surge, and the corpus luteum then runs progesterone down until the next
    period starts.
    """
    if profile.gender != "female" or not settings.enable_cycle or state.pregnant:
        return
    if not state.cycle_start:
        return
    age = profile.physio_age
    if age is not None:
        if age < _menarche_age(profile.persona_id):
            return
        if age >= _menopause_age(profile.persona_id):
            return
    try:
        cycle_start = date.fromisoformat(state.cycle_start)
    except ValueError:
        return
    day = max(1, (now.date() - cycle_start).days + 1)
    period = _period_days(profile)
    pubertal = _is_pubertal(age)
    stress = _cycle_stress(state, mood)
    target = _cycle_length_for(
        profile.cycle_length,
        0,
        profile.persona_id,
        _cycle_span(
            profile,
            pubertal,
            _is_perimenopausal(age, _menopause_age(profile.persona_id)),
        ),
        period,
        stress,
    )
    state.cycle_day = day
    expected = max(period + 1, target - LUTEAL_DAYS)
    axis = hpo.Axis(
        estrogen=state.estrogen,
        progesterone=state.progesterone,
        lh=state.lh,
        fsh=state.fsh,
        follicle=state.follicle,
    )
    axis, ovulated = hpo.step(
        axis,
        hours,
        day=day,
        ovulation_day=state.ovulation_day,
        length=expected,
        anovulatory=state.anovulatory,
        stress=stress,
    )
    state.estrogen = _clamp(axis.estrogen)
    state.progesterone = _clamp(axis.progesterone)
    state.lh = _clamp(axis.lh)
    state.fsh = _clamp(axis.fsh)
    state.follicle = max(0.0, min(1.0, axis.follicle))
    if ovulated and state.ovulation_day == 0:
        state.ovulation_day = day
    if state.ovulation_day and day - state.ovulation_day >= LUTEAL_DAYS:
        _restart_cycle(state, now, profile)
    elif state.anovulatory and day > target:
        _restart_cycle(state, now, profile)


def cycle_view(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
    mood: dict[str, Any] | None = None,
) -> CycleView | None:
    """Compute the current menstrual phase, hormones and symptoms."""
    if not settings.enable_cycle or profile.gender != "female":
        return None

    if state.pregnant:
        weeks = max(0, int((now.timestamp() - state.conceived_at) // (7 * 86400)))
        count = ""
        if weeks >= 12 and state.fetuses == 2:
            count = "，双胎"
        elif weeks >= 12 and state.fetuses >= 3:
            count = "，三胎"
        return CycleView(
            cycle_start=now.date(),
            day=0,
            phase="pregnant",
            flow=0,
            cramps=0,
            mood=0,
            appetite=0,
            days_to_next=0,
            note=f"怀孕第 {weeks} 周{count}",
            estrogen=75.0,
            progesterone=80.0,
            lh=8.0,
            weeks=weeks,
            cycle_length=max(21, profile.cycle_length),
        )

    age = profile.physio_age
    pubertal = _is_pubertal(age)
    if age is None:
        # An unknown age is treated as adult: no puberty, no menopause.
        pass
    elif age < _menarche_age(profile.persona_id):
        return CycleView(
            cycle_start=now.date(),
            day=0,
            phase="premenarche",
            flow=0,
            cramps=0,
            mood=0,
            appetite=0,
            days_to_next=0,
            note="还没来初潮，身体还在长",
            estrogen=8.0,
            progesterone=3.0,
            lh=6.0,
            puberty=True,
            cycle_length=max(21, profile.cycle_length),
            discharge=1,
        )
    elif age >= _menopause_age(profile.persona_id):
        return CycleView(
            cycle_start=now.date(),
            day=0,
            phase="menopause",
            flow=0,
            cramps=0,
            mood=0,
            appetite=0,
            days_to_next=0,
            note="已经绝经，周期停了",
            estrogen=12.0,
            progesterone=3.0,
            lh=35.0,
            cycle_length=max(21, profile.cycle_length),
            discharge=0,
        )

    perimenopausal = _is_perimenopausal(age, _menopause_age(profile.persona_id))
    _seed_cycle(state, now, profile, settings, mood)
    try:
        start = date.fromisoformat(state.cycle_start)
    except ValueError:
        start = now.date()
    day = max(1, (now.date() - start).days + 1)
    period = _period_days(profile)
    stress = _cycle_stress(state, mood)
    target = _cycle_length_for(
        profile.cycle_length,
        0,
        profile.persona_id,
        _cycle_span(profile, pubertal, perimenopausal),
        period,
        stress,
    )
    # Before ovulation the next period is only a projection; afterwards it is
    # pinned to a fixed luteal tail.
    expected = state.ovulation_day or max(period + 1, target - LUTEAL_DAYS)
    if state.ovulation_day:
        length = max(day + 1, state.ovulation_day + LUTEAL_DAYS)
    else:
        length = max(day + 1, target)
    anovulatory = state.anovulatory
    ovulation_day = expected
    if day <= period:
        phase = "menstrual"
    elif state.ovulation_day and abs(day - state.ovulation_day) <= 1:
        phase = "ovulation"
    elif state.ovulation_day and day > state.ovulation_day + 1:
        phase = "luteal"
    elif anovulatory and day > expected + 1:
        phase = "luteal"
    else:
        phase = "follicular"

    estrogen = state.estrogen
    progesterone = state.progesterone
    lh = state.lh
    if profile.pcos:
        # Chronically elevated LH is the usual endocrine signature.
        lh = min(100.0, lh * 1.8)
    rng = random.Random(int(_roll(profile.persona_id, start.isoformat()) * 1_000_000))
    flow = 0
    cramps = 0
    mood = 0
    appetite = 0
    breast = 0
    headache = 0
    backache = 0
    note = ""
    if phase == "menstrual":
        flow = 3 if day <= 2 else (2 if day <= 4 else 1)
        cramps = min(3, 1 + (1 if day <= 2 else 0) + rng.randint(0, 1))
        if pubertal and rng.random() < 0.35:
            cramps = min(3, cramps + 1)
        backache = rng.choice([0, 1, 2]) if cramps >= 2 else rng.randint(0, 1)
        appetite = rng.choice([-1, 0, 0, 1])
        note = "小腹坠胀，腰有点酸" if cramps >= 2 else "小腹有点坠"
    elif phase == "luteal":
        days_left = max(1, length - day + 1)
        premenstrual = days_left <= 7
        cramps = rng.choice([0, 0, 1]) if days_left <= 4 else 0
        breast = rng.choice([0, 1, 1, 2]) if premenstrual else 0
        headache = rng.choice([0, 0, 1, 2]) if days_left <= 5 else 0
        backache = rng.choice([0, 1, 2]) if days_left <= 4 else 0
        if pubertal:
            mood = -1
        appetite = 1
        if days_left <= 6 and pubertal:
            note = "经期快到了，情绪起伏比平时大，胃口也大一点"
        elif days_left <= 6:
            note = "经期快到了，容易烦躁，胃口也大一点"
        elif perimenopausal:
            note = "围绝经期，周期开始不太规律，偶尔潮热"
        elif anovulatory:
            note = "这个周期没有排卵，身体还是在照常准备"
        else:
            note = "排卵后，身体开始为经期做准备"
    elif phase == "ovulation":
        breast = 1
        note = "精力比前几天好"
    else:
        note = "经期后，身体轻快"

    # Mood is a continuous function of the hormones rather than a per-phase
    # label: estradiol lifts it, a high luteal progesterone drags it down, and
    # the first bleeding days add their own dip.
    mood = int(
        round(
            1.2 * (estrogen - 55.0) / 45.0
            - 1.7 * max(0.0, (progesterone - 45.0) / 30.0)
            + (-0.6 if phase == "menstrual" else 0.0)
            + (-0.5 if pubertal and phase == "luteal" else 0.0)
        )
    )
    mood = max(-3, min(3, mood))
    # Sperm survive about five days and the egg one, so the fertile window is
    # the five days before ovulation plus the day itself. It is anchored to
    # ovulation, not to the period, so on a short cycle it overlaps the
    # bleeding days and those are fertile too.
    fertile = not anovulatory and (ovulation_day - 5) <= day <= (ovulation_day + 1)
    axis = hpo.Axis(estrogen=estrogen, progesterone=progesterone, lh=lh)
    discharge = hpo.mucus_score(axis)
    if phase == "menstrual":
        # Blood masks the mucus during the period, so keep the old high value.
        discharge = 3
    bbt = hpo.basal_temperature(axis)

    return CycleView(
        cycle_start=start,
        day=day,
        phase=phase,
        flow=flow,
        cramps=cramps,
        mood=mood,
        appetite=appetite,
        days_to_next=max(1, length - day + 1),
        note=note,
        estrogen=estrogen,
        progesterone=progesterone,
        lh=lh,
        puberty=pubertal,
        cycle_length=length,
        anovulatory=anovulatory,
        fertile=fertile,
        breast=breast,
        headache=headache,
        backache=backache,
        discharge=discharge,
        bbt=bbt,
    )


CARE_LABELS = {
    "meal": "吃饭",
    "snack": "零食",
    "drink": "喝水",
    "toilet": "厕所",
    "shower": "洗澡",
    "brush": "刷牙",
}


def _add_care(state: BodyState, at: datetime, kind: str) -> None:
    """Append one self-care action for the page timeline."""
    state.care.append({"t": at.timestamp(), "kind": kind})
    if len(state.care) > 120:
        state.care = state.care[-120:]


def _add_event(
    state: BodyState,
    at: datetime,
    kind: str,
    text: str,
    key: str = "",
) -> None:
    """Append a notable event once per key and remember the key."""
    if key and state.flags.get(key):
        return
    state.events.append({"at": at.isoformat(), "kind": kind, "text": text})
    if key:
        state.flags[key] = at.timestamp()


def _advance_cycle_events(
    state: BodyState,
    now: datetime,
    profile: Profile,
    view: CycleView | None,
) -> None:
    """Record period and phase transitions without duplicating them."""
    if view is None or view.phase in ("premenarche", "menopause", "pregnant"):
        state.cycle_phase = ""
        state.cycle_start = ""
        return
    phase_key = f"period:{view.cycle_start.isoformat()}"
    if view.phase == "menstrual" and not state.flags.get(phase_key):
        text = "经期开始了，第 1 天" if view.day == 1 else f"经期第 {view.day} 天"
        _add_event(state, now, "period", text, key=phase_key)
    if state.cycle_phase == "menstrual" and view.phase != "menstrual":
        previous = state.cycle_start or view.cycle_start.isoformat()
        _add_event(
            state,
            now,
            "period_end",
            "经期结束了",
            key=f"period_end:{previous}",
        )
    if state.cycle_phase and state.cycle_phase != view.phase:
        key = f"phase:{view.cycle_start.isoformat()}:{view.phase}"
        text = ""
        if view.phase == "ovulation":
            text = "进入排卵期"
        elif view.phase == "luteal":
            text = "进入黄体期"
        if text:
            _add_event(state, now, "phase", text, key=key)
    state.cycle_phase = view.phase
    state.cycle_start = view.cycle_start.isoformat()


def _libido_rate(
    state: BodyState,
    view: CycleView | None,
    profile: Profile,
    mood: dict[str, Any] | None = None,
) -> float:
    """Return the daily libido increase for the current profile."""
    if profile.gender == "male":
        age = profile.physio_age or 30
        if age < 20:
            rate = 80.0
        elif age < 30:
            rate = 70.0
        elif age < 45:
            rate = 50.0
        else:
            rate = 32.0
    else:
        rate = 45.0
        if view is not None:
            rate += view.estrogen / 100.0 * 15.0
            if view.phase == "ovulation":
                rate += 20.0
            elif view.phase == "luteal":
                rate += 8.0
    scale = 1.0
    if profile.libido_habit is not None:
        scale = max(0.25, min(4.0, profile.libido_habit / 1.2))
    return rate * _mood_effect(mood)[1] * scale


def _emission_age_factor(age: float) -> float:
    """Return the age multiplier applied to nocturnal-emission odds.

    Emissions peak in the late teens and early twenties and become rare from
    the mid fifties on, so an older persona needs a much longer gap before the
    night is likely to end in one.

    Args:
        age: The persona's age in years.

    Returns:
        A multiplier on the base per-night chance.
    """
    if age < 18:
        return 1.7
    if age < 25:
        return 1.5
    if age < 35:
        return 1.0
    if age < 45:
        return 0.55
    if age < 55:
        return 0.25
    return 0.1


def _morning_age_factor(age: float) -> float:
    """Return the age multiplier for morning-erection odds.

    Morning erections are most frequent in the late teens and twenties and
    fade steadily afterwards.

    Args:
        age: The persona's age in years.

    Returns:
        A multiplier on the base per-morning chance.
    """
    if age < 20:
        return 1.25
    if age < 30:
        return 1.15
    if age < 40:
        return 1.0
    if age < 50:
        return 0.85
    if age < 60:
        return 0.6
    return 0.35


def _routine(
    state: BodyState,
    moment: datetime,
    profile: Profile,
    settings: Settings,
    const: Constitution,
) -> None:
    """Let a persona run her own body on an ordinary daily rhythm.

    Meals, drinks, hygiene, the toilet and supplies are hers to handle, so they
    are driven by her own state and the clock, never by a chat command. The
    model only ever learns that they happened.

    Args:
        state: Body state being advanced.
        moment: Current simulation instant.
        profile: Persona profile; supplies only matter for a cycling body.
        settings: Plugin settings; hygiene can be switched off.
        const: Per-persona baseline, used for the meal size.
    """
    hour = moment.hour
    day = moment.date().isoformat()
    since_meal = moment.timestamp() - state.last_eat_at

    # Meals live in a three-hour window, but the preferred hour is drawn once
    # per persona and day, so breakfast is not always exactly 08:00.
    for start, end, slot, portion in (
        (7, 10, "breakfast", 30.0),
        (12, 15, "lunch", 32.0),
        (18, 21, "dinner", 32.0),
    ):
        if not start <= hour < end:
            continue
        key = f"self-meal:{day}:{slot}"
        if state.flags.get(key):
            break
        preferred = start + int(_roll(profile.persona_id, day, slot) * (end - start))
        preferred_minute = int(_roll(profile.persona_id, day, slot + "-min") * 60)
        if hour < preferred or (hour == preferred and moment.minute < preferred_minute):
            break
        if state.stomach > 90.0 or since_meal <= 3600:
            break
        state.stomach = _clamp(state.stomach + portion / const.stomach_capacity)
        state.glucose = _clamp(state.glucose + 16.0)
        state.gut = _clamp(state.gut + GUT_RESIDUE_MEAL * 0.8)
        state.hydration = _clamp(state.hydration + 2.0)
        state.oral = _clamp(state.oral + 1.5)
        state.last_eat_at = moment.timestamp()
        state.last_meal_kind = "meal"
        _add_care(state, moment, "meal")
        meal_name = {"breakfast": "早饭", "lunch": "午饭", "dinner": "晚饭"}[slot]
        _add_event(
            state,
            moment,
            "self_care",
            f"自己吃了{meal_name}",
            key=key,
        )
        break

    if (
        7 <= hour <= 23
        and state.hydration <= 72.0
        and moment.timestamp() - state.last_drink_at > 3600
    ):
        state.hydration = _clamp(state.hydration + 10.0)
        state.oral = _clamp(state.oral - 1.5)
        state.last_drink_at = moment.timestamp()
        _add_care(state, moment, "drink")

    if 7 <= hour <= 23 and (state.urine >= 30.0 or (hour == 7 and state.urine >= 10.0)):
        state.urine = 2.0
        state.last_toilet_at = moment.timestamp()
        _add_care(state, moment, "toilet")

    if settings.enable_hygiene:
        shower_pref = 7 + int(_roll(profile.persona_id, day, "shower-am") * 3)
        shower_minute = int(_roll(profile.persona_id, day, "shower-am-min") * 60)
        evening_pref = 20 + int(_roll(profile.persona_id, day, "shower-pm") * 3)
        evening_minute = int(_roll(profile.persona_id, day, "shower-pm-min") * 60)
        if state.skin >= 60.0 and (
            (
                7 <= hour < 10
                and (
                    hour > shower_pref
                    or (hour == shower_pref and moment.minute >= shower_minute)
                )
            )
            or (
                20 <= hour < 23
                and (
                    hour > evening_pref
                    or (hour == evening_pref and moment.minute >= evening_minute)
                )
            )
        ):
            state.skin = 2.0
            state.last_shower_at = moment.timestamp()
            _add_care(state, moment, "shower")
            _add_event(
                state,
                moment,
                "self_care",
                "洗了个澡",
                key=f"self-shower:{day}",
            )
        brush_pref = 7 + int(_roll(profile.persona_id, day, "brush-am") * 3)
        brush_minute = int(_roll(profile.persona_id, day, "brush-am-min") * 60)
        evening_brush = 21 + int(_roll(profile.persona_id, day, "brush-pm") * 3)
        evening_brush_minute = int(_roll(profile.persona_id, day, "brush-pm-min") * 60)
        if state.oral >= 55.0 and (
            (
                7 <= hour < 10
                and (
                    hour > brush_pref
                    or (hour == brush_pref and moment.minute >= brush_minute)
                )
            )
            or (
                21 <= hour < 24
                and (
                    hour > evening_brush
                    or (hour == evening_brush and moment.minute >= evening_brush_minute)
                )
            )
        ):
            state.oral = 2.0
            state.last_brush_at = moment.timestamp()
            _add_care(state, moment, "brush")
    if (
        profile.gender == "female"
        and settings.enable_cycle
        and state.pads <= 2
        and not state.pregnant
    ):
        state.pads += PAD_RESTOCK
        state.last_supply_at = moment.timestamp()
        _add_event(
            state,
            moment,
            "self_care",
            "买了卫生用品",
            key=f"self-supplies:{moment.date().isoformat()}",
        )
    if state.lactating and state.milk >= 85.0:
        state.milk = 25.0
        state.last_express_at = moment.timestamp()
        _add_event(
            state,
            moment,
            "self_care",
            "喂了奶，胸口松下来",
            key=f"self-feed:{moment.date().isoformat()}",
        )
    if state.bowel >= 75:
        state.gut = max(0.0, state.gut - 85.0)
        state.bowel = _clamp((state.gut - GUT_URGE_START) * GUT_URGE_GAIN)
        state.last_bowel_at = moment.timestamp()
        state.last_toilet_at = moment.timestamp()
        _add_care(state, moment, "toilet")
        _add_event(
            state,
            moment,
            "self_care",
            "去了趟厕所",
            key=f"self-toilet:{moment.date().isoformat()}",
        )


def _maybe_release(
    state: BodyState,
    moment: datetime,
    profile: Profile,
    settings: Settings,
    const: Constitution,
    step_hours: float,
    sleep_fraction: float,
) -> None:
    """Roll a self-release for one simulation step.

    The chance scales with the persona habit and the current libido, so a
    high-habit persona can release two or three times a day while a low-habit
    one goes days between. Release does not depend on gender or age: both
    bodies have the drive. The event only reaches a private chat, where the
    prompt filter allows it.

    Args:
        state: Body state being advanced.
        moment: Midpoint of the current step.
        profile: Resolved persona profile.
        settings: Plugin settings.
        const: Per-persona baseline, including the habit multiplier.
        step_hours: Length of the current step in hours.
        sleep_fraction: Share of the step spent asleep.
    """
    if not (profile.gender_resolved and settings.enable_intimate):
        return
    if profile.gender not in ("female", "male"):
        return
    if state.libido < 12.0 or not 8 <= moment.hour <= 23:
        return
    stamp = moment.timestamp()
    if profile.gender == "male" and state.refractory_until > stamp:
        return
    if state.last_self_at and stamp - state.last_self_at < 1800:
        return

    comfort = 1.0
    comfort -= min(0.4, max(0.0, state.sleep_debt - 6.0) / 12.0 * 0.4)
    comfort -= min(0.3, max(0.0, 30.0 - state.satiety) / 30.0 * 0.3)
    habit = const.self_rate * max(0.3, comfort)
    per_hour = min(0.4, habit * (0.3 + 0.7 * state.libido / 100.0) / 14.0)
    if sleep_fraction > 0.5:
        per_hour *= 0.15
    chance = 1.0 - (1.0 - per_hour) ** max(1e-6, step_hours)
    bucket = int(stamp // 900)
    if _roll(profile.persona_id, f"self:{bucket}", "roll") >= chance:
        return

    state.libido = _clamp(state.libido - 20.0)
    state.wetness = max(0.0, state.wetness - 30.0)
    state.last_self_at = stamp
    state.release_count += 1
    if profile.gender == "male":
        state.refractory_until = (
            stamp
            + (1.0 + _roll(profile.persona_id, f"self:{bucket}", "refractory") * 2.0)
            * 3600
        )
    _add_event(
        state,
        moment,
        "self_release",
        "自己解决了一次，身体松下来",
        key=f"self-release:{moment.date().isoformat()}",
    )


def _miscarriage_chance(week: int) -> float:
    """Return the weekly miscarriage hazard before twenty weeks.

    Args:
        week: Completed pregnancy weeks.

    Returns:
        A per-week probability, zero after the twentieth week.
    """
    if week < 6:
        return 0.025
    if week < 10:
        return 0.020
    if week < 13:
        return 0.010
    if week < 20:
        return 0.004
    return 0.0


def _fetal_count(persona_id: str, key: str) -> int:
    """Return 1-3 fetuses for one conception, with twins and triplets rare.

    Args:
        persona_id: Persona seed.
        key: Conception timestamp used as the event key.

    Returns:
        The number of fetuses.
    """
    roll = _roll(persona_id, key, "fetuses")
    if roll < TRIPLET_CHANCE:
        return 3
    if roll < TWIN_CHANCE:
        return 2
    return 1


def _roll_pregnancy(
    state: BodyState,
    now: datetime,
    profile: Profile,
    weeks: int,
) -> None:
    """Roll miscarriage per completed week and record prenatal checks.

    Args:
        state: Body state being advanced.
        now: Current local instant.
        profile: Persona profile.
        weeks: Completed pregnancy weeks.
    """
    previous = 0
    if state.updated_at > 0 and state.conceived_at > 0:
        previous = max(0, int((state.updated_at - state.conceived_at) // (7 * 86400)))
    for week in range(previous, min(20, weeks) + 1):
        key = f"miscarriage:{int(state.conceived_at)}:{week}"
        if state.flags.get(key):
            continue
        state.flags[key] = now.timestamp()
        chance = _miscarriage_chance(week) * (1.0 + min(0.5, state.sleep_debt / 24.0))
        if _roll(profile.persona_id, key, "miscarriage") < chance:
            state.pregnant = False
            state.conceived_at = 0.0
            state.fetuses = 0
            state.cycle_anchor = now.date().isoformat()
            state.cycle_start = ""
            state.cycle_phase = ""
            _add_event(
                state,
                now,
                "miscarriage",
                "流产了",
                key=f"miscarried:{now.date().isoformat()}",
            )
            return

    for milestone, text in PREGNANCY_CHECKS:
        if weeks < milestone:
            continue
        key = f"prenatal:{int(state.conceived_at)}:{milestone}"
        if state.flags.get(key):
            continue
        if state.fetuses == 2:
            count = "双胎"
        elif state.fetuses >= 3:
            count = "三胎"
        else:
            count = "单胎"
        _add_event(state, now, "prenatal", f"产检：{text}，{count}", key=key)


def _give_birth(state: BodyState, now: datetime) -> None:
    """End the pregnancy, record the birth and start lactation.

    Args:
        state: Body state being advanced.
        now: Current local instant.
    """
    count = max(1, state.fetuses)
    state.pregnant = False
    state.conceived_at = 0.0
    state.fetuses = 0
    state.cycle_anchor = now.date().isoformat()
    state.cycle_start = ""
    state.cycle_phase = ""
    state.birth_at = now.timestamp()
    state.lactating = True
    state.milk = 55.0
    label = {2: "，是双胎", 3: "，是三胎"}.get(count, "")
    _add_event(
        state,
        now,
        "birth",
        f"孩子出生了{label}",
        key=f"birth:{now.date().isoformat()}",
    )


def _advance_male_events(
    state: BodyState,
    start_ts: float,
    now: datetime,
    profile: Profile,
    settings: Settings,
) -> None:
    """Roll nocturnal emissions, morning erections and daytime arousal."""
    if not (
        profile.gender_resolved
        and settings.enable_intimate
        and profile.gender == "male"
    ):
        return
    if state.last_release_at <= 0:
        state.last_release_at = start_ts - 3 * 86400
    first = datetime.fromtimestamp(start_ts, now.tzinfo).date()
    last = now.date()
    if (last - first).days > 30:
        first = last - timedelta(days=30)
    current = first
    while current <= last:
        night = datetime.combine(current, time(4, 0), tzinfo=now.tzinfo)
        if start_ts < night.timestamp() <= now.timestamp():
            key = f"wet:{current.isoformat()}"
            if not state.flags.get(key):
                days = (night.timestamp() - state.last_release_at) / 86400
                sleep_bonus = max(0.0, state.sleep_debt - 6.0) * 0.008
                base = max(0.0, (days - 2) * 0.10 + sleep_bonus)
                chance = min(
                    0.65, base * _emission_age_factor(profile.physio_age or 30)
                )
                if (
                    days >= 2
                    and _roll(profile.persona_id, current.isoformat(), "wet") < chance
                ):
                    at = min(
                        now,
                        datetime.combine(current, time(6, 30), tzinfo=now.tzinfo),
                    )
                    _add_event(state, at, "wet_dream", "昨晚梦遗了", key=key)
                    state.last_release_at = at.timestamp()
                    state.libido = _clamp(state.libido - 50)
                    state.refractory_until = (
                        at.timestamp()
                        + (
                            2
                            + _roll(
                                profile.persona_id, current.isoformat(), "refractory"
                            )
                            * 4
                        )
                        * 3600
                    )
        morning = datetime.combine(current, time(7, 30), tzinfo=now.tzinfo)
        if start_ts < morning.timestamp() <= now.timestamp():
            key = f"morning:{current.isoformat()}"
            base = 0.22 + max(0.0, 1.0 - state.sleep_debt / 12.0) * 0.14
            chance = min(0.5, base * _morning_age_factor(profile.physio_age or 30))
            if (
                not state.flags.get(key)
                and _roll(profile.persona_id, current.isoformat(), "morning") < chance
            ):
                _add_event(
                    state,
                    morning,
                    "morning_erection",
                    "早晨有晨勃，过一会儿就好了",
                    key=key,
                )
        current += timedelta(days=1)

    if 8 <= now.hour <= 23:
        key = f"arousal:{now.date().isoformat()}"
        if (
            not state.flags.get(key)
            and state.libido >= 70
            and _roll(profile.persona_id, now.date().isoformat(), "arousal") < 0.12
        ):
            _add_event(
                state,
                now,
                "arousal",
                "白天身体有点反应，过一会儿自己消了",
                key=key,
            )
            state.libido = _clamp(state.libido - 8)


def _recompute_primary(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
    in_period: bool = False,
    mood: dict[str, Any] | None = None,
) -> None:
    """Refresh displayed meters from internal state after a time step."""
    appetite = appetite_level(now)
    state.satiety = _clamp(
        0.55 * state.stomach
        + 0.45 * state.glucose
        - (appetite - 1.0) * 20.0
        - _mood_effect(mood)[0]
    )
    state.bladder = _clamp(state.urine)
    state.cleanliness = _clamp(100.0 - state.skin)
    state.oral_health = _clamp(100.0 - state.oral)
    alert = circadian_alertness(now)
    state.bowel = _clamp((state.gut - GUT_URGE_START) * GUT_URGE_GAIN)
    penalty = max(0.0, 40.0 - state.satiety) * 0.20
    penalty += max(0.0, 40.0 - state.hydration) * 0.25
    penalty += max(0.0, state.bowel - 70.0) * 0.10
    if in_period:
        penalty += 4.0
    drain = 0.55 * (state.sleep_debt / 24.0 * 100.0) + 0.45 * (100.0 - alert) + penalty
    state.energy = _clamp(100.0 - drain)
    if not settings.enable_hygiene:
        state.skin = 0.0
        state.oral = 0.0
        state.cleanliness = 100.0
        state.oral_health = 100.0
    if not (profile.gender_resolved and settings.enable_intimate):
        state.libido = 0.0


def advance(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
    mood: dict[str, Any] | None = None,
) -> BodyState:
    """Let time pass and update internal state, meters and events."""
    now_ts = now.timestamp()
    if state.updated_at <= 0:
        state.updated_at = now_ts
        return state
    if now_ts <= state.updated_at:
        return state

    start = datetime.fromtimestamp(state.updated_at, now.tzinfo)
    elapsed = now_ts - state.updated_at
    if elapsed > MAX_SIM_DAYS * 86400:
        # Beyond a year only the last year matters; the meters clamp anyway.
        start = now - timedelta(days=MAX_SIM_DAYS)
        elapsed = MAX_SIM_DAYS * 86400
    window = (
        parse_sleep_window(settings.sleep_window) if settings.enable_sleep else None
    )
    # Seed and seed-phase against the moment the replay starts, not against
    # now: a persona who was away for months must live through those cycles
    # instead of having the current cycle back-dated over the whole gap.
    view = cycle_view(state, start, profile, settings, mood)
    in_period = bool(view and view.phase == "menstrual")
    pubertal = _is_pubertal(profile.physio_age)
    const = constitution(profile.persona_id)
    if profile.libido_habit is not None:
        const = replace(const, self_rate=profile.libido_habit)

    cursor = start
    remaining = float(elapsed)
    while remaining > 1e-6:
        if elapsed <= 86400:
            step_seconds = min(900.0, remaining)
        elif elapsed <= LONG_GAP_DAYS * 86400:
            step_seconds = min(3600.0, remaining)
        else:
            # A month away is replayed at two-hour resolution: enough for the
            # cycle and the meters, without a year of hourly steps.
            step_seconds = min(7200.0, remaining)
        step_hours = step_seconds / 3600.0
        chunk_end = cursor + timedelta(seconds=step_seconds)
        sleep_seconds = 0.0
        if window is not None:
            sleep_seconds = sleep_seconds_between(cursor, chunk_end, window)
            if state.last_active_at and now_ts - state.last_active_at <= 1800:
                active_from = max(
                    cursor, datetime.fromtimestamp(state.last_active_at, now.tzinfo)
                )
                if chunk_end > active_from:
                    sleep_seconds -= sleep_seconds_between(
                        active_from, chunk_end, window
                    )
        sleep_fraction = min(1.0, max(0.0, sleep_seconds / step_seconds))
        awake_fraction = 1.0 - sleep_fraction
        midpoint = cursor + timedelta(seconds=step_seconds / 2.0)
        alert = circadian_alertness(midpoint)
        _step_cycle(state, chunk_end, profile, settings, step_hours, mood)
        # Run the daily rhythm once per clock hour, including the hour the
        # step starts in. Checking only hour changes skipped meals whenever an
        # advance happened to start after the scheduled hour.
        _routine(state, cursor, profile, settings, const)
        mark = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        while mark <= chunk_end:
            _routine(state, mark, profile, settings, const)
            mark += timedelta(hours=1)
        cycle_day = state.cycle_day
        in_period_now = (
            profile.gender == "female"
            and settings.enable_cycle
            and 0 < cycle_day <= _period_days(profile)
        )

        decay = (
            STOMACH_DECAY_AWAKE * awake_fraction + STOMACH_DECAY_SLEEP * sleep_fraction
        ) * const.metabolism
        state.stomach = _clamp(state.stomach * math.exp(-decay * step_hours))

        glucose_use = (
            GLUCOSE_USE_AWAKE * awake_fraction + GLUCOSE_USE_SLEEP * sleep_fraction
        )
        glucose_release = GLUCOSE_RELEASE if state.stomach > 25.0 else 0.0
        state.glucose = _clamp(
            state.glucose + (glucose_release - glucose_use) * step_hours
        )

        hydration_use = (
            HYDRATION_USE_AWAKE * awake_fraction + HYDRATION_USE_SLEEP * sleep_fraction
        )
        state.hydration = _clamp(state.hydration - hydration_use * step_hours)

        urine_rate = (
            URINE_BASE_AWAKE * awake_fraction
            + URINE_BASE_SLEEP * sleep_fraction
            + max(0.0, state.hydration - 50.0) * 0.04
        )
        state.urine = _clamp(state.urine + urine_rate * step_hours)

        state.gut = _clamp(
            state.gut + (GUT_BASE - GUT_PASSAGE * const.gut_transit) * step_hours
        )
        state.bowel = _clamp((state.gut - GUT_URGE_START) * GUT_URGE_GAIN)

        if state.lactating:
            state.milk = _clamp(state.milk + MILK_RATE * step_hours)
            if state.milk >= 95.0:
                _add_event(
                    state,
                    chunk_end,
                    "engorged",
                    "涨奶得厉害",
                    key=f"engorged:{chunk_end.date().isoformat()}",
                )

        sweat = max(0.0, alert - 70.0) / 100.0 * 0.4
        period_extra = SKIN_PERIOD_EXTRA if in_period_now else 0.0
        puberty_extra = SKIN_PUBERTY_EXTRA if pubertal else 0.0
        skin_rate = (
            SKIN_AWAKE * awake_fraction
            + SKIN_SLEEP * sleep_fraction
            + sweat
            + period_extra
            + puberty_extra
        )
        state.skin = _clamp(state.skin + skin_rate * step_hours)

        oral_rate = ORAL_AWAKE * awake_fraction + ORAL_SLEEP * sleep_fraction
        state.oral = _clamp(state.oral + oral_rate * step_hours)

        state.sleep_debt = _clamp(
            state.sleep_debt
            + (
                SLEEP_DEBT_AWAKE * (const.sleep_need / 8.0) * awake_fraction
                - SLEEP_DEBT_SLEEP * (const.sleep_need / 8.0) * sleep_fraction
            )
            * step_hours,
            0.0,
            24.0,
        )

        if (
            profile.gender_resolved
            and settings.enable_intimate
            and profile.gender in ("female", "male")
        ):
            rate = _libido_rate(state, view, profile, mood)
            comfort = 1.0
            comfort -= min(0.4, max(0.0, state.sleep_debt - 6.0) / 12.0 * 0.4)
            comfort -= min(0.3, max(0.0, 30.0 - state.satiety) / 30.0 * 0.3)
            rate *= max(0.3, comfort)
            if profile.gender == "male" and state.refractory_until > cursor.timestamp():
                rate *= 0.25
            state.libido = _clamp(state.libido + rate * step_hours / 24.0)
            # Arousal fades on its own with a one-day half-life, so a persona
            # who rarely acts settles at a lower level instead of pinning 100.
            state.libido *= 0.5 ** (step_hours / 24.0)
            _maybe_release(
                state, midpoint, profile, settings, const, step_hours, sleep_fraction
            )
        else:
            state.libido = 0.0

        if profile.gender == "female" and settings.enable_intimate:
            target = state.libido * 0.6
            if view is not None:
                if view.fertile:
                    target += 12.0
                if view.phase == "ovulation":
                    target += 8.0
            state.wetness += (target - state.wetness) * min(1.0, step_hours / 2.0)
            state.wetness = _clamp(state.wetness)
        else:
            state.wetness = 0.0

        if in_period_now:
            flow_now = 3 if cycle_day <= 2 else (2 if cycle_day <= 4 else 1)
            state.pad_hours += step_hours * awake_fraction
            interval = PAD_INTERVAL.get(flow_now, 4.0)
            while state.pad_hours >= interval:
                state.pad_hours -= interval
                if state.pads > 0:
                    state.pads -= 1
        else:
            state.pad_hours = 0.0

        cursor = chunk_end
        remaining -= step_seconds

    view = cycle_view(state, now, profile, settings, mood)
    in_period = bool(view and view.phase == "menstrual")
    _recompute_primary(state, now, profile, settings, in_period, mood)
    if state.pregnant and state.conceived_at > 0:
        weeks = max(0, int((now_ts - state.conceived_at) // (7 * 86400)))
        _roll_pregnancy(state, now, profile, weeks)
        if state.pregnant:
            duration = PREGNANCY_DAYS - 14 * (max(1, state.fetuses) - 1)
            if (now_ts - state.conceived_at) >= duration * 86400:
                _give_birth(state, now)
    if state.lactating and state.birth_at > 0:
        if now_ts - state.birth_at > LACTATION_DAYS * 86400:
            state.lactating = False
            state.milk = 0.0
    _advance_cycle_events(state, now, profile, view)
    if in_period and state.pads <= 0:
        _add_event(
            state,
            now,
            "supplies",
            "卫生用品用完了，只能先凑合",
            key=f"pads-empty:{now.date().isoformat()}",
        )
    _advance_male_events(state, state.updated_at, now, profile, settings)
    state.updated_at = now_ts
    state.cycle_length = profile.cycle_length
    state.period_length = profile.period_length
    if profile.gender != "female":
        state.cycle_phase = ""
        state.cycle_start = ""
        state.cycle_anchor = ""
    if not settings.enable_hygiene:
        state.skin = 0.0
        state.oral = 0.0
        state.cleanliness = 100.0
        state.oral_health = 100.0

    cutoff = now_ts - settings.event_keep_days * 86400
    state.events = [
        event for event in state.events if _parse_ts(event.get("at")) >= cutoff
    ]
    state.events.sort(key=lambda event: _parse_ts(event.get("at")))
    state.events = state.events[-settings.max_events :]
    care_cutoff = now_ts - 2 * 86400
    state.care = [item for item in state.care if _number(item.get("t")) >= care_cutoff][
        -120:
    ]
    flag_cutoff = now_ts - 60 * 86400
    state.flags = {
        key: value for key, value in state.flags.items() if value >= flag_cutoff
    }
    return state


def _meter_snapshot(state: BodyState) -> dict[str, float]:
    """Return the displayed meters as a plain dict."""
    return {
        "satiety": state.satiety,
        "hydration": state.hydration,
        "bladder": state.bladder,
        "cleanliness": state.cleanliness,
        "oral_health": state.oral_health,
        "energy": state.energy,
        "libido": state.libido,
    }


def apply_action(
    state: BodyState,
    action: str,
    now: datetime,
    profile: Profile,
    settings: Settings,
    ejaculated: bool = True,
) -> ActionResult:
    """Apply one deliberate action and report what changed."""
    if action not in ACTION_LABELS:
        raise ValueError(f"unknown action: {action}")
    if action in ("shower", "brush") and not settings.enable_hygiene:
        raise ValueError("hygiene module is disabled")
    if action in ("intimacy", "release") and not (
        profile.gender_resolved and settings.enable_intimate
    ):
        raise ValueError("intimacy requires a resolved gender")
    if action == "prenatal" and not state.pregnant:
        raise ValueError("现在没有怀孕")
    if action == "express" and (not state.lactating or profile.gender != "female"):
        raise ValueError("现在没有在哺乳")

    before = _meter_snapshot(state)
    now_ts = now.timestamp()
    const = constitution(profile.persona_id)
    if action == "eat":
        state.stomach = _clamp(state.stomach + 55.0 / const.stomach_capacity)
        state.gut = _clamp(state.gut + GUT_RESIDUE_MEAL)
        state.glucose = _clamp(state.glucose + 28.0)
        state.hydration = _clamp(state.hydration + 2.0)
        state.oral = _clamp(state.oral + 4.0)
        state.sleep_debt = _clamp(state.sleep_debt - 0.3, 0.0, 24.0)
        state.last_eat_at = now_ts
        state.last_meal_kind = "meal"
        state.last_meal_size = 55.0
        _add_care(state, now, "meal")
    elif action == "snack":
        state.stomach = _clamp(state.stomach + 22.0 / const.stomach_capacity)
        state.gut = _clamp(state.gut + GUT_RESIDUE_SNACK)
        state.glucose = _clamp(state.glucose + 12.0)
        state.hydration = _clamp(state.hydration + 1.0)
        state.oral = _clamp(state.oral + 3.0)
        state.last_eat_at = now_ts
        state.last_meal_kind = "snack"
        state.last_meal_size = 22.0
        _add_care(state, now, "snack")
    elif action == "drink":
        state.hydration = _clamp(state.hydration + 32.0)
        state.urine = _clamp(state.urine + 12.0)
        state.oral = _clamp(state.oral - 1.5)
        state.last_drink_at = now_ts
        state.last_drink_ml = 300.0
        _add_care(state, now, "drink")
    elif action == "shower":
        state.skin = 2.0
        state.sleep_debt = _clamp(state.sleep_debt - 0.3, 0.0, 24.0)
        state.last_shower_at = now_ts
        _add_care(state, now, "shower")
    elif action == "brush":
        state.oral = 2.0
        state.last_brush_at = now_ts
        _add_care(state, now, "brush")
    elif action == "toilet":
        if state.bowel >= 20.0:
            state.gut = max(0.0, state.gut - 85.0)
            state.bowel = _clamp((state.gut - GUT_URGE_START) * GUT_URGE_GAIN)
            state.last_bowel_at = now_ts
        state.urine = 2.0
        state.last_toilet_at = now_ts
        _add_care(state, now, "toilet")
    elif action == "nap":
        state.sleep_debt = _clamp(state.sleep_debt - 2.5, 0.0, 24.0)
        state.last_nap_at = now_ts
    elif action == "supplies":
        state.pads += PAD_RESTOCK
        state.last_supply_at = now_ts
    elif action == "intimacy":
        cycle = cycle_view(state, now, profile, settings)
        state.intimacy_count += 1
        state.last_intimacy_at = now_ts
        state.wetness = max(0.0, state.wetness - 25.0)
        if ejaculated:
            state.insemination_count += 1
            state.last_insemination_at = now_ts
        _add_event(
            state,
            now,
            "intimacy",
            "做爱了一次" + ("，受精了" if ejaculated else ""),
            key=f"intimacy:{int(now_ts)}",
        )
        can_conceive = (
            ejaculated
            and settings.enable_pregnancy
            and profile.gender == "female"
            and not state.pregnant
            and cycle is not None
            and cycle.fertile
        )
        if can_conceive:
            factor = CONTRACEPTION_FACTOR.get(settings.contraception, 1.0)
            if _roll(profile.persona_id, str(int(now_ts)), "conception") < (
                CONCEPTION_BASE * factor
            ):
                state.pregnant = True
                state.conceived_at = now_ts
                state.fetuses = _fetal_count(profile.persona_id, str(int(now_ts)))
                state.prenatal_week = 0
                state.birth_at = 0.0
                state.lactating = False
                state.milk = 0.0
                _add_event(
                    state,
                    now,
                    "conception",
                    "怀孕了",
                    key=f"conceived:{int(now_ts)}",
                )
        state.libido = 0.0
        state.sleep_debt = _clamp(state.sleep_debt - 0.4, 0.0, 24.0)
        state.skin = _clamp(state.skin + 4.0)
        state.last_release_at = now_ts
        if profile.gender == "male":
            state.refractory_until = (
                now_ts
                + (2.0 + _roll(profile.persona_id, str(now_ts), "refractory") * 4.0)
                * 3600
            )
    elif action == "release":
        state.libido = 0.0
        state.wetness = max(0.0, state.wetness - 30.0)
        state.last_self_at = now_ts
        state.release_count += 1
        state.away_until = max(
            state.away_until,
            now_ts
            + (3.0 + _roll(profile.persona_id, str(now_ts), "away") * 5.0) * 60.0,
        )
        state.away_reason = "self_release"
        _add_event(
            state,
            now,
            "self_release",
            "自己解决了一次，身体松下来",
            key=f"self-release-action:{int(now_ts)}",
        )
        if profile.gender == "male":
            state.refractory_until = (
                now_ts
                + (
                    1.0
                    + _roll(profile.persona_id, str(now_ts), "self-refractory") * 2.0
                )
                * 3600
            )
    elif action == "prenatal":
        weeks = max(0, int((now_ts - state.conceived_at) // (7 * 86400)))
        state.prenatal_week = weeks
        _add_event(
            state,
            now,
            "prenatal",
            f"去做了产检，怀孕 {weeks} 周",
            key=f"visit:{int(now_ts)}",
        )
    elif action == "express":
        state.milk = max(0.0, state.milk - 65.0)
        state.last_express_at = now_ts

    view = cycle_view(state, now, profile, settings)
    _recompute_primary(
        state,
        now,
        profile,
        settings,
        bool(view and view.phase == "menstrual"),
    )
    after = _meter_snapshot(state)
    changes = []
    for key in (
        "satiety",
        "hydration",
        "bladder",
        "cleanliness",
        "oral_health",
        "energy",
        "libido",
    ):
        if round(before[key]) != round(after[key]):
            changes.append(
                f"{METER_TEXT[key]} {round(before[key])}→{round(after[key])}"
            )
    detail = "，".join(changes) if changes else "没有明显变化"
    return ActionResult(
        action=action,
        label=ACTION_LABELS[action],
        detail=detail,
        before=before,
        after=after,
    )
