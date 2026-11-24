from __future__ import annotations


class ProviderRequest:
    """Minimal stand-in carrying the fields the plugin touches."""

    def __init__(self, prompt: str = "", **kwargs) -> None:
        self.prompt = prompt
        self.contexts: list = []
        self.extra_user_content_parts: list = []
        self.func_tool = None
        for key, value in kwargs.items():
            setattr(self, key, value)


__all__ = ["ProviderRequest"]
