from __future__ import annotations


class AstrMessageEvent:
    """Opaque message event."""


class EventMessageType:
    GROUP_MESSAGE = 1
    PRIVATE_MESSAGE = 2


class _Filter:
    EventMessageType = EventMessageType

    def command(self, name, alias=None, **kwargs):
        def decorator(func):
            return func

        return decorator

    def event_message_type(self, message_type, priority=0):
        def decorator(func):
            return func

        return decorator

    def on_llm_request(self, priority=0):
        def decorator(func):
            return func

        return decorator


filter = _Filter()

__all__ = ["AstrMessageEvent", "EventMessageType", "filter"]
