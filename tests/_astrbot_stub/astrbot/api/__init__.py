from __future__ import annotations

import logging


class AstrBotConfig(dict):
    """Dict-like plugin config with the save hook AstrBot provides."""

    def save_config(self) -> None:
        return None


logger = logging.getLogger("astrbot")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())

__all__ = ["AstrBotConfig", "logger"]
