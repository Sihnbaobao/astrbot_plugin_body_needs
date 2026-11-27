"""Integration smoke tests with a fake AstrBot context."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from astrbot_plugin_body_needs import main as plugin_module
from astrbot_plugin_body_needs.core.model import BodyState

from astrbot.api.provider import ProviderRequest


class FakeResult:
    """Minimal MessageEventResult replacement."""

    def __init__(self, text: str) -> None:
        self.text = text

    def get_plain_text(self) -> str:
        return self.text


class At:
    """Minimal At component; the class name is what the recorder checks."""

    def __init__(self, qq) -> None:
        self.qq = qq


class Reply:
    """Minimal Reply component; the class name is what the recorder checks."""

    def __init__(self, sender_id, message_str: str = "") -> None:
        self.sender_id = sender_id
        self.message_str = message_str


class FakeEvent:
    """Minimal AstrMessageEvent replacement."""

    def __init__(
        self,
        *,
        private: bool = True,
        text: str = "/身体 状态",
        components=None,
        sender: str = "小明",
        at_or_wake: bool = False,
    ) -> None:
        self.unified_msg_origin = "test:umo"
        self.message_str = text
        self.private = private
        self.message_obj = SimpleNamespace(message=list(components or []))
        self.sender = sender
        self.at_or_wake = at_or_wake

    def get_self_id(self) -> str:
        return "42"

    def get_sender_name(self) -> str:
        return self.sender

    @property
    def is_at_or_wake_command(self) -> bool:
        return self.at_or_wake

    def plain_result(self, text: str) -> FakeResult:
        return FakeResult(text)

    def stop_event(self) -> None:
        self.stopped = True

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
        "body_diary",
        "body_recall",
        "body_sense",
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
    before = plugin.store.load("test", plugin._now()).satiety
    event = FakeEvent(text="/身体 喂")
    results = []
    async for result in plugin.body_command(event):
        results.append(result.get_plain_text())
    assert "身体指令" in results[0]
    assert "喂" not in results[0].split("她的身体由她自己维持")[0]
    state = plugin.store.load("test", plugin._now())
    assert state.satiety == before
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
async def test_page_config_toggles_intimate_and_validates(
    monkeypatch, tmp_path
) -> None:
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


def test_parse_libido_score() -> None:
    parse = plugin_module._parse_libido_score
    assert parse('{"libido": 73}') == 73
    assert parse("性欲：82") == 82
    assert parse("完全无法判断") is None
    assert parse('{"libido": 140}') == 100


@pytest.mark.asyncio
async def test_libido_score_is_cached(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "一个主动又温柔的人。")

    class FakeResponse:
        completion_text = '{"libido": 90}'

    class FakeProvider:
        async def text_chat(self, **_kwargs):
            return FakeResponse()

    async def fake_provider():
        return FakeProvider()

    monkeypatch.setattr(
        plugin.context, "get_using_provider_async", fake_provider, raising=False
    )
    prompt = "一个主动又温柔的人。"
    digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]
    await plugin._score_libido("test", prompt, digest)
    assert plugin._libido_scores["test"]["score"] == 90
    assert (plugin.data_dir / "libido_scores.json").exists()
    profile = plugin._profile_for("test", "test", prompt)
    assert profile.libido_habit is not None
    assert 0.3 <= profile.libido_habit <= 4.8
    await plugin.terminate()


@pytest.mark.asyncio
async def test_private_female_tool_exposes_ejaculated_flag(
    monkeypatch, tmp_path
) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    await plugin.initialize()
    profile = plugin._profile_for("test", "test", "她是一个成年女性。")
    tools = plugin._body_tools(profile, True)
    body_action = next(tool for tool in tools.tools if tool.name == "body_action")
    assert "intimacy" in body_action.parameters["properties"]["action"]["enum"]
    assert "ejaculated" in body_action.parameters["properties"]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_daily_diary_is_written_once(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    await plugin.initialize()
    profile = plugin._profile_for("test", "test", "她是一个成年女性。")
    now = datetime(2026, 11, 18, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    state = BodyState(persona_id="test")
    state.care = [
        {
            "t": now.timestamp() - 3600,
            "kind": "toilet",
            "facts": {
                "kind": "toilet",
                "bladder_before": 80,
                "bladder_after": 2,
                "urine_ml": 320,
                "urine_color": 1,
                "urethra": 5,
                "urgency": "急",
                "posture": "坐",
            },
        }
    ]
    plugin._maybe_daily_diary(state, profile, now)
    assert state.last_diary_day == now.date().isoformat()
    data = json.loads((plugin.data_dir / "diary.json").read_text(encoding="utf-8"))
    assert "320 毫升" in data["test"][now.date().isoformat()]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_pending_persona_request_applies_release(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    await plugin.initialize()
    profile = plugin._profile_for("test", "test", "她是一个成年女性。")
    state = BodyState(persona_id="test")
    request = plugin.data_dir / "body_request.json"
    request.write_text('{"action": "release"}', encoding="utf-8")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    assert plugin._consume_body_request(state, profile, now) is True
    assert state.release_count == 1
    assert state.release_until > now.timestamp()
    assert not request.exists()
    await plugin.terminate()


@pytest.mark.asyncio
async def test_persona_request_is_ignored_during_cooldown(
    monkeypatch, tmp_path
) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    await plugin.initialize()
    profile = plugin._profile_for("test", "test", "她是一个成年女性。")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    state = BodyState(persona_id="test")
    state.last_self_at = now.timestamp() - 600
    request = plugin.data_dir / "body_request.json"
    request.write_text('{"action": "release"}', encoding="utf-8")
    assert plugin._consume_body_request(state, profile, now) is False
    assert state.release_count == 0
    assert not request.exists()
    await plugin.terminate()


@pytest.mark.asyncio
async def test_persona_request_allows_a_second_round_in_afterglow(
    monkeypatch, tmp_path
) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    await plugin.initialize()
    profile = plugin._profile_for("test", "test", "她是一个成年女性。")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    state = BodyState(persona_id="test")
    state.last_self_at = now.timestamp() - 600
    state.afterglow_until = now.timestamp() + 600
    state.afterglow_want = 70.0
    request = plugin.data_dir / "body_request.json"
    request.write_text('{"action": "release"}', encoding="utf-8")
    assert plugin._consume_body_request(state, profile, now) is True
    assert state.release_count == 1
    assert not request.exists()
    await plugin.terminate()


@pytest.mark.asyncio
async def test_persona_request_stays_in_its_own_session(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    await plugin.initialize()
    profile = plugin._profile_for("test", "test", "她是一个成年女性。")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    state = BodyState(persona_id="test")
    request = plugin.data_dir / "body_request.json"
    request.write_text(
        json.dumps(
            {
                "action": "release",
                "umo": "aiocqhttp:FriendMessage:1",
                "created_at": now.timestamp(),
            }
        ),
        encoding="utf-8",
    )
    assert (
        plugin._consume_body_request(
            state, profile, now, umo="aiocqhttp:FriendMessage:2"
        )
        is False
    )
    assert state.release_count == 0
    assert request.exists()
    await plugin.terminate()


@pytest.mark.asyncio
async def test_consume_release_request_applies_immediately(
    monkeypatch, tmp_path
) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    await plugin.initialize()
    event = FakeEvent()
    now = plugin._now()
    request = plugin.data_dir / "body_request.json"
    request.write_text(
        json.dumps(
            {
                "action": "release",
                "umo": event.unified_msg_origin,
                "created_at": now.timestamp(),
            }
        ),
        encoding="utf-8",
    )
    assert await plugin.consume_release_request(event) is True
    assert not request.exists()
    state = plugin.store.load("test", now)
    assert state.release_count == 1
    assert state.release_until > now.timestamp()
    await plugin.terminate()


@pytest.mark.asyncio
async def test_quiet_window_forwards_messages_to_persona(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    forwarded: list[tuple[str, str, str]] = []

    async def fake_forward(persona_id, event, reason) -> None:
        forwarded.append((persona_id, reason, event.message_str))

    monkeypatch.setattr(plugin, "_forward_window_message", fake_forward)
    now = plugin._now()
    state = plugin.store.load("test", now)
    state.sleeping = True
    state.sleep_since = now.timestamp() - 1800
    state.sleep_wake_at = now.timestamp() + 3600
    state.sleep_kind = "night"
    plugin.store.save(state)
    await plugin.stay_asleep(FakeEvent(text="在吗"))
    assert forwarded == [("test", "sleep", "在吗")]


@pytest.mark.asyncio
async def test_busy_window_forwards_messages_to_persona(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    forwarded: list[tuple[str, str, str]] = []

    async def fake_forward(persona_id, event, reason) -> None:
        forwarded.append((persona_id, reason, event.message_str))

    monkeypatch.setattr(plugin, "_forward_window_message", fake_forward)
    now = plugin._now()
    state = plugin.store.load("test", now)
    state.release_until = now.timestamp() + 300
    plugin.store.save(state)
    await plugin.pause_for_release(FakeEvent(text="在吗？"))
    assert forwarded == [("test", "busy", "在吗？")]


@pytest.mark.asyncio
async def test_return_from_release_notifies_persona(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    now = plugin._now()
    state = plugin.store.load("test", now)
    state.release_until = now.timestamp() + 1
    plugin.store.save(state)

    notified: list[tuple[str, str, str]] = []

    async def fake_notify(persona_id, reason, trigger_umo="") -> None:
        notified.append((persona_id, reason, trigger_umo))

    monkeypatch.setattr(plugin, "_notify_body_return", fake_notify)
    profile = plugin._profile_for("test", "test", "她是一个成年女性。")
    await plugin._load_advanced("test", profile, now, umo="g")
    await plugin._load_advanced("test", profile, now + timedelta(seconds=3), umo="g")
    assert notified == [("test", "busy", "g")]


@pytest.mark.asyncio
async def test_genderless_persona_gets_no_intimacy_tool(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "一个普通的助手。")
    await plugin.initialize()
    profile = plugin._profile_for("test", "test", "一个普通的助手。")
    tools = plugin._body_tools(profile, True)
    body_action = next(tool for tool in tools.tools if tool.name == "body_action")
    assert "intimacy" not in body_action.parameters["properties"]["action"]["enum"]
    assert "自己解决" not in body_action.description
    assert body_action.description.startswith("记录自己做过的一件照顾身体的事")
    await plugin.terminate()


@pytest.mark.asyncio
async def test_sleep_gate_silences_sessions_but_not_commands(
    monkeypatch, tmp_path
) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    now = plugin._now()
    state = plugin.store.load("test", now)
    state.sleeping = True
    state.sleep_since = now.timestamp() - 1800
    state.sleep_wake_at = now.timestamp() + 3600
    state.sleep_kind = "night"
    state.updated_at = now.timestamp()
    plugin.store.save(state)

    chat = FakeEvent(text="在吗")
    chat.stopped = False
    await plugin.stay_asleep(chat)
    assert chat.stopped is True

    command = FakeEvent(text="/身体 状态")
    command.stopped = False
    await plugin.stay_asleep(command)
    assert command.stopped is False


def test_sleep_snapshot_and_contract(monkeypatch, tmp_path) -> None:
    import json

    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    now = plugin._now()
    state = plugin.store.load("test", now)
    state.sleeping = True
    state.sleep_since = now.timestamp() - 1200
    state.sleep_wake_at = now.timestamp() + 2400
    state.sleep_kind = "night"
    state.updated_at = now.timestamp()
    plugin.store.save(state)

    snapshot = plugin.sleep_snapshot()
    assert snapshot["enabled"] is True
    assert snapshot["asleep"] is True
    assert snapshot["wake_at"] > now.timestamp()

    contract = tmp_path / "astrbot_plugin_body_needs" / "sleep.json"
    payload = json.loads(contract.read_text(encoding="utf-8"))
    entry = payload["personas"]["test"]
    assert entry["asleep"] is True
    assert entry["gate"] is True
    assert payload["current"] == "test"


@pytest.mark.asyncio
async def test_private_self_release_pauses_replies(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    now = plugin._now()
    state = plugin.store.load("test", now)
    state.release_until = now.timestamp() + 600
    state.release_kind = "self"
    state.updated_at = now.timestamp()
    plugin.store.save(state)
    event = FakeEvent(private=True, text="在吗")
    event.stopped = False
    await plugin.pause_for_release(event)
    assert event.stopped is True


@pytest.mark.asyncio
async def test_group_self_release_pauses_replies(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    now = plugin._now()
    state = plugin.store.load("test", now)
    state.release_until = now.timestamp() + 600
    state.release_kind = "shower"
    state.updated_at = now.timestamp()
    plugin.store.save(state)
    event = FakeEvent(private=False, text="在吗")
    event.stopped = False
    await plugin.pause_for_release(event)
    assert event.stopped is True


@pytest.mark.asyncio
async def test_shower_pauses_replies(monkeypatch, tmp_path) -> None:
    plugin, _ = make_plugin(monkeypatch, tmp_path, "她是一个成年女性。")
    now = plugin._now()
    state = plugin.store.load("test", now)
    state.care_until = now.timestamp() + 600
    state.care_kind = "shower"
    state.updated_at = now.timestamp()
    plugin.store.save(state)
    event = FakeEvent(private=False, text="在吗")
    event.stopped = False
    await plugin.pause_for_release(event)
    assert event.stopped is True
