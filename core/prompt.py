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
from .model import CARE_LABELS, BodyState, CycleView, cycle_view
from .profile import Profile, gender_label

METER_NAMES = {
    "satiety": "饱食度",
    "hydration": "水分",
    "bladder": "膀胱",
    "cleanliness": "清洁",
    "oral_health": "口腔",
    "energy": "精力",
    "libido": "性欲",
}
PHASE_LABELS = {
    "menstrual": "经期",
    "follicular": "经期后",
    "ovulation": "排卵期",
    "luteal": "黄体期",
    "premenarche": "未初潮",
    "menopause": "已绝经",
    "pregnant": "怀孕中",
}
FLOW_LABELS = {1: "量少", 2: "量中等", 3: "量多"}
DISCHARGE_LABELS = {0: "干爽", 1: "少量", 2: "偏多", 3: "蛋清样拉丝"}


def satiety_label(value: float) -> str:
    """Return a natural fullness description for a 100 = full meter."""
    if value >= 88:
        return "很饱"
    if value >= 70:
        return "不饿"
    if value >= 50:
        return "有点想吃"
    if value >= 30:
        return "饿了"
    if value >= 15:
        return "很饿"
    return "饿得难受"


def hydration_label(value: float) -> str:
    """Return a natural hydration description for a 100 = well hydrated meter."""
    if value >= 88:
        return "很润"
    if value >= 70:
        return "不渴"
    if value >= 50:
        return "有点渴"
    if value >= 30:
        return "渴了"
    if value >= 15:
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


def bowel_label(value: float) -> str:
    """Return a natural bowel-pressure description."""
    if value < 20:
        return "没感觉"
    if value < 40:
        return "有一点"
    if value < 60:
        return "想排便"
    if value < 80:
        return "有点憋"
    return "很急"


def clean_label(value: float) -> str:
    """Return a natural cleanliness description for a 100 = clean meter."""
    if value >= 88:
        return "刚洗过"
    if value >= 70:
        return "干净"
    if value >= 50:
        return "还行"
    if value >= 30:
        return "有点脏"
    if value >= 15:
        return "该洗澡了"
    return "浑身难受"


def oral_label(value: float) -> str:
    """Return a natural oral-hygiene description for a 100 = clean meter."""
    if value >= 88:
        return "刚刷过"
    if value >= 70:
        return "清爽"
    if value >= 50:
        return "还行"
    if value >= 30:
        return "有点口气"
    if value >= 15:
        return "该刷牙了"
    return "嘴里发黏"


def energy_label(value: float) -> str:
    """Return a natural energy description for a 100 = rested meter."""
    if value >= 88:
        return "很有精神"
    if value >= 70:
        return "精神"
    if value >= 50:
        return "还行"
    if value >= 30:
        return "有点困"
    if value >= 15:
        return "很困"
    return "快撑不住了"


def libido_label(value: float) -> str:
    """Return a restrained adult libido description."""
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
        "satiety": satiety_label,
        "hydration": hydration_label,
        "bladder": bladder_label,
        "cleanliness": clean_label,
        "oral_health": oral_label,
        "energy": energy_label,
        "libido": libido_label,
    }
    return labels.get(key, lambda _value: "正常")(value)


def display_meters(
    state: BodyState,
    profile: Profile,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Return meters in display space, where 100 is full, rested or clean.

    The stored meters are reserves now: satiety, hydration, energy and
    cleanliness all read 100 as the good end. This function only formats them
    and applies the tone rules; it no longer inverts anything. Bladder
    pressure and libido stay pressures on purpose, because their labels
    describe the pressure itself rather than a reserve.

    Args:
        state: Advanced body state.
        profile: Resolved persona profile.
        settings: Plugin settings.

    Returns:
        Meter dictionaries with key, label, name, value, level and tone.
    """
    rows = [
        ("satiety", "饱食度", "胃", float(state.satiety), satiety_label, True),
        ("hydration", "水分", "渴", float(state.hydration), hydration_label, True),
        ("energy", "精力", "精力", float(state.energy), energy_label, True),
    ]
    if settings.enable_hygiene:
        rows.extend(
            [
                (
                    "cleanliness",
                    "清洁",
                    "清洁",
                    float(state.cleanliness),
                    clean_label,
                    True,
                ),
                (
                    "oral_health",
                    "口腔",
                    "口腔",
                    float(state.oral_health),
                    oral_label,
                    True,
                ),
            ]
        )
    rows.append(
        ("bladder", "膀胱压力", "膀胱", float(state.bladder), bladder_label, False)
    )
    if profile.gender_resolved and settings.enable_intimate:
        rows.append(
            ("libido", "性欲", "性欲", float(state.libido), libido_label, False)
        )

    meters: list[dict[str, Any]] = []
    for key, label, name, value, labeler, higher_is_better in rows:
        value = max(0.0, min(100.0, value))
        if higher_is_better:
            tone = "hot" if value < 15 else ("warn" if value < 35 else "ok")
        else:
            tone = "hot" if value >= 80 else ("warn" if value >= 55 else "ok")
        meters.append(
            {
                "key": key,
                "label": label,
                "name": name,
                "value": round(value, 1),
                "level": labeler(value),
                "tone": tone,
            }
        )
    return meters


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
        if not private and kind in (
            "wet_dream",
            "morning_erection",
            "arousal",
            "self_release",
        ):
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


def _symptom_text(view: CycleView) -> str:
    """Return the premenstrual symptoms that are actually present."""
    bits = []
    if view.breast >= 2:
        bits.append("乳房发胀")
    elif view.breast == 1:
        bits.append("胸口有点胀")
    if view.headache >= 2:
        bits.append("头痛")
    elif view.headache == 1:
        bits.append("头有点沉")
    if view.backache >= 2:
        bits.append("腰酸")
    elif view.backache == 1:
        bits.append("腰有点软")
    return "、".join(bits)


def _cycle_text(view: CycleView) -> str:
    """Build the one-line cycle description used in prompts and cards."""
    if view.phase == "premenarche":
        return f"青春期：{view.note}"
    if view.phase == "menopause":
        return f"妇科：{view.note}"
    if view.phase == "pregnant":
        text = f"身体：{view.note}"
        if 6 <= view.weeks <= 12:
            text += "，最近容易恶心、犯困"
        elif view.weeks >= 28:
            text += "，行动开始不太方便"
        return text
    if view.phase == "menstrual":
        flow = FLOW_LABELS.get(view.flow, "")
        detail = "，".join(part for part in (flow, view.note) if part)
        text = f"经期：第 {view.day} 天，{detail}；距下次约 {view.days_to_next} 天"
    elif view.phase == "luteal":
        text = f"周期：黄体期，{view.note}；距下次约 {view.days_to_next} 天"
    elif view.phase == "ovulation":
        text = f"周期：排卵期，{view.note}；距下次约 {view.days_to_next} 天"
    else:
        text = f"周期：第 {view.day} 天（经期后），距下次约 {view.days_to_next} 天"
    symptoms = _symptom_text(view) if view.phase != "menstrual" else ""
    if symptoms:
        text += f"；{symptoms}"
    if view.phase != "menstrual":
        label = DISCHARGE_LABELS.get(view.discharge, "")
        if label:
            text += f"；分泌物{label}"
    if view.anovulatory:
        text += "；本周期无排卵"
    if view.puberty:
        text += "（青春期，周期还不太规律）"
    return text


def _supply_text(state: BodyState, view: CycleView | None) -> str:
    """Return the sanitary-supply line, only while it matters."""
    if view is None or view.phase != "menstrual":
        return ""
    if state.pads <= 0:
        return "卫生用品：已经用完了"
    return f"卫生用品：还剩 {state.pads} 片"


def _male_text(state: BodyState, now: datetime, profile: Profile) -> str:
    """Build the private adult-male line."""
    if not (profile.gender_resolved and profile.gender == "male"):
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


def _lactation_text(state: BodyState) -> str:
    """Return the private lactation line while it matters."""
    if state.milk >= 80:
        return "哺乳：涨得厉害，需要挤一下"
    if state.milk >= 45:
        return "哺乳：有点涨"
    return "哺乳：还好"


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
        "satiety": state.satiety,
        "hydration": state.hydration,
        "bladder": state.bladder,
        "cleanliness": state.cleanliness,
        "oral_health": state.oral_health,
        "energy": state.energy,
        "libido": state.libido,
    }

    if settings.intensity == "subtle":
        important = [
            key
            for key in ("satiety", "hydration", "cleanliness", "oral_health", "energy")
            if values[key] <= 52
        ]
        important += ["bladder"] if values["bladder"] >= 48 else []
        if not important:
            lines.append("身体暂时没什么特别的感觉。")
        else:
            for key in important:
                lines.append(f"{METER_NAMES[key]}：{meter_label(key, values[key])}")
    elif show_all:
        lines.append(
            f"胃：{satiety_label(state.satiety)}（{_ago(state.last_eat_at, now)}吃过东西）"
        )
        lines.append(
            f"渴：{hydration_label(state.hydration)}（{_ago(state.last_drink_at, now)}喝过水）"
        )
        lines.append(f"膀胱：{bladder_label(state.bladder)}")
        if state.bowel >= 35:
            lines.append(f"排便：{bowel_label(state.bowel)}")
        if settings.enable_hygiene:
            lines.append(
                f"清洁：{clean_label(state.cleanliness)}；"
                f"口腔：{oral_label(state.oral_health)}"
            )
        lines.append(f"精力：{energy_label(state.energy)}")
    else:
        lines.append(f"胃：{satiety_label(state.satiety)}")
        lines.append(f"渴：{hydration_label(state.hydration)}")
        lines.append(f"精力：{energy_label(state.energy)}")
        if view is not None and view.phase == "menstrual" and view.cramps >= 2:
            lines.append("身体：肚子有点不舒服，不太想动。")

    if show_private and settings.intensity != "subtle":
        lines.append(_internal_line(state, now))
    if view is not None and (show_private or full_group):
        lines.append(_cycle_text(view))
        if show_private:
            warm = "，排卵后偏高" if view.progesterone > 25.0 else ""
            lines.append(f"基础体温：{view.bbt:.2f}℃{warm}")
        if show_private and view.fertile:
            lines.append("身体：这几天更容易受孕。")
        supply = _supply_text(state, view) if show_private else ""
        if supply:
            lines.append(supply)
        if show_private and state.lactating:
            lines.append(_lactation_text(state))
    if show_private and settings.enable_intimate and profile.gender_resolved:
        male = _male_text(state, now, profile)
        if male:
            lines.append(male)
        if state.libido >= 35:
            lines.append(
                f"性欲：{libido_label(state.libido)}（私密，只在合适的时候自然流露）"
            )

    if show_private:
        load = (
            max(0.0, 35.0 - state.satiety)
            + max(0.0, 35.0 - state.hydration)
            + max(0.0, state.sleep_debt - 8.0) * 4.0
            + max(0.0, state.bowel - 60.0)
        )
        if load >= 45.0:
            lines.append("情绪：身体有点不舒服，耐心比平时差一点。")

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
    meters = {meter["key"]: meter for meter in display_meters(state, profile, settings)}
    satiety = meters["satiety"]
    hydration = meters["hydration"]
    energy = meters["energy"]
    if private:
        lines.extend(
            [
                f"饱食度：{satiety['level']}（{round(satiety['value'])}/100，{_ago(state.last_eat_at, now)}吃过东西）",
                f"水分：{hydration['level']}（{round(hydration['value'])}/100，{_ago(state.last_drink_at, now)}喝过水）",
                f"膀胱压力：{meters['bladder']['level']}（{round(meters['bladder']['value'])}/100）",
            ]
        )
        if settings.enable_hygiene:
            lines.append(
                f"清洁：{meters['cleanliness']['level']}（{round(meters['cleanliness']['value'])}/100）"
                f" · 口腔：{meters['oral_health']['level']}（{round(meters['oral_health']['value'])}/100）"
            )
        lines.append(f"精力：{energy['level']}（{round(energy['value'])}/100）")
        lines.append(_internal_line(state, now))
        if settings.enable_intimate and profile.gender_resolved and state.libido >= 20:
            lines.append(
                f"性欲：{libido_label(state.libido)}（{round(state.libido)}/100）"
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
                f"饱食度：{satiety['level']}（{round(satiety['value'])}/100）",
                f"水分：{hydration['level']}（{round(hydration['value'])}/100）",
                f"精力：{energy['level']}（{round(energy['value'])}/100）",
            ]
        )
        if view is not None and view.phase == "menstrual" and view.cramps >= 2:
            lines.append("身体：肚子不太舒服。")
        lines.append("群聊里只看得到这些；私聊能看完整状态。")

    recent = _recent_events(state, now, private, private)
    if recent:
        lines.append("最近：" + "；".join(recent))
    if private and show_help:
        lines.append("她的饮食起居由她自己维持；想知道周期可以用 /身体 经期。")
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
    meters = display_meters(state, profile, settings)
    care = []
    for item in state.care:
        try:
            stamp = float(item.get("t") or 0)
        except (TypeError, ValueError):
            continue
        if stamp <= 0 or now.timestamp() - stamp > 86400:
            continue
        moment = datetime.fromtimestamp(stamp, now.tzinfo)
        kind = str(item.get("kind") or "")
        care.append(
            {
                "kind": kind,
                "label": CARE_LABELS.get(kind, kind),
                "at": f"{moment:%H:%M}",
                "minutes": moment.hour * 60 + moment.minute,
            }
        )
    care.sort(key=lambda item: item["minutes"])

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
            "label": "水合",
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

    if state.bowel >= 25.0:
        details.append(
            {
                "key": "bowel",
                "label": "排便",
                "value": round(state.bowel, 1),
                "unit": "%",
                "level": bowel_label(state.bowel),
                "tone": "hot"
                if state.bowel >= 70
                else ("warn" if state.bowel >= 45 else "ok"),
            }
        )

    cycle = None
    if view is not None and settings.enable_cycle:
        cycle = {
            "phase": view.phase,
            "phase_label": PHASE_LABELS.get(view.phase, view.phase),
            "day": view.day,
            "cycle_length": view.cycle_length,
            "period_length": profile.period_length,
            "puberty": view.puberty,
            "days_to_next": view.days_to_next,
            "flow": view.flow,
            "flow_label": FLOW_LABELS.get(view.flow, ""),
            "cramps": view.cramps,
            "mood": view.mood,
            "appetite": view.appetite,
            "anovulatory": view.anovulatory,
            "fertile": view.fertile,
            "weeks": view.weeks,
            "fetuses": state.fetuses,
            "breast": view.breast,
            "headache": view.headache,
            "backache": view.backache,
            "discharge": view.discharge,
            "discharge_label": DISCHARGE_LABELS.get(view.discharge, ""),
            "pads": state.pads,
            "race": profile.race,
            "physio_age": (
                round(profile.physio_age, 1) if profile.physio_age is not None else None
            ),
            "note": view.note,
            "start": view.cycle_start.isoformat(),
            "progress": round(view.day / max(1, view.cycle_length), 3),
            "estrogen": round(view.estrogen, 1),
            "progesterone": round(view.progesterone, 1),
            "lh": round(view.lh, 1),
            "bbt": view.bbt,
        }

    lactation = None
    if state.lactating or state.birth_at > 0:
        lactation = {
            "active": state.lactating,
            "milk": round(state.milk, 1),
            "line": _lactation_text(state) if state.lactating else "",
        }

    male = None
    if (
        profile.gender_resolved
        and profile.gender == "male"
        and settings.enable_intimate
    ):
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
        "release": _ago(state.last_self_at, now),
    }

    summary = (
        f"{satiety_label(state.satiety)}，{hydration_label(state.hydration)}，"
        f"{energy_label(state.energy)}"
    )
    if view is not None and view.phase == "menstrual":
        summary += f"，经期第 {view.day} 天"

    return {
        "id": profile.persona_id,
        "name": profile.name,
        "gender": profile.gender,
        "gender_label": gender_label(profile.gender),
        "gender_resolved": profile.gender_resolved,
        "summary": summary,
        "meters": meters,
        "details": details,
        "cycle": cycle,
        "lactation": lactation,
        "male": male,
        "events": events,
        "care": care,
        "last": last,
        "can_intimacy": bool(profile.gender_resolved and settings.enable_intimate),
    }
