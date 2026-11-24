/* 身体插件页。只用 DOM API 和字符串拼接，不引第三方库。 */

let bridge = null;

const state = {
  data: null,
  personaId: "",
  timer: 0,
  noticeTimer: 0,
  config: { values: {}, options: {} },
};

const CONFIG_FIELDS = [
  {
    group: "基础",
    fields: [
      { key: "ENABLED", label: "启用身体系统", type: "bool", hint: "总开关；关闭后不再注入提示词，已有状态保留。" },
      { key: "GENDER", label: "生理性别", type: "select", options: ["auto", "female", "male", "none"], optionLabels: { auto: "自动识别", female: "女性", male: "男性", none: "不区分" }, hint: "auto 从人格正文判断；判断不准时手动指定。" },
      { key: "AGE", label: "年龄兜底", type: "number", min: 0, max: 100000, hint: "0 = 从人格读；喂给生理曲线。明确 <18 只关闭性内容，不关闭周期、分泌物和器官状态。" },
      { key: "LIFESPAN", label: "种族寿命", type: "number", min: 0, max: 100000, hint: "0 = 从人格认（精灵/恶魔/吸血鬼/矮人/龙族/天使/神族）；人类按 80 年。" },
      { key: "PERSONA_ID", label: "固定人格", type: "persona", hint: "留空 = 跟随当前会话；插件页显示 AstrBot 默认人格。选一个 = 固定这个人的身体。" },
      { key: "TIMEZONE", label: "时区", type: "text", hint: "如 Asia/Shanghai；留空用 AstrBot 全局时区。" },
    ],
  },
  {
    group: "作息与代谢",
    fields: [
      { key: "SLEEP_WINDOW", label: "睡眠时段", type: "text", hint: "HH:MM-HH:MM，例如 00:00-08:00。" },
      { key: "ENABLE_SLEEP", label: "启用睡眠", type: "bool", hint: "她会自己困、自己上床、自己醒；关闭后全部按清醒算。" },
      { key: "ENABLE_SLEEP_GATE", label: "睡着时不回消息", type: "bool", hint: "真睡着时任何会话都不回；躺着没睡着不算。指令仍放行。" },
      { key: "ENABLE_NAP", label: "允许午睡", type: "bool", hint: "下午困的时候可能睡 20-60 分钟。" },
      { key: "ENABLE_HYGIENE", label: "启用清洁与口腔", type: "bool", hint: "关闭后隐藏清洁/口腔，也不允许洗澡/刷牙。" },
      { key: "ENABLE_TOOLS", label: "给她身体工具", type: "bool", hint: "允许模型调用 body_status / body_action。" },
    ],
  },
  {
    group: "女性生理期",
    fields: [
      { key: "ENABLE_CYCLE", label: "启用生理期", type: "bool", hint: "只对女性人格生效。" },
      { key: "CYCLE_LENGTH", label: "周期天数", type: "number", min: 18, max: 45, hint: "两次经期开始之间的天数。" },
      { key: "PERIOD_LENGTH", label: "经期天数", type: "number", min: 2, max: 10, hint: "每次经期持续几天。" },
      { key: "CYCLE_START", label: "经期起点", type: "text", hint: "YYYY-MM-DD；留空自动错开。" },
    ],
  },
  {
    group: "私密生理",
    fields: [
      { key: "PUBIC_HAIR", label: "阴毛外观", type: "select", options: ["auto", "none", "sparse", "medium", "bushy"], optionLabels: { auto: "按人格", none: "无毛（白虎）", sparse: "稀疏", medium: "适中", bushy: "浓密" }, hint: "auto 由人格体质决定，多数角色无毛；也可以强制无毛或指定疏密。只是外观，不影响任何功能。" },
      { key: "ENABLE_INTIMATE", label: "启用私密生理", type: "bool", hint: "性欲与亲密记录；需要能识别出性别，只在私聊生效。判定不看年龄。" },
      { key: "ENABLE_AWAY", label: "自慰后暂时不回消息", type: "bool", hint: "私聊里自慰后的 4-12 分钟内不回复；关掉后照常回。" },
      { key: "ENABLE_PREGNANCY", label: "启用怀孕", type: "bool", hint: "易孕期亲密有概率受孕；受孕后停经并按孕周推进。判定不看年龄。" },
      { key: "CONTRACEPTION", label: "避孕方式", type: "select", options: ["none", "condom", "pill"], optionLabels: { none: "不避孕", condom: "安全套", pill: "避孕药" }, hint: "把受孕概率从约 18% 降到 1-2%。" },
    ],
  },
  {
    group: "表达与数据",
    fields: [
      { key: "GROUP_DETAIL", label: "群聊细节", type: "select", options: ["basic", "full"], optionLabels: { basic: "基础", full: "完整（非性相关）" }, hint: "full 才给群聊非性相关的经期信息；成人细节永远不进群。" },
      { key: "INTENSITY", label: "身体存在感", type: "select", options: ["subtle", "natural", "strong"], optionLabels: { subtle: "轻微", natural: "自然", strong: "强烈" }, hint: "subtle 只在明显时提醒；strong 更强调身体对情绪的影响。" },
      { key: "PROMPT_EXTRA", label: "附加提示", type: "textarea", wide: true, hint: "追加在身体提示块末尾，最多 500 字。" },
      { key: "EVENT_KEEP_DAYS", label: "事件保留天数", type: "number", min: 1, max: 30 },
      { key: "MAX_EVENTS", label: "最多事件数", type: "number", min: 5, max: 100 },
      { key: "DEBUG", label: "调试日志", type: "bool", hint: "输出更多注入信息，平时不用开。" },
    ],
  },
];

function el(id) {
  return document.getElementById(id);
}

function withTimeout(promise, timeoutMs, message) {
  return Promise.race([
    promise,
    new Promise(function (_, reject) {
      setTimeout(function () {
        reject(new Error(message));
      }, timeoutMs);
    }),
  ]);
}

async function waitForBridge(timeoutMs) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const found = window.AstrBotPluginPage || window.AstrBotPluginView;
    if (found) return found;
    await new Promise(function (resolve) {
      setTimeout(resolve, 50);
    });
  }
  return null;
}

function showNotice(text, tone) {
  const box = el("notice");
  box.textContent = text;
  box.hidden = false;
  box.dataset.tone = tone || "info";
  box.style.borderColor = "";
  box.style.background = "";
  box.style.color = "";
  if (state.noticeTimer) clearTimeout(state.noticeTimer);
  state.noticeTimer = setTimeout(function () {
    box.hidden = true;
  }, tone === "error" ? 6000 : 2200);
}

function clockText(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function selectedPersona() {
  const personas = (state.data && state.data.personas) || [];
  let found = personas.find(function (item) {
    return item.id === state.personaId;
  });
  if (!found) {
    found = personas[0] || null;
    state.personaId = found ? found.id : "";
  }
  return found;
}

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function renderPersonaPicker(personas) {
  const wrap = el("personaWrap");
  const select = el("personaSelect");
  if (!personas || personas.length <= 1) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  select.replaceChildren();
  personas.forEach(function (persona) {
    const option = document.createElement("option");
    option.value = persona.id;
    option.textContent = persona.name || persona.id;
    select.appendChild(option);
  });
  select.value = state.personaId;
}

function renderHero(persona, now) {
  el("heroName").textContent = persona.name || persona.id || "身体";
  el("summary").textContent = persona.summary || "身体暂时很安静";
  const bits = [];
  const gender =
    persona.gender_label && persona.gender_label !== "未知"
      ? persona.gender_label + "性"
      : "性别未知";
  bits.push(gender);
  if (persona.cycle && persona.cycle.phase_label) {
    const day = Number(persona.cycle.day) || 0;
    bits.push(
      persona.cycle.phase_label + (day > 0 ? " · 第 " + day + " 天" : ""),
    );
  }
  el("profileLine").textContent = bits.join(" · ");
  el("clock").textContent = now ? clockText(now) : "—";
  el("subtitle").textContent = persona.name || persona.id || "";

  const chips = el("heroChips");
  chips.replaceChildren();
  function addChip(label, tone) {
    chips.appendChild(make("span", "chip " + (tone || ""), label));
  }
  function meterValue(key) {
    const found = (persona.meters || []).find(function (item) {
      return item.key === key;
    });
    return found ? Number(found.value) : 100;
  }
  if (meterValue("satiety") < 40) addChip("饿", "warm");
  if (meterValue("hydration") < 45) addChip("渴", "warm");
  if (meterValue("energy") < 40) addChip("困", "cool");
  const sleep = persona.sleep || {};
  if (sleep.asleep) addChip("睡着", "cool");
  else if (sleep.in_bed) addChip("躺着", "cool");
  const cycle = persona.cycle || {};
  if (Number(cycle.cramps) > 0) addChip("痛经", "bad");
  else if (cycle.phase === "luteal" && Number(cycle.days_to_next) <= 7) addChip("经前", "warm");
  if (!chips.children.length) addChip("平稳", "");
}

function renderMeters(meters) {
  const host = el("meters");
  host.replaceChildren();
  if (!meters || !meters.length) {
    host.appendChild(make("p", "empty", "没有可显示的身体指标。"));
    return;
  }
  meters.forEach(function (meter, index) {
    const classes = ["metric"];
    if (index < 3) classes.push("primary");
    if (meter.tone && meter.tone !== "ok") classes.push(meter.tone);
    const cell = make("div", classes.join(" "));
    const head = make("div", "metric-head");
    head.appendChild(make("span", "metric-name", meter.name || meter.label));
    head.appendChild(
      make("span", "metric-value", Math.round(meter.value) + "/100"),
    );
    cell.appendChild(head);
    cell.appendChild(make("div", "metric-level", meter.level || ""));
    const bar = make("div", "metric-bar");
    const fill = make("div", "metric-fill " + (meter.tone || "ok"));
    fill.style.width = Math.max(0, Math.min(100, Number(meter.value) || 0)) + "%";
    bar.appendChild(fill);
    cell.appendChild(bar);
    host.appendChild(cell);
  });
  const more = make("button", "metric more-tile");
  more.id = "moreBtn";
  more.type = "button";
  more.appendChild(make("span", "more-label", "更多指标"));
  const moreCount = make("span", "more-count");
  moreCount.id = "moreCount";
  more.appendChild(moreCount);
  more.appendChild(make("span", "more-arrow", "↗"));
  host.appendChild(more);
}

const CARE_ROWS = [
  ["meal", "吃饭"],
  ["drink", "喝水"],
  ["toilet", "厕所"],
  ["shower", "洗澡"],
  ["brush", "刷牙"],
];

function renderCare(care, nowMinutes) {
  const host = el("careRows");
  host.replaceChildren();
  const items = Array.isArray(care) ? care : [];
  const now = Number(nowMinutes);
  const hasNow = Number.isFinite(now);
  const summary = el("careSummary");
  if (summary) {
    summary.textContent = items.length ? "共 " + items.length + " 件" : "";
  }
  if (!items.length) {
    host.appendChild(make("p", "empty", "最近 24 小时还没有记录。"));
    return;
  }

  const short = {
    meal: "吃饭",
    drink: "喝水",
    toilet: "厕所",
    shower: "洗澡",
    brush: "刷牙",
    nap: "小睡",
  };
  const periods = [
    ["凌晨", 0, 6],
    ["上午", 6, 12],
    ["下午", 12, 18],
    ["晚上", 18, 24],
  ];

  items.sort(function (left, right) {
    const leftDay = hasNow && Number(left.minutes) > now ? 0 : 1;
    const rightDay = hasNow && Number(right.minutes) > now ? 0 : 1;
    if (leftDay !== rightDay) return leftDay - rightDay;
    return (Number(left.minutes) || 0) - (Number(right.minutes) || 0);
  });

  const groups = [];
  items.forEach(function (item) {
    const minutes = Number(item.minutes) || 0;
    const yesterday = hasNow && minutes > now;
    const hour = Math.max(0, Math.min(23, Math.floor(minutes / 60)));
    const period = periods.find(function (entry) {
      return hour >= entry[1] && hour < entry[2];
    });
    const title =
      (yesterday ? "昨天" : "今天") + " · " + (period ? period[0] : "其他");
    let group = groups.find(function (entry) {
      return entry.title === title;
    });
    if (!group) {
      group = { title: title, items: [] };
      groups.push(group);
    }
    group.items.push(item);
  });

  groups.forEach(function (group) {
    const section = make("div", "care-period");
    section.appendChild(make("div", "care-period-title", group.title));
    const chips = make("div", "care-chips");
    group.items.forEach(function (item) {
      const chip = make("span", "care-chip");
      chip.appendChild(make("i", "care-dot " + (item.kind || "")));
      chip.appendChild(make("span", "care-chip-time", item.at || "—"));
      chip.appendChild(
        make("span", "care-chip-what", short[item.kind] || item.label || item.kind || ""),
      );
      chips.appendChild(chip);
    });
    section.appendChild(chips);
    host.appendChild(section);
  });
}

function attachCareTip(cell, lines) {
  cell.tabIndex = 0;
  cell.addEventListener("mouseenter", function () {
    showCareTip(cell, lines);
  });
  cell.addEventListener("focus", function () {
    showCareTip(cell, lines);
  });
  cell.addEventListener("mouseleave", hideCareTip);
  cell.addEventListener("blur", hideCareTip);
  cell.addEventListener("click", function (event) {
    event.stopPropagation();
    showCareTip(cell, lines);
  });
}

function showCareTip(cell, lines) {
  const tip = el("careTip");
  const card = cell.closest(".care-timeline-card");
  if (!tip || !card) return;
  tip.replaceChildren();
  lines.forEach(function (line, index) {
    tip.appendChild(make(index === 0 ? "strong" : "span", "care-tip-line", line));
  });
  tip.hidden = false;
  const cardRect = card.getBoundingClientRect();
  const cellRect = cell.getBoundingClientRect();
  const left = cellRect.left - cardRect.left + cellRect.width / 2;
  tip.style.left = Math.max(90, Math.min(cardRect.width - 90, left)) + "px";
  tip.style.top = cellRect.top - cardRect.top - 8 + "px";
}

function hideCareTip() {
  const tip = el("careTip");
  if (tip) tip.hidden = true;
}

function renderDetails(details) {
  const host = el("innerStats");
  host.replaceChildren();
  const count = el("moreCount");
  if (count) count.textContent = details && details.length ? String(details.length) : "";
  if (!details || !details.length) return;
  details.forEach(function (item) {
    const row = make("div", "inner-stat");
    const head = make("div", "inner-stat-head");
    head.appendChild(make("span", "", item.label));
    const value = Number(item.value) || 0;
    const display =
      item.display ||
      (item.key === "sleep_debt"
        ? (Math.round(value * 10) / 10) + " 小时"
        : Math.round(value) + "%");
    head.appendChild(make("b", "", (item.level || "") + " · " + display));
    row.appendChild(head);
    const bar = make("div", "inner-bar");
    const fill = make("i", item.tone || "ok");
    const percent =
      item.bar !== undefined
        ? Math.min(100, Math.max(0, Number(item.bar) || 0))
        : item.key === "sleep_debt"
          ? Math.min(100, Math.max(0, (value / 24) * 100))
          : Math.min(100, Math.max(0, value));
    fill.style.width = percent + "%";
    bar.appendChild(fill);
    row.appendChild(bar);
    host.appendChild(row);
  });
}

function crampText(level) {
  const labels = ["没有", "轻微", "坠胀", "明显"];
  return labels[Math.max(0, Math.min(3, Number(level) || 0))];
}


function renderSleep(persona) {
  const card = el("sleepCard");
  if (!card) return;
  const sleep = persona.sleep;
  if (!sleep || !sleep.enabled) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const host = el("sleepBody");
  host.replaceChildren();
  el("sleepHint").textContent = sleep.asleep
    ? "睡着了 · " + (sleep.kind === "nap" ? "午睡" : "夜里")
    : sleep.in_bed
      ? "躺下了 · 还没睡着"
      : sleep.just_woke
        ? "刚睡醒"
        : "醒着";
  if (sleep.line) {
    host.appendChild(make("p", "cycle-note", sleep.line));
  }
  const facts = make("dl", "facts");
  const rows = [];
  rows.push(["睡眠债", (Number(sleep.debt) || 0).toFixed(1) + " 小时"]);
  if (sleep.asleep) {
    rows.push(["睡了", (Number(sleep.slept_hours) || 0).toFixed(1) + " 小时"]);
    rows.push([
      "预计醒",
      sleep.wake_at ? clockText(new Date(sleep.wake_at * 1000).toISOString()) : "不确定",
    ]);
  } else if (sleep.in_bed) {
    rows.push([
      "上床",
      sleep.bed_at ? clockText(new Date(sleep.bed_at * 1000).toISOString()) : "—",
    ]);
  } else {
    rows.push(["上次醒来", sleep.last_wake || "—"]);
    rows.push([
      "习惯就寝",
      sleep.bedtime ? clockText(new Date(sleep.bedtime * 1000).toISOString()) : "—",
    ]);
  }
  if (sleep.busy_kind) {
    rows.push([
      "手上",
      sleep.busy_kind +
        "（" +
        clockText(new Date(sleep.busy_until * 1000).toISOString()) +
        " 结束）",
    ]);
  }
  if (Number(sleep.night_wakes) > 0) {
    rows.push([
      "夜里醒",
      sleep.night_wakes +
        " 次 · " +
        Math.round(Number(sleep.night_wake_minutes) || 0) +
        " 分钟",
    ]);
  }
  rows.push(["午睡", sleep.nap ? "允许" : "关闭"]);
  rows.forEach(function (pair) {
    const row = document.createElement("div");
    row.appendChild(make("dt", "", pair[0]));
    row.appendChild(make("dd", "", pair[1]));
    facts.appendChild(row);
  });
  host.appendChild(facts);
}

function infoRow(row) {
  const line = document.createElement("div");
  line.appendChild(make("dt", "", row.label || ""));
  const dd = make("dd", "", row.text || "");
  if (row.note) dd.title = row.note;
  line.appendChild(dd);
  if (row.detail) {
    line.classList.add("clickable");
    line.addEventListener("click", function () {
      openInfo(row.label || "详情", row.text || "", row.detail);
    });
  }
  return line;
}

function bodyBlock(title, rows) {
  const list = (rows || []).filter(Boolean);
  if (!list.length) return null;
  const block = make("div", "body-block");
  block.appendChild(make("h3", "body-block-title", title));
  const facts = make("dl", "facts");
  list.forEach(function (row) {
    facts.appendChild(infoRow(row));
  });
  block.appendChild(facts);
  return block;
}

function openInfo(title, subtitle, detail) {
  el("infoTitle").textContent = title;
  el("infoSubtitle").textContent = subtitle || "";
  el("infoDetail").textContent = detail || "";
  el("infoDialog").showModal();
}

function renderBodyStatus(persona) {
  const card = el("bodyCard");
  if (!card) return;
  const blocks = [];

  const organRows = (persona.organs || []).map(function (row) {
    return {
      label: row.label || "",
      text: (row.level || "") + " · " + Math.round(Number(row.value) || 0) + "%",
      note: row.note,
      detail: row.detail,
    };
  });
  blocks.push(bodyBlock("器官", organRows));

  const a = persona.anatomy;
  if (a && a.rows) blocks.push(bodyBlock("下体", a.rows));

  const mouth = persona.mouth;
  if (mouth) {
    blocks.push(
      bodyBlock(
        "口腔",
        Object.keys(mouth).map(function (key) {
          const row = mouth[key];
          return {
            label: row.label || key,
            text:
              row.text ||
              (row.level || "") + " · " + Math.round(Number(row.value) || 0) + "%",
            detail: row.detail,
          };
        }),
      ),
    );
  }

  const visible = blocks.filter(Boolean);
  if (!visible.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const host = el("bodyBody");
  host.replaceChildren.apply(host, visible);
}

function renderScents(persona) {
  const card = el("scentCard");
  const rows = persona.scents || [];
  if (!rows.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const host = el("scentBody");
  host.replaceChildren();
  const facts = make("dl", "facts");
  rows.forEach(function (row) {
    facts.appendChild(
      infoRow({
        label: row.label || "",
        text: (row.level || "") + " · " + Math.round(Number(row.value) || 0) + "%",
        detail: row.detail,
      }),
    );
  });
  host.appendChild(facts);
}

function renderCycle(persona) {
  const card = el("cycleCard");
  const cycle = persona.cycle;
  if (!cycle) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const host = el("cycleBody");
  host.replaceChildren();

  if (cycle.phase === "premenarche" || cycle.phase === "menopause" || cycle.phase === "pregnant") {
    el("cyclePhase").textContent = cycle.phase_label || "周期";
    host.appendChild(make("p", "cycle-note", cycle.note || ""));
    const rows = [];
    if (cycle.phase === "pregnant") {
      const weeks = Number(cycle.weeks) || 0;
      const fetuses = Number(cycle.fetuses) || 1;
      rows.push(["孕周", "第 " + weeks + " 周"]);
      rows.push(["胎数", fetuses >= 3 ? "三胎" : fetuses === 2 ? "双胎" : "单胎"]);
      rows.push(["预产", "约 " + Math.max(0, 40 - weeks) + " 周后"]);
    }
    rows.push([
      "分泌物",
      (cycle.mucus_volume_label || "—") +
        " · " +
        (cycle.mucus_texture_label || "—") +
        " · " +
        (cycle.mucus_color_label || "—"),
    ]);
    rows.push([
      "宫颈",
      (cycle.cervix_position_label || "—") +
        " · " +
        (cycle.cervix_firmness_label || "—") +
        " · " +
        (cycle.cervix_open ? "微张" : "闭合"),
    ]);
    if (cycle.breast_fullness_label) {
      rows.push(["乳房", cycle.breast_fullness_label]);
    }
    if (cycle.race) {
      rows.push([
        "种族",
        cycle.race + (cycle.physio_age ? "（约人类 " + cycle.physio_age + " 岁）" : ""),
      ]);
    }
    if (rows.length) {
      const facts = make("dl", "facts");
      rows.forEach(function (pair) {
        const row = document.createElement("div");
        row.appendChild(make("dt", "", pair[0]));
        row.appendChild(make("dd", "", pair[1]));
        facts.appendChild(row);
      });
      host.appendChild(facts);
    }
    el("cycleActions").hidden = true;
    return;
  }

  el("cyclePhase").textContent =
    (cycle.phase_label || "周期") + " · 第 " + cycle.day + " 天";

  const hero = make("div", "cycle-hero");
  const heroCopy = make("div", "cycle-hero-copy");
  heroCopy.appendChild(make("b", "", "第 " + cycle.day + " 天"));
  heroCopy.appendChild(make("span", "", cycle.phase_label || ""));
  hero.appendChild(heroCopy);
  hero.appendChild(
    make("span", "cycle-hero-total", "共 " + cycle.cycle_length + " 天"),
  );
  host.appendChild(hero);

  const progress = make("div", "cycle-progress");
  const progressFill = make("i");
  progressFill.style.width =
    Math.min(100, Math.max(0, (cycle.day / cycle.cycle_length) * 100)) + "%";
  progress.appendChild(progressFill);
  host.appendChild(progress);

  const facts = make("dl", "facts");
  const symptomBits = [];
  if (cycle.breast) symptomBits.push("乳房胀");
  if (cycle.headache) symptomBits.push("头痛");
  if (cycle.backache) symptomBits.push("腰酸");
  let rows = [["下次", "约 " + cycle.days_to_next + " 天后"]];
  if (cycle.ovulation_pain) {
    rows.push([
      "排卵痛",
      (cycle.ovulation_side || "一") + "侧小腹抽痛",
    ]);
  }
  if (Number(cycle.ovulation_delay) >= 2) {
    rows.push(["排卵", "晚了 " + cycle.ovulation_delay + " 天"]);
  }
  if (cycle.perimenopausal) {
    rows.push(["周期", "围绝经期，可能忽长忽短"]);
  }
  rows.push(
    ["易孕", cycle.fertile ? "是" : "否"],
    ["经量", cycle.flow_label || "—"],
    ["小腹", crampText(cycle.cramps)],
    ["周期", cycle.cycle_length + " 天"],
    [
      cycle.phase === "menstrual" ? "经血" : "分泌物",
      (cycle.mucus_volume_label || "—") +
        " · " +
        (cycle.mucus_texture_label || "—") +
        " · " +
        (cycle.mucus_color_label || "—"),
    ],
    [
      "拉丝",
      Number(cycle.mucus_stretch) >= 1
        ? "约 " + Math.round(Number(cycle.mucus_stretch)) + " cm"
        : "—",
    ],
    [
      "宫颈",
      (cycle.cervix_position_label || "—") +
        " · " +
        (cycle.cervix_firmness_label || "—") +
        " · " +
        (cycle.cervix_open ? "微张" : "闭合"),
    ],
    ["盆腔", cycle.pelvic_pressure_label || "—"],
    ["乳房", cycle.breast_fullness_label || "—"],
    ["经前", symptomBits.length ? symptomBits.join("、") : "—"],
    [
      "血块",
      Number(cycle.clots) >= 2 ? "明显" : Number(cycle.clots) === 1 ? "少量" : "没有",
    ],
    ["用品", typeof cycle.pads === "number" ? cycle.pads + " 片" : "—"],
    ["今天用品", (Number(cycle.pads_today) || 0) + " 片"],
    ["潮热", cycle.hot_flash ? "刚才一阵" : "没有"],
    ["点滴出血", cycle.spotting ? "有一点" : "没有"],
  );
  rows.forEach(function (pair) {
    const row = document.createElement("div");
    row.appendChild(make("dt", "", pair[0]));
    row.appendChild(make("dd", "", pair[1]));
    facts.appendChild(row);
  });
  host.appendChild(facts);

  if (cycle.note) {
    host.appendChild(make("p", "cycle-note", cycle.note));
  }

  host.appendChild(make("div", "hormones-head", "激素水平 · 相对值 0-100"));
  const hormones = make("div", "hormones");
  [
    ["雌激素", "estrogen", "estrogen"],
    ["孕激素", "progesterone", "progesterone"],
    ["LH", "lh", "lh"],
    ["FSH", "fsh", "fsh"],
  ].forEach(function (row) {
    const value = Math.min(100, Math.max(0, Number(cycle[row[1]]) || 0));
    const line = make("div", "hormone-row");
    line.appendChild(make("span", "hormone-name", row[0]));
    const bar = make("div", "hormone-bar");
    const fill = make("i", row[2]);
    fill.style.width = value + "%";
    bar.appendChild(fill);
    line.appendChild(bar);
    line.appendChild(make("span", "hormone-value", String(Math.round(value))));
    hormones.appendChild(line);
  });
  host.appendChild(hormones);
  el("cycleActions").hidden = false;
}

function renderMale(persona) {
  const card = el("maleCard");
  const male = persona.male;
  if (!male) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const host = el("maleBody");
  host.replaceChildren();
  const facts = make("dl", "facts");
  const rows = [
    ["距上次梦遗", male.days_since_release + " 天"],
    [
      "恢复",
      male.refractory_hours > 0 ? male.refractory_hours + " 小时" : "—",
    ],
    ["状态", male.line ? "有变化" : "平稳"],
  ];
  rows.forEach(function (pair) {
    const row = document.createElement("div");
    row.appendChild(make("dt", "", pair[0]));
    row.appendChild(make("dd", "", pair[1]));
    facts.appendChild(row);
  });
  host.appendChild(facts);
  if (male.line) {
    host.appendChild(make("p", "cycle-note", male.line));
  }
}

function renderLast(last) {
  const host = el("lastRow");
  if (!last) {
    host.textContent = "";
    return;
  }
  const settings = (state.data && state.data.settings) || {};
  const hygiene = settings.enable_hygiene !== false;
  const parts = [];
  if (last.eat) parts.push("吃东西 " + last.eat);
  if (last.drink) parts.push("喝水 " + last.drink);
  if (hygiene && last.shower) parts.push("洗澡 " + last.shower);
  if (hygiene && last.brush) parts.push("刷牙 " + last.brush);
  if (last.toilet) parts.push("上厕所 " + last.toilet);
  if (last.nap && last.nap !== "很久以前") parts.push("小睡 " + last.nap);
  host.textContent = parts.length ? "上次：" + parts.join(" · ") : "";
}

function renderEvents(events) {
  const host = el("events");
  host.replaceChildren();
  const notable = (events || []).filter(function (event) {
    return event.kind !== "self_care";
  });
  if (!notable.length) {
    host.appendChild(make("li", "empty", "还没有值得记下的身体变化。"));
    el("eventCount").textContent = "";
    return;
  }
  notable.forEach(function (event) {
    const item = document.createElement("li");
    item.appendChild(make("time", "", event.time || ""));
    item.appendChild(make("span", "", event.text || ""));
    host.appendChild(item);
  });
  el("eventCount").textContent = notable.length + " 条";
}

function stopFertilityArt() {
  const anim = state.fertilityAnim;
  if (!anim) return;
  anim.pause();
  if (anim.observer) anim.observer.disconnect();
  state.fertilityAnim = null;
}

function startFertilityArt(data, sceneKey) {
  const current = state.fertilityAnim;
  if (current && current.sceneKey === sceneKey) {
    current.data = data;
    if (!el("panel-fertility").hidden && !current.running && !current.reduce) {
      current.resume();
    }
    return;
  }
  stopFertilityArt();
  const host = el("fertilityArt");
  host.replaceChildren();
  const canvas = document.createElement("canvas");
  canvas.className = "fertility-canvas";
  host.appendChild(canvas);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const anim = {
    canvas: canvas,
    ctx: ctx,
    data: data,
    sceneKey: sceneKey,
    running: false,
    raf: 0,
    width: 0,
    height: 0,
    cx: 0,
    cy: 0,
    eggR: 100,
    time: 0,
    last: 0,
    sperm: [],
    particles: [],
    bokeh: [],
    observer: null,
    reduce:
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    pause: function () {
      this.running = false;
      if (this.raf) cancelAnimationFrame(this.raf);
      this.raf = 0;
    },
  };
  state.fertilityAnim = anim;

  function maxRadius() {
    return Math.min(anim.width, anim.height) * 0.55;
  }

  function spawnSperm() {
    const minR = anim.eggR * 1.24;
    const maxR = Math.max(minR + 18, maxRadius());
    const amp = 5 + Math.random() * Math.min(16, (maxR - minR) * 0.5);
    const base = Math.min(
      maxR - amp * 0.2,
      minR + amp + Math.random() * Math.max(4, maxR - minR - amp),
    );
    return {
      angle: Math.random() * Math.PI * 2,
      baseRadius: base,
      radius: base,
      radialAmp: amp,
      radialPhase: Math.random() * Math.PI * 2,
      radialSpeed: 0.1 + Math.random() * 0.2,
      angularSpeed:
        (0.012 + Math.random() * 0.024) * (Math.random() < 0.5 ? -1 : 1),
      size: 0.86 + Math.random() * 0.6,
      phase: Math.random() * Math.PI * 2,
      z: Math.random(),
    };
  }

  function seed() {
    anim.sperm = [];
    const count = 10 + Math.floor(Math.random() * 8);
    for (let index = 0; index < count; index += 1) {
      anim.sperm.push(spawnSperm());
    }
    anim.particles = [];
    for (let index = 0; index < 42; index += 1) {
      anim.particles.push({
        x: Math.random() * anim.width,
        y: Math.random() * anim.height,
        r: 0.6 + Math.random() * 1.8,
        speed: 2.5 + Math.random() * 8,
        drift: (Math.random() - 0.5) * 5,
        alpha: 0.08 + Math.random() * 0.34,
        warm: Math.random() > 0.5,
      });
    }
    anim.bokeh = [];
    for (let index = 0; index < 11; index += 1) {
      anim.bokeh.push({
        x: Math.random() * anim.width,
        y: Math.random() * anim.height,
        r: 16 + Math.random() * 54,
        alpha: 0.05 + Math.random() * 0.09,
        drift: 6 + Math.random() * 18,
        speed: 0.12 + Math.random() * 0.22,
        phase: Math.random() * Math.PI * 2,
        cool: Math.random() > 0.7,
      });
    }
  }

  function resize() {
    const rect = host.getBoundingClientRect();
    const width = Math.max(280, rect.width || 620);
    const height = Math.max(260, rect.height || 430);
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const sizeChanged = width !== anim.width || height !== anim.height;
    anim.width = width;
    anim.height = height;
    anim.cx = width / 2;
    anim.cy = height * 0.5;
    anim.eggR = Math.min(width, height) * 0.235;
    if (sizeChanged || !anim.sperm.length) seed();
  }

  function drawBackground() {
    const grad = ctx.createRadialGradient(
      anim.cx,
      anim.cy * 0.9,
      0,
      anim.cx,
      anim.cy,
      Math.max(anim.width, anim.height) * 0.78,
    );
    grad.addColorStop(0, "#2b1039");
    grad.addColorStop(0.42, "#10081d");
    grad.addColorStop(1, "#05040b");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, anim.width, anim.height);

    const bloom = ctx.createRadialGradient(
      anim.cx - anim.width * 0.23,
      anim.cy - anim.height * 0.28,
      0,
      anim.cx - anim.width * 0.23,
      anim.cy - anim.height * 0.28,
      Math.max(anim.width, anim.height) * 0.7,
    );
    bloom.addColorStop(0, "rgba(255, 42, 133, 0.2)");
    bloom.addColorStop(0.52, "rgba(178, 24, 106, 0.08)");
    bloom.addColorStop(1, "rgba(178, 24, 106, 0)");
    ctx.fillStyle = bloom;
    ctx.fillRect(0, 0, anim.width, anim.height);

    const cool = ctx.createRadialGradient(
      anim.cx + anim.width * 0.3,
      anim.cy + anim.height * 0.28,
      0,
      anim.cx + anim.width * 0.3,
      anim.cy + anim.height * 0.28,
      Math.max(anim.width, anim.height) * 0.55,
    );
    cool.addColorStop(0, "rgba(78, 94, 255, 0.09)");
    cool.addColorStop(1, "rgba(78, 94, 255, 0)");
    ctx.fillStyle = cool;
    ctx.fillRect(0, 0, anim.width, anim.height);

    anim.bokeh.forEach(function (b) {
      const x = b.x + Math.sin(anim.time * b.speed + b.phase) * b.drift;
      const y = b.y + Math.cos(anim.time * b.speed * 0.8 + b.phase) * b.drift;
      const glow = ctx.createRadialGradient(x, y, 0, x, y, b.r);
      const color = b.cool ? "110, 150, 255" : "255, 112, 162";
      glow.addColorStop(0, "rgba(" + color + ", " + b.alpha + ")");
      glow.addColorStop(0.6, "rgba(" + color + ", " + b.alpha * 0.35 + ")");
      glow.addColorStop(1, "rgba(" + color + ", 0)");
      ctx.beginPath();
      ctx.fillStyle = glow;
      ctx.arc(x, y, b.r, 0, Math.PI * 2);
      ctx.fill();
    });

    const vignette = ctx.createRadialGradient(
      anim.cx,
      anim.cy,
      Math.min(anim.width, anim.height) * 0.16,
      anim.cx,
      anim.cy,
      Math.max(anim.width, anim.height) * 0.7,
    );
    vignette.addColorStop(0, "rgba(0,0,0,0)");
    vignette.addColorStop(1, "rgba(0,0,0,0.68)");
    ctx.fillStyle = vignette;
    ctx.fillRect(0, 0, anim.width, anim.height);
  }

  function drawParticles(dt) {
    anim.particles.forEach(function (p) {
      p.y -= p.speed * dt;
      p.x += p.drift * dt;
      if (p.y < -8) {
        p.y = anim.height + 8;
        p.x = Math.random() * anim.width;
      }
      if (p.x < -8) p.x = anim.width + 8;
      if (p.x > anim.width + 8) p.x = -8;
      ctx.beginPath();
      ctx.fillStyle = p.warm
        ? "rgba(255, 246, 234, " + p.alpha + ")"
        : "rgba(255, 142, 184, " + p.alpha + ")";
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  function drawSperm(s, dt) {
    s.angle += s.angularSpeed * dt;
    s.radius =
      s.baseRadius +
      Math.sin(anim.time * s.radialSpeed + s.radialPhase) * s.radialAmp;
    const x = anim.cx + Math.cos(s.angle) * s.radius;
    const y = anim.cy + Math.sin(s.angle) * s.radius;
    const alpha = (0.32 + s.z * 0.68) * (anim.data.pregnant ? 0.12 : 1);
    if (alpha <= 0.01) return;
    const depthScale = 0.62 + s.z * 0.95;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(s.angle);
    ctx.scale(s.size * depthScale, s.size * depthScale);
    ctx.globalAlpha = alpha;
    const distanceBlur = s.z < 0.34 ? (0.34 - s.z) * 4.2 : 0;
    if (distanceBlur > 0.25) {
      ctx.filter = "blur(" + distanceBlur.toFixed(2) + "px)";
    }

    const tailLength = 92;
    const segments = 26;
    for (let pass = 0; pass < 2; pass += 1) {
      let px = 5;
      let py = 0;
      for (let index = 1; index <= segments; index += 1) {
        const t = index / segments;
        const nx = 5 + t * tailLength;
        const ramp = Math.min(1, t * 7);
        const ny =
          (Math.sin(t * 6.4 - anim.time * 3.2 + s.phase) * 9.2 * (1 - t * 0.16) +
            Math.sin(t * 12.5 + s.phase * 1.6) * 2.1 * (1 - t)) *
          ramp;
        ctx.beginPath();
        ctx.moveTo(px, py);
        ctx.lineTo(nx, ny);
        ctx.lineWidth = (pass === 0 ? 3.4 : 1.15) * (1 - t * 0.72);
        ctx.strokeStyle =
          pass === 0
            ? "rgba(215, 230, 255, 0.13)"
            : "rgba(255, 255, 255, 0.94)";
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.stroke();
        px = nx;
        py = ny;
      }
    }

    if (s.z > 0.62) {
      const aura = ctx.createRadialGradient(0, 0, 0, 0, 0, 18);
      aura.addColorStop(0, "rgba(215, 230, 255, " + (0.12 * s.z).toFixed(3) + ")");
      aura.addColorStop(1, "rgba(215, 230, 255, 0)");
      ctx.beginPath();
      ctx.fillStyle = aura;
      ctx.arc(0, 0, 18, 0, Math.PI * 2);
      ctx.fill();
    }

    const head = ctx.createRadialGradient(-2.6, -1.7, 0.3, 0, 0, 8);
    head.addColorStop(0, "#ffffff");
    head.addColorStop(0.5, "#fbf8ff");
    head.addColorStop(0.82, "#eadff4");
    head.addColorStop(1, "#d2c2e3");
    const headWobble = Math.sin(anim.time * 2.3 + s.phase * 1.3);
    ctx.save();
    ctx.translate(0, headWobble * 0.9);
    ctx.rotate(headWobble * 0.09);
    ctx.scale(1 + headWobble * 0.025, 1 - headWobble * 0.018);
    ctx.shadowColor = "rgba(215, 230, 255, 0.45)";
    ctx.shadowBlur = 4 + s.z * 5;
    ctx.beginPath();
    ctx.moveTo(7.4, 0);
    ctx.bezierCurveTo(6.4, -3.6, 1.5, -4.7, -2.8, -4.2);
    ctx.bezierCurveTo(-7.4, -3.7, -9.2, -1.2, -8.6, 0);
    ctx.bezierCurveTo(-9.2, 1.2, -7.4, 3.7, -2.8, 4.2);
    ctx.bezierCurveTo(1.5, 4.7, 6.4, 3.6, 7.4, 0);
    ctx.closePath();
    ctx.fillStyle = head;
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
    ctx.lineWidth = 0.7;
    ctx.stroke();

    ctx.beginPath();
    ctx.fillStyle = "rgba(255, 255, 255, 0.82)";
    ctx.ellipse(-2, -1.3, 2.15, 0.9, -0.16, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.strokeStyle = "rgba(198, 180, 224, 0.42)";
    ctx.lineWidth = 0.65;
    ctx.arc(0, 0, 6.2, Math.PI * 1.05, Math.PI * 1.55);
    ctx.stroke();
    ctx.filter = "none";
    ctx.restore();
    ctx.restore();
  }

  function drawEgg() {
    const t = anim.time;
    const pulse = 1 + Math.sin(t * 1.25) * 0.012;
    const r = anim.eggR * pulse;
    const cx = anim.cx;
    const cy = anim.cy;
    const fertile = anim.data.fertile || anim.data.pregnant;

    const bloom = ctx.createRadialGradient(cx, cy, r * 0.58, cx, cy, r * 2.05);
    bloom.addColorStop(0, fertile ? "rgba(255, 46, 122, 0.3)" : "rgba(255, 120, 150, 0.13)");
    bloom.addColorStop(0.5, fertile ? "rgba(255, 60, 125, 0.1)" : "rgba(255, 120, 150, 0.04)");
    bloom.addColorStop(1, "rgba(255, 60, 125, 0)");
    ctx.beginPath();
    ctx.fillStyle = bloom;
    ctx.arc(cx, cy, r * 2.05, 0, Math.PI * 2);
    ctx.fill();

    const jelly = ctx.createRadialGradient(cx, cy, r * 0.78, cx, cy, r * 1.22);
    jelly.addColorStop(0, "rgba(255, 80, 130, 0)");
    jelly.addColorStop(0.7, "rgba(255, 80, 130, 0.05)");
    jelly.addColorStop(1, "rgba(255, 60, 120, 0.17)");
    ctx.beginPath();
    ctx.fillStyle = jelly;
    ctx.arc(cx, cy, r * 1.22, 0, Math.PI * 2);
    ctx.fill();

    const zona = ctx.createLinearGradient(cx - r, cy - r, cx + r, cy + r);
    zona.addColorStop(0, "rgba(255, 248, 240, 0.98)");
    zona.addColorStop(0.42, "rgba(255, 123, 146, 0.66)");
    zona.addColorStop(1, "rgba(111, 12, 46, 0.9)");
    ctx.beginPath();
    ctx.strokeStyle = zona;
    ctx.lineWidth = Math.max(3, r * 0.062);
    ctx.arc(cx, cy, r * 1.1, 0, Math.PI * 2);
    ctx.stroke();

    const body = ctx.createRadialGradient(
      cx - r * 0.38,
      cy - r * 0.44,
      r * 0.04,
      cx,
      cy,
      r * 1.1,
    );
    body.addColorStop(0, "#fff5e7");
    body.addColorStop(0.16, "#ffb09c");
    body.addColorStop(0.4, "#ff5d70");
    body.addColorStop(0.64, "#d6204b");
    body.addColorStop(0.84, "#8f1238");
    body.addColorStop(1, "#48051f");
    ctx.beginPath();
    ctx.fillStyle = body;
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();

    [0.82, 0.64, 0.46].forEach(function (scale, index) {
      ctx.beginPath();
      ctx.strokeStyle = "rgba(255, 216, 206, " + (0.055 + index * 0.018) + ")";
      ctx.lineWidth = Math.max(1, r * 0.012);
      ctx.arc(cx, cy, r * scale, 0, Math.PI * 2);
      ctx.stroke();
    });

    const depth = ctx.createRadialGradient(cx, cy, r * 0.52, cx, cy, r * 1.01);
    depth.addColorStop(0, "rgba(48, 0, 25, 0)");
    depth.addColorStop(0.76, "rgba(48, 0, 25, 0.12)");
    depth.addColorStop(1, "rgba(34, 0, 18, 0.72)");
    ctx.beginPath();
    ctx.fillStyle = depth;
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();

    const shine = ctx.createRadialGradient(
      cx - r * 0.4,
      cy - r * 0.46,
      0,
      cx - r * 0.4,
      cy - r * 0.46,
      r * 0.52,
    );
    shine.addColorStop(0, "rgba(255, 255, 255, 0.62)");
    shine.addColorStop(0.45, "rgba(255, 245, 235, 0.16)");
    shine.addColorStop(1, "rgba(255, 245, 235, 0)");
    ctx.beginPath();
    ctx.fillStyle = shine;
    ctx.ellipse(cx - r * 0.3, cy - r * 0.38, r * 0.4, r * 0.22, -0.5, 0, Math.PI * 2);
    ctx.fill();

    ctx.beginPath();
    ctx.strokeStyle = "rgba(255, 250, 244, 0.72)";
    ctx.lineWidth = Math.max(2, r * 0.028);
    ctx.arc(cx, cy, r * 0.985, -Math.PI * 0.78, -Math.PI * 0.06);
    ctx.stroke();

    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, r * 0.56);
    core.addColorStop(0, "rgba(255, 232, 214, 0.17)");
    core.addColorStop(0.42, "rgba(255, 160, 148, 0.09)");
    core.addColorStop(1, "rgba(255, 160, 148, 0)");
    ctx.beginPath();
    ctx.fillStyle = core;
    ctx.arc(cx, cy, r * 0.56, 0, Math.PI * 2);
    ctx.fill();

    if (anim.data.pregnant) {
      [
        [cx - r * 0.18, cy - r * 0.1],
        [cx + r * 0.18, cy + r * 0.13],
      ].forEach(function (point) {
        const pronucleus = ctx.createRadialGradient(
          point[0],
          point[1],
          0,
          point[0],
          point[1],
          r * 0.22,
        );
        pronucleus.addColorStop(0, "rgba(255, 244, 228, 0.95)");
        pronucleus.addColorStop(0.45, "rgba(255, 200, 182, 0.32)");
        pronucleus.addColorStop(1, "rgba(255, 200, 182, 0)");
        ctx.beginPath();
        ctx.fillStyle = pronucleus;
        ctx.arc(point[0], point[1], r * 0.22, 0, Math.PI * 2);
        ctx.fill();
      });
    }
  }

  function draw(dt) {
    drawBackground();
    drawParticles(dt);
    drawEgg();
    anim.sperm
      .slice()
      .sort(function (a, b) { return a.z - b.z; })
      .forEach(function (s) { drawSperm(s, dt); });
  }

  function frame(now) {
    if (!anim.running) return;
    const dt = Math.min(0.05, (now - (anim.last || now)) / 1000);
    anim.last = now;
    anim.time += dt;
    draw(dt);
    anim.raf = requestAnimationFrame(frame);
  }

  anim.resume = function () {
    if (anim.running) return;
    if (anim.reduce) {
      resize();
      draw(0.016);
      return;
    }
    anim.running = true;
    anim.last = 0;
    resize();
    anim.raf = requestAnimationFrame(frame);
  };

  if (window.ResizeObserver) {
    anim.observer = new ResizeObserver(function () {
      if (anim.running) resize();
    });
    anim.observer.observe(host);
  }
  resize();
  if (!el("panel-fertility").hidden) anim.resume();
}

function renderFertility(persona) {
  const tab = el("fertilityTab");
  const art = el("fertilityArt");
  const info = el("fertilityInfo");
  const hint = el("fertilityHint");
  const data = persona.intimacy;
  if (!data) {
    stopFertilityArt();
    art.replaceChildren();
    info.replaceChildren();
    hint.textContent = "—";
    return;
  }
  tab.hidden = false;
  const waiting =
    data.fertile &&
    data.inseminations > 0 &&
    data.last_insemination &&
    data.last_insemination !== "很久以前";
  let status = "未尝试";
  let desc = "还没有做爱记录；先进入易孕窗口，再决定要不要靠近。";
  if (data.pregnant) {
    status = "已受精";
    desc = "受精卵已经着床，身体进入孕期。";
  } else if (waiting) {
    status = "等待受精";
    desc = "精子已经进入，仍在判定窗口内。";
  } else if (data.fertile) {
    status = "易孕中";
    desc = "当前处于排卵前后，受孕概率最高。";
  } else if (data.count > 0) {
    status = "未受精";
    desc = "有做爱记录，但当前不在易孕窗口。";
  }
  hint.textContent = data.pregnant
    ? "怀孕中"
    : data.fertile
      ? "易孕窗口"
      : "非易孕期";
  art.className =
    "fertility-art" +
    (data.pregnant ? " is-pregnant" : data.fertile ? " is-fertile" : "");
  startFertilityArt(
    data,
    String(persona.id || persona.persona_id || persona.name || "default"),
  );

  info.replaceChildren();
  const statusBox = make("div", "fertility-status");
  statusBox.appendChild(make("span", "fertility-status-dot"));
  const copy = make("div", "fertility-status-copy");
  copy.appendChild(make("strong", "fertility-status-title", status));
  copy.appendChild(make("span", "fertility-status-desc", desc));
  statusBox.appendChild(copy);
  info.appendChild(statusBox);

  const grid = make("div", "fertility-metrics");
  const wetnessLabel =
    data.wetness >= 75
      ? "内裤湿了一小片"
      : data.wetness >= 45
        ? "内裤有点潮"
        : data.wetness >= 20
          ? "有点湿"
          : "干爽";
  const contraception = { none: "无避孕", condom: "避孕套", pill: "避孕药" };
  [
    ["做爱", data.count + " 次"],
    ["受精", data.inseminations + " 次"],
    ["自慰", (data.self_releases || 0) + " 次"],
    ["湿润", data.wetness + " · " + wetnessLabel],
    ["单次概率", data.chance + "%"],
    ["易孕", data.fertile ? "是" : "否"],
    ["避孕", contraception[data.contraception] || data.contraception || "—"],
    ["性欲基线", data.baseline || "默认"],
    ["上次做爱", data.last_at || "—"],
    ["上次受精", data.last_insemination || "—"],
  ].forEach(function (pair) {
    const item = make("div", "fertility-metric");
    item.appendChild(make("span", "fertility-metric-label", pair[0]));
    item.appendChild(make("strong", "fertility-metric-value", pair[1]));
    grid.appendChild(item);
  });
  info.appendChild(grid);

  const stages = ["未尝试", "易孕窗口", "等待受精", "已受精"];
  const activeStage = data.pregnant ? 3 : waiting ? 2 : data.fertile ? 1 : 0;
  const steps = make("div", "fertility-steps");
  stages.forEach(function (label, index) {
    const step = make("div", "fertility-step" + (index === activeStage ? " active" : ""));
    step.appendChild(make("i"));
    step.appendChild(make("span", "", label));
    steps.appendChild(step);
  });
  info.appendChild(steps);
  info.appendChild(
    make("p", "fertility-note", "页面只显示这个 persona 的记录；群聊不会出现这些数据。"),
  );
}

function render(data) {
  state.data = data;
  const personas = data.personas || [];
  if (!state.personaId || !personas.some(function (p) { return p.id === state.personaId; })) {
    state.personaId = personas[0] ? personas[0].id : "";
  }
  renderPersonaPicker(personas);
  const persona = selectedPersona();
  if (!persona) {
    el("heroName").textContent = "还没有读取到人格";
    el("summary").textContent = "还没有读取到人格";
    el("profileLine").textContent = "先在 AstrBot 里配置一个人格，再刷新这一页。";
    el("subtitle").textContent = "未读取到人格";
    el("clock").textContent = "—";
    el("meters").replaceChildren(make("p", "empty", "没有可显示的身体指标。"));
    el("innerStats").replaceChildren();
    el("careRows").replaceChildren();
    el("lastRow").textContent = "";
    el("events").replaceChildren(make("li", "empty", "还没有值得记下的事。"));
    el("eventCount").textContent = "";
    el("cycleCard").hidden = true;
    el("bodyCard").hidden = true;
    el("scentCard").hidden = true;
    el("maleCard").hidden = true;
    el("fertilityTab").hidden = true;
    stopFertilityArt();
    updateSettingsHint();
    return;
  }
  renderHero(persona, data.now);
  renderMeters(persona.meters);
  renderCare(persona.care, persona.now_minutes);
  renderSleep(persona);
  renderBodyStatus(persona);
  renderScents(persona);
  renderDetails(persona.details);
  renderLast(persona.last);
  renderCycle(persona);
  renderFertility(persona);
  renderMale(persona);
  renderEvents(persona.events);
  updateSettingsHint();
  const hasCycleTab = Boolean(persona.cycle || persona.intimacy);
  el("fertilityTab").hidden = !hasCycleTab;
  if (!hasCycleTab && !el("panel-fertility").hidden) setTab("live");
}

async function refresh() {
  try {
    const data = await bridge.apiGet("page/status");
    render(data);
  } catch (error) {
    showNotice(error && error.message ? error.message : "读取状态失败", "error");
  }
}

async function changeCycle(action) {
  const persona = selectedPersona();
  if (!persona || !persona.cycle) return;
  try {
    const payload = { persona_id: persona.id, action: action };
    if (action === "shift") payload.days = 1;
    const result = await bridge.apiPost("page/cycle", payload);
    if (result && result.state) {
      const index = state.data.personas.findIndex(function (item) {
        return item.id === persona.id;
      });
      if (index >= 0) state.data.personas[index] = result.state;
      render(state.data);
    }
    showNotice(action === "start" ? "已经把这天记为第 1 天" : "周期延后了一天", "ok");
  } catch (error) {
    showNotice(error && error.message ? error.message : "周期调整失败", "error");
  }
}

function updateSettingsHint() {
  const hint = el("settingsHint");
  if (!hint) return;
  const persona = selectedPersona();
  if (!persona) {
    hint.textContent = "没有选中人格";
    return;
  }
  const name = persona.name || persona.id;
  hint.textContent = "当前人格：" + name;
}

function renderSettings(values) {
  const host = el("settingsForm");
  host.replaceChildren();
  if (!values) return;
  CONFIG_FIELDS.forEach(function (group) {
    const section = make("section", "settings-group");
    section.appendChild(make("h3", "", group.group));
    const grid = make("div", "field-grid");
    group.fields.forEach(function (field) {
      if (field.type === "bool") {
        const row = make("div", "switch-row wide");
        const copy = make("div", "switch-copy");
        copy.appendChild(make("strong", "", field.label));
        if (field.hint) copy.appendChild(make("span", "", field.hint));
        row.appendChild(copy);
        const toggle = make("label", "switch");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.dataset.key = field.key;
        input.checked = !!values[field.key];
        toggle.appendChild(input);
        toggle.appendChild(document.createElement("i"));
        row.appendChild(toggle);
        grid.appendChild(row);
        return;
      }

      const wrap = make("div", "field" + (field.wide ? " wide" : ""));
      wrap.appendChild(make("label", "", field.label));
      let input;
      if (field.type === "select" || field.type === "persona") {
        input = document.createElement("select");
        if (field.type === "persona") {
          const follow = document.createElement("option");
          follow.value = "";
          follow.textContent = "跟随当前会话 / 默认人格";
          input.appendChild(follow);
          ((state.config && state.config.personas) || []).forEach(function (persona) {
            const option = document.createElement("option");
            option.value = persona.id;
            option.textContent = persona.name || persona.id;
            input.appendChild(option);
          });
        } else {
          (field.options || []).forEach(function (optionValue) {
            const option = document.createElement("option");
            option.value = optionValue;
            option.textContent =
              (field.optionLabels && field.optionLabels[optionValue]) || optionValue;
            input.appendChild(option);
          });
        }
        input.value = values[field.key] !== undefined ? values[field.key] : "";
      } else if (field.type === "textarea") {
        input = document.createElement("textarea");
        input.value = values[field.key] || "";
      } else {
        input = document.createElement("input");
        input.type = field.type === "number" ? "number" : "text";
        if (field.min !== undefined) input.min = String(field.min);
        if (field.max !== undefined) input.max = String(field.max);
        input.value =
          values[field.key] !== undefined && values[field.key] !== null
            ? String(values[field.key])
            : "";
      }
      input.dataset.key = field.key;
      wrap.appendChild(input);
      if (field.hint) wrap.appendChild(make("div", "field-hint", field.hint));
      grid.appendChild(wrap);
    });
    section.appendChild(grid);
    host.appendChild(section);
  });
  updateSettingsHint();
}

async function loadConfig() {
  try {
    const data = await bridge.apiGet("page/config");
    state.config = data || { values: {}, options: {}, personas: [] };
    renderSettings(state.config.values);
    el("settingsStatus").textContent = "";
  } catch (error) {
    el("settingsStatus").textContent =
      "读取设置失败：" + (error && error.message ? error.message : "未知错误");
  }
}

function collectSettings() {
  const updates = {};
  document.querySelectorAll("#settingsForm [data-key]").forEach(function (input) {
    const key = input.dataset.key;
    if (input.type === "checkbox") {
      updates[key] = input.checked;
    } else if (input.type === "number") {
      updates[key] = Number(input.value);
    } else {
      updates[key] = input.value;
    }
  });
  return updates;
}

async function saveSettings() {
  const button = el("saveSettingsBtn");
  const status = el("settingsStatus");
  button.disabled = true;
  status.textContent = "保存中…";
  try {
    const result = await bridge.apiPost("page/config/save", {
      updates: collectSettings(),
    });
    state.config = {
      values: (result && result.values) || {},
      options: state.config.options || {},
      personas: state.config.personas || [],
    };
    renderSettings(state.config.values);
    status.textContent = "已保存";
    showNotice("设置已保存并生效", "ok");
    await refresh();
  } catch (error) {
    status.textContent = "保存失败";
    showNotice(
      error && error.message ? error.message : "保存设置失败",
      "error",
    );
  } finally {
    button.disabled = false;
  }
}

function setTab(name) {
  if (name === "fertility" && el("fertilityTab").hidden) name = "live";
  document.querySelectorAll("#tabs [data-tab]").forEach(function (tab) {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  el("panel-live").hidden = name !== "live";
  el("panel-body").hidden = name !== "body";
  el("panel-fertility").hidden = name !== "fertility";
  el("panel-settings").hidden = name !== "settings";
  if (state.fertilityAnim) {
    if (name === "fertility") state.fertilityAnim.resume();
    else state.fertilityAnim.pause();
  }
  if (name === "settings") {
    loadConfig();
  }
}

function bind() {
  el("refreshBtn").addEventListener("click", refresh);
  el("meters").addEventListener("click", function (event) {
    if (event.target.closest("#moreBtn")) el("moreDialog").showModal();
  });
  el("closeMore").addEventListener("click", function () {
    el("moreDialog").close();
  });
  el("moreDialog").addEventListener("click", function (event) {
    if (event.target === el("moreDialog")) el("moreDialog").close();
  });
  el("closeInfo").addEventListener("click", function () {
    el("infoDialog").close();
  });
  el("infoDialog").addEventListener("click", function (event) {
    if (event.target === el("infoDialog")) el("infoDialog").close();
  });
  document.querySelectorAll("#tabs [data-tab]").forEach(function (tab) {
    tab.addEventListener("click", function () {
      setTab(tab.dataset.tab);
    });
  });
  el("saveSettingsBtn").addEventListener("click", saveSettings);
  el("personaSelect").addEventListener("change", function (event) {
    state.personaId = event.target.value;
    render(state.data);
    if (!el("panel-settings").hidden) {
      renderSettings(state.config.values);
    }
  });
  el("cycleActions").addEventListener("click", function (event) {
    const button = event.target.closest("[data-cycle]");
    if (!button) return;
    changeCycle(button.dataset.cycle === "start" ? "start" : "shift");
  });
}

function arrangeGrid() {
  // Cards are stacked in one column; no moving between columns after render,
  // so expanding or collapsing a section cannot shift the layout around.
  return;
}

async function boot() {
  arrangeGrid();
  bridge = await waitForBridge(5000);
  if (!bridge) {
    showNotice(
      "没有检测到 AstrBot 页面桥接 —— 请从 WebUI 的「插件 → 身体」进入；如果就是从那里进来的，刷新一次页面。",
      "error",
    );
    return;
  }
  try {
    await withTimeout(bridge.ready(), 6000, "桥接响应超时");
  } catch (error) {
    showNotice("页面桥接响应超时，如果下面一直是空白，刷新一次页面。", "error");
  }
  bind();
  document.addEventListener("click", function (event) {
    if (!event.target.closest || !event.target.closest(".care-cell")) hideCareTip();
  });
  await refresh();
  await loadConfig();
  state.timer = setInterval(refresh, 15000);
}

boot();
