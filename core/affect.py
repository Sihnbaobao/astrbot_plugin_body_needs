"""Publish a small affect snapshot for the persona emotion layer.

The body does not own the persona's mood. It only publishes what the body is
currently doing to it: how much distress it adds, how much tension it puts on
the temper, whether it hurts, and a short line a prompt can quote. Another
plugin (persona presence) reads this file and folds it into its own emotion
state, so the coupling stays a one-way contract instead of two plugins
importing each other.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from .config import Settings
from .model import BodyState, CycleView, sleep_view
from .profile import Profile

VITALS_FILE = "vitals.json"
VITALS_VERSION = 1
SLEEP_FILE = "sleep.json"
SLEEP_VERSION = 1
MAX_AGE_DAYS = 30


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp a number into a range."""
    return max(low, min(high, value))


def build_affect(state: BodyState, view: CycleView | None) -> dict[str, Any]:
    """Turn one body state into the affect the emotion layer consumes.

    Args:
        state: Current body state.
        view: Cycle view, or None when the persona has no cycle.

    Returns:
        A JSON-serialisable snapshot with valence, tension, pain and a line.
    """
    hunger = _clamp((100.0 - state.satiety) / 100.0)
    thirst = _clamp((100.0 - state.hydration) / 100.0)
    fatigue = _clamp((100.0 - state.energy) / 100.0)
    sleep = _clamp(state.sleep_debt / 24.0)
    bowel = _clamp(state.bowel / 100.0)
    dirt = _clamp(max(state.skin, state.oral) / 100.0)
    libido = _clamp(state.libido / 100.0)
    pain = 0.0
    cycle_mood = 0.0
    if view is not None:
        pain = _clamp(
            view.cramps / 3.0 * 0.8
            + view.headache / 2.0 * 0.1
            + view.backache / 2.0 * 0.1
        )
        cycle_mood = max(-1.0, min(1.0, view.mood / 3.0))

    comfort = max(
        -1.0,
        min(
            1.0,
            0.5 * (state.glucose - 45.0) / 55.0
            + 0.3 * (state.satiety - 50.0) / 100.0
            - 0.5 * state.indigestion / 100.0
            - 0.25 * max(0.0, 55.0 - state.hydration) / 55.0,
        ),
    )
    distress = _clamp(
        0.15 * state.indigestion / 100.0
        + 0.30 * hunger
        + 0.18 * thirst
        + 0.22 * fatigue
        + 0.15 * pain
        + 0.08 * max(0.0, bowel - 0.5) * 2.0
        + 0.07 * dirt
    )
    tension = _clamp(
        0.45 * max(0.0, hunger - 0.55) / 0.45
        + 0.25 * max(0.0, thirst - 0.55) / 0.45
        + 0.25 * pain
        + 0.25 * max(0.0, sleep - 0.35) / 0.65
        + 0.15 * max(0.0, fatigue - 0.6) / 0.4
        + 0.15 * max(0.0, bowel - 0.6) / 0.4
    )
    energy = _clamp(1.0 - fatigue)
    phase = ""
    cycle_day = 0
    pms = 0.0
    irritability = 0.0
    sensitivity = 0.0
    tearfulness = 0.0
    if view is not None:
        phase = view.phase
        cycle_day = view.day
        # The last five days before the period are the classic PMS window.
        if view.phase == "luteal" and 0 < view.days_to_next <= 5:
            pms = _clamp(0.35 + 0.12 * view.mood + 0.08 * view.headache)
        irritability = _clamp(
            0.35 * tension
            + 0.45 * pms
            + 0.10 * (view.cramps / 3.0)
            + 0.12 * max(0.0, -cycle_mood)
        )
        sensitivity = _clamp(0.25 * distress + 0.40 * pms + 0.15 * pain)
        tearfulness = _clamp(0.20 * distress + 0.35 * pms + 0.10 * pain)
    weather_mood = {
        "clear": 0.06,
        "cloudy": -0.03,
        "rain": -0.08,
        "snow": -0.05,
    }.get(state.weather, 0.0)
    seasonal = (state.daylight - 12.0) / 3.5 * 0.05
    valence = max(
        -1.0,
        min(
            1.0,
            0.55 * (0.45 - distress)
            + 0.25 * cycle_mood
            + 0.20 * (energy - 0.5)
            + 0.15 * comfort
            + weather_mood
            + seasonal,
        ),
    )
    arousal = _clamp(0.25 + 0.35 * tension + 0.20 * (1.0 - energy) + 0.20 * libido)

    labels: list[str] = []
    if hunger >= 0.65:
        labels.append("饿")
    if thirst >= 0.65:
        labels.append("渴")
    if fatigue >= 0.65:
        labels.append("困")
    if pain >= 0.2:
        labels.append("疼")
    if bowel >= 0.65:
        labels.append("憋")
    if dirt >= 0.6:
        labels.append("脏")
    if libido >= 0.65:
        labels.append("想要")

    if pms >= 0.45:
        labels.append("经前")
    if labels:
        line = "、".join(labels[:2])
        if pms >= 0.45:
            line += "，这几天容易烦、也容易委屈"
        elif tension >= 0.5:
            line += "，耐心比平时薄"
    elif valence <= -0.3:
        line = "身体有点拖着，情绪比平时低"
    elif tension >= 0.5:
        line = "身体有点绷着，容易不耐烦"
    else:
        line = ""

    return {
        "updated_at": datetime.now().astimezone().isoformat(),
        "valence": round(valence, 3),
        "arousal": round(arousal, 3),
        "distress": round(distress, 3),
        "tension": round(tension, 3),
        "pain": round(pain, 3),
        "energy": round(energy, 3),
        "dirt": round(dirt, 3),
        "comfort": round(comfort, 3),
        "daylight": round(state.daylight, 2),
        "seasonal": round(seasonal, 3),
        "libido": round(libido, 3),
        "wetness": round(_clamp(state.wetness / 100.0), 3),
        "scent": round(_clamp(state.scent / 100.0), 3),
        "cycle_phase": phase,
        "cycle_day": cycle_day,
        "pms": round(pms, 3),
        "irritability": round(irritability, 3),
        "sensitivity": round(sensitivity, 3),
        "tearfulness": round(tearfulness, 3),
        "away_until": state.away_until,
        "away_reason": state.away_reason,
        "labels": labels,
        "line": line,
    }


def match_emotion(personas: dict[str, Any], persona_id: str) -> dict[str, Any] | None:
    """Find the emotion entry that belongs to one body persona.

    Args:
        personas: The ``personas`` mapping from the emotion plugin.
        persona_id: Body persona id.

    Returns:
        The matching entry, or None when nothing matches.
    """
    if not isinstance(personas, dict) or not persona_id:
        return None
    if persona_id in personas:
        entry = personas[persona_id]
        return entry if isinstance(entry, dict) else None
    for key, item in personas.items():
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or key)
        if name and (persona_id in name or name in persona_id):
            return item
    if len(personas) == 1:
        only = next(iter(personas.values()))
        return only if isinstance(only, dict) else None
    return None


def publish_sleep(
    data_dir: str,
    state: BodyState,
    profile: Profile,
    settings: Settings,
    now: datetime,
    set_current: bool = False,
) -> dict[str, Any]:
    """Publish one persona's sleep status for the other plugins.

    The file is a one-way contract: the body says whether she is asleep, in
    bed, or free, and the persona, QZone, music and Bilibili plugins read it so
    their own plans follow her day instead of a fixed clock.

    Args:
        data_dir: The body plugin's data directory.
        state: Current body state.
        profile: Resolved persona profile.
        settings: Plugin settings; the sleep module can be switched off.
        now: Current local time.
        set_current: Make this persona the default entry other plugins follow.

    Returns:
        The snapshot that was written for this persona.
    """
    path = os.path.join(str(data_dir), SLEEP_FILE)
    payload: dict[str, Any] = {
        "version": SLEEP_VERSION,
        "current": "",
        "personas": {},
    }
    try:
        with open(path, encoding="utf-8") as file:
            existing = json.load(file)
        if isinstance(existing, dict):
            if isinstance(existing.get("personas"), dict):
                payload["personas"] = existing["personas"]
            payload["current"] = str(existing.get("current") or "")
    except (OSError, ValueError):
        pass

    view = sleep_view(state, now, profile, settings)
    snapshot = {
        "persona_id": state.persona_id,
        "name": profile.name,
        "asleep": view.asleep,
        "in_bed": view.in_bed,
        "kind": view.kind,
        "since": view.since,
        "bed_at": view.bed_at,
        "wake_at": view.wake_at,
        "slept_hours": round(view.slept_hours, 2),
        "bedtime": 0.0 if view.asleep else view.bedtime,
        "nap_at": view.nap_at,
        "awake_since": state.last_wake_at,
        "busy_until": state.busy_until,
        "busy_kind": state.busy_kind,
        "enabled": bool(settings.enabled and settings.enable_sleep),
        "gate": bool(settings.enable_sleep_gate),
        "line": view.line,
        "debt": round(state.sleep_debt, 2),
        "care_until": state.care_until,
        "care_kind": state.care_kind,
        "phase_hours": round(state.sleep_phase / 3600.0, 2),
        "updated_at": now.isoformat(),
    }
    payload["personas"][state.persona_id] = snapshot
    if set_current or not payload["current"]:
        payload["current"] = state.persona_id

    cutoff = now.timestamp() - MAX_AGE_DAYS * 86400
    kept = {}
    for key, item in payload["personas"].items():
        try:
            stamp = datetime.fromisoformat(str(item.get("updated_at"))).timestamp()
        except (TypeError, ValueError):
            stamp = 0.0
        if stamp >= cutoff:
            kept[key] = item
    payload["personas"] = kept
    if payload["current"] not in kept:
        payload["current"] = state.persona_id
    payload["updated_at"] = now.isoformat()

    try:
        os.makedirs(str(data_dir), exist_ok=True)
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temp, path)
    except OSError:
        pass
    return snapshot


def publish(
    data_dir: str | os.PathLike[str],
    state: BodyState,
    view: CycleView | None,
    now: datetime,
) -> dict[str, Any]:
    """Merge one persona's affect into the shared vitals file.

    Args:
        data_dir: The body plugin's data directory.
        state: Current body state.
        view: Cycle view, or None.
        now: Current local time, used to prune stale entries.

    Returns:
        The snapshot that was written for this persona.
    """
    path = os.path.join(str(data_dir), VITALS_FILE)
    payload: dict[str, Any] = {"version": VITALS_VERSION, "personas": {}}
    try:
        with open(path, encoding="utf-8") as file:
            existing = json.load(file)
        if isinstance(existing, dict) and isinstance(existing.get("personas"), dict):
            payload["personas"] = existing["personas"]
    except (OSError, ValueError):
        pass

    snapshot = build_affect(state, view)
    snapshot["updated_at"] = now.isoformat()
    payload["personas"][state.persona_id] = snapshot
    cutoff = now.timestamp() - MAX_AGE_DAYS * 86400
    kept = {}
    for key, item in payload["personas"].items():
        try:
            stamp = datetime.fromisoformat(str(item.get("updated_at"))).timestamp()
        except (TypeError, ValueError):
            stamp = 0.0
        if stamp >= cutoff:
            kept[key] = item
    payload["personas"] = kept
    payload["updated_at"] = now.isoformat()

    try:
        os.makedirs(str(data_dir), exist_ok=True)
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temp, path)
    except OSError:
        pass
    return snapshot
