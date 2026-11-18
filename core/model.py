"""Physiology v2: digestion, hydration, sleep debt, cycle hormones.

The model is still intentionally simpler than a medical simulation, but the
primary meters are now derived from slower internal state instead of being
constant linear ramps:

- stomach fullness and glucose drive hunger
- hydration drives thirst and urine production
- urine volume drives bladder pressure
- sleep debt and circadian alertness drive fatigue
- skin and oral dirtiness drive cleanliness
- a simplified estrogen/progesterone curve drives cycle symptoms and desire

All time integration is piecewise: 15-minute steps for the first day, hourly
steps afterwards. That keeps a 60-day absence cheap while letting digestion and
circadian changes happen at a human-ish resolution.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass, field
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
URINE_BASE_AWAKE = 2.0
URINE_BASE_SLEEP = 1.0
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
CONTRACEPTION_FACTOR = {"none": 1.0, "condom": 0.02, "pill": 0.01}
PAD_INTERVAL = {3: 2.0, 2: 4.0, 1: 6.0}
PAD_WAKING_HOURS = 16.0
PAD_RESTOCK = 20
ORAL_AWAKE = 2.8
ORAL_SLEEP = 1.3
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
SELF_CARE_IDLE_HOURS = 6.0

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
        self_rate=0.6 + 1.0 * _roll(persona_id, "body", "release"),
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

    - hunger is derived from stomach fullness and glucose
    - thirst is derived from hydration
    - bladder is urine volume
    - cleanliness is skin dirtiness
    - fatigue is derived from sleep debt and circadian alertness
    - desire is a tension meter that only matters while ENABLE_INTIMATE is on
    """

    persona_id: str
    updated_at: float = 0.0
    last_active_at: float = 0.0
    hunger: float = 20.0
    thirst: float = 20.0
    bladder: float = 18.0
    cleanliness: float = 18.0
    oral: float = 20.0
    fatigue: float = 22.0
    desire: float = 10.0
    stomach: float = 72.0
    glucose: float = 78.0
    hydration: float = 80.0
    urine: float = 18.0
    skin: float = 18.0
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
    refractory_until: float = 0.0
    last_meal_kind: str = ""
    last_meal_size: float = 0.0
    last_drink_ml: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)
    flags: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain JSON-compatible data."""
        return {
            "persona_id": self.persona_id,
            "updated_at": self.updated_at,
            "last_active_at": self.last_active_at,
            "hunger": self.hunger,
            "thirst": self.thirst,
            "bladder": self.bladder,
            "cleanliness": self.cleanliness,
            "oral": self.oral,
            "fatigue": self.fatigue,
            "desire": self.desire,
            "stomach": self.stomach,
            "glucose": self.glucose,
            "hydration": self.hydration,
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
            "refractory_until": self.refractory_until,
            "last_meal_kind": self.last_meal_kind,
            "last_meal_size": self.last_meal_size,
            "last_drink_ml": self.last_drink_ml,
            "events": list(self.events),
            "flags": dict(self.flags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BodyState:
        """Rebuild a state from JSON, migrating old single-meter files."""
        state = cls(persona_id=str(data.get("persona_id") or ""))
        state.updated_at = _number(data.get("updated_at"))
        state.last_active_at = _number(data.get("last_active_at"))
        has_internal = "stomach" in data
        for key in (
            "hunger",
            "thirst",
            "bladder",
            "cleanliness",
            "oral",
            "fatigue",
            "desire",
            "stomach",
            "glucose",
            "hydration",
            "urine",
            "skin",
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

        for key in (
            "hunger",
            "thirst",
            "bladder",
            "cleanliness",
            "oral",
            "fatigue",
            "desire",
            "stomach",
            "glucose",
            "hydration",
            "urine",
            "skin",
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
                state.stomach = _clamp(100.0 - state.hunger)
                state.glucose = _clamp(100.0 - state.hunger * 0.7)
            if state.last_drink_at and reference:
                drink_hours = max(0.0, (reference - state.last_drink_at) / 3600.0)
                state.hydration = _clamp(40.0 + 60.0 * math.exp(-0.12 * drink_hours))
            else:
                state.hydration = _clamp(100.0 - state.thirst)
            state.urine = _clamp(state.bladder)
            state.skin = _clamp(state.cleanliness)
            state.sleep_debt = _clamp(state.fatigue / 4.0, 0.0, 24.0)
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
        state.pads = max(0, int(_number(data.get("pads"), 12)))
        state.pad_hours = max(0.0, _number(data.get("pad_hours"), 0.0))
        state.last_pad_day = str(data.get("last_pad_day") or "")
        state.last_supply_at = _number(data.get("last_supply_at"), 0.0)
        state.last_meal_kind = str(data.get("last_meal_kind") or "")
        events = data.get("events")
        if isinstance(events, list):
            state.events = [
                item for item in events if isinstance(item, dict) and item.get("text")
            ][:100]
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
    "intimacy": "亲密过后",
    "release": "自己解决了一次",
}
METER_TEXT = {
    "hunger": "饥饿",
    "thirst": "口渴",
    "bladder": "膀胱",
    "cleanliness": "清洁",
    "oral": "口腔",
    "fatigue": "精力",
    "desire": "欲望",
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


def _cycle_stress(state: BodyState) -> float:
    """Return the follicular stretch factor; sleep debt delays ovulation."""
    return 1.0 + min(0.35, max(0.0, state.sleep_debt - 6.0) / 24.0 * 0.6)


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
    stress = _cycle_stress(state)
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
    stress = _cycle_stress(state)
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
) -> CycleView | None:
    """Compute the current menstrual phase, hormones and symptoms."""
    if not settings.enable_cycle or profile.gender != "female":
        return None

    if state.pregnant:
        weeks = max(0, int((now.timestamp() - state.conceived_at) // (7 * 86400)))
        return CycleView(
            cycle_start=now.date(),
            day=0,
            phase="pregnant",
            flow=0,
            cramps=0,
            mood=0,
            appetite=0,
            days_to_next=0,
            note=f"怀孕第 {weeks} 周",
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
    _seed_cycle(state, now, profile, settings)
    try:
        start = date.fromisoformat(state.cycle_start)
    except ValueError:
        start = now.date()
    day = max(1, (now.date() - start).days + 1)
    period = _period_days(profile)
    stress = _cycle_stress(state)
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
        mood = -1 if day <= 2 else 0
        appetite = rng.choice([-1, 0, 0, 1])
        note = "小腹坠胀，腰有点酸" if cramps >= 2 else "小腹有点坠"
    elif phase == "luteal":
        days_left = max(1, length - day + 1)
        premenstrual = days_left <= 7
        cramps = rng.choice([0, 0, 1]) if days_left <= 4 else 0
        breast = rng.choice([0, 1, 1, 2]) if premenstrual else 0
        headache = rng.choice([0, 0, 1, 2]) if days_left <= 5 else 0
        backache = rng.choice([0, 1, 2]) if days_left <= 4 else 0
        mood = -1 - (1 if progesterone > 45 else 0) - (1 if breast >= 2 else 0)
        if pubertal:
            mood = max(-3, mood - 1)
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
        mood = 1
        breast = 1
        note = "精力比前几天好"
    else:
        note = "经期后，身体轻快"

    mood = max(-3, mood)
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


def _desire_rate(state: BodyState, view: CycleView | None, profile: Profile) -> float:
    """Return the daily desire increase for the current profile."""
    if profile.gender == "male":
        age = profile.physio_age or 30
        if age < 20:
            return 2.0
        if age < 30:
            return 1.8
        if age < 45:
            return 1.4
        return 1.0
    rate = 0.9
    if view is not None:
        rate += view.estrogen / 100.0 * 0.5
        if view.phase == "ovulation":
            rate += 0.6
        elif view.phase == "luteal":
            rate += 0.2
    return rate


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


def _routine(state: BodyState, moment: datetime, const: Constitution) -> None:
    """Let a persona look after herself while nobody is interacting.

    Meals, drinks and a full gut are handled at ordinary hours instead of
    piling up forever. This only runs after a long idle gap, so an active
    conversation still sees real hunger, thirst and pressure.

    Args:
        state: Body state being advanced.
        moment: Current simulation instant.
        const: Per-persona baseline, used for the meal size.
    """
    hour = moment.hour
    if hour in (8, 13, 19) and state.stomach <= 55.0:
        state.stomach = _clamp(state.stomach + 42.0 / const.stomach_capacity)
        state.glucose = _clamp(state.glucose + 18.0)
        state.gut = _clamp(state.gut + GUT_RESIDUE_MEAL * 0.8)
        state.last_eat_at = moment.timestamp()
        state.last_meal_kind = "meal"
        _add_event(
            state,
            moment,
            "self_care",
            "自己吃了点东西",
            key=f"self-meal:{moment.date().isoformat()}",
        )
    if hour in (10, 16, 21) and state.hydration <= 55.0:
        state.hydration = _clamp(state.hydration + 22.0)
        state.last_drink_at = moment.timestamp()
    if state.bowel >= 75:
        state.gut = max(0.0, state.gut - 85.0)
        state.bowel = _clamp((state.gut - GUT_URGE_START) * GUT_URGE_GAIN)
        state.last_bowel_at = moment.timestamp()
        state.last_toilet_at = moment.timestamp()
        _add_event(
            state,
            moment,
            "self_care",
            "去了趟厕所",
            key=f"self-toilet:{moment.date().isoformat()}",
        )


def _maybe_release(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
    const: Constitution,
    idle: bool,
) -> None:
    """Roll at most one self-release per day once desire has built up.

    Release does not depend on gender or age: both bodies have the drive, and
    each persona keeps a stable habit multiplier. The event only reaches a
    private chat, where the prompt filter allows it.

    Args:
        state: Body state being advanced.
        now: Current local instant.
        profile: Resolved persona profile.
        settings: Plugin settings.
        const: Per-persona baseline, including the habit multiplier.
        idle: Whether nobody has interacted for a while.
    """
    if not (profile.adult and settings.enable_intimate and idle):
        return
    if state.desire < 55.0 or not 8 <= now.hour <= 23:
        return
    key = f"self-release:{now.date().isoformat()}"
    if state.flags.get(key):
        return
    since = (
        (now.timestamp() - state.last_self_at) / 86400.0 if state.last_self_at else 30.0
    )
    chance = min(
        0.85,
        max(0.0, since - 0.3) * 0.5 * const.self_rate * (state.desire / 60.0),
    )
    if _roll(profile.persona_id, key, "roll") >= chance:
        return
    state.desire = _clamp(state.desire - 55.0)
    state.last_self_at = now.timestamp()
    if profile.gender == "male":
        state.refractory_until = (
            now.timestamp()
            + (1.0 + _roll(profile.persona_id, key, "refractory") * 2.0) * 3600
        )
    _add_event(
        state,
        now,
        "self_release",
        "自己解决了一次，身体松下来",
        key=key,
    )


def _advance_male_events(
    state: BodyState,
    start_ts: float,
    now: datetime,
    profile: Profile,
    settings: Settings,
) -> None:
    """Roll nocturnal emissions, morning erections and daytime arousal."""
    if not (profile.adult and settings.enable_intimate and profile.gender == "male"):
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
                    state.desire = _clamp(state.desire - 50)
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
            and state.desire >= 70
            and _roll(profile.persona_id, now.date().isoformat(), "arousal") < 0.12
        ):
            _add_event(
                state,
                now,
                "arousal",
                "白天身体有点反应，过一会儿自己消了",
                key=key,
            )
            state.desire = _clamp(state.desire - 8)


def _recompute_primary(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
    in_period: bool = False,
) -> None:
    """Refresh displayed meters from internal state after a time step."""
    appetite = appetite_level(now)
    state.hunger = _clamp(
        0.55 * (100.0 - state.stomach)
        + 0.45 * (100.0 - state.glucose)
        + (appetite - 1.0) * 20.0
    )
    state.thirst = _clamp(100.0 - state.hydration)
    state.bladder = _clamp(state.urine)
    state.cleanliness = _clamp(state.skin)
    alert = circadian_alertness(now)
    state.bowel = _clamp((state.gut - GUT_URGE_START) * GUT_URGE_GAIN)
    penalty = max(0.0, state.hunger - 60.0) * 0.20
    penalty += max(0.0, state.thirst - 60.0) * 0.25
    penalty += max(0.0, state.bowel - 70.0) * 0.10
    if in_period:
        penalty += 4.0
    state.fatigue = _clamp(
        0.55 * (state.sleep_debt / 24.0 * 100.0) + 0.45 * (100.0 - alert) + penalty
    )
    if not settings.enable_hygiene:
        state.skin = 0.0
        state.cleanliness = 0.0
        state.oral = 0.0
    if not (profile.adult and settings.enable_intimate):
        state.desire = 0.0


def advance(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
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
    if elapsed > 60 * 86400:
        # A year away should not run a year of hourly steps. Cap the simulation
        # window; all meters clamp anyway.
        start = now - timedelta(days=60)
        elapsed = 60 * 86400
    window = (
        parse_sleep_window(settings.sleep_window) if settings.enable_sleep else None
    )
    view = cycle_view(state, now, profile, settings)
    in_period = bool(view and view.phase == "menstrual")
    pubertal = _is_pubertal(profile.physio_age)
    const = constitution(profile.persona_id)
    idle = (
        state.last_active_at > 0
        and now_ts - state.last_active_at > SELF_CARE_IDLE_HOURS * 3600
    )

    cursor = start
    remaining = float(elapsed)
    while remaining > 1e-6:
        if elapsed <= 86400:
            step_seconds = min(900.0, remaining)
        else:
            step_seconds = min(3600.0, remaining)
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
        _step_cycle(state, chunk_end, profile, settings, step_hours)
        if idle and chunk_end.hour != cursor.hour:
            _routine(state, chunk_end, const)
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
            profile.adult
            and settings.enable_intimate
            and profile.gender in ("female", "male")
        ):
            rate = _desire_rate(state, view, profile)
            comfort = 1.0
            comfort -= min(0.4, max(0.0, state.sleep_debt - 6.0) / 12.0 * 0.4)
            comfort -= min(0.3, max(0.0, state.hunger - 70.0) / 30.0 * 0.3)
            rate *= max(0.3, comfort)
            if profile.gender == "male" and state.refractory_until > cursor.timestamp():
                rate *= 0.25
            state.desire = _clamp(state.desire + rate * step_hours / 24.0)
        else:
            state.desire = 0.0

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

    _maybe_release(state, now, profile, settings, const, idle)
    view = cycle_view(state, now, profile, settings)
    in_period = bool(view and view.phase == "menstrual")
    _recompute_primary(state, now, profile, settings, in_period)
    if state.pregnant and state.conceived_at > 0:
        if (now_ts - state.conceived_at) >= PREGNANCY_DAYS * 86400:
            state.pregnant = False
            state.conceived_at = 0.0
            state.cycle_anchor = now.date().isoformat()
            _add_event(
                state,
                now,
                "birth",
                "孩子出生了",
                key=f"birth:{now.date().isoformat()}",
            )
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
        state.cleanliness = 0.0
        state.oral = 0.0

    cutoff = now_ts - settings.event_keep_days * 86400
    state.events = [
        event for event in state.events if _parse_ts(event.get("at")) >= cutoff
    ]
    state.events.sort(key=lambda event: _parse_ts(event.get("at")))
    state.events = state.events[-settings.max_events :]
    flag_cutoff = now_ts - 60 * 86400
    state.flags = {
        key: value for key, value in state.flags.items() if value >= flag_cutoff
    }
    return state


def _meter_snapshot(state: BodyState) -> dict[str, float]:
    """Return the displayed meters as a plain dict."""
    return {
        "hunger": state.hunger,
        "thirst": state.thirst,
        "bladder": state.bladder,
        "cleanliness": state.cleanliness,
        "oral": state.oral,
        "fatigue": state.fatigue,
        "desire": state.desire,
    }


def apply_action(
    state: BodyState,
    action: str,
    now: datetime,
    profile: Profile,
    settings: Settings,
) -> ActionResult:
    """Apply one deliberate action and report what changed."""
    if action not in ACTION_LABELS:
        raise ValueError(f"unknown action: {action}")
    if action in ("shower", "brush") and not settings.enable_hygiene:
        raise ValueError("hygiene module is disabled")
    if action in ("intimacy", "release") and not (
        profile.adult and settings.enable_intimate
    ):
        raise ValueError("intimacy is only available for adult personas")

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
    elif action == "snack":
        state.stomach = _clamp(state.stomach + 22.0 / const.stomach_capacity)
        state.gut = _clamp(state.gut + GUT_RESIDUE_SNACK)
        state.glucose = _clamp(state.glucose + 12.0)
        state.hydration = _clamp(state.hydration + 1.0)
        state.oral = _clamp(state.oral + 3.0)
        state.last_eat_at = now_ts
        state.last_meal_kind = "snack"
        state.last_meal_size = 22.0
    elif action == "drink":
        state.hydration = _clamp(state.hydration + 32.0)
        state.urine = _clamp(state.urine + 12.0)
        state.last_drink_at = now_ts
        state.last_drink_ml = 300.0
    elif action == "shower":
        state.skin = 2.0
        state.sleep_debt = _clamp(state.sleep_debt - 0.3, 0.0, 24.0)
        state.last_shower_at = now_ts
    elif action == "brush":
        state.oral = 2.0
        state.last_brush_at = now_ts
    elif action == "toilet":
        if state.bowel >= 20.0:
            state.gut = max(0.0, state.gut - 85.0)
            state.bowel = _clamp((state.gut - GUT_URGE_START) * GUT_URGE_GAIN)
            state.last_bowel_at = now_ts
        state.urine = 2.0
        state.last_toilet_at = now_ts
    elif action == "nap":
        state.sleep_debt = _clamp(state.sleep_debt - 2.5, 0.0, 24.0)
        state.last_nap_at = now_ts
    elif action == "supplies":
        state.pads += PAD_RESTOCK
        state.last_supply_at = now_ts
    elif action == "intimacy":
        cycle = cycle_view(state, now, profile, settings)
        can_conceive = (
            settings.enable_pregnancy
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
                _add_event(
                    state,
                    now,
                    "conception",
                    "怀孕了",
                    key=f"conceived:{int(now_ts)}",
                )
        state.desire = 0.0
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
        state.desire = 0.0
        state.last_self_at = now_ts
        if profile.gender == "male":
            state.refractory_until = (
                now_ts
                + (
                    1.0
                    + _roll(profile.persona_id, str(now_ts), "self-refractory") * 2.0
                )
                * 3600
            )

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
        "hunger",
        "thirst",
        "bladder",
        "cleanliness",
        "oral",
        "fatigue",
        "desire",
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
