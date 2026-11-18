"""Integration smoke tests with a fake AstrBot context."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from astrbot.api.provider import ProviderRequest
from astrbot_plugin_body_needs import main as plugin_module


class FakeResult:
    """Minimal MessageEventResult replacement."""

    def __init__(self, text: str) -> None:
        self.text = text

    def get_plain_text(self) -> str:
        return self.text


class FakeEvent:
    """Minimal AstrMessageEvent replacement."""

    def __init__(self, *, private: bool = True, text: str = "/身体 状态") -> None:
        self.unified_msg_origin = "test:umo"
        self.message_str = text
        self.private = private

    def plain_result(self, text: str) -> FakeResult:
        return FakeResult(text)

    def is_private_chat(self) -> bool:
        return self.private

    def is_admin(self) -> bool:
        return True

    def get_platform_name(self) -> str:
        return "test"


class FakePersonaManager:
    """Persona manager with one fixture persona."""

    def __init__(self, prompt: str) -> None:
        self.persona = {"name": "test", "prompt": prompt}
        self.personas_v3 = [self.persona]
        self.default_persona = "test"

    def get_persona_v3_by_id(self, persona_id):
        return self.persona if persona_id == "test" else None

    async def get_default_persona_v3(self, umo=None):
        return self.persona

    async def resolve_selected_persona(self, **kwargs):
        return (None, self.persona, None, False)


class FakeConversationManager:
    """No stored conversation is needed for these tests."""

    async def get_curr_conversation_id(self, umo):
        return None

    async def get_conversation(self, umo, conversation_id):
        return None


class FakeConfig(dict):
    """Dict-like config with AstrBot's save hook."""

    def __init__(self, values):
        super().__init__(values)
        self.saved = 0

    def save_config(self):
        self.saved += 1


class FakeContext:
    """Context surface used by BodyNeedsPlugin."""

    def __init__(self, prompt: str) -> None:
        self.persona_manager = FakePersonaManager(prompt)
        self.conversation_manager = FakeConversationManager()
        self.routes = []

    def get_config(self, umo=None):
        return {"timezone": "Asia/Shanghai", "provider_settings": {}}

    def register_web_api(self, path, handler, methods, desc):
        self.routes.append(path)


def make_plugin(monkeypatch, tmp_path, prompt: str):
    """Build a plugin whose data directory is isolated to tmp_path."""
    monkeypatch.setattr(
        plugin_module,
        "get_astrbot_plugin_data_path",
        lambda: str(tmp_path),
    )
    context = FakeContext(prompt)
    config = FakeConfig({"TIMEZONE": "Asia/Shanghai", "ENABLE_INTIMATE": True})
    plugin = plugin_module.BodyNeedsPlugin(context, config)
    return plugin, context


@pytest.mark.asyncio
async def test_inject_body_and_tools(monkeypatch, tmp_path) -> None:
    plugin, context = make_plugin(monkeypatch, tmp_path, "一个25岁的女性，性格安静。")
    await plugin.initialize()
    event = FakeEvent()
    req = ProviderRequest(prompt="你好")
    await plugin.inject_body(event, req)
    parts = req.extra_user_content_parts
    assert len(parts) == 1
    assert parts[0]["text"].startswith(plugin_module.BODY_MARKER)
    assert {tool.name for tool in req.func_tool.tools} == {
        "body_status",
        "body_action",
    }
    # A second pass in the same tool loop replaces rather than duplicates.
    await plugin.inject_body(event, req)
    assert len(req.extra_user_content_parts) == 1
    assert "/astrbot_plugin_body_needs/page/status" in context.routes
    await plugin.terminate()


@pytest.mark.asyncio
async def test_command_no_longer_feeds_her(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "一个25岁的女性，性格安静。")
    await plugin.initialize()
    before = plugin.store.load("test", plugin._now()).hunger
    event = FakeEvent(text="/身体 喂")
    results = []
    async for result in plugin.body_command(event):
        results.append(result.get_plain_text())
    assert "身体指令" in results[0]
    assert "喂" not in results[0].split("她的身体由她自己维持")[0]
    state = plugin.store.load("test", plugin._now())
    assert state.hunger == before
    await plugin.terminate()


@pytest.mark.asyncio
async def test_debug_command_remains_for_maintenance(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "一个25岁的女性，性格安静。")
    await plugin.initialize()
    event = FakeEvent(text="/身体 调试 喂")
    results = []
    async for result in plugin.body_command(event):
        results.append(result.get_plain_text())
    assert "[调试]" in results[0]
    assert "吃了一顿饭" in results[0]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_page_config_toggles_adult_and_validates(monkeypatch, tmp_path) -> None:
    plugin, context = make_plugin(monkeypatch, tmp_path, "一个25岁的女性，性格安静。")
    await plugin.initialize()
    ok, message, values = plugin._apply_config_updates(
        {"ENABLE_INTIMATE": False, "CYCLE_LENGTH": 30}
    )
    assert ok, message
    assert plugin.settings.enable_intimate is False
    assert plugin.settings.cycle_length == 30
    assert values["ENABLE_INTIMATE"] is False
    assert plugin.config.saved >= 1
    assert "/astrbot_plugin_body_needs/page/config" in context.routes

    ok, message, _ = plugin._apply_config_updates({"CYCLE_LENGTH": 99})
    assert not ok
    assert "18-45" in message

    ok, message, _ = plugin._apply_config_updates({"CYCLE_START": "2026-11-01"})
    assert ok, message
    state = plugin.store.load("test", plugin._now())
    assert state.cycle_anchor == "2026-11-01"
    await plugin.terminate()


@pytest.mark.asyncio
async def test_page_tracks_one_persona(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "一个25岁的女性，性格安静。")
    await plugin.initialize()
    assert len(plugin._persona_entries()) == 1
    profile = plugin._profile_for("test", "test", "一个25岁的女性，性格安静。")
    assert profile.gender == "female"
    ok, message, values = plugin._apply_config_updates({"PERSONA_ID": "test"})
    assert ok, message
    assert values["PERSONA_ID"] == "test"
    await plugin.terminate()


@pytest.mark.asyncio
async def test_genderless_persona_gets_no_intimacy_tool(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "一个普通的助手。")
    await plugin.initialize()
    event = FakeEvent()
    profile = plugin._profile_for("test", "test", "一个普通的助手。")
    tools = plugin._body_tools(profile, True)
    body_action = next(tool for tool in tools.tools if tool.name == "body_action")
    assert "intimacy" not in body_action.parameters["properties"]["action"]["enum"]
    await plugin.terminate()
