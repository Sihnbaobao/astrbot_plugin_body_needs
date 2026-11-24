"""A small hypothalamic-pituitary-ovarian axis.

The model is a coupled differential system rather than a calendar lookup:

- FSH recruits and grows the dominant follicle.
- The follicle secretes estradiol, which feeds back negatively on FSH.
- Estradiol also drives LH through a steep Hill term, so the LH surge is an
  emergent event once the follicle is mature enough.
- The surge ovulates; the corpus luteum then secretes progesterone for a fixed
  luteal life, and its lysis lets FSH escape again.

The configured cycle length still sets the time scale: the follicular growth
rate is calibrated so a persona with a 28-day cycle ovulates around day 14.
Stress and sleep debt scale that rate, which is why a bad week can push
ovulation and the next period later.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

LUTEAL_DAYS = 14.0
FSH_BASE = 30.0
FSH_TAU = 8.0
E2_TAU = 1.5
P4_TAU = 6.0
SURGE_LH = 70.0
SURGE_E2 = 83.5
FOLLICLE_OVULATION = 0.911


@dataclass
class Axis:
    """The five coupled hormone variables for one persona.

    Attributes:
        estrogen: Estradiol proxy, 0-100.
        progesterone: Progesterone proxy, 0-100.
        lh: Luteinizing hormone proxy, 0-100.
        fsh: Follicle-stimulating hormone proxy, 0-100.
        follicle: Dominant-follicle maturity, 0-1.
    """

    estrogen: float = 12.0
    progesterone: float = 5.0
    lh: float = 10.0
    fsh: float = 32.0
    follicle: float = 0.0


def _sigmoid(value: float) -> float:
    """Return a numerically safe logistic value."""
    if value < -60:
        return 0.0
    if value > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-value))


_RATE_CACHE: dict[int, float] = {}


def _crossing_day(rate: float, ovulation_day: int) -> int:
    """Return the day the surge fires for one candidate growth rate."""
    axis = Axis(fsh=34.0)
    for day in range(1, ovulation_day + 12):
        for _ in range(24):
            axis, hit = _step_follicular(axis, 1.0, rate=rate, anovulatory=False)
            if hit:
                return day
    return 999


def growth_rate(ovulation_day: int) -> float:
    """Return the follicle growth rate that lands ovulation on that day.

    The rate is found by bisection against the same equations the runtime
    uses, so the configured cycle length stays authoritative even though the
    surge itself is an emergent threshold event.

    Args:
        ovulation_day: Expected ovulation day of the configured cycle.

    Returns:
        A per-day growth constant, cached per expected day.
    """
    cached = _RATE_CACHE.get(ovulation_day)
    if cached is not None:
        return cached
    low, high = 0.01, 0.80
    for _ in range(28):
        middle = (low + high) / 2.0
        if _crossing_day(middle, ovulation_day) <= ovulation_day:
            high = middle
        else:
            low = middle
    # `high` is the fast side of the discontinuity that flips the crossing
    # day, so it is the rate that actually lands on the requested day.
    rate = high
    _RATE_CACHE[ovulation_day] = rate
    return rate


def step(
    axis: Axis,
    dt_hours: float,
    *,
    day: int,
    ovulation_day: int,
    length: int,
    anovulatory: bool,
    stress: float = 1.0,
) -> tuple[Axis, bool]:
    """Advance the axis by a slice of hours.

    Args:
        axis: Current axis values.
        dt_hours: Slice length in hours.
        day: Current cycle day, 1-based.
        ovulation_day: Day ovulation happened this cycle, 0 while follicular.
        length: Expected ovulation day, used to calibrate follicular growth.
        anovulatory: Whether this cycle skips ovulation.
        stress: Follicular stretch factor from sleep debt and illness.

    Returns:
        The updated axis and whether ovulation happened in this slice.
    """
    if ovulation_day > 0:
        return _step_luteal(axis, dt_hours, day - ovulation_day), False
    rate = growth_rate(length) / max(1.0, stress)
    return _step_follicular(axis, dt_hours, rate=rate, anovulatory=anovulatory)


def _step_follicular(
    axis: Axis,
    dt_hours: float,
    *,
    rate: float,
    anovulatory: bool,
) -> tuple[Axis, bool]:
    """Grow the follicle, apply feedback and check for the LH surge."""
    fsh_target = FSH_BASE * (1.0 - 0.55 * axis.estrogen / 100.0)
    fsh_target += 6.0 * max(0.0, 1.0 - axis.progesterone / 15.0)
    axis.fsh += (fsh_target - axis.fsh) * dt_hours / FSH_TAU

    drive = 0.95 + 0.05 * axis.fsh / FSH_BASE
    rate *= drive
    if anovulatory and axis.follicle >= 0.78:
        axis.follicle = max(0.0, axis.follicle - 0.02 * dt_hours / 24.0)
    else:
        axis.follicle = min(
            1.0,
            axis.follicle
            + (rate * (1.0 - axis.follicle) - 0.005 * axis.follicle) * dt_hours / 24.0,
        )

    target = 5.0 + 92.0 * axis.follicle**1.7
    axis.estrogen += (target - axis.estrogen) * dt_hours / E2_TAU
    if anovulatory:
        axis.lh = 10.0 + 12.0 * axis.estrogen / 100.0
        return axis, False

    axis.lh = 10.0 + 92.0 * _sigmoid((axis.estrogen - 82.0) / 2.5)
    if axis.lh >= SURGE_LH and axis.estrogen >= SURGE_E2 - 3.5:
        axis.follicle = 0.0
        axis.progesterone = 20.0
        axis.estrogen = 88.0
        axis.fsh = 20.0
        return axis, True
    return axis, False


def _step_luteal(axis: Axis, dt_hours: float, luteal_day: int) -> Axis:
    """Run the corpus luteum, whose life sets the luteal phase length.

    Args:
        axis: Current axis values.
        dt_hours: Slice length in hours.
        luteal_day: Days since ovulation, 1-based.

    Returns:
        The updated axis.
    """
    fraction = min(1.0, max(0.0, luteal_day / LUTEAL_DAYS))
    wave = math.sin(math.pi * fraction)
    axis.progesterone += (20.0 + 55.0 * wave - axis.progesterone) * dt_hours / P4_TAU
    axis.estrogen += (40.0 + 22.0 * wave - axis.estrogen) * dt_hours / 8.0
    axis.lh = max(10.0, axis.lh - 90.0 * dt_hours)
    axis.fsh += (
        (12.0 + 18.0 * max(0.0, 1.0 - axis.progesterone / 40.0) - axis.fsh)
        * dt_hours
        / 10.0
    )
    axis.follicle = 0.0
    return axis


def seed(day: int, length: int, period: int, anovulatory: bool) -> Axis:
    """Rebuild the axis for one cycle day by running the model forward.

    Args:
        day: 1-based cycle day.
        length: Configured cycle length in days.
        period: Bleeding days at the start of the cycle.
        anovulatory: Whether this cycle skips ovulation.

    Returns:
        The axis and the ovulation day (0 when it has not happened).
    """
    axis = Axis(fsh=34.0)
    ovulation_day = 0
    expected = max(period + 1, length - int(LUTEAL_DAYS))
    for current in range(1, max(1, day) + 1):
        for _ in range(24):
            axis, ovulated = step(
                axis,
                1.0,
                day=current,
                ovulation_day=ovulation_day,
                length=expected,
                anovulatory=anovulatory,
            )
            if ovulated and not ovulation_day:
                ovulation_day = current
                if current == day:
                    return axis, ovulation_day
    return axis, ovulation_day


def basal_temperature(axis: Axis) -> float:
    """Return the waking basal temperature in Celsius.

    Progesterone shifts the set point up by a few tenths of a degree, which is
    the classic sign that ovulation has happened.

    Args:
        axis: Current axis values.

    Returns:
        A temperature between about 36.15 and 36.75 Celsius.
    """
    value = 36.32 + 0.0042 * (axis.progesterone - 10.0)
    return round(max(36.15, min(36.75, value)), 2)


def mucus_score(axis: Axis) -> int:
    """Return cervical mucus from dry (0) to egg-white (3).

    Estrogen thins and stretches the mucus; progesterone makes it thick again.

    Args:
        axis: Current axis values.

    Returns:
        An integer 0-3.
    """
    raw = (
        4.4
        * (max(0.0, axis.estrogen) / 100.0) ** 1.5
        * max(0.0, 1.0 - axis.progesterone / 90.0)
    )
    if raw < 0.5:
        return 0
    if raw < 1.3:
        return 1
    if raw < 2.3:
        return 2
    return 3
