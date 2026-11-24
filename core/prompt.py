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
from .model import (
    CARE_LABELS,
    CONCEPTION_BASE,
    CONTRACEPTION_FACTOR,
    BodyState,
    CycleView,
    constitution,
    cycle_view,
    sleep_view,
)
from .profile import Profile, gender_label

METER_NAMES = {
    "satiety": "饱食度",
    "hydration": "水分",
    "bladder": "膀胱",
    "bowel": "排便",
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
MUCUS_TEXTURE_LABELS = {
    "dry": "干爽",
    "sticky": "黏稠",
    "creamy": "乳白糊状",
    "watery": "水样",
    "egg_white": "蛋清样",
    "bloody": "混着经血",
    "spotting": "带一点血丝",
}
MUCUS_COLOR_LABELS = {
    "clear": "透明",
    "milky": "乳白",
    "white": "白色",
    "pale_yellow": "淡黄",
    "pink": "淡粉",
    "brown": "褐色",
    "red": "红色",
}
CERVIX_POSITION_LABELS = {"low": "偏低", "mid": "中位", "high": "偏高"}
CERVIX_FIRMNESS_LABELS = {"firm": "偏硬", "medium": "中等", "soft": "偏软"}
LABIA_MAJORA_LABELS = {"plump": "饱满", "full": "微微分开", "parted": "张开"}
LABIA_MINORA_LABELS = {"tucked": "贴合", "peek": "微露", "flared": "外翻"}
CLITORIS_LABELS = {"hidden": "藏着", "peek": "微露", "engorged": "充血挺起"}
VESTIBULE_LABELS = {"dry": "干燥", "damp": "湿润", "glossy": "泛着水光", "red": "泛红"}
PERINEUM_LABELS = {"normal": "正常", "warm": "有点发红", "tender": "发胀"}
VAGINA_TONE_LABELS = {
    "firm": "紧致",
    "soft": "柔软",
    "yielding": "松软",
    "thin": "薄而干",
}
VAGINA_WARMTH_LABELS = {"warm": "温热", "hot": "发烫"}
VAGINA_OPEN_LABELS = {"closed": "闭合", "parted": "微张", "open": "张开"}
PUBIC_HAIR_LABELS = {
    "sparse": "稀疏",
    "medium": "适中",
    "bushy": "浓密",
    "shaved": "剃光",
}
PUBIC_STYLE_LABELS = {
    "natural": "自然",
    "trimmed": "修剪过",
    "landing_strip": "留一小条",
    "shaved": "剃净",
}
AREOLA_LABELS = {"small": "小", "medium": "中等", "large": "大"}
AREOLA_TINT_LABELS = {"pink": "淡粉", "rose": "粉褐", "brown": "深褐"}
NIPPLE_LABELS = {"soft": "软着", "erect": "挺起"}
PUBERTY_STAGE_LABELS = {
    "prepubertal": "发育前",
    "early": "发育早期",
    "late": "青春期",
    "adult": "成年",
    "menopause": "绝经后",
}


def mucus_volume_label(value: float) -> str:
    """Return a plain-language amount for cervical mucus."""
    if value < 10:
        return "很少"
    if value < 30:
        return "少量"
    if value < 55:
        return "中等"
    if value < 75:
        return "偏多"
    return "很多"


def pelvic_label(value: float) -> str:
    """Return a plain-language pelvic fullness description."""
    if value < 20:
        return "没有"
    if value < 40:
        return "有点坠"
    if value < 65:
        return "坠胀"
    return "明显坠胀"


def breast_label(value: float) -> str:
    """Return a plain-language breast fullness description."""
    if value < 30:
        return "柔软"
    if value < 55:
        return "有点胀"
    if value < 80:
        return "胀得明显"
    return "很胀"


def load_label(value: float) -> str:
    """Return a neutral label for a 0-100 organ-load proxy."""
    if value < 20:
        return "轻松"
    if value < 40:
        return "正常"
    if value < 60:
        return "有点忙"
    if value < 80:
        return "负担偏重"
    return "吃力"


def load_tone(value: float) -> str:
    """Return the page tone for an organ-load proxy."""
    if value >= 78:
        return "hot"
    if value >= 55:
        return "warn"
    return "ok"


def plaque_label(value: float) -> str:
    """Describe how much plaque is on the teeth."""
    if value < 20:
        return "干净"
    if value < 45:
        return "薄薄一层"
    if value < 70:
        return "有点厚"
    return "厚"


def gum_label(value: float) -> str:
    """Describe the gums, which react to plaque and illness."""
    if value < 15:
        return "健康"
    if value < 35:
        return "刷牙会出血"
    return "红肿"


def saliva_label(value: float) -> str:
    """Describe saliva flow, not thirst."""
    if value < 25:
        return "发干"
    if value < 40:
        return "偏少"
    if value < 70:
        return "正常"
    if value < 85:
        return "偏多"
    return "很多"


def tongue_label(value: float) -> str:
    """Describe the tongue coating."""
    if value < 18:
        return "薄白"
    if value < 40:
        return "有点厚"
    if value < 65:
        return "厚腻"
    return "发黄厚腻"


def throat_label(value: float) -> str:
    """Describe throat soreness or dryness."""
    if value < 15:
        return "正常"
    if value < 40:
        return "发干"
    return "有点痛"


def rectum_label(value: float) -> str:
    """Describe how much is waiting in the rectum."""
    if value < 12:
        return "空的"
    if value < 35:
        return "有点内容"
    if value < 65:
        return "有便意"
    return "憋得慌"


def urethra_label(value: float) -> str:
    """Describe how the urethra feels, not how full the bladder is."""
    if value < 25:
        return "正常"
    if value < 50:
        return "有点热"
    return "灼热"


def anus_label(value: float) -> str:
    """Describe the anus after a bowel movement or a long day."""
    if value < 25:
        return "正常"
    if value < 55:
        return "有点刺激"
    return "火辣发胀"


def _uterus_text(view: CycleView) -> str:
    """Describe what the uterus is doing right now."""
    if view.phase == "premenarche":
        return "还没发育成熟，安静"
    if view.phase == "menopause":
        return "已经萎缩，安静"
    if view.phase == "pregnant":
        return f"随孕周增大，怀孕第 {view.weeks} 周"
    if view.phase == "menstrual":
        return "内膜脱落，正在收缩"
    if view.phase == "ovulation":
        return "内膜增厚，接近排卵"
    if view.phase == "luteal":
        return "内膜继续增厚，等下一次经期"
    return "内膜在修复"


def _ovaries_text(view: CycleView) -> str:
    """Describe what the ovaries are doing right now."""
    if view.phase == "premenarche":
        return "还没开始周期性排卵"
    if view.phase == "menopause":
        return "功能衰退，激素很低"
    if view.phase == "pregnant":
        return "黄体维持早期，之后由胎盘接管"
    if view.phase == "ovulation":
        return f"{view.ovulation_side or '一'}侧卵巢正在排卵"
    if view.phase == "luteal":
        return "排卵后形成黄体，在分泌孕激素"
    if view.phase == "menstrual":
        return "新的卵泡开始长，另一侧安静"
    if view.anovulatory:
        return "这个周期没有排卵，卵泡没有长成"
    return "一侧卵泡在长，另一侧安静"


def organ_rows(state: BodyState, view: CycleView | None) -> list[dict[str, Any]]:
    """Return the five organ-load rows shown in private detail.

    The values are deliberately soft proxies, not medical readings. They give
    the model a "current state" to speak from instead of only a hormone number.

    Args:
        state: Current body state.
        view: Current cycle view, or None.

    Returns:
        A list of row dictionaries with key, label, value, level and note.
    """
    bladder_note = (
        "尿色偏深，喝点水会好一些" if state.urine_color >= 2 else "没什么特别"
    )
    kidney_note = "喝水少，尿液偏浓" if state.hydration < 50 else "水分够，过滤负担不重"
    if state.caffeine >= 40:
        kidney_note = "咖啡因和水分一起在推着肾脏干活"
    liver_note = "最近吃得偏油偏辣" if state.indigestion >= 35 else "饮食还算平稳"
    if state.diet_long < 40:
        liver_note = "最近油和糖偏多，肝脏在加班"
    rows = [
        {
            "key": "bladder",
            "label": "膀胱",
            "value": round(state.bladder, 1),
            "level": bladder_label(state.bladder),
            "tone": "hot"
            if state.bladder >= 80
            else ("warn" if state.bladder >= 55 else "ok"),
            "note": bladder_note,
        },
        {
            "key": "kidney",
            "label": "肾脏",
            "value": round(state.kidney_load, 1),
            "level": load_label(state.kidney_load),
            "tone": load_tone(state.kidney_load),
            "note": kidney_note,
        },
        {
            "key": "liver",
            "label": "肝脏",
            "value": round(state.liver_load, 1),
            "level": load_label(state.liver_load),
            "tone": load_tone(state.liver_load),
            "note": liver_note,
        },
    ]
    if view is not None:
        rows.append(
            {
                "key": "uterus",
                "label": "子宫",
                "value": round(state.uterine_congestion, 1),
                "level": _uterus_text(view),
                "tone": "warn" if view.phase in ("menstrual", "pregnant") else "ok",
                "note": "经期充血" if view.phase == "menstrual" else "随周期变化",
            }
        )
        if view.phase == "follicular":
            ovary_value = max(view.follicle, min(85.0, view.estrogen * 0.9))
        elif view.phase == "menstrual":
            ovary_value = 15.0
        elif view.phase == "ovulation":
            ovary_value = 88.0
        elif view.phase == "luteal":
            ovary_value = 42.0
        elif view.phase == "pregnant":
            ovary_value = 58.0
        elif view.phase == "premenarche":
            ovary_value = 8.0
        elif view.phase == "menopause":
            ovary_value = 6.0
        else:
            ovary_value = 20.0
        rows.append(
            {
                "key": "ovaries",
                "label": "卵巢",
                "value": round(ovary_value, 1),
                "level": _ovaries_text(view),
                "tone": "warn" if view.phase == "ovulation" else "ok",
                "note": "这个周期无排卵" if view.anovulatory else "随周期活动",
            }
        )
    rows.extend(
        [
            {
                "key": "rectum",
                "label": "直肠",
                "value": round(state.rectum, 1),
                "level": rectum_label(state.rectum),
                "tone": "hot"
                if state.rectum >= 70
                else ("warn" if state.rectum >= 40 else "ok"),
                "note": "刚排空" if state.rectum < 12 else "等着排出去",
            },
            {
                "key": "urethra",
                "label": "尿道",
                "value": round(state.urethra_irritation, 1),
                "level": urethra_label(state.urethra_irritation),
                "tone": "hot"
                if state.urethra_irritation >= 55
                else ("warn" if state.urethra_irritation >= 30 else "ok"),
                "note": "水喝少了" if state.hydration < 50 else "没什么特别",
            },
            {
                "key": "anus",
                "label": "肛门",
                "value": round(state.anus_irritation, 1),
                "level": anus_label(state.anus_irritation),
                "tone": "hot"
                if state.anus_irritation >= 55
                else ("warn" if state.anus_irritation >= 30 else "ok"),
                "note": "刚排过，擦得有点多"
                if state.anus_irritation >= 30
                else "没什么特别",
            },
        ]
    )
    return rows


def scent_label(value: float) -> str:
    """Return a neutral description of the body-smell meter."""
    if value >= 70:
        return "味道有点重"
    if value >= 50:
        return "自己闻得到"
    if value >= 30:
        return "淡淡的"
    return "清爽"


def wetness_label(value: float) -> str:
    """Return a private arousal description for the wetness meter."""
    if value >= 75:
        return "内裤湿了一小片，坐着有点黏"
    if value >= 45:
        return "内裤有点潮"
    if value >= 20:
        return "下面有点湿，身体发热"
    return ""


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
        return "充足"
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


def weather_label(kind: str) -> str:
    """Return a short Chinese label for a simulated weather kind."""
    return {
        "clear": "晴",
        "cloudy": "阴",
        "rain": "下雨",
        "snow": "下雪",
    }.get(kind, "晴")


def clothing_label(ambient: float, warmth: float) -> str:
    """Describe whether the current clothing fits the ambient temperature."""
    if ambient < 10 and warmth < 55:
        return "穿得有点少"
    if ambient > 27 and warmth > 65:
        return "穿得有点多"
    return "穿得刚好"


def sleep_quality_label(value: float) -> str:
    """Describe last night's sleep quality."""
    if value < 45:
        return "没睡好"
    if value < 65:
        return "睡得一般"
    return "睡得还行"


def iron_label(value: float) -> str:
    """Describe iron status in everyday language."""
    if value < 30:
        return "有点缺铁"
    if value < 50:
        return "偏低"
    return "正常"


def eye_label(value: float) -> str:
    """Describe eye strain."""
    if value < 30:
        return "还好"
    if value < 60:
        return "有点干"
    return "很干"


def oil_label(value: float) -> str:
    """Describe skin oiliness."""
    if value < 30:
        return "清爽"
    if value < 60:
        return "有点油"
    return "很油"


def skin_quality_label(value: float) -> str:
    """Describe the current skin condition."""
    if value < 45:
        return "气色不太好"
    if value < 60:
        return "有点粗糙"
    if value < 75:
        return "还行"
    return "状态不错"


def skin_type_label(rate: float) -> str:
    """Describe the persona's stable skin type."""
    if rate < 0.82:
        return "偏干"
    if rate > 1.18:
        return "偏油"
    return "中性"


def illness_label(value: float) -> str:
    """Describe a mild cold by how much it is still affecting her."""
    if value >= 45:
        return "感冒，低烧"
    if value >= 20:
        return "感冒还没好"
    return "快好了"


def pain_label(value: float) -> str:
    """Describe pain intensity without turning it into a number in dialogue."""
    if value >= 65:
        return "疼得明显"
    if value >= 40:
        return "有点疼"
    return ""


def temperature_label(value: float) -> str:
    """Describe a body temperature in everyday language."""
    if value < 36.2:
        return "偏低"
    if value < 36.9:
        return "正常"
    if value < 37.3:
        return "有点热"
    return "发热"


def sweat_label(value: float) -> str:
    """Describe how damp the skin feels."""
    if value < 12:
        return "干爽"
    if value < 30:
        return "有点潮"
    if value < 60:
        return "出汗了"
    return "汗湿"


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
    if value >= 90:
        return "刚刷过"
    if value >= 65:
        return "清爽"
    if value >= 40:
        return "还行"
    if value >= 18:
        return "有点口气"
    if value >= 10:
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
        "bowel": bowel_label,
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
        ("satiety", "饱食度", "饱食度", float(state.satiety), satiety_label, True),
        ("hydration", "水分", "水分", float(state.hydration), hydration_label, True),
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
                (
                    "scent",
                    "体味",
                    "体味",
                    float(state.scent),
                    scent_label,
                    False,
                ),
            ]
        )
    rows.append(
        ("bladder", "膀胱压力", "膀胱", float(state.bladder), bladder_label, False)
    )
    rows.append(("bowel", "排便", "排便", float(state.bowel), bowel_label, False))
    if profile.gender_resolved and settings.enable_intimate:
        rows.append(
            ("libido", "性欲", "性欲", float(state.libido), libido_label, False)
        )

    meter_groups = {
        "satiety": "饮食与能量",
        "hydration": "水分与排泄",
        "energy": "精力与作息",
        "cleanliness": "清洁与体味",
        "oral_health": "清洁与体味",
        "scent": "清洁与体味",
        "bladder": "水分与排泄",
        "bowel": "水分与排泄",
        "libido": "周期与亲密",
    }
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
                "group": meter_groups.get(key, "其他"),
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
            "intimacy",
            "engorged",
            "lactation",
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
                "conception",
                "prenatal",
                "miscarriage",
                "birth",
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
        if not private and "喂了奶" in text:
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
    """Build one short line for things the meters above do not say.

    Args:
        state: Current body state.
        now: Current local time.

    Returns:
        A short sentence, or an empty string when nothing is notable.
    """
    bits: list[str] = []
    if state.sleep_debt >= 3.0:
        bits.append(f"睡眠债约 {state.sleep_debt:.1f} 小时")
    if state.glucose < 45.0:
        bits.append("血糖偏低")
    if state.hydration < 45.0:
        bits.append("缺水")
    if not bits:
        return ""
    return "内部：" + "，".join(bits) + "。"


def _symptom_text(view: CycleView) -> str:
    """Return the premenstrual symptoms that are actually present."""
    bits = []
    if view.breast >= 2:
        bits.append("乳房发胀")
    elif view.breast == 1:
        bits.append("乳房有点胀")
    if view.headache >= 2:
        bits.append("头痛")
    elif view.headache == 1:
        bits.append("头有点沉")
    if view.backache >= 2:
        bits.append("腰酸")
    elif view.backache == 1:
        bits.append("腰有点软")
    return "、".join(bits)


def _cycle_text(view: CycleView, include_mucus: bool = True) -> str:
    """Build the one-line cycle description used in prompts and cards.

    Args:
        view: Current cycle view.
        include_mucus: Whether to append the short mucus summary. Private
            prompts pass False because they get the detailed mucus line
            separately.
    """
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
        colour = "鲜红" if view.day <= 2 else ("暗红" if view.day <= 4 else "褐色收尾")
        if view.clots >= 2:
            colour += "，有明显血块"
        elif view.clots == 1:
            colour += "，偶尔有小血块"
        if view.cramps >= 2:
            colour += "，小腹坠痛"
        elif view.cramps == 1:
            colour += "，小腹有点坠"
        detail = "，".join(part for part in (flow, colour, view.note) if part)
        text = f"经期：第 {view.day} 天，{detail}；距下次约 {view.days_to_next} 天"
    elif view.phase == "luteal":
        text = f"周期：黄体期，{view.note}；距下次约 {view.days_to_next} 天"
    elif view.phase == "ovulation":
        text = f"周期：排卵期，{view.note}；距下次约 {view.days_to_next} 天"
    else:
        text = f"周期：第 {view.day} 天（经期后），距下次约 {view.days_to_next} 天"
    symptoms = _symptom_text(view)
    if symptoms:
        text += f"；{symptoms}"
    if include_mucus and view.phase != "menstrual":
        amount = mucus_volume_label(view.mucus_volume)
        texture = MUCUS_TEXTURE_LABELS.get(view.mucus_texture, "")
        if texture:
            text += f"；分泌物{amount}，{texture}"
    if view.anovulatory:
        text += "；本周期无排卵"
    if view.spotting:
        text += "；有点点出血"
    if view.ovulation_pain:
        text += f"；{view.ovulation_side or '一'}侧小腹有点抽痛"
    if view.ovulation_delay >= 2 and view.phase in ("ovulation", "luteal"):
        text += f"；这个周期排卵晚了 {view.ovulation_delay} 天"
    if view.puberty:
        text += "（青春期，周期还不太规律）"
    return text


def _missed_lines(
    state: BodyState,
    now: datetime,
    umo: str,
    limit: int = 4,
) -> list[str]:
    """Return the messages that arrived while she was asleep.

    Args:
        state: Current body state.
        now: Current local time.
        umo: Session origin to match; empty matches every session.
        limit: How many of the newest messages to keep.

    Returns:
        Short clock-and-excerpt strings, oldest first.
    """
    cutoff = now.timestamp() - 12 * 3600
    rows: list[str] = []
    for item in state.missed[-12:]:
        stamp = float(item.get("at") or 0)
        if stamp < cutoff:
            continue
        owner = str(item.get("umo") or "")
        if umo and owner and owner != umo:
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        moment = datetime.fromtimestamp(stamp, now.tzinfo)
        rows.append(f"{moment:%H:%M}「{text[:40]}」")
    return rows[-limit:]


def _supply_text(state: BodyState, view: CycleView | None) -> str:
    """Return the sanitary-supply line, only while it matters."""
    if view is None or view.phase != "menstrual":
        return ""
    if state.pads <= 0:
        return "卫生用品：已经用完了"
    return f"卫生用品：还剩 {state.pads} 片"


def _male_text(state: BodyState, now: datetime, profile: Profile) -> str:
    """Build the private male line."""
    if not (profile.gender_resolved and profile.gender == "male"):
        return ""
    if state.last_release_at <= 0:
        days = 0
    else:
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


def _cycle_mood_text(view: CycleView) -> str:
    """Return a short private line about cycle-driven mood.

    Args:
        view: Current cycle view.

    Returns:
        A Chinese sentence, or an empty string outside the notable phases.
    """
    if view.phase == "luteal" and 0 < view.days_to_next <= 5:
        return "情绪：经前这几天耐心比平时薄，容易烦躁，也容易一点小事就委屈。"
    if view.phase == "menstrual":
        return "情绪：经期容易累、情绪偏低，不太想被打扰。"
    if view.phase == "ovulation":
        return "情绪：这几天心情和精力都好一些。"
    return ""


def _mucus_text(view: CycleView) -> str:
    """Return a private, clinical cervical-mucus line.

    Args:
        view: Current cycle view.

    Returns:
        A Chinese sentence describing amount, texture, colour and cervix.
    """
    amount = mucus_volume_label(view.mucus_volume)
    texture = MUCUS_TEXTURE_LABELS.get(view.mucus_texture, view.mucus_texture)
    color = MUCUS_COLOR_LABELS.get(view.mucus_color, view.mucus_color)
    position = CERVIX_POSITION_LABELS.get(view.cervix_position, "")
    firmness = CERVIX_FIRMNESS_LABELS.get(view.cervix_firmness, "")
    opened = "、微微张开" if view.cervix_open else "、闭合"
    stretch = (
        f"，能拉丝约 {view.mucus_stretch:.0f} cm" if view.mucus_stretch >= 1.0 else ""
    )
    if view.phase == "menstrual":
        return (
            f"分泌物：经血为主，{FLOW_LABELS.get(view.flow, '')}，{color}；"
            f"宫颈{position}、{firmness}{opened}。"
        )
    return (
        f"分泌物：量{amount}，{texture}，{color}{stretch}；"
        f"宫颈{position}、{firmness}{opened}。"
    )


def _genital_text(view: CycleView) -> str:
    """Return the private line for the outer genitals and the vagina.

    The old line said only "外阴湿". A body has several parts that change on
    their own, so the labia, clitoris, vestibule and perineum are described
    separately from the vaginal tone, warmth and opening.

    Args:
        view: Current cycle view.

    Returns:
        One Chinese sentence.
    """
    major = LABIA_MAJORA_LABELS.get(view.labia_majora, view.labia_majora)
    minor = LABIA_MINORA_LABELS.get(view.labia_minora, view.labia_minora)
    clitoris = CLITORIS_LABELS.get(view.clitoris, view.clitoris)
    vestibule = VESTIBULE_LABELS.get(view.vestibule, view.vestibule)
    perineum = PERINEUM_LABELS.get(view.perineum, view.perineum)
    tone = VAGINA_TONE_LABELS.get(view.vagina_tone, view.vagina_tone)
    warmth = VAGINA_WARMTH_LABELS.get(view.vagina_warmth, view.vagina_warmth)
    opened = VAGINA_OPEN_LABELS.get(view.vagina_open, view.vagina_open)
    return (
        f"外阴：大阴唇{major}、小阴唇{minor}、阴蒂{clitoris}、"
        f"前庭{vestibule}、会阴{perineum}；"
        f"阴道：{tone}、{warmth}、{opened}。"
    )


def _traits_text(view: CycleView) -> str:
    """Return the private line for pubic hair, areola and nipples.

    These are stable per persona (seeded from the constitution), so two
    personas do not share one default body; pregnancy and lactation darken and
    enlarge the areola, and arousal hardens the nipple.

    Args:
        view: Current cycle view.

    Returns:
        One Chinese sentence.
    """
    if view.pubic_hair == "shaved":
        hair = "剃光"
    else:
        hair = (
            PUBIC_HAIR_LABELS.get(view.pubic_hair, view.pubic_hair)
            + "、"
            + PUBIC_STYLE_LABELS.get(view.pubic_hair_style, view.pubic_hair_style)
        )
    areola = AREOLA_LABELS.get(view.areola, view.areola)
    tint = AREOLA_TINT_LABELS.get(view.areola_tint, view.areola_tint)
    nipple = NIPPLE_LABELS.get(view.nipple, view.nipple)
    glands = "、蒙氏腺明显" if view.montgomery else ""
    return f"阴毛：{hair}；乳晕：{areola}、{tint}{glands}；乳头：{nipple}。"


def _oral_text(state: BodyState) -> str:
    """Return the private line for the mouth beyond the oral meter.

    Args:
        state: Current body state.

    Returns:
        One Chinese sentence about teeth, gums, tongue, saliva and throat.
    """
    return (
        f"口腔：牙齿{plaque_label(state.plaque)}、牙龈{gum_label(state.gum_irritation)}、"
        f"舌苔{tongue_label(state.tongue_coat)}、唾液{saliva_label(state.saliva)}、"
        f"咽部{throat_label(state.throat)}。"
    )


def _organ_text(state: BodyState, view: CycleView | None) -> str:
    """Return one compact private line about the organ-load proxies.

    Args:
        state: Current body state.
        view: Current cycle view, or None.

    Returns:
        A Chinese sentence, or an empty string when no cycle is available.
    """
    rows = organ_rows(state, view)
    if not rows:
        return ""
    parts = []
    for row in rows:
        if row["key"] in ("rectum", "urethra", "anus") and row["level"] in (
            "正常",
            "空的",
        ):
            # These three are quiet by default; only speak up when notable.
            continue
        if row["key"] in ("uterus", "ovaries", "rectum", "urethra", "anus"):
            parts.append(f"{row['label']}：{row['level']}")
        else:
            parts.append(f"{row['label']}{row['level']}")
    return "器官：" + "；".join(parts) + "。"


def _last_act_at(state: BodyState) -> float:
    """Return when she last came, whatever caused it, or 0 when never."""
    return max(state.last_intimacy_at, state.last_self_at, state.last_release_at)


def _intimate_level(hours: float | None) -> str:
    """Describe a scent by how long ago the act was, not by a meter."""
    if hours is None:
        return "没有"
    if hours <= 1.0:
        return "刚结束"
    if hours <= 8.0:
        return "还留着一点"
    if hours <= 36.0:
        return "很淡了"
    return "没有"


def _intimate_line(profile: Profile, hours: float) -> str:
    """Describe the scent of the last act, from just-finished to faint."""
    flavor = "腥甜" if profile.gender == "female" else "腥"
    if hours <= 1.0:
        return f"身体：刚结束，事后身上{flavor}味和汗味还很重，黏着没干。"
    if hours <= 8.0:
        return f"身体：事后身上留着一点淡淡的{flavor}味，混着汗味，洗一下会淡。"
    return f"身体：还留着一点很淡的{flavor}味，自己闻得到。"


def _scent_rows(
    state: BodyState,
    view: CycleView | None,
    profile: Profile,
    now: datetime,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Return the scent sources shown on the page.

    Args:
        state: Current body state.
        view: Current cycle view, or None.
        profile: Resolved profile; the intimate line reads by gender.
        now: Current local time.
        settings: Plugin settings; the intimate row follows ENABLE_INTIMATE.

    Returns:
        A list of row dictionaries.
    """
    rows = [
        {
            "key": "scent",
            "label": "体味",
            "value": round(state.scent, 1),
            "level": scent_label(state.scent),
            "tone": "hot"
            if state.scent >= 70
            else ("warn" if state.scent >= 50 else "ok"),
        },
        {
            "key": "scalp",
            "label": "头发",
            "value": round(state.scalp_scent, 1),
            "level": "有点油味" if state.scalp_scent >= 45 else "清爽",
            "tone": "warn" if state.scalp_scent >= 45 else "ok",
        },
        {
            "key": "breath",
            "label": "口气",
            "value": round(state.breath_scent, 1),
            "level": "有点味道" if state.breath_scent >= 40 else "清新",
            "tone": "warn" if state.breath_scent >= 40 else "ok",
        },
        {
            "key": "foot",
            "label": "脚",
            "value": round(state.foot_scent, 1),
            "level": "有点闷" if state.foot_scent >= 45 else "清爽",
            "tone": "warn" if state.foot_scent >= 45 else "ok",
        },
        {
            "key": "period",
            "label": "经期气味",
            "value": round(state.period_scent, 1),
            "level": "有一点" if state.period_scent >= 30 else "没有",
            "tone": "warn" if state.period_scent >= 30 else "ok",
        },
    ]
    if settings.enable_intimate:
        last_act = _last_act_at(state)
        hours = (
            max(0.0, (now.timestamp() - last_act) / 3600.0) if last_act > 0.0 else None
        )
        rows.append(
            {
                "key": "intimate",
                "label": "亲密后",
                "value": round(state.intimate_scent, 1),
                "level": _intimate_level(hours),
                "tone": "warn" if hours is not None and hours <= 8.0 else "ok",
            }
        )
    if state.lactating:
        rows.append(
            {
                "key": "milk",
                "label": "奶味",
                "value": round(min(100.0, state.milk * 0.8), 1),
                "level": "明显" if state.milk >= 60 else "有一点",
                "tone": "ok",
            }
        )
    return rows


def _scent_lines(
    state: BodyState,
    view: CycleView | None,
    profile: Profile,
    now: datetime,
    settings: Settings,
) -> list[str]:
    """Return private scent lines, one per noticeable source.

    The intimate line is driven by when the act happened rather than by the
    meter: right afterwards there is always something to smell, and a wash is
    what removes it.

    Args:
        state: Current body state.
        view: Cycle view, or None.
        profile: Resolved profile; the intimate line reads by gender.
        now: Current local time.
        settings: Plugin settings; the line follows ENABLE_INTIMATE.

    Returns:
        A list of Chinese sentences.
    """
    lines: list[str] = []
    if state.scent >= 50.0:
        if view is not None and view.phase == "menstrual":
            lines.append("气味：身上有淡淡的铁锈味，她自己闻得到。")
        elif view is not None and view.phase == "ovulation":
            lines.append("气味：这几天身上比平时更容易被闻到。")
        else:
            lines.append("气味：身上有点汗味，想洗个澡。")
    if state.scalp_scent >= 45.0:
        lines.append("头发：发根有一点油味，枕头上也沾了一点。")
    if state.breath_scent >= 40.0:
        lines.append("口气：呼气有点味道，她自己能感觉到。")
    if state.foot_scent >= 45.0:
        lines.append("脚：闷了一天，袜子上有点味道。")
    if state.period_scent >= 30.0:
        lines.append("经期：身上有一点血腥味。")
    if settings.enable_intimate:
        last_act = _last_act_at(state)
        if last_act > 0.0:
            hours = max(0.0, (now.timestamp() - last_act) / 3600.0)
            washed = state.last_shower_at >= last_act and state.intimate_scent < 10.0
            if hours <= 36.0 and not washed:
                lines.append(_intimate_line(profile, hours))
    if state.lactating and state.milk >= 40.0:
        lines.append("胸前：有一点奶味，衣服上也能闻到。")
    return lines


def build_prompt_block(
    state: BodyState,
    now: datetime,
    profile: Profile,
    settings: Settings,
    private: bool,
    umo: str = "",
) -> str:
    """Build the per-turn body block injected next to the user message.

    Args:
        state: Advanced body state.
        now: Local timezone-aware current time.
        profile: Resolved persona profile.
        settings: Plugin settings.
        private: Whether this is a private chat.
        umo: Session origin, used to pick the messages she slept through.

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
        "bowel": state.bowel,
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
        important += ["bowel"] if values["bowel"] >= 50 else []
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
            f"水分：{hydration_label(state.hydration)}（{_ago(state.last_drink_at, now)}喝过水）"
        )
        if state.bladder >= 35:
            lines.append(f"小便：{bladder_label(state.bladder)}")
        if state.bowel >= 35:
            lines.append(f"排便：{bowel_label(state.bowel)}")
        if state.temperature < 36.3 or state.temperature >= 36.9:
            lines.append(
                f"体温：{state.temperature:.1f}℃"
                f"（{temperature_label(state.temperature)}）"
            )
        if state.sweat >= 20:
            lines.append(f"皮肤：{sweat_label(state.sweat)}")
        if state.illness:
            lines.append(f"感冒：{illness_label(state.illness_severity)}")
        if state.allergy >= 25.0:
            lines.append(
                "过敏：鼻子痒，偶尔打喷嚏。"
                if state.allergy < 45.0
                else "过敏：鼻子堵，眼睛也痒。"
            )
        if state.migraine_until > now.timestamp():
            lines.append("偏头痛：太阳穴一跳一跳，光有点刺眼。")
        if state.pain >= 25:
            lines.append(f"疼：{state.pain_site or '身上'}，{pain_label(state.pain)}")
        if state.heart_rate >= 88:
            lines.append("心跳：有点快")
        if state.breath_rate >= 19:
            lines.append("呼吸：有点急")
        if (
            state.weather != "clear"
            or state.ambient_temp < 10
            or state.ambient_temp > 30
        ):
            lines.append(
                f"天气：{weather_label(state.weather)}，{state.ambient_temp:.0f}℃；"
                f"{clothing_label(state.ambient_temp, state.clothing_warmth)}"
            )
        if state.sleep_quality < 60 or state.dream:
            sleep_line = f"睡眠：{sleep_quality_label(state.sleep_quality)}"
            if state.dream:
                sleep_line += f"，{state.dream}"
            lines.append(sleep_line)
        if state.iron < 45:
            lines.append(f"铁：{iron_label(state.iron)}，容易头晕")
        if state.craving:
            lines.append(f"食欲：{state.craving}")
        if state.eye_strain >= 55:
            lines.append(f"眼睛：{eye_label(state.eye_strain)}")
        if state.skin_oil >= 55 or state.skin_quality < 60:
            lines.append(
                f"皮肤：{skin_quality_label(state.skin_quality)}，"
                f"{oil_label(state.skin_oil)}"
            )
        if settings.enable_hygiene and (
            state.cleanliness < 70 or state.oral_health < 65
        ):
            lines.append(
                f"清洁：{clean_label(state.cleanliness)}；"
                f"口腔：{oral_label(state.oral_health)}"
            )
        lines.append(f"精力：{energy_label(state.energy)}")
    else:
        lines.append(f"胃：{satiety_label(state.satiety)}")
        lines.append(f"水分：{hydration_label(state.hydration)}")
        lines.append(f"精力：{energy_label(state.energy)}")
        if view is not None and view.phase == "menstrual" and view.cramps >= 2:
            lines.append("身体：肚子有点不舒服，不太想动。")

    if (
        show_private
        and settings.intensity != "subtle"
        and (state.stomach < 35 or state.glucose < 50 or state.sleep_debt > 6)
    ):
        lines.append(_internal_line(state, now))
    if view is not None and (show_private or full_group):
        lines.append(
            _cycle_text(
                view,
            )
        )
        if state.hot_flash_until > now.timestamp():
            lines.append("身体：突然一阵潮热，后背有点汗。")
        if show_private:
            mood_line = _cycle_mood_text(view)
            if mood_line:
                lines.append(mood_line)
            lines.append(_mucus_text(view))
            lines.append(_genital_text(view))
            lines.append(_traits_text(view))
            lines.append(_organ_text(state, view))
            warm = "，排卵后偏高" if view.progesterone > 25.0 else ""
            lines.append(f"基础体温：{view.bbt:.2f}℃{warm}")
        if show_private and view.fertile:
            lines.append("身体：这几天更容易受孕。")
        supply = _supply_text(state, view) if show_private else ""
        if supply:
            lines.append(supply)
        if show_private and state.lactating:
            lines.append(_lactation_text(state))
    if show_private:
        for scent_line in _scent_lines(state, view, profile, now, settings):
            lines.append(scent_line)
        if settings.enable_hygiene and (
            state.plaque >= 35
            or state.gum_irritation >= 25
            or state.throat >= 25
            or state.tongue_coat >= 35
            or state.saliva <= 40
        ):
            lines.append(_oral_text(state))
        if view is None:
            organ_line = _organ_text(state, view)
            if organ_line:
                lines.append(organ_line)
    if show_private and settings.enable_intimate and profile.gender_resolved:
        male = _male_text(state, now, profile)
        if male:
            lines.append(male)
        if state.libido >= 60:
            lines.append("性欲：身体很想要；这种事她心里清楚，不用对任何人说。")
        elif state.libido >= 35:
            lines.append(
                f"性欲：{libido_label(state.libido)}（私密，只在合适的时候自然流露）"
            )
        if profile.gender == "female" and state.wetness >= 20:
            lines.append(f"身体：{wetness_label(state.wetness)}。")
        if profile.gender == "female" and state.libido >= 75 and state.wetness >= 45:
            lines.append("身体：突然有点想要，注意力容易被拉走。")
        if (
            profile.gender == "female"
            and view is not None
            and view.phase == "menstrual"
        ):
            if state.libido < 55:
                lines.append("身体：经期不太想做，更想被抱着、被照顾。")
            elif state.libido >= 60:
                lines.append("身体：经期也想要，只是更想慢慢来、被照顾。")

    sleep = sleep_view(state, now, profile, settings) if settings.enable_sleep else None
    if sleep is not None and sleep.asleep:
        lines.append(f"睡眠：{sleep.line}，已经睡着了，不该开口。")
    elif sleep is not None and sleep.in_bed:
        lines.append(
            f"睡眠：{sleep.line}，躺着但还没睡着；要是有人说话，可以迷迷糊糊回一句。"
        )
    elif sleep is not None and not sleep.asleep:
        missed_rows = _missed_lines(state, now, umo)
        woke_recently = bool(
            state.last_wake_at and 0 <= now.timestamp() - state.last_wake_at <= 6 * 3600
        )
        if sleep.just_woke:
            lines.append(f"睡眠：{sleep.line}，脑子还在慢慢清醒。")
        elif sleep.line:
            lines.append(f"睡眠：{sleep.line}")
        if missed_rows and (sleep.just_woke or woke_recently):
            lines.append("睡着时没看到的消息：" + "；".join(missed_rows))
    if state.busy_kind and state.busy_until > now.timestamp():
        busy = datetime.fromtimestamp(state.busy_until, now.tzinfo)
        lines.append(f"手上的事：{state.busy_kind}，大概 {busy:%H:%M} 才告一段落。")
    if state.location == "out":
        lines.append("现在：在外面。")
    if show_private and state.caffeine >= 45.0:
        lines.append("咖啡因：刚喝过咖啡或茶，心跳有点快，不太困。")
    if show_private and state.stress >= 60.0:
        lines.append("压力：身体一直绷着，肩背发紧。")
    if show_private and state.sedentary >= 65.0:
        lines.append("久坐：坐得久了，腰背有点发酸。")
    if show_private and state.sodium >= 60.0:
        lines.append("口味：今天吃得偏咸，口有点干，想多喝水。")
    if show_private and state.leg_fatigue >= 55.0:
        lines.append("腿：走得多，小腿有点酸。")
    if show_private and state.indigestion >= 40.0:
        lines.append("肠胃：辣的或油的吃多了，胃里有点胀。")
    if show_private and state.diet_quality < 40.0:
        lines.append("饮食：最近吃得有点将就，蔬菜水果少了。")
    if show_private and state.daylight <= 9.5:
        lines.append("日照：冬天白天短，人容易提不起劲。")
    if show_private and state.diet_long < 40.0:
        lines.append("身体：最近油和糖吃多了，皮肤有点闹脾气，经前也更容易难受。")
    if state.temperature >= 37.3:
        lines.append(f"体温：{state.temperature:.1f}℃，有点发烧。")
    if state.illness:
        if state.illness_severity >= 60.0:
            lines.append("感冒：正难受的时候，鼻塞、浑身发酸。")
        elif state.illness_severity >= 30.0:
            lines.append("感冒：还没好利索，嗓子有点哑。")
    if state.urine_color >= 2:
        lines.append("尿：颜色有点深，量也不多。")
    if state.dry_mouth >= 40.0:
        lines.append("嘴：有点干。")
    if state.nasal >= 30.0:
        lines.append("鼻子：有点堵，流清鼻涕。")

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
    rules = ""
    if settings.enable_tools:
        rules += (
            "真的做了吃饭、喝水、上厕所、小睡、洗澡、刷牙这些事时，"
            "可以用 body_action 记录。"
        )
    if not private:
        if full_group:
            rules += "群聊里可以提非性相关的身体状态，但不要提亲密细节。"
        else:
            rules += "群聊里不必提经期或身体私密细节。"
    if rules:
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
    sleep = sleep_view(state, now, profile, settings)
    lines = [f"{profile.name} · 身体"]
    lines.append(f"现在 {now:%H:%M}")
    if settings.enable_sleep:
        if sleep.asleep:
            lines.append(f"睡眠：{sleep.line}，这会儿谁发消息都不会回。")
        elif sleep.in_bed:
            lines.append(f"睡眠：{sleep.line}")
        elif sleep.line:
            lines.append(f"睡眠：{sleep.line}")
        else:
            lines.append(f"睡眠：醒着（上次醒来 {_ago(state.last_wake_at, now)}）")
    if state.busy_kind and state.busy_until > now.timestamp():
        busy = datetime.fromtimestamp(state.busy_until, now.tzinfo)
        lines.append(f"手上：{state.busy_kind}，约 {busy:%H:%M} 结束")
    meters = {meter["key"]: meter for meter in display_meters(state, profile, settings)}
    satiety = meters["satiety"]
    hydration = meters["hydration"]
    energy = meters["energy"]
    if private:
        lines.extend(
            [
                f"饱食度：{satiety['level']}（{round(satiety['value'])}/100，{_ago(state.last_eat_at, now)}吃过东西）",
                f"水分：{hydration['level']}（{round(hydration['value'])}/100，{_ago(state.last_drink_at, now)}喝过水）",
                f"小便：{meters['bladder']['level']}（{round(meters['bladder']['value'])}/100）",
                f"排便：{meters['bowel']['level']}（{round(meters['bowel']['value'])}/100）",
            ]
        )
        if settings.enable_hygiene:
            lines.append(
                f"清洁：{meters['cleanliness']['level']}（{round(meters['cleanliness']['value'])}/100）"
                f" · 口腔：{meters['oral_health']['level']}（{round(meters['oral_health']['value'])}/100）"
            )
        lines.append(
            f"体温：{state.temperature:.1f}℃（{temperature_label(state.temperature)}）"
        )
        if state.sweat >= 15:
            lines.append(f"皮肤：{sweat_label(state.sweat)}")
        if state.illness:
            lines.append(f"感冒：{illness_label(state.illness_severity)}")
        if state.pain >= 25:
            lines.append(f"疼：{state.pain_site or '身上'}，{pain_label(state.pain)}")
        if state.heart_rate >= 88:
            lines.append("心跳：有点快")
        if state.breath_rate >= 19:
            lines.append("呼吸：有点急")
        if (
            state.weather != "clear"
            or state.ambient_temp < 10
            or state.ambient_temp > 30
        ):
            lines.append(
                f"天气：{weather_label(state.weather)}，{state.ambient_temp:.0f}℃；"
                f"{clothing_label(state.ambient_temp, state.clothing_warmth)}"
            )
        if state.sleep_quality < 60 or state.dream:
            sleep_line = f"睡眠：{sleep_quality_label(state.sleep_quality)}"
            if state.dream:
                sleep_line += f"，{state.dream}"
            lines.append(sleep_line)
        if state.iron < 45:
            lines.append(f"铁：{iron_label(state.iron)}，容易头晕")
        if state.craving:
            lines.append(f"食欲：{state.craving}")
        if state.eye_strain >= 55:
            lines.append(f"眼睛：{eye_label(state.eye_strain)}")
        if state.skin_oil >= 55 or state.skin_quality < 60:
            lines.append(
                f"皮肤：{skin_quality_label(state.skin_quality)}，"
                f"{oil_label(state.skin_oil)}"
            )
        lines.append(f"精力：{energy['level']}（{round(energy['value'])}/100）")
        lines.append(_internal_line(state, now))
        if settings.enable_intimate and profile.gender_resolved and state.libido >= 20:
            lines.append(
                f"性欲：{libido_label(state.libido)}（{round(state.libido)}/100）"
            )
        if view is not None and settings.enable_cycle:
            lines.append(
                _cycle_text(
                    view,
                )
            )
            if private:
                mood_line = _cycle_mood_text(view)
                if mood_line:
                    lines.append(mood_line)
                lines.append(_mucus_text(view))
                lines.append(_genital_text(view))
                lines.append(_traits_text(view))
                lines.append(_organ_text(state, view))
            if view.fertile:
                lines.append("身体：这几天更容易受孕。")
        if private:
            for scent_line in _scent_lines(state, view, profile, now, settings):
                lines.append(scent_line)
            if settings.enable_hygiene and (
                state.plaque >= 35
                or state.gum_irritation >= 25
                or state.throat >= 25
                or state.tongue_coat >= 35
                or state.saliva <= 40
            ):
                lines.append(_oral_text(state))
            if view is None:
                organ_line = _organ_text(state, view)
                if organ_line:
                    lines.append(organ_line)
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

    details.extend(
        [
            {
                "key": "temperature",
                "label": "体温",
                "value": round(state.temperature, 1),
                "unit": "℃",
                "display": f"{state.temperature:.1f} ℃",
                "bar": round((state.temperature - 35.5) / 2.5 * 100, 1),
                "level": temperature_label(state.temperature),
                "tone": "hot"
                if state.temperature >= 37.3
                else ("warn" if state.temperature >= 36.9 else "ok"),
            },
            {
                "key": "sweat",
                "label": "出汗",
                "value": round(state.sweat, 1),
                "unit": "%",
                "level": sweat_label(state.sweat),
                "tone": "hot"
                if state.sweat >= 60
                else ("warn" if state.sweat >= 30 else "ok"),
            },
            {
                "key": "heart_rate",
                "label": "心跳",
                "value": round(state.heart_rate, 0),
                "unit": "bpm",
                "display": f"{round(state.heart_rate)} bpm",
                "bar": round((state.heart_rate - 50.0) / 80.0 * 100, 1),
                "level": "有点快" if state.heart_rate >= 88 else "平稳",
                "tone": "hot"
                if state.heart_rate >= 105
                else ("warn" if state.heart_rate >= 88 else "ok"),
            },
            {
                "key": "breath_rate",
                "label": "呼吸",
                "value": round(state.breath_rate, 0),
                "unit": "/min",
                "display": f"{round(state.breath_rate)} /min",
                "bar": round((state.breath_rate - 8.0) / 20.0 * 100, 1),
                "level": "有点急" if state.breath_rate >= 19 else "平稳",
                "tone": "hot"
                if state.breath_rate >= 24
                else ("warn" if state.breath_rate >= 19 else "ok"),
            },
            {
                "key": "pain",
                "label": "疼痛",
                "value": round(state.pain, 1),
                "unit": "%",
                "level": pain_label(state.pain) or "没有",
                "tone": "hot"
                if state.pain >= 65
                else ("warn" if state.pain >= 40 else "ok"),
            },
            {
                "key": "weather",
                "label": "天气",
                "value": round(state.ambient_temp, 1),
                "unit": "℃",
                "display": (
                    f"{weather_label(state.weather)} {state.ambient_temp:.0f}℃ · "
                    f"{clothing_label(state.ambient_temp, state.clothing_warmth)}"
                ),
                "bar": round((state.ambient_temp + 10.0) / 45.0 * 100, 1),
                "level": weather_label(state.weather),
                "tone": "warn"
                if state.ambient_temp < 10 or state.ambient_temp > 30
                else "ok",
            },
            {
                "key": "sleep_quality",
                "label": "睡眠",
                "value": round(state.sleep_quality, 1),
                "unit": "%",
                "level": sleep_quality_label(state.sleep_quality),
                "tone": "hot"
                if state.sleep_quality < 45
                else ("warn" if state.sleep_quality < 65 else "ok"),
            },
            {
                "key": "iron",
                "label": "铁",
                "value": round(state.iron, 1),
                "unit": "%",
                "level": iron_label(state.iron),
                "tone": "hot"
                if state.iron < 30
                else ("warn" if state.iron < 50 else "ok"),
            },
            {
                "key": "eye_strain",
                "label": "眼睛",
                "value": round(state.eye_strain, 1),
                "unit": "%",
                "level": eye_label(state.eye_strain),
                "tone": "warn" if state.eye_strain >= 55 else "ok",
            },
            {
                "key": "skin_quality",
                "label": "皮肤",
                "value": round(state.skin_quality, 1),
                "unit": "%",
                "level": skin_quality_label(state.skin_quality),
                "tone": "warn" if state.skin_quality < 60 else "ok",
            },
            {
                "key": "skin_oil",
                "label": "皮肤出油",
                "value": round(state.skin_oil, 1),
                "unit": "%",
                "level": oil_label(state.skin_oil),
                "tone": "warn" if state.skin_oil >= 55 else "ok",
            },
            {
                "key": "skin_type",
                "label": "肤质",
                "value": round(
                    constitution(profile.persona_id).skin_oil_rate * 50.0, 0
                ),
                "unit": "%",
                "display": skin_type_label(
                    constitution(profile.persona_id).skin_oil_rate
                ),
                "level": "个人底子",
                "tone": "ok",
            },
        ]
    )
    if state.illness:
        details.append(
            {
                "key": "illness",
                "label": "身体",
                "value": round(state.illness_severity, 1),
                "unit": "%",
                "level": illness_label(state.illness_severity),
                "tone": "hot"
                if state.illness_severity >= 45
                else ("warn" if state.illness_severity >= 20 else "ok"),
            }
        )
    if state.allergy >= 12:
        details.append(
            {
                "key": "allergy",
                "label": "过敏",
                "value": round(state.allergy, 1),
                "unit": "%",
                "level": "鼻子痒" if state.allergy < 40 else "鼻子堵、眼睛痒",
                "tone": "warn" if state.allergy < 45 else "hot",
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
            "mucus_volume": view.mucus_volume,
            "mucus_volume_label": mucus_volume_label(view.mucus_volume),
            "mucus_texture": view.mucus_texture,
            "mucus_texture_label": MUCUS_TEXTURE_LABELS.get(
                view.mucus_texture, view.mucus_texture
            ),
            "mucus_color": view.mucus_color,
            "mucus_color_label": MUCUS_COLOR_LABELS.get(
                view.mucus_color, view.mucus_color
            ),
            "mucus_stretch": view.mucus_stretch,
            "cervix_position": view.cervix_position,
            "cervix_position_label": CERVIX_POSITION_LABELS.get(
                view.cervix_position, view.cervix_position
            ),
            "cervix_firmness": view.cervix_firmness,
            "cervix_firmness_label": CERVIX_FIRMNESS_LABELS.get(
                view.cervix_firmness, view.cervix_firmness
            ),
            "cervix_open": view.cervix_open,
            "pelvic_pressure": view.pelvic_pressure,
            "pelvic_pressure_label": pelvic_label(view.pelvic_pressure),
            "breast_fullness": view.breast_fullness,
            "breast_fullness_label": breast_label(view.breast_fullness),
            "follicle": view.follicle,
            "endocrine_maturity": view.endocrine_maturity,
            "puberty_stage": view.puberty_stage,
            "puberty_stage_label": PUBERTY_STAGE_LABELS.get(
                view.puberty_stage, view.puberty_stage
            ),
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
            "ovulation_pain": view.ovulation_pain,
            "ovulation_side": view.ovulation_side,
            "ovulation_delay": view.ovulation_delay,
            "clots": view.clots,
            "fsh": round(state.fsh, 1),
            "pads_today": state.pads_today,
            "hot_flash": state.hot_flash_until > now.timestamp(),
            "spotting": view.spotting,
            "perimenopausal": view.perimenopausal,
        }

    anatomy = None
    if view is not None:
        anatomy = {
            "labia_majora_label": LABIA_MAJORA_LABELS.get(
                view.labia_majora, view.labia_majora
            ),
            "labia_minora_label": LABIA_MINORA_LABELS.get(
                view.labia_minora, view.labia_minora
            ),
            "clitoris_label": CLITORIS_LABELS.get(view.clitoris, view.clitoris),
            "vestibule_label": VESTIBULE_LABELS.get(view.vestibule, view.vestibule),
            "perineum_label": PERINEUM_LABELS.get(view.perineum, view.perineum),
            "vagina_tone_label": VAGINA_TONE_LABELS.get(
                view.vagina_tone, view.vagina_tone
            ),
            "vagina_warmth_label": VAGINA_WARMTH_LABELS.get(
                view.vagina_warmth, view.vagina_warmth
            ),
            "vagina_open_label": VAGINA_OPEN_LABELS.get(
                view.vagina_open, view.vagina_open
            ),
            "pubic_hair": view.pubic_hair,
            "pubic_hair_label": PUBIC_HAIR_LABELS.get(view.pubic_hair, view.pubic_hair),
            "pubic_hair_style": view.pubic_hair_style,
            "pubic_hair_style_label": PUBIC_STYLE_LABELS.get(
                view.pubic_hair_style, view.pubic_hair_style
            ),
            "areola_label": AREOLA_LABELS.get(view.areola, view.areola),
            "areola_tint_label": AREOLA_TINT_LABELS.get(
                view.areola_tint, view.areola_tint
            ),
            "nipple_label": NIPPLE_LABELS.get(view.nipple, view.nipple),
            "montgomery": bool(view.montgomery),
        }

    mouth = None
    if settings.enable_hygiene:
        mouth = {
            "plaque": {
                "label": "牙菌斑",
                "value": round(state.plaque, 1),
                "level": plaque_label(state.plaque),
                "tone": "hot"
                if state.plaque >= 70
                else ("warn" if state.plaque >= 45 else "ok"),
            },
            "gum": {
                "label": "牙龈",
                "value": round(state.gum_irritation, 1),
                "level": gum_label(state.gum_irritation),
                "tone": "hot"
                if state.gum_irritation >= 55
                else ("warn" if state.gum_irritation >= 30 else "ok"),
            },
            "tongue": {
                "label": "舌苔",
                "value": round(state.tongue_coat, 1),
                "level": tongue_label(state.tongue_coat),
                "tone": "hot"
                if state.tongue_coat >= 65
                else ("warn" if state.tongue_coat >= 40 else "ok"),
            },
            "saliva": {
                "label": "唾液",
                "value": round(state.saliva, 1),
                "level": saliva_label(state.saliva),
                "tone": "warn" if state.saliva < 40 or state.saliva >= 85 else "ok",
            },
            "throat": {
                "label": "咽部",
                "value": round(state.throat, 1),
                "level": throat_label(state.throat),
                "tone": "hot"
                if state.throat >= 55
                else ("warn" if state.throat >= 25 else "ok"),
            },
        }

    intimacy = None
    if settings.enable_intimate and profile.gender == "female":
        chance = 0.0
        if view is not None and view.fertile and not state.pregnant:
            factor = CONTRACEPTION_FACTOR.get(settings.contraception, 1.0)
            chance = CONCEPTION_BASE * factor
        habit = profile.libido_habit
        baseline = "默认"
        if habit is not None:
            baseline = "高" if habit >= 2.5 else "中" if habit >= 1.2 else "低"
        intimacy = {
            "count": state.intimacy_count,
            "inseminations": state.insemination_count,
            "last_at": _ago(state.last_intimacy_at, now),
            "last_insemination": _ago(state.last_insemination_at, now),
            "wetness": round(state.wetness),
            "fertile": bool(view is not None and view.fertile),
            "chance": round(chance * 100.0, 1),
            "pregnant": state.pregnant,
            "contraception": settings.contraception,
            "self_releases": state.release_count,
            "last_self": _ago(state.last_self_at, now),
            "baseline": baseline,
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
    sleep = sleep_view(state, now, profile, settings)
    if settings.enable_sleep:
        if sleep.asleep:
            summary = "睡着了，" + summary
        elif sleep.in_bed:
            summary = "躺下了，" + summary
    sleep_info = {
        "asleep": sleep.asleep,
        "in_bed": sleep.in_bed,
        "kind": sleep.kind,
        "line": sleep.line,
        "since": sleep.since,
        "bed_at": sleep.bed_at,
        "wake_at": sleep.wake_at,
        "bedtime": sleep.bedtime,
        "nap_at": sleep.nap_at,
        "just_woke": sleep.just_woke,
        "slept_hours": round(sleep.slept_hours, 1),
        "last_wake": _ago(state.last_wake_at, now),
        "debt": round(state.sleep_debt, 1),
        "busy_until": state.busy_until,
        "busy_kind": state.busy_kind,
        "enabled": settings.enable_sleep,
        "gate": settings.enable_sleep_gate,
        "nap": settings.enable_nap,
        "night_wakes": sleep.night_wakes,
        "night_wake_minutes": round(sleep.night_wake_minutes, 1),
    }

    details.extend(
        [
            {
                "key": "urine",
                "label": "尿液",
                "value": round(state.urine_color * 33.3, 1),
                "unit": "%",
                "level": ["清亮", "淡黄", "偏深", "深黄"][
                    max(0, min(3, int(state.urine_color)))
                ],
                "tone": "hot"
                if state.urine_color >= 3
                else ("warn" if state.urine_color >= 2 else "ok"),
            },
            {
                "key": "saliva",
                "label": "口干",
                "value": round(state.dry_mouth, 1),
                "unit": "%",
                "level": "有点干" if state.dry_mouth >= 40 else "正常",
                "tone": "warn" if state.dry_mouth >= 40 else "ok",
            },
            {
                "key": "nasal",
                "label": "鼻腔",
                "value": round(state.nasal, 1),
                "unit": "%",
                "level": "有点堵" if state.nasal >= 30 else "通畅",
                "tone": "warn" if state.nasal >= 40 else "ok",
            },
            {
                "key": "cortisol",
                "label": "皮质醇",
                "value": round(state.stress, 1),
                "unit": "%",
                "level": "偏高" if state.stress >= 60 else "平稳",
                "tone": "warn" if state.stress >= 60 else "ok",
            },
            {
                "key": "insulin",
                "label": "胰岛素",
                "value": round(
                    min(100.0, state.glucose * 0.9 + state.stomach * 0.1), 1
                ),
                "unit": "%",
                "level": "偏高" if state.glucose >= 75 else "平稳",
                "tone": "warn" if state.glucose >= 85 else "ok",
            },
            {
                "key": "adh",
                "label": "抗利尿",
                "value": round(
                    min(100.0, max(0.0, 100.0 - state.hydration + state.sodium * 0.2)),
                    1,
                ),
                "unit": "%",
                "level": "偏高" if state.hydration < 55 else "正常",
                "tone": "warn" if state.hydration < 45 else "ok",
            },
        ]
    )

    detail_order = [
        "stomach",
        "glucose",
        "iron",
        "sleep_debt",
        "sleep_quality",
        "stress",
        "sedentary",
        "fitness",
        "temperature",
        "heart_rate",
        "breath_rate",
        "pain",
        "eye_strain",
        "sweat",
        "skin_quality",
        "skin_oil",
        "skin_type",
        "weather",
        "illness",
    ]
    details.sort(
        key=lambda item: (
            detail_order.index(item["key"]) if item["key"] in detail_order else 99
        )
    )
    detail_groups = {
        "stomach": "饮食与能量",
        "glucose": "饮食与能量",
        "iron": "饮食与能量",
        "temperature": "生命体征",
        "heart_rate": "生命体征",
        "breath_rate": "生命体征",
        "pain": "生命体征",
        "eye_strain": "生命体征",
        "sweat": "清洁与体味",
        "skin_quality": "清洁与体味",
        "skin_oil": "清洁与体味",
        "skin_type": "清洁与体味",
        "sleep_debt": "精力与作息",
        "sleep_quality": "精力与作息",
        "stress": "精力与作息",
        "sedentary": "精力与作息",
        "fitness": "精力与作息",
        "sodium": "饮食与能量",
        "weather": "环境与状态",
        "illness": "环境与状态",
    }
    for item in details:
        item["group"] = detail_groups.get(str(item.get("key") or ""), "其他")

    return {
        "id": profile.persona_id,
        "name": profile.name,
        "gender": profile.gender,
        "gender_label": gender_label(profile.gender),
        "gender_resolved": profile.gender_resolved,
        "summary": summary,
        "sleep": sleep_info,
        "now_minutes": now.hour * 60 + now.minute,
        "meters": meters,
        "details": details,
        "cycle": cycle,
        "anatomy": anatomy,
        "mouth": mouth,
        "organs": organ_rows(state, view),
        "scents": _scent_rows(state, view, profile, now, settings),
        "intimacy": intimacy,
        "lactation": lactation,
        "male": male,
        "events": events,
        "care": care,
        "last": last,
        "can_intimacy": bool(profile.gender_resolved and settings.enable_intimate),
    }
