from __future__ import annotations


class FunctionTool:
    def __init__(
        self, name="", description="", parameters=None, handler=None, **kwargs
    ):
        self.name = name
        self.description = description
        self.parameters = parameters or {}
        self.handler = handler


class ToolSet:
    def __init__(self) -> None:
        self.tools: list = []

    def add_tool(self, tool) -> None:
        self.tools.append(tool)


__all__ = ["FunctionTool", "ToolSet"]
