"""Configuration parsing for the body-needs plugin.

AstrBot hands plugins a dict-like config object. Everything downstream receives
a plain Settings object so the simulation never has to guess defaults or
re-read the raw config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

CONTRACEPTIONS = ("none", "condom", "pill")
GENDERS = ("auto", "female", "male", "none")
GROUP_DETAILS = ("basic", "full")
INTENSITIES = ("subtle", "natural", "strong")

_WINDOW_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


def _as_int(value: Any, default: int) -> int:
    """Convert a config value to int without raising on user input."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    """Accept real bools plus the usual string spellings used in config files."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _choice(value: Any, options: tuple[str, ...], default: str) -> str:
    """Return a normalized enum value, falling back to the documented default."""
    candidate = str(value or "").strip().lower()
    return candidate if candidate in options else default


def _valid_window(value: str) -> bool:
    """Check a HH:MM-HH:MM sleep window, allowing 24:00 as an end time."""
    match = _WINDOW_RE.match(value.strip())
    if not match:
        return False
    hours = (int(match.group(1)), int(match.group(3)))
    minutes = (int(match.group(2)), int(match.group(4)))
    return all(0 <= hour <= 24 for hour in hours) and all(
        0 <= minute <= 59 for minute in minutes
    )


@dataclass(frozen=True)
class Settings:
    """One parsed plugin configuration.

    Attributes:
        enabled: Master switch; when false the plugin does nothing.
        gender: "auto" reads the persona prompt, otherwise forces a body type.
        age: Fallback age for the physiology curves; 0 means read the persona
            prompt only.
        lifespan: Species lifespan in years; 0 means detect it from the prompt.
        persona_id: Fixed persona whose body is used; empty follows the chat.
        timezone: IANA time zone; empty means use AstrBot's configured zone.
        sleep_window: Local HH:MM-HH:MM window used for slower metabolism.
        cycle_length: Menstrual cycle length in days.
        period_length: Bleeding days at the start of a cycle.
        cycle_start: Optional anchor date (day 1) in ISO format.
        group_detail: How much body detail a group chat may see.
        intensity: How strongly the prompt block is phrased.
        enable_cycle: Track the menstrual cycle.
        enable_intimate: Track intimate physiology; needs a resolvable gender
            and a private chat, and never depends on age.
        enable_pregnancy: Let a fertile encounter possibly start a pregnancy.
        contraception: none, condom or pill; scales the conception chance.
        enable_hygiene: Track showering and tooth brushing.
        enable_sleep: Let the sleep window change metabolism and recovery.
        enable_tools: Offer the body tools to the chat model.
        prompt_extra: Optional extra instruction appended to the body block.
        event_keep_days: How long notable body events stay in the prompt.
        max_events: Hard cap on stored notable events.
        debug: Emit extra diagnostics.
    """

    enabled: bool = True
    gender: str = "auto"
    age: int = 0
    lifespan: int = 0
    persona_id: str = ""
    timezone: str = ""
    sleep_window: str = "00:00-08:00"
    cycle_length: int = 28
    period_length: int = 5
    cycle_start: str = ""
    group_detail: str = "basic"
    intensity: str = "natural"
    enable_cycle: bool = True
    enable_intimate: bool = True
    enable_pregnancy: bool = True
    contraception: str = "none"
    enable_hygiene: bool = True
    enable_sleep: bool = True
    enable_tools: bool = True
    prompt_extra: str = ""
    event_keep_days: int = 7
    max_events: int = 30
    debug: bool = False

    @classmethod
    def from_config(cls, config: Any) -> Settings:
        """Build settings from AstrBot's plugin config.

        Args:
            config: The plugin config object injected by AstrBot.

        Returns:
            A validated, immutable settings snapshot.
        """

        def get(key: str, default: Any = "") -> Any:
            try:
                value = config.get(key, default)
            except AttributeError:
                return default
            return default if value is None else value

        cycle_length = min(45, max(18, _as_int(get("CYCLE_LENGTH", 28), 28)))
        period_length = min(10, max(2, _as_int(get("PERIOD_LENGTH", 5), 5)))
        if period_length >= cycle_length:
            period_length = max(2, cycle_length // 4)

        cycle_start = str(get("CYCLE_START", "")).strip()
        if cycle_start:
            try:
                date.fromisoformat(cycle_start)
            except ValueError:
                cycle_start = ""

        sleep_window = str(get("SLEEP_WINDOW", "00:00-08:00")).strip()
        if not _valid_window(sleep_window):
            sleep_window = "00:00-08:00"

        return cls(
            enabled=_as_bool(get("ENABLED", True), True),
            gender=_choice(get("GENDER", "auto"), GENDERS, "auto"),
            age=max(0, min(100000, _as_int(get("AGE", 0), 0))),
            lifespan=max(0, min(100000, _as_int(get("LIFESPAN", 0), 0))),
            persona_id=str(get("PERSONA_ID", "")).strip(),
            timezone=str(get("TIMEZONE", "")).strip(),
            sleep_window=sleep_window,
            cycle_length=cycle_length,
            period_length=period_length,
            cycle_start=cycle_start,
            group_detail=_choice(get("GROUP_DETAIL", "basic"), GROUP_DETAILS, "basic"),
            intensity=_choice(get("INTENSITY", "natural"), INTENSITIES, "natural"),
            enable_cycle=_as_bool(get("ENABLE_CYCLE", True), True),
            enable_intimate=_as_bool(get("ENABLE_INTIMATE", True), True),
            enable_pregnancy=_as_bool(get("ENABLE_PREGNANCY", True), True),
            contraception=_choice(get("CONTRACEPTION", "none"), CONTRACEPTIONS, "none"),
            enable_hygiene=_as_bool(get("ENABLE_HYGIENE", True), True),
            enable_sleep=_as_bool(get("ENABLE_SLEEP", True), True),
            enable_tools=_as_bool(get("ENABLE_TOOLS", True), True),
            prompt_extra=str(get("PROMPT_EXTRA", "")).strip()[:500],
            event_keep_days=min(30, max(1, _as_int(get("EVENT_KEEP_DAYS", 7), 7))),
            max_events=min(100, max(5, _as_int(get("MAX_EVENTS", 30), 30))),
            debug=_as_bool(get("DEBUG", False), False),
        )
