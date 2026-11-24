from __future__ import annotations

from typing import Any


def json_response(payload: Any, **kwargs) -> Any:
    return payload


def error_response(message: str, **kwargs) -> dict:
    return {"error": message}


class request:  # noqa: N801 - mirrors AstrBot's request singleton
    @staticmethod
    async def json(default: Any = None) -> Any:
        return default


__all__ = ["error_response", "json_response", "request"]
