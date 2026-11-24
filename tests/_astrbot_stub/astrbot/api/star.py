from __future__ import annotations


class Context:
    """Opaque plugin context."""


class Star:
    def __init__(self, context=None) -> None:
        self.context = context


__all__ = ["Context", "Star"]
