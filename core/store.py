"""Small JSON store for per-persona body state.

The plugin keeps one file per persona so switching persona in a chat switches
bodies cleanly. Writes are atomic, and a corrupt file is moved aside instead of
breaking every later reply.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

from .model import BodyState


def _safe_name(persona_id: str) -> str:
    """Turn an arbitrary persona id into a short filesystem-safe stem."""
    cleaned = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", persona_id or "")
    cleaned = cleaned.strip("_")[:40]
    digest = hashlib.sha1((persona_id or "persona").encode("utf-8")).hexdigest()[:8]
    return f"{cleaned or 'persona'}-{digest}"


def new_state(persona_id: str, now: datetime) -> BodyState:
    """Create a plausible first state instead of starting every meter at zero.

    Args:
        persona_id: Persona identifier used to seed stable initial values.
        now: Local timezone-aware current time.

    Returns:
        A fresh state with recent meal, drink, shower and sleep timestamps.
    """
    seed = int(hashlib.sha1(persona_id.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    stamp = now.timestamp()
    satiety = rng.uniform(70, 88)
    hydration = rng.uniform(68, 88)
    energy = rng.uniform(65, 88)
    bladder = rng.uniform(8, 28)
    cleanliness = rng.uniform(72, 92)
    oral_health = rng.uniform(62, 90)
    return BodyState(
        persona_id=persona_id,
        updated_at=stamp,
        last_active_at=stamp,
        satiety=satiety,
        hydration=hydration,
        bladder=bladder,
        cleanliness=cleanliness,
        oral_health=oral_health,
        energy=energy,
        desire=rng.uniform(5, 20),
        stomach=max(10.0, satiety * 0.95),
        glucose=max(10.0, satiety * 0.9),
        urine=bladder,
        skin=100.0 - cleanliness,
        oral=100.0 - oral_health,
        sleep_debt=min(24.0, (100.0 - energy) / 4.5),
        last_eat_at=stamp - rng.uniform(0.5, 2.0) * 3600,
        last_drink_at=stamp - rng.uniform(1, 4) * 3600,
        last_shower_at=stamp - rng.uniform(10, 30) * 3600,
        last_brush_at=stamp - rng.uniform(3, 10) * 3600,
        last_toilet_at=stamp - rng.uniform(1, 3) * 3600,
        last_release_at=stamp - rng.uniform(2, 8) * 86400,
        last_meal_kind="meal",
        last_meal_size=45.0,
        last_drink_ml=250.0,
    )


class Store:
    """Persistence for body state files."""

    def __init__(self, root: Path) -> None:
        """Create the store and ensure its directory exists.

        Args:
            root: Plugin data directory.
        """
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, persona_id: str) -> Path:
        """Return the JSON path for one persona."""
        return self.root / f"{_safe_name(persona_id)}.json"

    def load(self, persona_id: str, now: datetime | None = None) -> BodyState:
        """Load one state, creating a fresh one when no file exists.

        Args:
            persona_id: Persona identifier.
            now: Current local time used only when creating a state.

        Returns:
            The loaded state.
        """
        now = now or datetime.now().astimezone()
        path = self.path_for(persona_id)
        if not path.exists():
            return new_state(persona_id, now)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("state root is not an object")
            state = BodyState.from_dict(data)
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            broken = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
            try:
                path.replace(broken)
            except OSError:
                pass
            return new_state(persona_id, now)
        state.persona_id = persona_id
        if state.updated_at <= 0:
            state.updated_at = now.timestamp()
        return state

    def save(self, state: BodyState) -> None:
        """Atomically write one state file.

        Args:
            state: State to persist.
        """
        path = self.path_for(state.persona_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def list_states(self, now: datetime | None = None) -> list[BodyState]:
        """Load every stored state.

        Args:
            now: Current local time used for files that need repair.

        Returns:
            States sorted by persona id.
        """
        now = now or datetime.now().astimezone()
        states = []
        for path in sorted(self.root.glob("*.json")):
            if path.name.endswith(".tmp"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                persona_id = str(data.get("persona_id") or path.stem)
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                continue
            states.append(self.load(persona_id, now))
        return states

    def delete(self, persona_id: str) -> bool:
        """Remove a persona's state file.

        Args:
            persona_id: Persona identifier.

        Returns:
            True when a file was removed.
        """
        path = self.path_for(persona_id)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
