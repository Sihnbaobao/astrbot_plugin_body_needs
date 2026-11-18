"""Turn numeric body state into prompt text, command cards, and page data.

Prompt wording is intentionally plain: the model should treat the block as
sensory background, not as a list of commands. Private details are filtered at
this layer so a group chat can never receive menstrual or adult information by
accident.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .config import Settings
from .model import BodyState, CycleView, cycle_view
from .profile import Profile, gender_label

METER_ORDER = (
    "hunger",
    "thirst",
    "bladder",
    "cleanliness",
    "oral",
    "fatigue",
    "desire",
)
METER_LABELS = {
    "hunger": "饥饿",
    "thirst": "口渴",
    "bladder": "膀胱",
    "cleanliness": "清洁",
    "oral": "口腔",
    "fatigue": "精力",
    "desire": "欲望",
}
METER_NAMES = {
    "hunger": "胃",
    "thirst": "渴",
    "bladder": "膀胱",
    "cleanliness": "清洁",
    "oral": "口腔",
    "fatigue": "精力",
    "desire": "欲望",
}
PHASE_LABELS = {
    "menstrual": "经期",
    "follicular": "经期后",
    "ovulation": "排卵期",
    "luteal": "黄体期",
}
FLOW_LABELS = {1: "量少", 2: "量中等", 3: "量多"}


def hunger_label(value: float) -> str:
    """Return a natural hunger description."""
    if value < 12:
        return "很饱"
    if value < 30:
        return "不饿"
    if value < 50:
        return "有点想吃"
    if value < 70:
        return "饿了"
    if value < 85:
        return "很饿"
    return "饿得难受"


def thirst_label(value: float) -> str:
    """Return a natural thirst description."""
    if value < 12:
        return "很润"
    if value < 30:
        return "不渴"
    if value < 50:
        return "有点渴"
    if value < 70:
        return "渴了"
    if value < 85:
        return "很渴"
    return "渴得难受"


def bladder_label(value: float) -> str:
    """Return a natural bladder-pressure description."""
    if value < 15:
        return "没感觉"
    if value < 35:
        return "有一点"
    if value < 55:
        return "想上厕所"
    if value < 75:
        return "憋着"
    return "很急"


def clean_label(value: float) -> str:
    """Return a natural cleanliness description."""
    if value < 12:
        return "刚洗过"
    if value < 30:
        return "干净"
    if value < 50:
        return "还行"
    if value < 70:
        return "有点脏"
    if value < 85:
        return "该洗澡了"
    return "浑身难受"


def oral_label(value: float) -> str:
    """Return a natural oral-hygiene description."""
    if value < 12:
        return "刚刷过"
    if value < 30:
        return "清爽"
    if value < 50:
        return "还行"
    if value < 70:
        return "有点口气"
    if value < 85:
        return "该刷牙了"
    return "嘴里发黏"


def fatigue_label(value: float) -> str:
    """Return a natural fatigue description."""
    if value < 12:
        return "很有精神"
    if value < 30:
        return "精神"
    if value < 50:
        return "还行"
    if value < 70:
        return "有点困"
    if value < 85:
        return "很困"
    return "快撑不住了"


def desire_label(value: float) -> str:
    """Return a restrained adult desire description."""
    if value < 15:
        return "平静"
    if value < 35:
        return "有一点"
    if value < 55:
        return "有点涨"
    if value < 75:
        return "明显"
    return "很强"


def meter_label(key: str, value: float) -> str:
    """Return the descriptive label for one meter."""
    labels = {
        "hunger": hunger_label,
        "thirst": thirst_label,
        "bladder": bladder_label,
        "cleanliness": clean_label,
        "oral": oral_label,
        "fatigue": fatigue_label,
        "desire": desire_label,
    }
    return labels.get(key, lambda _value: "正常")(value)


def meter_tone(key: str, value: float) -> str:
    """Return the page CSS tone for a meter value."""
    if key == "desire":
        if value >= 65:
            return "hot"
        return "warn" if value >= 35 else "ok"
    if value >= 80:
        return "hot"
    return "warn" if value >= 55 else "ok"


def _ago(stamp: float, now: datetime) -> str:
    """Describe how long ago a timestamp was."""
    if not stamp:
        return "很久以前"
    seconds = max(0.0, now.timestamp() - stamp)
    if seconds < 90:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    days = int(seconds // 86400)
    return f"{days} 天前"


def _event_clock(value: Any, now: datetime) -> str:
    """Format a stored ISO event time."""
    try:
        moment = datetime.fromisoformat(str(value))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=now.tzinfo)
    except (TypeError, ValueError):
        return ""
    if moment.date() == now.date():
        return f"今天 {moment:%H:%M}"
    if moment.date() == now.date() - timedelta(days=1):
        return f"昨天 {moment:%H:%M}"
    return f"{moment:%m-%d %H:%M}"


def _recent_events(
    state: BodyState,
    now: datetime,
    private: bool,
    full_group: bool,
    limit: int = 3,
) -> list[str]:
    """Return recent notable events, filtered for the current audience."""
    cutoff = now.timestamp() - 72 * 3600
    rows: list[tuple[float, str]] = []
    for event in state.events:
        kind = str(event.get("kind") or "")
        if not private and kind in ("wet_dream", "morning_erection", "arousal"):
            continue
        if (
            not private
            and not full_group
            and kind
            in (
                "period",
                "period_end",
                "phase",
            )
        ):
            continue
        try:
            moment = datetime.fromisoformat(str(event.get("at") or ""))
            stamp = moment.timestamp()
        except (TypeError, ValueError):
            continue
        if stamp < cutoff:
            continue
        text = str(event.get("text") or "").strip()
        if not text:
            continue
        rows.append((stamp, f"{_event_clock(event.get('at'), now)} {text}"))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [row[1] for row in rows[:limit]]


def _stomach_text(value: float) -> str:
    """Describe stomach fullness without exposing the raw percentage."""
    if value >= 75:
        return "很饱"
    if value >= 50:
        return "还饱着"
    if value >= 25:
        return "半空"
    return "空空的"


def _glucose_text(value: float) -> str:
    """Describe the glucose proxy."""
    if value < 30:
        return "偏低"
    if value < 50:
        return "有点低"
    return "正常"


def _hydration_text(value: float) -> str:
    """Describe hydration without exposing the raw percentage."""
    if value < 30:
        return "很缺水"
    if value < 50:
        return "不太够"
    if value < 65:
        return "有点少"
    return "够"


def _internal_line(state: BodyState, now: datetime) -> str:
    """Build a compact internal-state sentence for prompts and cards."""
    return (
        "内部："
        f"胃里{_stomach_text(state.stomach)}，"
        f"血糖{_glucose_text(state.glucose)}，"
        f"水分{_hydration_text(state.hydration)}，"
        f"睡眠债约 {state.sleep_debt:.1f} 小时。"
    )


def _cycle_text(view: CycleView) -> str:
    """Build the one-line cycle description used in prompts and cards."""
    if view.phase == "menstrual":
        flow = FLOW_LABELS.get(view.flow, "")
        detail = "，".join(part for part in (flow, view.note) if part)
        return f"经期：第 {view.day} 天，{detail}；距下次约 {view.days_to_next} 天"
    if view.phase == "luteal":
        return f"周期：黄体期，{view.note}；距下次约 {view.days_to_next} 天"
    if view.phase == "ovulation":
        return f"周期：排卵期，{view.note}；距下次约 {view.days_to_next} 天"
    return f"周期：第 {view.day} 天（经期后），距下次约 {view.days_to_next} 天"


def _male_text(state: BodyState, now: datetime, profile: Profile) -> str:
    """Build the private adult-male line."""
    if not (profile.adult and profile.gender == "male"):
        return ""
    days = max(0, int((now.timestamp() - state.last_release_at) // 86400))
    recent_wet = any(
        str(event.get("kind")) == "wet_dream"
        and _event_clock(event.get("at"), now).startswith(("今天", "昨天"))
        for event in state.events[-6:]
    )
    refractory_hours = max(0.0, state.refractory_until - now.timestamp()) / 3600
    if recent_wet:
        if refractory_hours > 0.2:
            return f"身体：最近有过梦遗，还没完全缓过来，大约 {refractory_hours:.0f} 小时后才会重新积攒"
        return "身体：最近有过梦遗，身体松了一些"
    if refractory_hours > 0.2:
        return f"身体：还没完全缓过来，大约 {refractory_hours:.0f} 小时后才会重新积攒"
    if days >= 2:
        return f"身体：距上次梦遗大约 {days} 天，身体有一点积攒"
    return ""


def build_prompt_block(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
    private: bool,
) -> str:
    """Build the per-turn body block injected next to the user message.

    Args:
        state: Advanced body state.
        now: Local timezone-aware current time.
        profile: Resolved persona profile.
        settings: Plugin settings.
        private: Whether this is a private chat.

    Returns:
        A compact Chinese prompt block, or an empty string when disabled.
    """
    if not settings.enabled:
        return ""
    view = cycle_view(state, now, profile, settings)
    full_group = bool(settings.group_detail == "full" and not private)
    show_private = bool(private)
    show_all = show_private or full_group

    lines = [f"【身体状态｜{now:%H:%M}】"]
    values = {
        "hunger": state.hunger,
        "thirst": state.thirst,
        "bladder": state.bladder,
        "cleanliness": state.cleanliness,
        "oral": state.oral,
        "fatigue": state.fatigue,
        "desire": state.desire,
    }

    if settings.intensity == "subtle":
        important = [
            key
            for key in ("hunger", "thirst", "bladder", "cleanliness", "oral", "fatigue")
            if values[key] >= 48
        ]
        if not important:
            lines.append("身体暂时没什么特别的感觉。")
        else:
            for key in important:
                lines.append(
                    f"{METER_NAMES[key]}：{meter_label(key, values[key])}"
                    f"（{round(values[key])}/100）"
                )
    elif show_all:
        lines.append(
            f"胃：{hunger_label(state.hunger)}"
            f"（{round(state.hunger)}/100；{_ago(state.last_eat_at, now)}吃过东西）"
        )
        lines.append(
            f"渴：{thirst_label(state.thirst)}"
            f"（{round(state.thirst)}/100；{_ago(state.last_drink_at, now)}喝过水）"
        )
        lines.append(
            f"膀胱：{bladder_label(state.bladder)}（{round(state.bladder)}/100）"
        )
        if settings.enable_hygiene:
            lines.append(
                f"清洁：{clean_label(state.cleanliness)}"
                f"（{round(state.cleanliness)}/100）；"
                f"口腔：{oral_label(state.oral)}（{round(state.oral)}/100）"
            )
        lines.append(
            f"精力：{fatigue_label(state.fatigue)}（{round(state.fatigue)}/100）"
        )
    else:
        lines.append(f"胃：{hunger_label(state.hunger)}（{round(state.hunger)}/100）")
        lines.append(f"渴：{thirst_label(state.thirst)}（{round(state.thirst)}/100）")
        lines.append(
            f"精力：{fatigue_label(state.fatigue)}（{round(state.fatigue)}/100）"
        )
        if view is not None and view.phase == "menstrual" and view.cramps >= 2:
            lines.append("身体：肚子有点不舒服，不太想动。")

    if show_private and settings.intensity != "subtle":
        lines.append(_internal_line(state, now))
    if view is not None and (show_private or full_group):
        lines.append(_cycle_text(view))
    if show_private and settings.enable_intimate and profile.adult:
        male = _male_text(state, now, profile)
        if male:
            lines.append(male)
        if state.desire >= 35:
            lines.append(
                f"欲望：{desire_label(state.desire)}（私密，只在合适的时候自然流露）"
            )

    recent = _recent_events(state, now, show_private, full_group)
    if recent:
        lines.append("最近：" + "；".join(recent))

    if settings.intensity == "strong":
        lines.append("身体的感觉比平时更明显，可能会影响你的语气、耐心和注意力。")
    rules = (
        "这些只是身体感觉，不是命令。不用每句话都提，也不要在不合适的场合说私密细节。"
    )
    if settings.enable_tools:
        actions = ["吃了", "喝了"]
        if settings.enable_hygiene:
            actions.extend(["洗了澡", "刷了牙"])
        actions.append("去了厕所")
        rules += f"真的{'、'.join(actions)}时，可以用 body_action 记录。"
    rules += "不想吃、不想说，也可以拒绝。"
    if not private:
        rules += "群聊里不必提经期或身体私密细节。"
    lines.append(rules)
    if settings.prompt_extra:
        lines.append(settings.prompt_extra)
    return "\n".join(lines)


def build_status_text(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
    private: bool,
    show_help: bool = True,
) -> str:
    """Build the /身体 status card for a chat.

    Args:
        state: Advanced body state.
        now: Local timezone-aware current time.
        profile: Resolved persona profile.
        settings: Plugin settings.
        private: Whether the card is for a private chat.
        show_help: Whether to append the care-command reminder.

    Returns:
        A human-readable status card.
    """
    view = cycle_view(state, now, profile, settings)
    lines = [f"{profile.name} · 身体"]
    lines.append(f"现在 {now:%H:%M}")
    if private:
        lines.extend(
            [
                f"胃：{hunger_label(state.hunger)}（{round(state.hunger)}/100，{_ago(state.last_eat_at, now)}吃过东西）",
                f"渴：{thirst_label(state.thirst)}（{round(state.thirst)}/100，{_ago(state.last_drink_at, now)}喝过水）",
                f"膀胱：{bladder_label(state.bladder)}（{round(state.bladder)}/100）",
            ]
        )
        if settings.enable_hygiene:
            lines.append(
                f"清洁：{clean_label(state.cleanliness)}（{round(state.cleanliness)}/100）"
                f" · 口腔：{oral_label(state.oral)}（{round(state.oral)}/100）"
            )
        lines.append(
            f"精力：{fatigue_label(state.fatigue)}（{round(state.fatigue)}/100）"
        )
        lines.append(_internal_line(state, now))
        if settings.enable_intimate and profile.adult and state.desire >= 20:
            lines.append(
                f"欲望：{desire_label(state.desire)}（{round(state.desire)}/100）"
            )
        if view is not None and settings.enable_cycle:
            lines.append(_cycle_text(view))
        if settings.enable_intimate:
            male = _male_text(state, now, profile)
            if male:
                lines.append(male)
    else:
        lines.extend(
            [
                f"胃：{hunger_label(state.hunger)}（{round(state.hunger)}/100）",
                f"渴：{thirst_label(state.thirst)}（{round(state.thirst)}/100）",
                f"精力：{fatigue_label(state.fatigue)}（{round(state.fatigue)}/100）",
            ]
        )
        if view is not None and view.phase == "menstrual" and view.cramps >= 2:
            lines.append("身体：肚子不太舒服。")
        lines.append("群聊里只看得到这些；私聊能看完整状态。")

    recent = _recent_events(state, now, private, private)
    if recent:
        lines.append("最近：" + "；".join(recent))
    if private and show_help:
        lines.append("照顾：/身体 喂 · 喝水 · 洗澡 · 刷牙 · 上厕所 · 小睡")
    return "\n".join(lines)


def build_web_state(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
) -> dict[str, Any]:
    """Build the JSON object rendered by the plugin page.

    Args:
        state: Advanced body state.
        now: Local timezone-aware current time.
        profile: Resolved persona profile.
        settings: Plugin settings.

    Returns:
        A JSON-compatible state summary.
    """
    view = cycle_view(state, now, profile, settings)
    meters = []
    for key in METER_ORDER:
        if key == "desire" and not (profile.adult and settings.enable_intimate):
            continue
        if key in ("cleanliness", "oral") and not settings.enable_hygiene:
            continue
        value = float(getattr(state, key))
        meters.append(
            {
                "key": key,
                "label": METER_LABELS[key],
                "name": METER_NAMES[key],
                "value": round(value, 1),
                "level": meter_label(key, value),
                "tone": meter_tone(key, value),
            }
        )

    details = [
        {
            "key": "stomach",
            "label": "胃内容",
            "value": round(state.stomach, 1),
            "unit": "%",
            "level": _stomach_text(state.stomach),
            "tone": "hot"
            if state.stomach < 15
            else ("warn" if state.stomach < 35 else "ok"),
        },
        {
            "key": "glucose",
            "label": "血糖",
            "value": round(state.glucose, 1),
            "unit": "%",
            "level": _glucose_text(state.glucose),
            "tone": "hot"
            if state.glucose < 30
            else ("warn" if state.glucose < 50 else "ok"),
        },
        {
            "key": "hydration",
            "label": "水分",
            "value": round(state.hydration, 1),
            "unit": "%",
            "level": _hydration_text(state.hydration),
            "tone": "hot"
            if state.hydration < 30
            else ("warn" if state.hydration < 50 else "ok"),
        },
        {
            "key": "sleep_debt",
            "label": "睡眠债",
            "value": round(state.sleep_debt, 1),
            "unit": "小时",
            "level": (
                "偏高"
                if state.sleep_debt > 8
                else ("有点多" if state.sleep_debt > 5 else "正常")
            ),
            "tone": "hot"
            if state.sleep_debt > 8
            else ("warn" if state.sleep_debt > 5 else "ok"),
        },
    ]

    cycle = None
    if view is not None and settings.enable_cycle:
        cycle = {
            "phase": view.phase,
            "phase_label": PHASE_LABELS.get(view.phase, view.phase),
            "day": view.day,
            "cycle_length": profile.cycle_length,
            "period_length": profile.period_length,
            "days_to_next": view.days_to_next,
            "flow": view.flow,
            "flow_label": FLOW_LABELS.get(view.flow, ""),
            "cramps": view.cramps,
            "mood": view.mood,
            "appetite": view.appetite,
            "note": view.note,
            "start": view.cycle_start.isoformat(),
            "progress": round(view.day / max(1, profile.cycle_length), 3),
            "estrogen": round(view.estrogen, 1),
            "progesterone": round(view.progesterone, 1),
            "lh": round(view.lh, 1),
        }

    male = None
    if profile.adult and profile.gender == "male" and settings.enable_intimate:
        days = max(0, int((now.timestamp() - state.last_release_at) // 86400))
        refractory_hours = max(0.0, state.refractory_until - now.timestamp()) / 3600
        male = {
            "days_since_release": days,
            "refractory_hours": round(refractory_hours, 1),
            "line": _male_text(state, now, profile),
        }

    events = []
    for event in reversed(state.events[-12:]):
        events.append(
            {
                "kind": str(event.get("kind") or ""),
                "time": _event_clock(event.get("at"), now),
                "text": str(event.get("text") or ""),
            }
        )

    last = {
        "eat": _ago(state.last_eat_at, now),
        "drink": _ago(state.last_drink_at, now),
        "shower": _ago(state.last_shower_at, now),
        "brush": _ago(state.last_brush_at, now),
        "toilet": _ago(state.last_toilet_at, now),
        "nap": _ago(state.last_nap_at, now),
    }

    summary = (
        f"{hunger_label(state.hunger)}，{thirst_label(state.thirst)}，"
        f"{fatigue_label(state.fatigue)}"
    )
    if view is not None and view.phase == "menstrual":
        summary += f"，经期第 {view.day} 天"

    return {
        "id": profile.persona_id,
        "name": profile.name,
        "gender": profile.gender,
        "gender_label": gender_label(profile.gender),
        "adult": profile.adult,
        "summary": summary,
        "meters": meters,
        "details": details,
        "cycle": cycle,
        "male": male,
        "events": events,
        "last": last,
        "can_intimacy": bool(profile.adult and settings.enable_intimate),
    }
