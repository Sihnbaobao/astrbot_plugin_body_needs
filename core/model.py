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
HYDRATION_USE_AWAKE = 4.2
HYDRATION_USE_SLEEP = 1.6
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
HAIR_TRIM_AT = 82.0
NAIL_TRIM_AT = 76.0
# A persona only looks after herself once nobody has interacted for a while.
# A long absence still has to leave the cycle in the right place, so the
# simulation window is a year; beyond 60 days it takes coarser steps.
MAX_SIM_DAYS = 365
LONG_GAP_DAYS = 60

# Sleep is a two-process model: debt is the homeostatic pressure, circadian
# alertness is the clock, and the configured window only anchors the habitual
# bedtime. She keeps her own hours, because nothing in her day is scheduled.
BED_EARLY_SECONDS = 3600.0
BED_LATE_SECONDS = 3 * 3600.0
CHRONOTYPE_SPAN_SECONDS = 2700.0
DAILY_BED_SPAN_SECONDS = 2100.0
SLEEP_DURATION_JITTER = 0.9
SLEEP_DURATION_MIN = 5.5
OVERTIRED_DEBT = 14.0
# Even when something is more interesting than sleep, this much debt wins.
ENGAGEMENT_FORCE_DEBT = 17.0
SLEEP_QUIET_SECONDS = 1500.0
# One arousal roll per bucket, so the odds do not depend on how often the
# plugin happens to advance (a page poll every few seconds is not a fair die).
RELEASE_BUCKET_HOURS = 0.25
NAP_CHANCE = 0.5
NAP_EARLIEST_HOUR = 12
NAP_LATEST_HOUR = 17
NAP_MIN_DEBT = 3.0
NAP_MIN_HOURS = 0.35
NAP_MAX_HOURS = 1.0
NAP_COOLDOWN_SECONDS = 4 * 3600.0
# With nothing on her calendar, her rhythm slowly slides later: a free-running
# bit every day, plus inertia from last night's late or early bedtime. Morning
# light and exhaustion pull it back, and the phase is capped so she stays a
# person rather than drifting a whole day around.
SLEEP_PHASE_MIN = -4 * 3600.0
SLEEP_PHASE_MAX = 9 * 3600.0
FREERUN_BASE_SECONDS = 360.0
FREERUN_SPREAD_SECONDS = 540.0
LATE_BED_INERTIA = 0.45
EARLY_BED_PULL = 0.35
MORNING_LIGHT_PULL = 240.0
# Where the free-running drift sits comfortably: a late-night person who
# notices the slide and tries to pull it back, usually landing around
# midnight-to-three with a late morning. Past this the drift fades out and the
# early-night attempts get stronger, so she never runs away a whole day.
SLEEP_PHASE_COMFORT = 3600.0
EARLY_NIGHT_CHANCE = 0.7
EARLY_NIGHT_MAX = 3600.0

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
    skin_oil_rate: float = 1.0
    skin_clarity: float = 70.0


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
        skin_oil_rate=0.65 + 0.7 * _roll(persona_id, "body", "skin-oil"),
        skin_clarity=55.0 + 35.0 * _roll(persona_id, "body", "skin-clarity"),
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
    temperature: float = 36.6
    sweat: float = 0.0
    heart_rate: float = 72.0
    breath_rate: float = 15.0
    pain: float = 0.0
    pain_site: str = ""
    illness: str = ""
    illness_severity: float = 0.0
    illness_started_at: float = 0.0
    allergy: float = 0.0
    migraine_until: float = 0.0
    urine_color: int = 1
    dry_mouth: float = 0.0
    nasal: float = 0.0
    ambient_temp: float = 22.0
    weather: str = "clear"
    clothing_warmth: float = 55.0
    sleep_quality: float = 78.0
    dream: str = ""
    iron: float = 72.0
    craving: str = ""
    eye_strain: float = 12.0
    skin_oil: float = 15.0
    skin_quality: float = 75.0
    scent: float = 20.0
    foot_scent: float = 8.0
    period_scent: float = 0.0
    # Scent is split by source instead of being one undifferentiated meter:
    # scalp oil, breath, and (for adults) the afterglow of intimacy each have
    # their own build-up and wash-off curve.
    scalp_scent: float = 10.0
    breath_scent: float = 6.0
    intimate_scent: float = 0.0
    # Slow organ-load proxies. They are fictional summaries, not clinical
    # measurements: the kidney follows water/sodium/caffeine, the liver follows
    # diet and digestion, and the uterus follows the cycle and cramping.
    kidney_load: float = 8.0
    liver_load: float = 12.0
    uterine_congestion: float = 10.0
    location: str = "home"
    out_until: float = 0.0
    caffeine: float = 0.0
    stress: float = 20.0
    sedentary: float = 30.0
    last_exercise_at: float = 0.0
    sodium: float = 20.0
    leg_fatigue: float = 0.0
    indigestion: float = 0.0
    fiber: float = 50.0
    diet_quality: float = 55.0
    fitness: float = 40.0
    diet_long: float = 55.0
    daylight: float = 12.0
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
    ovulation_pain_until: float = 0.0
    ovulation_side: str = ""
    hot_flash_until: float = 0.0
    ovulation_spotting_until: float = 0.0
    pads_today: int = 0
    pads_day: str = ""
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
    sleeping: bool = False
    sleep_since: float = 0.0
    sleep_wake_at: float = 0.0
    sleep_kind: str = ""
    night_wakes: int = 0
    night_wake_minutes: float = 0.0
    sleep_awake_until: float = 0.0
    last_night_wake_at: float = 0.0
    bed_since: float = 0.0
    sleep_plan_at: float = 0.0
    last_wake_at: float = 0.0
    nap_day: str = ""
    busy_until: float = 0.0
    busy_kind: str = ""
    sleep_phase: float = 0.0
    sleep_anchor_at: float = 0.0
    sleep_intent: float = 0.0
    missed: list[dict[str, Any]] = field(default_factory=list)
    last_release_at: float = 0.0
    last_self_at: float = 0.0
    intimacy_count: int = 0
    insemination_count: int = 0
    last_intimacy_at: float = 0.0
    last_insemination_at: float = 0.0
    wetness: float = 0.0
    away_until: float = 0.0
    away_reason: str = ""
    release_until: float = 0.0
    release_kind: str = ""
    care_until: float = 0.0
    care_kind: str = ""
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
            "temperature": self.temperature,
            "sweat": self.sweat,
            "heart_rate": self.heart_rate,
            "breath_rate": self.breath_rate,
            "pain": self.pain,
            "pain_site": self.pain_site,
            "illness": self.illness,
            "illness_severity": self.illness_severity,
            "illness_started_at": self.illness_started_at,
            "allergy": self.allergy,
            "migraine_until": self.migraine_until,
            "urine_color": self.urine_color,
            "dry_mouth": self.dry_mouth,
            "nasal": self.nasal,
            "ambient_temp": self.ambient_temp,
            "weather": self.weather,
            "clothing_warmth": self.clothing_warmth,
            "sleep_quality": self.sleep_quality,
            "dream": self.dream,
            "iron": self.iron,
            "craving": self.craving,
            "eye_strain": self.eye_strain,
            "skin_oil": self.skin_oil,
            "skin_quality": self.skin_quality,
            "scent": self.scent,
            "foot_scent": self.foot_scent,
            "period_scent": self.period_scent,
            "scalp_scent": self.scalp_scent,
            "breath_scent": self.breath_scent,
            "intimate_scent": self.intimate_scent,
            "kidney_load": self.kidney_load,
            "liver_load": self.liver_load,
            "uterine_congestion": self.uterine_congestion,
            "location": self.location,
            "out_until": self.out_until,
            "caffeine": self.caffeine,
            "stress": self.stress,
            "sedentary": self.sedentary,
            "last_exercise_at": self.last_exercise_at,
            "sodium": self.sodium,
            "leg_fatigue": self.leg_fatigue,
            "indigestion": self.indigestion,
            "fiber": self.fiber,
            "diet_quality": self.diet_quality,
            "fitness": self.fitness,
            "diet_long": self.diet_long,
            "daylight": self.daylight,
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
            "ovulation_pain_until": self.ovulation_pain_until,
            "ovulation_side": self.ovulation_side,
            "hot_flash_until": self.hot_flash_until,
            "ovulation_spotting_until": self.ovulation_spotting_until,
            "pads_today": self.pads_today,
            "pads_day": self.pads_day,
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
            "sleeping": self.sleeping,
            "sleep_since": self.sleep_since,
            "sleep_wake_at": self.sleep_wake_at,
            "sleep_kind": self.sleep_kind,
            "night_wakes": self.night_wakes,
            "night_wake_minutes": self.night_wake_minutes,
            "sleep_awake_until": self.sleep_awake_until,
            "last_night_wake_at": self.last_night_wake_at,
            "bed_since": self.bed_since,
            "sleep_plan_at": self.sleep_plan_at,
            "last_wake_at": self.last_wake_at,
            "nap_day": self.nap_day,
            "busy_until": self.busy_until,
            "busy_kind": self.busy_kind,
            "sleep_phase": self.sleep_phase,
            "sleep_anchor_at": self.sleep_anchor_at,
            "sleep_intent": self.sleep_intent,
            "missed": list(self.missed),
            "last_release_at": self.last_release_at,
            "last_self_at": self.last_self_at,
            "intimacy_count": self.intimacy_count,
            "insemination_count": self.insemination_count,
            "last_intimacy_at": self.last_intimacy_at,
            "last_insemination_at": self.last_insemination_at,
            "wetness": self.wetness,
            "away_until": self.away_until,
            "away_reason": self.away_reason,
            "release_until": self.release_until,
            "release_kind": self.release_kind,
            "care_until": self.care_until,
            "care_kind": self.care_kind,
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
            "temperature",
            "sweat",
            "heart_rate",
            "breath_rate",
            "pain",
            "illness_severity",
            "illness_started_at",
            "ambient_temp",
            "clothing_warmth",
            "sleep_quality",
            "iron",
            "eye_strain",
            "skin_oil",
            "skin_quality",
            "scent",
            "foot_scent",
            "period_scent",
            "scalp_scent",
            "breath_scent",
            "intimate_scent",
            "kidney_load",
            "liver_load",
            "uterine_congestion",
            "out_until",
            "caffeine",
            "stress",
            "sedentary",
            "last_exercise_at",
            "sodium",
            "leg_fatigue",
            "indigestion",
            "fiber",
            "diet_quality",
            "fitness",
            "diet_long",
            "daylight",
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
        state.illness = str(data.get("illness") or "")
        state.pain_site = str(data.get("pain_site") or "")
        state.weather = str(data.get("weather") or "clear")
        state.dream = str(data.get("dream") or "")
        state.craving = str(data.get("craving") or "")
        state.temperature = _clamp(state.temperature, 34.0, 42.0)
        state.sweat = _clamp(state.sweat)
        state.heart_rate = _clamp(state.heart_rate, 40.0, 180.0)
        state.breath_rate = _clamp(state.breath_rate, 8.0, 40.0)
        state.pain = _clamp(state.pain)
        state.illness_severity = _clamp(state.illness_severity)
        state.allergy = _clamp(_number(data.get("allergy"), 0.0))
        state.migraine_until = _number(data.get("migraine_until"), 0.0)
        state.urine_color = max(0, min(3, int(_number(data.get("urine_color"), 1))))
        state.dry_mouth = _clamp(_number(data.get("dry_mouth"), 0.0))
        state.nasal = _clamp(_number(data.get("nasal"), 0.0))
        state.ambient_temp = _clamp(state.ambient_temp, -20.0, 45.0)
        state.clothing_warmth = _clamp(state.clothing_warmth)
        state.sleep_quality = _clamp(state.sleep_quality)
        state.iron = _clamp(state.iron)
        state.eye_strain = _clamp(state.eye_strain)
        state.skin_oil = _clamp(state.skin_oil)
        state.skin_quality = _clamp(state.skin_quality)
        state.scalp_scent = _clamp(state.scalp_scent)
        state.breath_scent = _clamp(state.breath_scent)
        state.intimate_scent = _clamp(state.intimate_scent)
        state.kidney_load = _clamp(state.kidney_load)
        state.liver_load = _clamp(state.liver_load)
        state.uterine_congestion = _clamp(state.uterine_congestion)
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
        state.ovulation_pain_until = _number(data.get("ovulation_pain_until"), 0.0)
        state.ovulation_side = str(data.get("ovulation_side") or "")
        state.hot_flash_until = _number(data.get("hot_flash_until"), 0.0)
        state.ovulation_spotting_until = _number(
            data.get("ovulation_spotting_until"), 0.0
        )
        state.pads_today = max(0, int(_number(data.get("pads_today"), 0)))
        state.pads_day = str(data.get("pads_day") or "")
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
        state.release_until = _number(data.get("release_until"), 0.0)
        state.release_kind = str(data.get("release_kind") or "")
        state.care_until = _number(data.get("care_until"), 0.0)
        state.care_kind = str(data.get("care_kind") or "")
        state.sleeping = bool(data.get("sleeping"))
        state.sleep_since = _number(data.get("sleep_since"))
        state.sleep_wake_at = _number(data.get("sleep_wake_at"))
        state.sleep_kind = str(data.get("sleep_kind") or "")
        state.night_wakes = int(_number(data.get("night_wakes"), 0.0))
        state.night_wake_minutes = _number(data.get("night_wake_minutes"), 0.0)
        state.sleep_awake_until = _number(data.get("sleep_awake_until"), 0.0)
        state.last_night_wake_at = _number(data.get("last_night_wake_at"), 0.0)
        state.bed_since = _number(data.get("bed_since"))
        state.sleep_plan_at = _number(data.get("sleep_plan_at"))
        state.last_wake_at = _number(data.get("last_wake_at"))
        state.nap_day = str(data.get("nap_day") or "")
        state.location = str(data.get("location") or "home")
        state.busy_until = _number(data.get("busy_until"))
        state.busy_kind = str(data.get("busy_kind") or "")
        state.sleep_phase = _clamp(
            _number(data.get("sleep_phase")),
            SLEEP_PHASE_MIN,
            SLEEP_PHASE_MAX,
        )
        state.sleep_anchor_at = _number(data.get("sleep_anchor_at"))
        state.sleep_intent = _number(data.get("sleep_intent"))
        missed = data.get("missed")
        if isinstance(missed, list):
            state.missed = [
                {
                    "at": _number(item.get("at")),
                    "umo": str(item.get("umo") or ""),
                    "text": str(item.get("text") or "")[:80],
                }
                for item in missed
                if isinstance(item, dict) and item.get("at")
            ][-12:]
        if not state.sleeping:
            state.sleep_since = 0.0
            state.sleep_wake_at = 0.0
            state.sleep_kind = ""
        if not state.sleeping and not state.bed_since:
            state.sleep_kind = ""
        state.pad_hours = max(0.0, _number(data.get("pad_hours"), 0.0))
        state.last_pad_day = str(data.get("last_pad_day") or "")
        state.last_supply_at = _number(data.get("last_supply_at"), 0.0)
        state.last_meal_kind = str(data.get("last_meal_kind") or "")
        events = data.get("events")
        if isinstance(events, list):
            state.events = [
                item for item in events if isinstance(item, dict) and item.get("text")
            ][:100]
            # Older files still carry the previous wording; migrate on load so
            # the prompt reads naturally even before the event ages out.
            for item in state.events:
                wording = str(item.get("text") or "")
                if "自己解决了一次" in wording:
                    item["text"] = wording.replace("自己解决了一次", "自慰了一次")
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
    # Cervical mucus and the surrounding reproductive-tract state are kept
    # separate from the old 0-3 score: volume, texture, colour, stretchiness
    # and cervix position together describe what the body is actually doing.
    mucus_volume: float = 0.0
    mucus_texture: str = "dry"
    mucus_color: str = "clear"
    mucus_stretch: float = 0.0
    cervix_position: str = "low"
    cervix_firmness: str = "firm"
    cervix_open: bool = False
    vulva: str = "comfortable"
    pelvic_pressure: float = 0.0
    breast_fullness: float = 0.0
    follicle: float = 0.0
    endocrine_maturity: float = 1.0
    puberty_stage: str = "adult"
    bbt: float = 36.3
    ovulation_pain: bool = False
    ovulation_side: str = ""
    ovulation_delay: int = 0
    clots: int = 0
    spotting: bool = False
    perimenopausal: bool = False


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
    "release": "自慰了一次",
    "exercise": "活动了一会儿",
    "medicine": "吃了药",
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


@dataclass(frozen=True)
class SleepView:
    """Current sleep status, used by prompts, cards and other plugins.

    Attributes:
        asleep: Whether she is actually asleep.
        in_bed: Whether she is lying in bed but still awake.
        kind: "night" or "nap" while asleep or in bed, otherwise empty.
        since: Unix timestamp of falling asleep.
        bed_at: Unix timestamp of getting into bed.
        wake_at: Expected Unix timestamp of waking.
        slept_hours: Hours slept in the current sleep so far.
        line: One short human sentence, empty when nothing is notable.
        just_woke: Whether she woke within the last waking window.
        woke_at: Unix timestamp of the last wake-up.
        bedtime: Expected Unix timestamp of the next bedtime.
        nap_at: Expected Unix timestamp of today's nap, 0 when none.
    """

    asleep: bool
    in_bed: bool
    kind: str
    since: float
    bed_at: float
    wake_at: float
    slept_hours: float
    line: str
    just_woke: bool
    woke_at: float
    bedtime: float
    nap_at: float
    night_wakes: int = 0
    night_wake_minutes: float = 0.0


def _midnight(day: date, tzinfo: Any) -> datetime:
    """Return local midnight for one calendar day."""
    return datetime.combine(day, time(0, 0), tzinfo=tzinfo)


def _bed_offset(profile: Profile, settings: Settings, day: date) -> float:
    """Return the habitual bedtime as seconds after local midnight.

    The configured sleep window supplies the anchor, a stable chronotype moves
    it once per persona, and a small daily jitter keeps two nights apart.
    """
    window = parse_sleep_window(settings.sleep_window)
    start = window[0] if window else time(0, 0)
    base = start.hour * 3600.0 + start.minute * 60.0
    chronotype = (
        (_roll(profile.persona_id, "sleep", "chronotype") - 0.5)
        * 2
        * CHRONOTYPE_SPAN_SECONDS
    )
    daily = (
        (_roll(profile.persona_id, day.isoformat(), "bedtime") - 0.5)
        * 2
        * DAILY_BED_SPAN_SECONDS
    )
    return base + chronotype + daily


def _night_points(
    profile: Profile,
    settings: Settings,
    moment: datetime,
    phase: float = 0.0,
) -> tuple[float, float, float]:
    """Return (earliest bedtime, latest bedtime, anchor) for the nearby night.

    Args:
        profile: Persona profile.
        settings: Plugin settings; the window is the base anchor.
        moment: Instant to place the night around.
        phase: Current sleep-phase drift in seconds; positive means she is
            living later than the nominal anchor.
    """
    now_ts = moment.timestamp()
    nearest: tuple[float, float, float] | None = None
    for offset in (-1, 0, 1):
        day = moment.date() + timedelta(days=offset)
        anchor = (
            _midnight(day, moment.tzinfo).timestamp()
            + _bed_offset(profile, settings, day)
            + phase
        )
        earliest = anchor - BED_EARLY_SECONDS
        latest = anchor + BED_LATE_SECONDS
        if latest < now_ts:
            continue
        if earliest <= now_ts:
            return earliest, latest, anchor
        if nearest is None or earliest < nearest[0]:
            nearest = (earliest, latest, anchor)
    if nearest is not None:
        return nearest
    day = moment.date() + timedelta(days=1)
    anchor = (
        _midnight(day, moment.tzinfo).timestamp()
        + _bed_offset(profile, settings, day)
        + phase
    )
    return anchor - BED_EARLY_SECONDS, anchor + BED_LATE_SECONDS, anchor


def _nap_plan(
    profile: Profile,
    settings: Settings,
    moment: datetime,
    phase: float = 0.0,
) -> tuple[float, float]:
    """Return today's nap moment and length in hours, or (0, 0)."""
    if not settings.enable_nap:
        return 0.0, 0.0
    day = moment.date().isoformat()
    if _roll(profile.persona_id, day, "nap") >= NAP_CHANCE:
        return 0.0, 0.0
    start = _midnight(moment.date(), moment.tzinfo).timestamp()
    start += (
        NAP_EARLIEST_HOUR
        + _roll(profile.persona_id, day, "nap-hour")
        * (NAP_LATEST_HOUR - NAP_EARLIEST_HOUR)
    ) * 3600.0
    # A late-sleeping persona naps late as well.
    start += _clamp(phase, 0.0, SLEEP_PHASE_MAX)
    length = NAP_MIN_HOURS + _roll(profile.persona_id, day, "nap-length") * (
        NAP_MAX_HOURS - NAP_MIN_HOURS
    )
    return start, length


def _planned_bed(
    state: BodyState,
    moment: datetime,
    profile: Profile,
    settings: Settings,
    busy_until: float,
) -> float:
    """Return the instant she means to get into bed tonight.

    The habitual anchor is the default, so even a persona who is not sleepy
    goes to bed around her usual time. Being very sleepy pulls it earlier,
    while being caught up in something (watching videos, talking) pushes it
    later up to a cap, unless she is so far behind that she simply collapses.
    """
    _earliest, _latest, anchor = _night_points(
        profile, settings, moment, state.sleep_phase
    )
    planned = anchor
    if state.sleep_debt >= OVERTIRED_DEBT:
        return moment.timestamp()
    if state.sleep_debt >= 10.0:
        earlier = moment.timestamp() + max(
            300.0, (40.0 - state.sleep_debt * 2.0) * 60.0
        )
        planned = min(planned, earlier)
    # Noticing that it has slid, she tells herself to go to bed early. It works
    # more often than not, but the phone, a video or a chat can still win.
    state.sleep_intent = 0.0
    if state.sleep_phase > SLEEP_PHASE_COMFORT:
        day = moment.date().isoformat()
        if _roll(profile.persona_id, day, "early-night") < EARLY_NIGHT_CHANCE:
            state.sleep_intent = min(
                EARLY_NIGHT_MAX,
                (state.sleep_phase - SLEEP_PHASE_COMFORT) * 0.5,
            )
            planned -= state.sleep_intent
    if busy_until > moment.timestamp() and state.sleep_debt < ENGAGEMENT_FORCE_DEBT:
        delay = (
            300.0 + _roll(profile.persona_id, moment.date().isoformat(), "busy") * 900.0
        )
        planned = max(planned, busy_until + delay)
        planned = min(planned, anchor + BED_LATE_SECONDS)
    return planned


def _latency_minutes(state: BodyState, profile: Profile, day: str) -> float:
    """Return how long she lies awake after getting into bed."""
    if state.sleep_debt >= OVERTIRED_DEBT:
        base = 3.0
    else:
        base = 8.0 + max(0.0, 12.0 - state.sleep_debt) * 3.5
    # Progesterone makes the luteal nights lighter and slower to arrive; so do
    # caffeine and a body that is still wound up.
    base += state.progesterone * 0.05 + state.caffeine * 0.08 + state.stress * 0.05
    return base + _roll(profile.persona_id, day, "latency") * 12.0


def _night_hours(state: BodyState, profile: Profile, day: str) -> float:
    """Return how long the coming night should last."""
    hours = 7.2 + min(1.3, max(0.0, state.sleep_debt - 6.0) * 0.15)
    if state.sleep_quality and state.sleep_quality < 55.0:
        hours += 0.5
    hours += (_roll(profile.persona_id, day, "duration") - 0.5) * SLEEP_DURATION_JITTER
    return max(SLEEP_DURATION_MIN, min(8.8, hours))


def _add_sleep_event(
    state: BodyState, moment: datetime, text: str, key: str = ""
) -> None:
    """Record one sleep event, deduplicated by key when given."""
    if key and state.flags.get(key):
        return
    state.events.append({"at": moment.isoformat(), "kind": "sleep", "text": text})
    if key:
        state.flags[key] = moment.timestamp()


def _begin_bed(
    state: BodyState, moment: datetime, reason: str, planned: float = 0.0
) -> None:
    """Move her into bed: awake, but not asleep yet.

    Args:
        state: Body state being advanced.
        moment: When she actually got into bed.
        reason: Short line for the event log.
        planned: When she meant to get into bed; kept so the next night can
            learn from how late or early this one was.
    """
    state.bed_since = moment.timestamp()
    state.sleep_plan_at = planned or moment.timestamp()
    _add_sleep_event(
        state,
        moment,
        reason,
        key=f"bed:{moment.date().isoformat()}",
    )


def _begin_sleep(state: BodyState, moment: datetime, kind: str) -> None:
    """Fall asleep for real."""
    stamp = moment.timestamp()
    state.sleeping = True
    state.sleep_kind = kind
    state.sleep_since = stamp
    state.sleep_wake_at = 0.0
    state.sleep_awake_until = 0.0
    state.night_wakes = 0
    state.night_wake_minutes = 0.0
    state.last_night_wake_at = 0.0
    if kind == "nap":
        state.nap_day = moment.date().isoformat()


def _update_phase(
    state: BodyState,
    moment: datetime,
    profile: Profile,
) -> None:
    """Slide her rhythm from one night to the next.

    Two things move the phase: a small persona-stable free-run (nothing on her
    calendar, so later is easy), and inertia from last night's actual bedtime
    compared with the plan. Morning light and a heavy sleep debt pull it back,
    so the drift slows instead of running away forever.

    Args:
        state: Body state that just woke up.
        moment: Wake-up instant.
        profile: Persona used as the stable seed.
    """
    anchor = state.sleep_anchor_at or state.sleep_plan_at
    if anchor and state.bed_since:
        overshoot = state.bed_since - anchor
        if overshoot > 1800.0:
            state.sleep_phase += min(1800.0, (overshoot - 1800.0) * LATE_BED_INERTIA)
    # Trying for an early night counts even when she still lands near her usual
    # time: the intention itself is what pulls the rhythm back a little.
    if state.sleep_intent > 0:
        state.sleep_phase -= min(1800.0, state.sleep_intent * 0.4)
        state.sleep_intent = 0.0
    free = _roll(profile.persona_id, "sleep", "freerun")
    # The further past her comfortable zone, the weaker the free-running push:
    # noticing it and going to bed early should actually be able to pull back.
    late = max(0.0, state.sleep_phase - SLEEP_PHASE_COMFORT)
    scale = max(0.0, 1.0 - late / (4 * 3600.0))
    state.sleep_phase += (FREERUN_BASE_SECONDS + free * FREERUN_SPREAD_SECONDS) * scale
    if state.sleep_debt >= 10.0:
        state.sleep_phase -= 300.0
    if 5 <= moment.hour <= 9 and state.sleep_debt <= 8.0:
        # Morning light is the strongest anchor a free day still has.
        state.sleep_phase -= MORNING_LIGHT_PULL
    state.sleep_phase = _clamp(state.sleep_phase, SLEEP_PHASE_MIN, SLEEP_PHASE_MAX)


def _wake_up(
    state: BodyState, moment: datetime, profile: Profile | None = None
) -> None:
    """End the current sleep and record the wake-up."""
    kind = state.sleep_kind
    slept = (
        max(0.0, moment.timestamp() - state.sleep_since) if state.sleep_since else 0.0
    )
    if kind != "nap":
        slept = max(0.0, slept - state.night_wake_minutes * 60.0)
    state.sleeping = False
    state.sleep_since = 0.0
    state.sleep_wake_at = 0.0
    state.sleep_kind = ""
    state.bed_since = 0.0
    state.last_wake_at = moment.timestamp()
    if kind != "nap" and profile is not None:
        _update_phase(state, moment, profile)
    if kind == "nap":
        state.last_nap_at = moment.timestamp()
        _add_sleep_event(
            state,
            moment,
            "午睡醒了，人清醒了点",
            key=f"napwake:{int(moment.timestamp())}",
        )
        return
    hours = slept / 3600.0
    text = f"睡醒了，睡了 {hours:.1f} 小时" if hours >= 1.0 else "睡醒了"
    penalty = min(18.0, state.night_wakes * 3.0 + state.night_wake_minutes * 0.35)
    if penalty:
        state.sleep_quality = _clamp(state.sleep_quality - penalty)
    if state.night_wakes:
        text += f"，夜里醒了 {state.night_wakes} 次"
    _add_sleep_event(state, moment, text, key=f"wake:{moment.date().isoformat()}")


def _night_wake_reason(
    state: BodyState,
    moment: datetime,
    chunk_end: datetime,
    profile: Profile,
) -> str:
    """Decide whether a sleeping body wakes briefly during the night.

    Args:
        state: Body state being advanced.
        moment: Start of the current simulation step.
        chunk_end: End of the current simulation step.
        profile: Persona profile, used for age and pregnancy effects.

    Returns:
        "toilet" for a bladder wake, "wake" for a spontaneous one, or "".
    """
    begin = moment.timestamp()
    if state.night_wakes >= 3:
        return ""
    if state.last_night_wake_at and begin - state.last_night_wake_at < 45 * 60:
        return ""
    if begin - state.sleep_since < 75 * 60:
        return ""
    if state.sleep_wake_at and state.sleep_wake_at - begin < 40 * 60:
        return ""
    step_hours = max(0.05, (chunk_end.timestamp() - begin) / 3600.0)
    age = profile.physio_age or profile.age
    age_factor = 1.25 if age >= 45 else 1.0
    if profile.gender == "female" and getattr(profile, "pregnant_weeks", 0) >= 20:
        age_factor *= 1.5
    caffeine = 1.0 + state.caffeine / 120.0
    # A full bladder after a few hours is the most common reason; it gets
    # likelier with caffeine, age and late pregnancy.
    if state.urine >= 36.0 and begin - state.last_toilet_at > 2.5 * 3600:
        rate = (0.12 + max(0.0, state.urine - 30.0) * 0.025) * caffeine * age_factor
        if _roll(profile.persona_id, str(int(begin // 900)), "night-toilet") < (
            1.0 - math.exp(-rate * step_hours)
        ):
            return "toilet"
    # Spontaneous arousals: rare in the first cycles, more common later.
    slept_hours = (begin - state.sleep_since) / 3600.0
    rate = (
        (0.05 if slept_hours < 3.0 else 0.16) * (1.0 + state.stress / 150.0) * caffeine
    )
    if _roll(profile.persona_id, str(int(begin // 900)), "night-wake") < (
        1.0 - math.exp(-rate * step_hours)
    ):
        return "wake"
    return ""


def _start_night_wake(
    state: BodyState,
    moment: datetime,
    reason: str,
    profile: Profile,
) -> None:
    """Begin a short awakening inside the night and record it.

    Args:
        state: Body state being advanced.
        moment: When the awakening starts.
        reason: "toilet" or "wake".
        profile: Persona profile, used for stable random durations.
    """
    begin = moment.timestamp()
    if reason == "toilet":
        minutes = (
            5.0 + _roll(profile.persona_id, str(int(begin)), "night-toilet-len") * 9.0
        )
        state.urine = 4.0
        state.last_toilet_at = begin
        text = "半夜起来上了个厕所"
    else:
        minutes = (
            2.0 + _roll(profile.persona_id, str(int(begin)), "night-wake-len") * 8.0
        )
        text = "夜里醒了一会儿" if minutes >= 4.0 else "翻了个身"
    state.sleep_awake_until = begin + minutes * 60.0
    state.night_wakes += 1
    state.night_wake_minutes += minutes
    state.last_night_wake_at = begin
    if reason == "toilet" or minutes >= 4.0:
        _add_sleep_event(
            state,
            moment,
            text,
            key=f"night-wake:{int(begin)}",
        )


def _sleep_tick(
    state: BodyState,
    cursor: datetime,
    chunk_end: datetime,
    profile: Profile,
    settings: Settings,
    const: Constitution,
    busy_until: float,
) -> float:
    """Advance the sleep state machine over one step and return asleep seconds."""
    if not settings.enable_sleep:
        state.sleeping = False
        state.sleep_since = 0.0
        state.sleep_wake_at = 0.0
        state.sleep_kind = ""
        state.bed_since = 0.0
        return 0.0
    begin = cursor.timestamp()
    end = chunk_end.timestamp()
    if state.sleeping:
        wake_at = state.sleep_wake_at or (begin + 60.0)
        if state.sleep_awake_until > begin:
            if chunk_end.timestamp() < state.sleep_awake_until:
                return 0.0
            state.sleep_awake_until = 0.0
            return 0.0
        if end < wake_at:
            if state.sleep_kind == "night":
                reason = _night_wake_reason(state, cursor, chunk_end, profile)
                if reason:
                    _start_night_wake(state, cursor, reason, profile)
                    return 0.0
            return max(0.0, end - begin)
        _wake_up(
            state,
            datetime.fromtimestamp(wake_at, cursor.tzinfo),
            profile,
        )
        return max(0.0, wake_at - begin)
    if state.bed_since:
        latency = _latency_minutes(state, profile, cursor.date().isoformat()) * 60.0
        sleep_at = state.bed_since + latency
        if end < sleep_at:
            return 0.0
        _begin_sleep(
            state,
            datetime.fromtimestamp(sleep_at, cursor.tzinfo),
            state.sleep_kind or "night",
        )
        if state.sleep_kind == "night":
            hours = _night_hours(state, profile, cursor.date().isoformat())
            state.sleep_wake_at = sleep_at + hours * 3600.0
        return max(0.0, end - max(begin, sleep_at))
    # Awake. An exhausted persona falls asleep even mid-conversation; anyone
    # else is kept up by the chat and by whatever she is caught up in.
    if (
        state.sleep_debt < OVERTIRED_DEBT
        and state.last_active_at
        and begin < state.last_active_at + SLEEP_QUIET_SECONDS
    ):
        return 0.0
    today = cursor.date().isoformat()
    if (
        settings.enable_nap
        and state.nap_day != today
        and state.sleep_debt >= NAP_MIN_DEBT
        and busy_until <= begin
        and state.sleep_debt < OVERTIRED_DEBT
    ):
        nap_at, nap_hours = _nap_plan(profile, settings, cursor, state.sleep_phase)
        if (
            nap_at
            and begin >= nap_at
            and end <= nap_at + 4 * 3600.0
            and begin - state.last_nap_at > NAP_COOLDOWN_SECONDS
        ):
            _begin_sleep(state, cursor, "nap")
            state.sleep_wake_at = begin + max(0.3, nap_hours) * 3600.0
            _add_sleep_event(
                state,
                cursor,
                "困了，去眯了一会儿",
                key=f"nap:{today}",
            )
            return max(0.0, min(end, state.sleep_wake_at) - begin)
    planned = _planned_bed(state, cursor, profile, settings, busy_until)
    if begin >= planned:
        reason = "到点躺下了"
        if state.sleep_debt >= 12.0:
            reason = "困得不行，早点躺下了"
        elif busy_until > begin:
            reason = "手上正忙着，忙完才躺下"
        state.sleep_anchor_at = _night_points(
            profile, settings, cursor, state.sleep_phase
        )[2]
        _begin_bed(state, cursor, reason, planned)
        state.sleep_kind = "night"
        return 0.0
    return 0.0


def sleep_view(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
) -> SleepView:
    """Describe whether she is asleep, in bed, or due to sleep soon."""
    if not settings.enable_sleep:
        return SleepView(
            asleep=False,
            in_bed=False,
            kind="",
            since=0.0,
            bed_at=0.0,
            wake_at=0.0,
            slept_hours=0.0,
            line="",
            just_woke=False,
            woke_at=state.last_wake_at,
            bedtime=0.0,
            nap_at=0.0,
        )
    stamp = now.timestamp()
    wake_at = state.sleep_wake_at
    asleep = bool(state.sleeping and (wake_at <= 0 or stamp < wake_at))
    in_bed = bool(not asleep and state.bed_since)
    slept = 0.0
    if asleep and state.sleep_since:
        slept = max(0.0, min(stamp, wake_at) - state.sleep_since) / 3600.0
        slept = max(0.0, slept - state.night_wake_minutes / 60.0)
    _earliest, _latest, bedtime = _night_points(
        profile, settings, now, state.sleep_phase
    )
    nap_at = 0.0
    if settings.enable_nap:
        nap_at, _hours = _nap_plan(profile, settings, now, state.sleep_phase)
    just_woke = bool(state.last_wake_at and 0 < stamp - state.last_wake_at <= 2400)
    line = ""
    if asleep:
        since_clock = datetime.fromtimestamp(state.sleep_since, now.tzinfo)
        wake_clock = datetime.fromtimestamp(wake_at, now.tzinfo) if wake_at else None
        kind = "午睡" if state.sleep_kind == "nap" else "睡"
        line = f"{kind}下了（{since_clock:%H:%M} 睡的"
        line += f"，大概 {wake_clock:%H:%M} 醒）" if wake_clock else "）"
    elif in_bed:
        bed_clock = datetime.fromtimestamp(state.bed_since, now.tzinfo)
        line = f"已经躺下了（{bed_clock:%H:%M} 上床，还没睡着）"
    elif just_woke:
        woke = datetime.fromtimestamp(state.last_wake_at, now.tzinfo)
        line = f"刚睡醒（{woke:%H:%M} 醒的）"
    elif 0 <= bedtime - stamp <= 5400:
        bed = datetime.fromtimestamp(bedtime, now.tzinfo)
        line = f"有点困了（习惯 {bed:%H:%M} 前后睡）"
    return SleepView(
        asleep=asleep,
        in_bed=in_bed,
        kind=state.sleep_kind if (asleep or in_bed) else "",
        since=state.sleep_since if asleep else 0.0,
        bed_at=state.bed_since if in_bed else 0.0,
        wake_at=wake_at if asleep else 0.0,
        slept_hours=slept,
        line=line,
        just_woke=just_woke,
        woke_at=state.last_wake_at,
        bedtime=bedtime,
        nap_at=nap_at,
        night_wakes=state.night_wakes if settings.enable_sleep else 0,
        night_wake_minutes=(state.night_wake_minutes if settings.enable_sleep else 0.0),
    )


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
    """Return the follicular stretch factor; sleep debt, stress and illness delay ovulation."""
    stress = 1.0 + min(0.35, max(0.0, state.sleep_debt - 6.0) / 24.0 * 0.6)
    stress += max(0.0, state.stress - 50.0) / 100.0 * 0.3
    stress += min(0.25, state.illness_severity / 100.0 * 0.5)
    return min(1.8, stress) * (1.0 + _mood_effect(mood)[2])


def _period_days(profile: Profile) -> int:
    """Return the bleeding length in days, clamped below the cycle."""
    length = max(1, int(profile.cycle_length))
    return max(1, min(profile.period_length, length - 1))


def _restart_cycle(state: BodyState, now: datetime, profile: Profile) -> None:
    """Begin a new cycle today and roll whether it will ovulate."""
    state.cycle_start = now.date().isoformat()
    state.cycle_day = 1
    state.ovulation_day = 0
    state.ovulation_pain_until = 0.0
    state.ovulation_side = ""
    state.ovulation_spotting_until = 0.0
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
        if _roll(profile.persona_id, now.date().isoformat(), "ovulation-pain") < 0.35:
            hours = (
                2.0
                + _roll(
                    profile.persona_id, now.date().isoformat(), "ovulation-pain-length"
                )
                * 10.0
            )
            state.ovulation_pain_until = now.timestamp() + hours * 3600.0
            state.ovulation_side = (
                "左"
                if _roll(profile.persona_id, now.date().isoformat(), "ovulation-side")
                < 0.5
                else "右"
            )
        if (
            _roll(profile.persona_id, now.date().isoformat(), "ovulation-spotting")
            < 0.12
        ):
            state.ovulation_spotting_until = (
                now.timestamp()
                + (
                    1.0
                    + _roll(profile.persona_id, now.date().isoformat(), "spotting-len")
                )
                * 86400.0
            )
    if state.ovulation_day and day - state.ovulation_day >= LUTEAL_DAYS:
        _restart_cycle(state, now, profile)
    elif state.anovulatory and day > target:
        _restart_cycle(state, now, profile)


def _endocrine_maturity(age: float | None, persona_id: str) -> tuple[float, str]:
    """Return how far the HPO axis has matured and a coarse stage name.

    The curve is deliberately about development, not sexual activity: it
    describes when the axis starts responding, when menarche arrives, and how
    long it takes to settle into adult regularity.

    Args:
        age: Human-equivalent age, or None when the persona did not state one.
        persona_id: Stable seed for the persona's menarche age.

    Returns:
        A (maturity 0-1, stage) pair.
    """
    if age is None:
        return 1.0, "adult"
    menarche = _menarche_age(persona_id)
    if age < menarche - 2.5:
        value = (age - (menarche - 4.0)) / 2.5
        return _clamp(value, 0.0, 0.55), "prepubertal"
    if age < menarche:
        value = 0.55 + (age - (menarche - 2.5)) / 2.5 * 0.25
        return _clamp(value, 0.55, 0.8), "early"
    if age < 18.0:
        value = 0.8 + (age - menarche) / max(1.0, 18.0 - menarche) * 0.2
        return _clamp(value, 0.8, 1.0), "late"
    return 1.0, "adult"


def _mucus_detail(
    state: BodyState,
    *,
    estrogen: float,
    progesterone: float,
    phase: str,
    flow: int,
    spotting: bool,
    weeks: int,
    maturity: float,
) -> dict[str, Any]:
    """Describe cervical mucus volume, texture, colour and the cervix.

    The old 0-3 score said only "dry to egg-white". A body has more than one
    axis: how much is there, how thick or stretchy it is, what colour it has,
    and whether the cervix sits high, soft and slightly open. All of it follows
    the same estrogen/progesterone curve, so the detail stays consistent with
    the existing hormones and BBT.

    Args:
        state: Current body state, used for moisture and pelvic congestion.
        estrogen: Estradiol proxy 0-100.
        progesterone: Progesterone proxy 0-100.
        phase: Current cycle phase.
        flow: Menstrual flow level 0-3.
        spotting: Whether ovulation spotting is present.
        weeks: Completed pregnancy weeks, 0 when not pregnant.
        maturity: Endocrine maturity 0-1.

    Returns:
        A dict with volume, texture, colour, stretch and cervix fields.
    """
    if phase == "menstrual":
        volume = _clamp(48.0 + flow * 13.0)
        texture = "bloody"
        color = "red" if flow >= 2 else "brown"
        stretch = 0.0
        position, firmness, opened = "low", "firm", False
    elif phase == "pregnant":
        volume = _clamp(36.0 + min(28.0, weeks * 0.8))
        texture = "creamy" if weeks < 24 else "watery"
        color = "milky"
        stretch = 1.0
        position, firmness, opened = "mid", "soft", False
    elif phase == "premenarche":
        volume = _clamp((5.0 + estrogen * 0.12) * (0.55 + 0.45 * maturity))
        texture = "dry" if volume < 10.0 else "sticky"
        color = "clear" if volume < 18.0 else "milky"
        stretch = 0.0
        position, firmness, opened = "low", "firm", False
    elif phase == "menopause":
        volume = _clamp(4.0 + estrogen * 0.1)
        texture = "dry" if volume < 10.0 else "sticky"
        color = "clear" if volume < 16.0 else "pale_yellow"
        stretch = 0.0
        position, firmness, opened = "low", "firm", False
    else:
        raw = (
            4.4
            * (max(0.0, estrogen) / 100.0) ** 1.5
            * max(0.0, 1.0 - progesterone / 90.0)
        )
        volume = _clamp(4.0 + raw * 22.0 - progesterone * 0.06)
        if volume < 9.0:
            texture = "dry"
        elif volume < 26.0:
            texture = "sticky"
        elif volume < 48.0:
            texture = "creamy"
        elif volume < 70.0:
            texture = "watery"
        else:
            texture = "egg_white"
        if texture in ("egg_white", "watery"):
            color = "clear"
        elif texture == "creamy":
            color = "milky"
        elif texture == "sticky":
            color = "white"
        else:
            color = "clear"
        stretch = round(max(0.0, (volume - 48.0) / 52.0 * 9.0), 1)
        if volume >= 62.0:
            position, firmness, opened = "high", "soft", True
        elif volume >= 30.0:
            position, firmness, opened = "mid", "medium", False
        else:
            position, firmness, opened = "low", "firm", False

    if spotting and phase not in ("menstrual", "pregnant"):
        color = "pink"
        volume = max(volume, 18.0)
        if texture == "dry":
            texture = "spotting"

    wetness = float(state.wetness)
    if wetness >= 65.0 or volume >= 78.0:
        vulva = "wet"
    elif wetness >= 42.0 or volume >= 52.0:
        vulva = "damp"
    else:
        vulva = "comfortable"

    pelvic = _clamp(
        state.uterine_congestion * 0.7
        + flow * 5.0
        + (18.0 if phase == "pregnant" else 0.0)
    )
    return {
        "mucus_volume": round(volume, 1),
        "mucus_texture": texture,
        "mucus_color": color,
        "mucus_stretch": stretch,
        "cervix_position": position,
        "cervix_firmness": firmness,
        "cervix_open": opened,
        "vulva": vulva,
        "pelvic_pressure": round(pelvic, 1),
    }


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

    age = profile.physio_age
    maturity, stage = _endocrine_maturity(age, profile.persona_id)

    if state.pregnant:
        weeks = max(0, int((now.timestamp() - state.conceived_at) // (7 * 86400)))
        count = ""
        if weeks >= 12 and state.fetuses == 2:
            count = "，双胎"
        elif weeks >= 12 and state.fetuses >= 3:
            count = "，三胎"
        detail = _mucus_detail(
            state,
            estrogen=75.0,
            progesterone=80.0,
            phase="pregnant",
            flow=0,
            spotting=False,
            weeks=weeks,
            maturity=maturity,
        )
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
            mucus_volume=detail["mucus_volume"],
            mucus_texture=detail["mucus_texture"],
            mucus_color=detail["mucus_color"],
            mucus_stretch=detail["mucus_stretch"],
            cervix_position=detail["cervix_position"],
            cervix_firmness=detail["cervix_firmness"],
            cervix_open=detail["cervix_open"],
            vulva=detail["vulva"],
            pelvic_pressure=detail["pelvic_pressure"],
            breast_fullness=round(min(100.0, 45.0 + weeks * 1.2), 1),
            endocrine_maturity=maturity,
            puberty_stage=stage,
        )

    pubertal = _is_pubertal(age)
    if age is None:
        # An unknown age is treated as adult: no puberty, no menopause.
        pass
    elif age < _menarche_age(profile.persona_id):
        detail = _mucus_detail(
            state,
            estrogen=8.0,
            progesterone=3.0,
            phase="premenarche",
            flow=0,
            spotting=False,
            weeks=0,
            maturity=maturity,
        )
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
            mucus_volume=detail["mucus_volume"],
            mucus_texture=detail["mucus_texture"],
            mucus_color=detail["mucus_color"],
            mucus_stretch=detail["mucus_stretch"],
            cervix_position=detail["cervix_position"],
            cervix_firmness=detail["cervix_firmness"],
            cervix_open=detail["cervix_open"],
            vulva=detail["vulva"],
            pelvic_pressure=detail["pelvic_pressure"],
            breast_fullness=round(10.0 + 18.0 * maturity, 1),
            endocrine_maturity=maturity,
            puberty_stage=stage,
        )
    elif age >= _menopause_age(profile.persona_id):
        detail = _mucus_detail(
            state,
            estrogen=12.0,
            progesterone=3.0,
            phase="menopause",
            flow=0,
            spotting=False,
            weeks=0,
            maturity=maturity,
        )
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
            mucus_volume=detail["mucus_volume"],
            mucus_texture=detail["mucus_texture"],
            mucus_color=detail["mucus_color"],
            mucus_stretch=detail["mucus_stretch"],
            cervix_position=detail["cervix_position"],
            cervix_firmness=detail["cervix_firmness"],
            cervix_open=detail["cervix_open"],
            vulva=detail["vulva"],
            pelvic_pressure=detail["pelvic_pressure"],
            breast_fullness=round(8.0 + 10.0 * maturity, 1),
            endocrine_maturity=maturity,
            puberty_stage="menopause",
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
    clots = 0
    if phase == "menstrual":
        flow = 3 if day <= 2 else (2 if day <= 4 else 1)
        if flow >= 2 and _roll(profile.persona_id, start.isoformat(), "clots") < 0.55:
            clots = 1
        if (
            flow >= 3
            and _roll(profile.persona_id, start.isoformat(), "clots-big") < 0.35
        ):
            clots = 2
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
    if state.diet_long < 40.0:
        cramps = min(3, cramps + 1)
        mood = max(-3, mood - 1)
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

    age_now = profile.physio_age
    perimenopausal = bool(
        age_now is not None
        and _is_perimenopausal(age_now, _menopause_age(profile.persona_id))
    )
    baseline_ovulation = max(1, profile.cycle_length - LUTEAL_DAYS)
    ovulation_delay = max(0, (state.ovulation_day or 0) - baseline_ovulation)
    spotting = state.ovulation_spotting_until > now.timestamp()
    detail = _mucus_detail(
        state,
        estrogen=estrogen,
        progesterone=progesterone,
        phase=phase,
        flow=flow,
        spotting=spotting,
        weeks=0,
        maturity=maturity,
    )
    breast_fullness = _clamp(
        {0: 18.0, 1: 45.0, 2: 72.0}.get(breast, 18.0)
        + progesterone * 0.15
        + (min(100.0, state.milk) * 0.4 if state.lactating else 0.0)
    )
    return CycleView(
        cycle_start=start,
        ovulation_pain=state.ovulation_pain_until > now.timestamp(),
        ovulation_side=state.ovulation_side,
        ovulation_delay=ovulation_delay,
        spotting=spotting,
        perimenopausal=perimenopausal,
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
        mucus_volume=detail["mucus_volume"],
        mucus_texture=detail["mucus_texture"],
        mucus_color=detail["mucus_color"],
        mucus_stretch=detail["mucus_stretch"],
        cervix_position=detail["cervix_position"],
        cervix_firmness=detail["cervix_firmness"],
        cervix_open=detail["cervix_open"],
        vulva=detail["vulva"],
        pelvic_pressure=detail["pelvic_pressure"],
        breast_fullness=round(breast_fullness, 1),
        follicle=round(state.follicle * 100.0, 1),
        endocrine_maturity=maturity,
        puberty_stage=stage,
        bbt=bbt,
        clots=clots,
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
    if view.phase == "menstrual":
        day_key = f"{view.cycle_start.isoformat()}:{view.day}"
        if view.headache >= 2:
            _add_event(
                state,
                now,
                "period_symptom",
                "经期头痛，太阳穴一跳一跳的",
                key=f"period-headache:{day_key}",
            )
        if view.backache >= 2:
            _add_event(
                state,
                now,
                "period_symptom",
                "经期腰酸，坐久了更明显",
                key=f"period-backache:{day_key}",
            )
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


def _female_libido_age_factor(age: float | None) -> float:
    """Return the age curve applied to a female persona's drive.

    The curve describes the slow decline from the early twenties onward and a
    mild lift below that. It only scales the rate; it never zeroes a persona
    out, because age does not switch the intimate layer off.

    Args:
        age: Human-equivalent age, or None when the persona did not state one.

    Returns:
        A multiplier on the daily libido increase.
    """
    if age is None:
        return 1.0
    if age < 22.0:
        return 1.18
    if age < 30.0:
        return 1.08
    if age < 40.0:
        return 1.0
    if age < 50.0:
        return 0.85
    if age < 55.0:
        return 0.65
    return 0.45


def _libido_rate(
    state: BodyState,
    view: CycleView | None,
    profile: Profile,
    mood: dict[str, Any] | None = None,
) -> float:
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
        rate *= _female_libido_age_factor(profile.physio_age)
        if view is not None:
            rate += view.estrogen / 100.0 * 15.0
            if view.phase == "ovulation":
                rate += 20.0
            elif view.phase == "luteal":
                rate += 8.0
            elif view.phase == "menstrual":
                # Some women want it during their period, some do not: this is
                # a stable personal trait, not a rule.
                rate += (
                    _roll(profile.persona_id, "cycle", "period-libido") - 0.5
                ) * 26.0
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


def _meal_kind(persona_id: str, day: str, slot: str) -> str:
    """Return what kind of meal this is, stable per persona and day.

    Args:
        persona_id: Persona identifier.
        day: Local date string.
        slot: Meal slot name.

    Returns:
        One of light, spicy, greasy, protein, sweet, veg, soup.
    """
    roll = _roll(persona_id, day, f"{slot}-kind")
    for limit, name in (
        (0.18, "light"),
        (0.34, "spicy"),
        (0.48, "greasy"),
        (0.62, "protein"),
        (0.76, "sweet"),
        (0.88, "veg"),
    ):
        if roll < limit:
            return name
    return "soup"


def _dinner_minute(bed_minutes: int) -> int:
    """Return the dinner minute for a bedtime, wrapping past midnight.

    Args:
        bed_minutes: Bedtime as minutes after midnight.

    Returns:
        Dinner as minutes after midnight, kept in the evening.
    """
    minute = (bed_minutes - 180) % (24 * 60)
    if minute > 21 * 60 + 30 or minute < 16 * 60:
        minute = 21 * 60 + 30
    return minute


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
    if state.sleeping and state.sleep_since <= moment.timestamp() < state.sleep_wake_at:
        return
    if state.care_until > moment.timestamp() and state.care_kind == "shower":
        # In the shower: no meals, drinks or scrolling until she is out.
        return
    if state.bed_since:
        # In bed: she may still get up if it is urgent, but nothing else.
        if state.urine >= 90.0:
            state.urine = 2.0
            state.last_toilet_at = moment.timestamp()
            _add_care(state, moment, "toilet")
        if state.bowel >= 85.0:
            state.gut = max(0.0, state.gut - 85.0)
            state.bowel = _clamp((state.gut - GUT_URGE_START) * GUT_URGE_GAIN)
            state.last_bowel_at = moment.timestamp()
            state.last_toilet_at = moment.timestamp()
            _add_care(state, moment, "toilet")
            _add_event(
                state,
                moment,
                "self_care",
                "憋不住，起来上了个大号",
                key=f"self-toilet-night:{int(moment.timestamp())}",
            )
        return
    hour = moment.hour
    day = moment.date().isoformat()
    midnight = _midnight(moment.date(), moment.tzinfo)
    wake_ts = state.last_wake_at
    woke_today = bool(wake_ts and wake_ts >= midnight.timestamp())
    if woke_today:
        wake_ref = datetime.fromtimestamp(wake_ts, moment.tzinfo)
    else:
        # Still up from yesterday: keep a normal daytime rhythm as a fallback.
        wake_ref = midnight.replace(hour=8, minute=30)
    wake_minutes = wake_ref.hour * 60 + wake_ref.minute
    _earliest, _latest, bed_ts = _night_points(
        profile, settings, moment, state.sleep_phase
    )
    bed_clock = datetime.fromtimestamp(bed_ts, moment.tzinfo)
    bed_minutes = bed_clock.hour * 60 + bed_clock.minute
    now_minutes = hour * 60 + moment.minute
    since_meal = moment.timestamp() - state.last_eat_at

    if (
        state.location == "out"
        and state.out_until
        and moment.timestamp() >= state.out_until
    ):
        state.location = "home"
        state.out_until = 0.0
        _add_event(state, moment, "self_care", "回到家了", key=f"self-home:{day}")
    outing_key = f"self-outing:{day}"
    if (
        state.location == "home"
        and not state.flags.get(outing_key)
        and woke_today
        and wake_minutes + 120 <= now_minutes <= wake_minutes + 420
        and (bed_minutes - now_minutes) % (24 * 60) > 240
    ):
        state.flags[outing_key] = moment.timestamp()
        if _roll(profile.persona_id, day, "go-out") < 0.4:
            state.location = "out"
            state.out_until = moment.timestamp() + (
                3600.0 + _roll(profile.persona_id, day, "outing-length") * 7200.0
            )
            _add_event(state, moment, "self_care", "出门走走", key=outing_key)

    def pick(base: int, spread: int, salt: str) -> int:
        """Draw this day's minute for one routine, stable per persona."""
        return base + int(_roll(profile.persona_id, day, salt) * spread)

    def due(target: int, window: int) -> bool:
        """True when a routine target has been reached within its window."""
        gap = now_minutes - target
        return 0 <= gap <= window

    # Meals follow her own wake-up and bedtime, not the wall clock.
    meals = (
        (
            "breakfast",
            min(11 * 60 + 30, max(6 * 60 + 30, pick(wake_minutes + 30, 45, "meal-am"))),
            30.0,
            "早饭",
        ),
        (
            "lunch",
            min(15 * 60, max(11 * 60 + 30, pick(wake_minutes + 300, 60, "meal-noon"))),
            32.0,
            "午饭",
        ),
        (
            "dinner",
            # Three hours before bed; a bedtime after midnight belongs to the
            # previous evening, not to 17:00.
            _dinner_minute(bed_minutes),
            32.0,
            "晚饭",
        ),
    )
    for slot_name, target, portion, label in meals:
        if not due(target, 180):
            continue
        key = f"self-meal:{day}:{slot_name}"
        if state.flags.get(key):
            continue
        if state.stomach > 90.0 or since_meal <= 3600:
            continue
        state.stomach = _clamp(state.stomach + portion / const.stomach_capacity)
        state.glucose = _clamp(state.glucose + 16.0)
        state.gut = _clamp(state.gut + GUT_RESIDUE_MEAL * 0.8)
        # Soup, porridge or fruit hydrate; dry food barely does.
        meal_water = 5.0 + _roll(profile.persona_id, day, f"{slot_name}-water") * 7.0
        state.hydration = _clamp(state.hydration + meal_water)
        if _roll(profile.persona_id, day, f"{slot_name}-salt") > 0.7:
            state.hydration = _clamp(state.hydration - 5.0)
            state.sodium = _clamp(state.sodium + 12.0, 0.0, 100.0)
        if _roll(profile.persona_id, day, f"{slot_name}-sugar") > 0.7:
            state.glucose = _clamp(state.glucose + 9.0)
        state.oral = _clamp(state.oral + 1.5)
        state.iron = _clamp(state.iron + 7.0)
        state.last_eat_at = moment.timestamp()
        state.last_meal_kind = "meal"
        kind = _meal_kind(profile.persona_id, day, slot_name)
        if kind == "spicy":
            state.indigestion = _clamp(state.indigestion + 10.0)
            state.sweat = _clamp(state.sweat + 2.0)
            state.breath_scent = _clamp(state.breath_scent + 8.0)
            state.diet_quality += (45.0 - state.diet_quality) * 0.02
        elif kind == "greasy":
            state.indigestion = _clamp(state.indigestion + 6.0)
            state.gut = _clamp(state.gut + 2.0)
            state.breath_scent = _clamp(state.breath_scent + 4.0)
            state.diet_quality += (30.0 - state.diet_quality) * 0.02
        elif kind == "protein":
            state.iron = _clamp(state.iron + 4.0)
            state.fiber += (40.0 - state.fiber) * 0.1
            state.diet_quality += (70.0 - state.diet_quality) * 0.02
        elif kind == "sweet":
            state.glucose = _clamp(state.glucose + 10.0)
            state.diet_quality += (35.0 - state.diet_quality) * 0.02
        elif kind == "veg":
            state.hydration = _clamp(state.hydration + 2.0)
            state.fiber += (80.0 - state.fiber) * 0.15
            state.diet_quality += (80.0 - state.diet_quality) * 0.02
        elif kind == "soup":
            state.hydration = _clamp(state.hydration + 4.0)
            state.diet_quality += (65.0 - state.diet_quality) * 0.02
        else:
            state.indigestion = max(0.0, state.indigestion - 3.0)
            state.diet_quality += (65.0 - state.diet_quality) * 0.02
        _add_care(state, moment, "meal")
        _add_event(state, moment, "self_care", f"自己吃了{label}", key=key)
        break

    # She sips through the day instead of waiting until she is parched: a
    # glass when the meter dips, a bigger one when she is actually dry.
    # A glass of water after getting up, before the day's first coffee or tea.
    water_key = f"self-water-am:{day}"
    if (
        woke_today
        and not state.flags.get(water_key)
        and now_minutes >= wake_minutes + 5
        and moment.timestamp() - state.last_drink_at > 900
    ):
        state.hydration = _clamp(state.hydration + 8.0)
        state.last_drink_at = moment.timestamp()
        state.last_drink_ml = 250.0
        _add_care(state, moment, "drink")
        _add_event(state, moment, "self_care", "起床后喝了杯水", key=water_key)

    painkiller_key = f"self-painkiller:{day}"
    if (
        state.cycle_phase == "menstrual"
        and not state.flags.get(painkiller_key)
        and state.pain >= 35.0
        and now_minutes >= wake_minutes + 30
        and _roll(profile.persona_id, day, "painkiller") < 0.6
    ):
        state.flags[painkiller_key] = moment.timestamp()
        state.pain = max(0.0, state.pain - 25.0)
        _add_care(state, moment, "medicine")
        _add_event(
            state,
            moment,
            "self_care",
            "吃了片止痛药",
            key=painkiller_key,
        )

    exercise_key = f"self-exercise:{day}"
    if (
        state.location == "home"
        and not state.flags.get(exercise_key)
        and woke_today
        and wake_minutes + 120 <= now_minutes <= wake_minutes + 600
        and (bed_minutes - now_minutes) % (24 * 60) > 180
        and _roll(profile.persona_id, day, "exercise") < 0.5
    ):
        state.flags[exercise_key] = moment.timestamp()
        minutes = 20.0 + _roll(profile.persona_id, day, "exercise-length") * 40.0
        state.sedentary = max(0.0, state.sedentary - 35.0)
        state.sweat = _clamp(state.sweat + 8.0)
        state.leg_fatigue = _clamp(state.leg_fatigue + 10.0)
        state.fitness = _clamp(state.fitness + 2.0, 0.0, 100.0)
        state.stress = max(0.0, state.stress - 12.0)
        state.energy = _clamp(state.energy + 3.0)
        state.last_exercise_at = moment.timestamp()
        _add_care(state, moment, "exercise")
        _add_event(
            state,
            moment,
            "self_care",
            f"活动了 {round(minutes)} 分钟",
            key=exercise_key,
        )

    if state.hydration <= 88.0 and moment.timestamp() - state.last_drink_at > 3600:
        thirst = 14.0 if state.hydration <= 65.0 else 10.0
        caffeinated = (
            _roll(
                profile.persona_id,
                str(int(moment.timestamp() // 3600)),
                "drink-type",
            )
            < 0.3
        )
        if caffeinated:
            state.caffeine = _clamp(state.caffeine + 28.0, 0.0, 100.0)
            state.hydration = _clamp(state.hydration + thirst * 0.6)
        else:
            state.hydration = _clamp(state.hydration + thirst)
        state.oral = _clamp(state.oral - 1.0)
        state.last_drink_at = moment.timestamp()
        state.last_drink_ml = 400.0 if thirst >= 10.0 else 250.0
        _add_care(state, moment, "drink")

    toilet_key = f"self-toilet-am:{day}"
    if (
        woke_today
        and not state.flags.get(toilet_key)
        and due(pick(wake_minutes + 10, 20, "toilet-wake"), 120)
        and state.urine >= 8.0
    ):
        state.urine = 2.0
        state.last_toilet_at = moment.timestamp()
        state.flags[toilet_key] = moment.timestamp()
        _add_care(state, moment, "toilet")
        _maybe_toilet_release(state, moment, profile, settings, "urine")
    elif state.urine >= 30.0:
        state.urine = 2.0
        state.last_toilet_at = moment.timestamp()
        _add_care(state, moment, "toilet")
        _maybe_toilet_release(state, moment, profile, settings, "urine")

    if settings.enable_hygiene:
        minutes_to_bed = (bed_minutes - now_minutes) % (24 * 60)
        morning_person = _roll(profile.persona_id, "hygiene", "shower-habit") < 0.5
        brush_am = pick(wake_minutes + 15, 30, "brush-am")
        brush_key = f"self-brush-am:{day}"
        if (
            woke_today
            and not state.flags.get(brush_key)
            and due(brush_am, 150)
            and moment.timestamp() - state.last_brush_at > 2 * 3600
        ):
            state.oral = 2.0
            state.last_brush_at = moment.timestamp()
            state.flags[brush_key] = moment.timestamp()
            _add_care(state, moment, "brush")
        bed_brush_key = f"self-brush-bed:{day}"
        if (
            not state.flags.get(bed_brush_key)
            and 0 < minutes_to_bed <= 40
            and moment.timestamp() - state.last_brush_at > 2 * 3600
        ):
            state.oral = 2.0
            state.last_brush_at = moment.timestamp()
            state.flags[bed_brush_key] = moment.timestamp()
            _add_care(state, moment, "brush")
        shower_target = (
            pick(wake_minutes + 40, 60, "shower-am")
            if morning_person
            else (bed_minutes - 90) % (24 * 60)
        )
        # A morning person still takes an evening shower if the morning got
        # away from her; otherwise she would skip a whole day because she was
        # not "dirty enough" at 11am.
        if morning_person:
            shower_due = (woke_today and due(shower_target, 240)) or (
                0 < minutes_to_bed <= 120
            )
        else:
            shower_due = 0 < minutes_to_bed <= 120
        if (
            shower_due
            and state.location == "home"
            and state.skin >= 15.0
            and not state.flags.get(f"self-shower:{day}")
        ):
            state.skin = 2.0
            state.scent = max(0.0, state.scent - 45.0)
            state.foot_scent = max(0.0, state.foot_scent - 60.0)
            state.period_scent = max(0.0, state.period_scent - 50.0)
            state.scalp_scent = max(0.0, state.scalp_scent - 70.0)
            state.breath_scent = max(0.0, state.breath_scent - 35.0)
            state.intimate_scent = max(0.0, state.intimate_scent - 80.0)
            state.last_shower_at = moment.timestamp()
            _add_care(state, moment, "shower")
            _add_event(state, moment, "self_care", "洗了个澡", key=f"self-shower:{day}")
            # A real shower takes a while; whether the phone is in there or not,
            # she is not going to type under the water.
            shower_minutes = (
                8.0 + _roll(profile.persona_id, day, "shower-length") * 10.0
            )
            state.care_until = max(
                state.care_until, moment.timestamp() + shower_minutes * 60.0
            )
            state.care_kind = "shower"
            # Some people prefer the shower; the water is already running.
            shower_chance = (
                0.12 + _roll(profile.persona_id, "self", "shower-habit") * 0.25
            )
            if (
                settings.enable_intimate
                and profile.gender_resolved
                and profile.gender in ("female", "male")
                and state.libido >= 55.0
                and _roll(profile.persona_id, day, "shower-self") < shower_chance
            ):
                state.libido = _clamp(state.libido - 20.0)
                state.wetness = max(0.0, state.wetness - 25.0)
                state.intimate_scent = max(
                    state.intimate_scent, 38.0 + state.libido * 0.20
                )
                state.last_self_at = moment.timestamp()
                state.release_count += 1
                state.release_until = max(
                    state.release_until,
                    moment.timestamp()
                    + _release_seconds(state, profile, moment, "shower"),
                )
                state.release_kind = "shower"
                _add_event(
                    state,
                    moment,
                    "self_release",
                    "洗澡的时候顺手解决了一下",
                    key=f"self-shower-release:{day}",
                )

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
            "喂了奶，涨奶缓下来了",
            key=f"self-feed:{moment.date().isoformat()}",
        )
    # Defecation follows an urge, not a fixed clock: the morning wave and the
    # gastrocolic reflex after a meal are the usual chances, and a very full
    # bowel eventually overrides both.
    morning_urge = 6 <= hour <= 11 and state.bowel >= 45.0
    meal_reflex = (
        state.last_eat_at
        and 0 < moment.timestamp() - state.last_eat_at <= 5400
        and state.bowel >= 50.0
    )
    if morning_urge or meal_reflex or state.bowel >= 65.0:
        state.gut = max(0.0, state.gut - 85.0)
        state.bowel = _clamp((state.gut - GUT_URGE_START) * GUT_URGE_GAIN)
        state.last_bowel_at = moment.timestamp()
        state.last_toilet_at = moment.timestamp()
        _add_care(state, moment, "toilet")
        _add_event(
            state,
            moment,
            "self_care",
            "去上了个大号",
            key=f"self-toilet:{moment.date().isoformat()}",
        )
        _maybe_toilet_release(state, moment, profile, settings, "bowel")
    if hour >= 8:
        day = moment.date().isoformat()
        weather_key = f"weather:{day}"
        if weather_key not in state.flags:
            state.flags[weather_key] = moment.timestamp()
            seasonal = 17.0 + 11.0 * math.sin((moment.month - 4) / 12.0 * 2.0 * math.pi)
            ambient = seasonal + (_roll(profile.persona_id, day, "temp") - 0.5) * 8.0
            state.ambient_temp = round(ambient, 1)
            # Rough northern-hemisphere daylight: long in June, short in
            # December. It feeds mood and, a little, how much she wants to sleep.
            state.daylight = 12.0 + 3.5 * math.sin(
                (moment.month - 4) / 12.0 * 2.0 * math.pi
            )
            weather_roll = _roll(profile.persona_id, day, "weather")
            if ambient < 2.0 and weather_roll < 0.25:
                state.weather = "snow"
            elif weather_roll < 0.24:
                state.weather = "rain"
            elif weather_roll < 0.55:
                state.weather = "cloudy"
            else:
                state.weather = "clear"
            state.clothing_warmth = _clamp(100.0 - (ambient - 5.0) * 3.0, 10.0, 100.0)
            quality = (
                86.0
                - state.sleep_debt * 2.1
                - state.illness_severity * 0.25
                - abs(ambient - 22.0) * 0.6
                - state.progesterone * 0.07
                - state.pain * 0.12
                - state.caffeine * 0.08
                - state.stress * 0.04
                + (_roll(profile.persona_id, day, "sleep-quality") - 0.5) * 10.0
            )
            state.sleep_quality = _clamp(quality)
            if _roll(profile.persona_id, day, "dream") < 0.32:
                dreams = (
                    "梦见下雨的街道",
                    "梦见自己在很高的地方",
                    "梦见有人一直在叫她",
                    "梦见找不到回去的路",
                    "梦见一只很黏人的猫",
                )
                index = int(_roll(profile.persona_id, day, "dream-pick") * len(dreams))
                state.dream = dreams[min(index, len(dreams) - 1)]
            else:
                state.dream = ""
    if hour >= 8 and not state.illness:
        day = moment.date().isoformat()
        if moment.month in (3, 4, 5, 9, 10):
            if state.allergy < 12.0:
                state.allergy = 12.0 + _roll(profile.persona_id, day, "allergy") * 30.0
        else:
            state.allergy *= 0.5 ** (1.0 / 48.0)
        migraine_key = f"migraine-roll:{day}"
        if (
            not state.flags.get(migraine_key)
            and state.migraine_until <= moment.timestamp()
            and state.cycle_phase in ("menstrual", "luteal")
            and state.stress >= 55.0
        ):
            state.flags[migraine_key] = moment.timestamp()
            if _roll(profile.persona_id, day, "migraine") < 0.04:
                state.migraine_until = (
                    moment.timestamp()
                    + (4.0 + _roll(profile.persona_id, day, "migraine-length") * 8.0)
                    * 3600.0
                )
                state.pain = max(state.pain, 45.0)
                state.pain_site = "头"
                _add_event(
                    state,
                    moment,
                    "body",
                    "偏头痛，太阳穴一跳一跳的",
                    key=f"migraine:{day}",
                )
        check_key = f"illness-check:{day}"
        if check_key not in state.flags:
            state.flags[check_key] = moment.timestamp()
            strain = (
                max(0.0, 35.0 - state.energy) * 1.2
                + max(0.0, state.sleep_debt - 6.0) * 6.0
                + max(0.0, 45.0 - state.hydration) * 0.5
            )
            if strain >= 45.0 and _roll(profile.persona_id, day, "illness") < 0.02:
                state.illness = "cold"
                state.illness_severity = 55.0
                state.illness_started_at = moment.timestamp()
                _add_event(
                    state,
                    moment,
                    "illness",
                    "有点感冒，头沉沉的",
                    key=f"self-illness:{day}",
                )


def _release_seconds(
    state: BodyState,
    profile: Profile,
    moment: datetime,
    kind: str = "self",
) -> float:
    """Return how long she takes to herself this time, in seconds.

    People differ a lot here, so the base comes from the persona and the day;
    lying in bed or being worn out shifts it a little, and combining it with a
    shower is shorter because the shower is already running. The body also has
    limits: four minutes at the quick end, twenty-five at the slow end.

    Args:
        state: Body state at the moment she starts.
        profile: Persona used as the stable seed.
        moment: When she starts.
        kind: "self", "bed", "shower" or "toilet".

    Returns:
        Duration in seconds.
    """
    day = moment.date().isoformat()
    if kind == "shower":
        minutes = 3.5 + _roll(profile.persona_id, "self", "shower-duration") * 4.5
    elif kind == "toilet":
        minutes = 2.5 + _roll(profile.persona_id, "self", "toilet-duration") * 3.5
    else:
        minutes = 7.0 + _roll(profile.persona_id, "self", "duration") * 9.0
        minutes += (_roll(profile.persona_id, day, "self-duration") - 0.5) * 4.0
        if kind == "bed" or state.bed_since:
            minutes += 2.0
        if state.energy < 40.0:
            minutes -= 1.5
        if state.libido >= 85.0:
            minutes -= 1.0
    return max(4.0, min(25.0, minutes)) * 60.0


def _release_rounds(
    state: BodyState,
    profile: Profile,
    moment: datetime,
) -> int:
    """Return how many rounds a self-release session has.

    Args:
        state: Body state at the moment she starts.
        profile: Persona used as the stable seed.
        moment: When she starts.

    Returns:
        1 or 2; male bodies stay at one because of the refractory period.
    """
    if profile.gender != "female":
        return 1
    if state.libido < 60.0:
        return 1
    chance = 0.15 + (min(100.0, state.libido) - 60.0) / 40.0 * 0.25
    if state.energy < 30.0:
        chance *= 0.5
    if state.stress > 65.0:
        chance *= 0.7
    if _roll(profile.persona_id, str(int(moment.timestamp())), "rounds") < chance:
        return 2
    return 1


def _maybe_hot_flash(
    state: BodyState,
    moment: datetime,
    profile: Profile,
    step_hours: float,
    sleep_fraction: float,
) -> None:
    """Roll a perimenopausal hot flash for one step.

    Args:
        state: Body state being advanced.
        moment: Current simulation instant.
        profile: Persona profile, used for age and stable seeding.
        step_hours: Length of the current step.
        sleep_fraction: Share of the step spent asleep.
    """
    if profile.gender != "female" or state.pregnant:
        return
    age = profile.physio_age
    if age is None:
        return
    menopause_at = _menopause_age(profile.persona_id)
    if not _is_perimenopausal(age, menopause_at):
        return
    if state.hot_flash_until > moment.timestamp():
        return
    closeness = 1.0 - max(0.0, menopause_at - age) / PERIMENOPAUSE_YEARS
    rate = 0.04 + 0.10 * max(0.0, min(1.0, closeness))
    if _roll(profile.persona_id, str(int(moment.timestamp() // 900)), "hot-flash") >= (
        1.0 - math.exp(-rate * step_hours)
    ):
        return
    minutes = (
        2.0
        + _roll(profile.persona_id, str(int(moment.timestamp())), "hot-flash-length")
        * 8.0
    )
    state.hot_flash_until = moment.timestamp() + minutes * 60.0
    state.sweat = _clamp(state.sweat + 8.0)
    if sleep_fraction > 0.5:
        state.sleep_quality = _clamp(state.sleep_quality - 2.5)
        text = "半夜热醒，出了一身汗"
    else:
        text = "突然一阵潮热，后背出了点汗"
    _add_event(
        state,
        moment,
        "body",
        text,
        key=f"hot-flash:{int(moment.timestamp())}",
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
    if state.location != "home":
        return
    if profile.gender not in ("female", "male"):
        return
    if state.libido < 12.0:
        return
    stamp = moment.timestamp()
    bucket = int(stamp // 900)
    if profile.gender == "male" and state.refractory_until > stamp:
        return
    if state.last_self_at and stamp - state.last_self_at < 1800:
        return

    comfort = 1.0
    comfort -= min(0.4, max(0.0, state.sleep_debt - 6.0) / 12.0 * 0.4)
    comfort -= min(0.3, max(0.0, 30.0 - state.satiety) / 30.0 * 0.3)
    habit = const.self_rate * max(0.3, comfort)
    per_hour = min(0.4, habit * (0.3 + 0.7 * state.libido / 100.0) / 14.0)
    # A high drive is not a subtle preference: it should make an outlet much
    # more likely, and a long stretch without one adds urgency.
    urge = 1.0 + max(0.0, (state.libido - 50.0) / 50.0)
    if state.last_self_at and stamp - state.last_self_at >= 24 * 3600:
        urge *= 1.25
    per_hour = min(0.5, per_hour * urge)
    if sleep_fraction > 0.5:
        per_hour *= 0.15
    chance = 1.0 - (1.0 - per_hour) ** RELEASE_BUCKET_HOURS

    # The libido meter itself is the pity counter: below 60 it is the base
    # rate, from 60 upward every point adds odds, and at 100 the drive is a
    # hard guarantee. Releasing drops libido, so the counter resets.
    awake = sleep_fraction <= 0.5
    if awake and state.libido >= 60.0:
        chance += (min(100.0, state.libido) - 60.0) * 0.012
    if awake and state.libido >= 99.5:
        chance = 1.0
    if _roll(profile.persona_id, f"self:{int(stamp // 900)}", "roll") >= min(
        1.0, chance
    ):
        return

    state.libido = _clamp(state.libido - 20.0)
    state.wetness = max(0.0, state.wetness - 30.0)
    state.intimate_scent = max(state.intimate_scent, 40.0 + state.libido * 0.25)
    state.last_self_at = stamp
    state.release_count += 1
    if profile.gender == "male":
        state.refractory_until = (
            stamp
            + (1.0 + _roll(profile.persona_id, f"self:{bucket}", "refractory") * 2.0)
            * 3600
        )
    # She is busy with herself, not looking at the phone; every chat stays
    # silent until she is done. The length is hers, not a fixed script.
    kind = "bed" if state.bed_since else "self"
    rounds = _release_rounds(state, profile, moment)
    extra = 0.0
    if rounds > 1:
        extra = (
            180.0 + _roll(profile.persona_id, str(int(stamp)), "rounds-length") * 240.0
        )
        state.libido = _clamp(state.libido - 6.0)
        state.wetness = max(0.0, state.wetness - 8.0)
    duration = min(25.0 * 60.0, _release_seconds(state, profile, moment, kind) + extra)
    state.release_until = max(state.release_until, stamp + duration)
    state.release_kind = kind
    _add_event(
        state,
        moment,
        "self_release",
        "自慰了两次，身体松下来" if rounds > 1 else "自慰了一次，身体松下来",
        key=f"self-release:{int(stamp)}",
    )


def _maybe_toilet_release(
    state: BodyState,
    moment: datetime,
    profile: Profile,
    settings: Settings,
    kind: str = "bowel",
) -> None:
    """Occasionally finish on the toilet during a bathroom trip.

    Args:
        state: Body state being advanced.
        moment: When the toilet visit happens.
        profile: Resolved persona profile.
        settings: Plugin settings; intimacy must be enabled.
        kind: "bowel" or "urine"; urination is the more frequent, less
            deliberate setting, so its chance is lower.
    """
    if not (profile.gender_resolved and settings.enable_intimate):
        return
    if state.location != "home":
        return
    if state.libido < (62.0 if kind == "urine" else 58.0):
        return
    stamp = moment.timestamp()
    if state.last_self_at and stamp - state.last_self_at < 3 * 3600:
        return
    if state.release_until > stamp:
        return
    if kind == "urine":
        chance = 0.03 + _roll(profile.persona_id, "self", "toilet-habit") * 0.06
    else:
        chance = 0.06 + _roll(profile.persona_id, "self", "toilet-habit") * 0.10
    if _roll(profile.persona_id, str(int(stamp)), "toilet-release") >= chance:
        return
    state.libido = _clamp(state.libido - 20.0)
    state.wetness = max(0.0, state.wetness - 25.0)
    state.intimate_scent = max(state.intimate_scent, 35.0 + state.libido * 0.20)
    state.last_self_at = stamp
    state.release_count += 1
    if profile.gender == "male":
        state.refractory_until = stamp + 3600.0
    state.release_until = max(
        state.release_until,
        stamp + _release_seconds(state, profile, moment, "toilet"),
    )
    state.release_kind = "toilet"
    _add_event(
        state,
        moment,
        "self_release",
        "上厕所时顺手解决了一下",
        key=f"self-release-toilet:{int(stamp)}",
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
                    state.intimate_scent = max(state.intimate_scent, 42.0)
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
    penalty += max(0.0, state.kidney_load - 75.0) * 0.04
    penalty += max(0.0, state.liver_load - 75.0) * 0.04
    penalty += state.allergy * 0.04
    state.eye_strain = _clamp(max(state.eye_strain, 12.0 + state.allergy * 0.25))
    if state.migraine_until > now.timestamp():
        penalty += 6.0
        state.eye_strain = _clamp(max(state.eye_strain, 42.0))
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
    busy_until: float = 0.0,
    busy_kind: str = "",
) -> BodyState:
    """Let time pass and update internal state, meters and events.

    Args:
        state: Body state to advance.
        now: Local timezone-aware current time.
        profile: Resolved persona profile.
        settings: Plugin settings.
        mood: Optional emotion snapshot that colors appetite and tension.
        busy_until: When the current hobby session ends, so bedtime can wait.
        busy_kind: What she is busy with, for prompts and the page.

    Returns:
        The same state, advanced.
    """
    now_ts = now.timestamp()
    state.busy_until = float(busy_until or 0.0)
    state.busy_kind = str(busy_kind or "") if state.busy_until > now_ts else ""
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
        sleep_seconds = min(
            float(step_seconds),
            max(
                0.0,
                _sleep_tick(
                    state,
                    cursor,
                    chunk_end,
                    profile,
                    settings,
                    const,
                    busy_until,
                ),
            ),
        )
        sleep_fraction = min(1.0, max(0.0, sleep_seconds / step_seconds))
        awake_fraction = 1.0 - sleep_fraction
        midpoint = cursor + timedelta(seconds=step_seconds / 2.0)
        alert = circadian_alertness(midpoint)
        _step_cycle(state, chunk_end, profile, settings, step_hours, mood)
        _maybe_hot_flash(state, cursor, profile, step_hours, sleep_fraction)
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
        ) + state.sodium * 0.008
        state.hydration = _clamp(state.hydration - hydration_use * step_hours)

        urine_rate = (
            URINE_BASE_AWAKE * awake_fraction
            + URINE_BASE_SLEEP * sleep_fraction
            + max(0.0, state.hydration - 50.0) * 0.04
        )
        state.urine = _clamp(
            state.urine + urine_rate * step_hours + state.caffeine * 0.01 * step_hours
        )
        # Urine colour is a cheap readout of concentration; saliva and nasal
        # mucus follow hydration, stress and the season.
        concentration = (
            100.0 - state.hydration * 0.8 + state.sodium * 0.25 + state.caffeine * 0.15
        )
        state.urine_color = (
            0
            if concentration < 14.0
            else 1
            if concentration < 32.0
            else 2
            if concentration < 52.0
            else 3
        )
        state.dry_mouth = _clamp(
            max(0.0, 60.0 - state.hydration) * 2.0
            + state.caffeine * 0.2
            + max(0.0, state.stress - 50.0) * 0.3
        )
        state.nasal = _clamp(
            state.allergy * 0.8
            + (state.illness_severity * 0.6 if state.illness else 0.0)
        )

        state.gut = _clamp(
            state.gut + (GUT_BASE - GUT_PASSAGE * const.gut_transit) * step_hours
        )
        fiber_factor = 0.8 + state.fiber / 250.0
        state.bowel = _clamp(
            (state.gut - GUT_URGE_START) * GUT_URGE_GAIN * fiber_factor
        )
        iron_rate = (
            1.0 + (float(view.flow) if view is not None else 2.0) * 0.5
            if in_period_now
            else 0.2
        )
        state.iron = _clamp(state.iron - iron_rate * step_hours / 24.0)
        if state.iron < 45.0:
            state.craving = "想吃点补铁的"
        elif in_period_now:
            state.craving = "想吃甜的"
        elif state.sleep_debt > 8.0:
            state.craving = "想喝点热的"
        elif state.hydration < 45.0:
            state.craving = "想喝水"
        else:
            state.craving = ""
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

        if state.illness:
            days = (
                max(
                    0.0,
                    (chunk_end.timestamp() - state.illness_started_at) / 86400.0,
                )
                if state.illness_started_at
                else 0.0
            )
            if days < 0.75:
                severity = 32.0 + days * 50.0
            elif days < 2.5:
                severity = 70.0 - (days - 0.75) * 8.0
            else:
                severity = 56.0 - (days - 2.5) * 18.0
            if days >= 4.0 or severity <= 1.0:
                state.illness = ""
                state.illness_severity = 0.0
                if state.flags.get("illness-recovered") != 1:
                    state.flags["illness-recovered"] = 1.0
                    _add_event(
                        state,
                        chunk_end,
                        "illness",
                        "感冒好得差不多了",
                        key="illness-recovered",
                    )
            else:
                state.illness_severity = _clamp(severity)
                if days >= 1.0 and state.flags.get("illness-peak") != 1:
                    state.flags["illness-peak"] = 1.0
                    _add_event(
                        state,
                        chunk_end,
                        "illness",
                        "发烧了，浑身发酸",
                        key="illness-peak",
                    )
        cramp_pain = float(view.cramps) * 8.0 if view is not None else 0.0
        illness_pain = state.illness_severity * 0.35
        pain_target = _clamp(max(cramp_pain, illness_pain))
        if profile.gender == "female":
            pain_target = _clamp(
                max(pain_target, (state.uterine_congestion - 70.0) * 0.15)
            )
        if state.sedentary > 65.0:
            pain_target = _clamp(max(pain_target, (state.sedentary - 65.0) * 0.35))
            if not state.pain_site:
                state.pain_site = "肩背"
        if state.indigestion > 45.0:
            pain_target = _clamp(max(pain_target, (state.indigestion - 45.0) * 0.4))
            if not state.pain_site:
                state.pain_site = "胃"
        state.pain += (pain_target - state.pain) * (1.0 - math.exp(-step_hours / 0.8))
        if cramp_pain >= illness_pain and cramp_pain > 0:
            state.pain_site = "小腹"
        elif illness_pain > 0:
            state.pain_site = "头"
        elif state.indigestion > 45.0:
            state.pain_site = "胃"
        elif state.sedentary > 65.0:
            state.pain_site = "肩背"
        else:
            state.pain_site = ""

        temp_target = (
            36.3
            + alert / 100.0 * 0.45
            + state.progesterone / 100.0 * 0.15
            + state.illness_severity / 100.0 * 0.9
        )
        state.temperature += (temp_target - state.temperature) * (
            1.0 - math.exp(-step_hours / 1.5)
        )
        sweat_target = _clamp(
            max(0.0, state.temperature - 36.75) * 40.0
            + max(0.0, 45.0 - state.hydration) * 0.5
            + max(0.0, state.ambient_temp - 26.0) * 1.4
            + max(0.0, state.clothing_warmth - 72.0) * 0.16
        )
        state.sweat += (sweat_target - state.sweat) * (
            1.0 - math.exp(-step_hours / 1.0)
        )
        arousal = _clamp(_number((mood or {}).get("arousal"), 0.0))
        heart_target = (
            62.0
            + arousal * 18.0
            + state.illness_severity * 0.18
            + state.pain * 0.12
            + state.caffeine * 0.10
            - sleep_fraction * 6.0
        )
        state.heart_rate += (heart_target - state.heart_rate) * (
            1.0 - math.exp(-step_hours / 0.6)
        )
        state.heart_rate = _clamp(state.heart_rate, 45.0, 140.0)
        breath_target = (
            14.0
            + arousal * 4.0
            + state.illness_severity * 0.05
            + state.pain * 0.025
            - sleep_fraction * 2.0
        )
        state.breath_rate += (breath_target - state.breath_rate) * (
            1.0 - math.exp(-step_hours / 0.6)
        )
        state.breath_rate = _clamp(state.breath_rate, 10.0, 30.0)
        eye_target = (
            8.0
            + max(0.0, state.sleep_debt - 4.0) * 5.0
            + max(0.0, arousal - 0.4) * 20.0
        ) * (1.0 - sleep_fraction * 0.5)
        state.eye_strain += (eye_target - state.eye_strain) * (
            1.0 - math.exp(-step_hours / 2.0)
        )
        oil_target = _clamp(
            6.0
            + const.skin_oil_rate * 14.0
            + state.sweat * 0.45
            + state.progesterone / 100.0 * 12.0
            + max(0.0, state.skin - 50.0) * 0.15
            + max(0.0, 45.0 - state.diet_long) * 0.4
        )
        state.skin_oil += (oil_target - state.skin_oil) * (
            1.0 - math.exp(-step_hours / 2.0)
        )
        scent_target = _clamp(
            12.0
            + state.skin_oil * 0.25
            + state.sweat * 0.35
            + (10.0 if in_period_now else 0.0)
            + (8.0 if view is not None and view.phase == "ovulation" else 0.0)
            + (4.0 if view is not None and view.phase == "luteal" else 0.0)
            + state.intimate_scent * 0.10
        )
        state.scent += (scent_target - state.scent) * (
            1.0 - math.exp(-step_hours / 3.0)
        )
        foot_target = _clamp(
            8.0
            + state.sweat * 0.5
            + state.sedentary * 0.25
            + max(0.0, state.ambient_temp - 24.0) * 1.2
        )
        state.foot_scent += (foot_target - state.foot_scent) * (
            1.0 - math.exp(-step_hours / 4.0)
        )
        period_flow = (
            (3 if cycle_day <= 2 else (2 if cycle_day <= 4 else 1))
            if in_period_now
            else 0
        )
        period_target = _clamp(period_flow * 22.0)
        state.period_scent += (period_target - state.period_scent) * (
            1.0 - math.exp(-step_hours / 6.0)
        )
        # The scalp, breath and intimate scent each have their own source and
        # half-life. They are still ordinary body smells, not a scoreboard:
        # the prompt only mentions the one that is actually noticeable.
        shower_hours = (
            max(0.0, (cursor.timestamp() - state.last_shower_at) / 3600.0)
            if state.last_shower_at
            else 24.0
        )
        scalp_target = _clamp(
            6.0
            + state.skin_oil * 0.32
            + state.sweat * 0.18
            + min(18.0, max(0.0, shower_hours - 18.0) * 0.30)
            + (4.0 if state.ambient_temp > 26.0 else 0.0)
        )
        state.scalp_scent += (scalp_target - state.scalp_scent) * (
            1.0 - math.exp(-step_hours / 6.0)
        )
        breath_target = _clamp(
            4.0
            + state.oral * 0.35
            + state.dry_mouth * 0.12
            + state.caffeine * 0.06
            + state.indigestion * 0.12
            + (8.0 if state.illness else 0.0)
        )
        state.breath_scent += (breath_target - state.breath_scent) * (
            1.0 - math.exp(-step_hours / 1.5)
        )
        if profile.gender == "female":
            intimate_target = _clamp(
                max(0.0, state.wetness - 25.0) * 0.22
                + (10.0 if in_period_now else 0.0)
                + (6.0 if view is not None and view.phase == "ovulation" else 0.0)
            )
        else:
            intimate_target = _clamp(4.0 + state.libido * 0.08)
        state.intimate_scent += (intimate_target - state.intimate_scent) * (
            1.0 - math.exp(-step_hours / 4.0)
        )
        # Organ load is a slow fictional proxy, not a lab result: the kidney
        # reacts to water, salt, caffeine and urine concentration; the liver to
        # diet and digestion; the uterus to the cycle and cramping.
        kidney_target = _clamp(
            (100.0 - state.hydration) * 0.55
            + state.sodium * 0.30
            + state.caffeine * 0.15
            + state.urine_color * 8.0
            + (10.0 if state.pregnant else 0.0)
            + (6.0 if state.illness else 0.0)
        )
        state.kidney_load += (kidney_target - state.kidney_load) * (
            1.0 - math.exp(-step_hours / 8.0)
        )
        liver_target = _clamp(
            (100.0 - state.diet_long) * 0.45
            + state.indigestion * 0.35
            + state.caffeine * 0.12
            + state.sodium * 0.10
            + (10.0 if state.illness else 0.0)
            + (8.0 if state.pain >= 40.0 else 0.0)
        )
        state.liver_load += (liver_target - state.liver_load) * (
            1.0 - math.exp(-step_hours / 10.0)
        )
        if profile.gender == "female" and view is not None:
            if state.pregnant and state.conceived_at:
                weeks_now = max(
                    0, int((cursor.timestamp() - state.conceived_at) // (7 * 86400))
                )
                uterus_target = 40.0 + min(45.0, weeks_now * 1.5)
            elif view.phase == "pregnant":
                uterus_target = 40.0 + min(45.0, view.weeks * 1.5)
            else:
                uterus_target = {
                    "menstrual": 62.0,
                    "follicular": 26.0,
                    "ovulation": 46.0,
                    "luteal": 52.0,
                    "premenarche": 8.0,
                    "menopause": 6.0,
                }.get(view.phase, 22.0)
                uterus_target += view.flow * 5.0 + view.cramps * 5.0
            uterus_target = _clamp(uterus_target)
        else:
            uterus_target = 10.0
        state.uterine_congestion += (uterus_target - state.uterine_congestion) * (
            1.0 - math.exp(-step_hours / 6.0)
        )
        quality_target = _clamp(
            const.skin_clarity
            - state.skin_oil * 0.35
            - max(0.0, state.skin - 40.0) * 0.3
            - max(0.0, state.sleep_debt - 4.0) * 2.0
            - state.illness_severity * 0.1
            - max(0.0, 45.0 - state.diet_long) * 0.5
            + max(0.0, state.diet_long - 60.0) * 0.2
        )
        state.skin_quality += (quality_target - state.skin_quality) * (
            1.0 - math.exp(-step_hours / 4.0)
        )

        state.caffeine = _clamp(state.caffeine * 0.5 ** (step_hours / 5.0), 0.0, 100.0)
        state.daylight = 12.0 + 3.5 * math.sin(
            (cursor.month - 4) / 12.0 * 2.0 * math.pi
        )
        state.sedentary = _clamp(
            state.sedentary
            + (1.1 * awake_fraction - 0.5 * sleep_fraction) * step_hours,
            0.0,
            100.0,
        )
        state.sodium = _clamp(state.sodium * 0.5 ** (step_hours / 6.0), 0.0, 100.0)
        state.indigestion = _clamp(
            state.indigestion * 0.5 ** (step_hours / 4.0), 0.0, 100.0
        )
        state.diet_quality += (50.0 - state.diet_quality) * min(1.0, step_hours / 48.0)
        state.diet_long += (state.diet_quality - state.diet_long) * min(
            1.0, step_hours / (24.0 * 14.0)
        )
        state.fitness = _clamp(
            state.fitness - 0.05 * step_hours * awake_fraction, 0.0, 100.0
        )
        state.leg_fatigue = _clamp(
            state.leg_fatigue * 0.5 ** (step_hours / 3.0), 0.0, 100.0
        )
        stress_target = _clamp(
            10.0
            + state.sleep_debt * 2.2
            + state.pain * 0.25
            + max(0.0, 40.0 - state.satiety) * 0.2
            + state.illness_severity * 0.2
        )
        state.stress += (stress_target - state.stress) * (
            1.0 - math.exp(-step_hours / 2.0)
        )
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

        if profile.gender_resolved and settings.enable_intimate:
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
            # Real bodies have some baseline discharge all the time; it follows
            # the cycle and is not the same thing as being aroused.
            basal = 10.0
            if view is not None:
                basal = {
                    "menstrual": 12.0,
                    "follicular": 12.0,
                    "ovulation": 24.0,
                    "luteal": 8.0,
                }.get(view.phase, 10.0)
            target = basal + state.libido * 0.45
            if view is not None and view.fertile:
                target += 6.0
            state.wetness += (target - state.wetness) * min(1.0, step_hours / 2.0)
            state.wetness = _clamp(state.wetness)
        else:
            state.wetness = 0.0

        if in_period_now:
            today = cursor.date().isoformat()
            if state.pads_day != today:
                state.pads_day = today
                state.pads_today = 0
            flow_now = 3 if cycle_day <= 2 else (2 if cycle_day <= 4 else 1)
            state.pad_hours += step_hours * awake_fraction
            interval = PAD_INTERVAL.get(flow_now, 4.0)
            while state.pad_hours >= interval:
                state.pad_hours -= interval
                if state.pads > 0:
                    state.pads -= 1
                    state.pads_today += 1
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
        state.iron = _clamp(state.iron + 8.0)
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
        state.iron = _clamp(state.iron + 2.0)
        state.last_eat_at = now_ts
        state.last_meal_kind = "snack"
        state.last_meal_size = 22.0
        _add_care(state, now, "snack")
    elif action == "drink":
        state.hydration = _clamp(state.hydration + 32.0)
        state.urine = _clamp(state.urine + 12.0)
        state.oral = _clamp(state.oral - 1.5)
        state.breath_scent = max(0.0, state.breath_scent - 6.0)
        state.last_drink_at = now_ts
        state.last_drink_ml = 300.0
        _add_care(state, now, "drink")
    elif action == "shower":
        state.skin = 2.0
        state.sweat = 0.0
        state.skin_oil = 0.0
        state.scent = max(0.0, state.scent - 45.0)
        state.foot_scent = max(0.0, state.foot_scent - 60.0)
        state.period_scent = max(0.0, state.period_scent - 50.0)
        state.scalp_scent = max(0.0, state.scalp_scent - 70.0)
        state.breath_scent = max(0.0, state.breath_scent - 35.0)
        state.intimate_scent = max(0.0, state.intimate_scent - 80.0)
        state.sleep_debt = _clamp(state.sleep_debt - 0.3, 0.0, 24.0)
        state.last_shower_at = now_ts
        _add_care(state, now, "shower")
    elif action == "brush":
        state.oral = 2.0
        state.breath_scent = 2.0
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
        state.intimate_scent = max(state.intimate_scent, 48.0 if ejaculated else 38.0)
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
        state.intimate_scent = max(state.intimate_scent, 42.0)
        state.last_self_at = now_ts
        state.release_count += 1
        state.release_until = max(
            state.release_until,
            now_ts + _release_seconds(state, profile, now, "self"),
        )
        state.release_kind = "self"
        _add_event(
            state,
            now,
            "self_release",
            "自慰了一次，身体松下来",
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
