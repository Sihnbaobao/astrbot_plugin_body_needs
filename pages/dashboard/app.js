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
      { key: "AGE", label: "年龄兜底", type: "number", min: 0, max: 100000, hint: "0 = 从人格读；只影响生理曲线，不限制功能。" },
      { key: "LIFESPAN", label: "种族寿命", type: "number", min: 0, max: 100000, hint: "0 = 从人格认（精灵/恶魔/吸血鬼/矮人/龙族/天使/神族）；人类按 80 年。" },
      { key: "PERSONA_ID", label: "固定人格", type: "persona", hint: "留空 = 跟随当前会话；插件页显示 AstrBot 默认人格。选一个 = 固定这个人的身体。" },
      { key: "TIMEZONE", label: "时区", type: "text", hint: "如 Asia/Shanghai；留空用 AstrBot 全局时区。" },
    ],
  },
  {
    group: "作息与代谢",
    fields: [
      { key: "SLEEP_WINDOW", label: "睡眠时段", type: "text", hint: "HH:MM-HH:MM，例如 00:00-08:00。" },
      { key: "ENABLE_SLEEP", label: "启用睡眠代谢", type: "bool", hint: "睡眠时消化变慢、睡眠债恢复；关闭后全部按清醒算。" },
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
    group: "成人模块",
    fields: [
      { key: "ENABLE_INTIMATE", label: "启用成人私密生理", type: "bool", adult: true, hint: "男/女人格的性欲与亲密记录；需要能识别出性别，只在私聊启用。" },
      { key: "ENABLE_AWAY", label: "自慰后暂时不回消息", type: "bool", adult: true, hint: "私聊里自慰后的 4-12 分钟内不回复；关掉后照常回。" },
      { key: "ENABLE_PREGNANCY", label: "启用怀孕", type: "bool", adult: true, hint: "易孕期亲密有概率受孕；受孕后停经并按孕周推进。判定不看年龄。" },
      { key: "CONTRACEPTION", label: "避孕方式", type: "select", options: ["none", "condom", "pill"], optionLabels: { none: "不避孕", condom: "安全套", pill: "避孕药" }, adult: true, hint: "把受孕概率从约 18% 降到 1-2%。" },
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
  el("subtitle").textContent = (persona.name || persona.id) + " · " + (persona.summary || "");
}

function renderMeters(meters) {
  const host = el("meters");
  host.replaceChildren();
  if (!meters || !meters.length) {
    host.appendChild(make("p", "empty", "没有可显示的身体指标。"));
    return;
  }
  meters.forEach(function (meter) {
    const row = make("div", "meter");
    row.appendChild(make("span", "meter-name", meter.name || meter.label));

    const main = make("div", "meter-main");
    const top = make("div", "meter-top");
    top.appendChild(make("span", "meter-level", meter.level || ""));
    top.appendChild(
      make("span", "meter-value", Math.round(meter.value) + "/100"),
    );
    main.appendChild(top);

    const bar = make("div", "meter-bar");
    const fill = make("div", "meter-fill " + (meter.tone || "ok"));
    fill.style.width = Math.max(0, Math.min(100, Number(meter.value) || 0)) + "%";
    bar.appendChild(fill);
    main.appendChild(bar);
    row.appendChild(main);
    row.appendChild(make("span", "meter-badge", meter.tone === "hot" ? "注意" : ""));
    host.appendChild(row);
  });
}

const CARE_ROWS = [
  ["meal", "吃饭"],
  ["drink", "喝水"],
  ["toilet", "厕所"],
  ["shower", "洗澡"],
  ["brush", "刷牙"],
];

function renderCare(care) {
  const host = el("careRows");
  host.replaceChildren();
  const items = Array.isArray(care) ? care : [];
  const nowMinutes = Number(state.data && state.data.now_minutes);
  const nowHour = Number.isFinite(nowMinutes) ? Math.floor(nowMinutes / 60) : -1;
  const summary = el("careSummary");
  if (summary) {
    const nowLabel = Number.isFinite(nowMinutes)
      ? String(Math.floor(nowMinutes / 60)).padStart(2, "0") +
        ":" +
        String(nowMinutes % 60).padStart(2, "0")
      : "";
    summary.textContent = nowLabel ? "现在 " + nowLabel : "";
  }
  CARE_ROWS.forEach(function (rowDef) {
    const row = make("div", "care-row");
    const label = make("span", "care-row-label");
    label.appendChild(make("i", "care-key " + rowDef[0]));
    label.appendChild(document.createTextNode(rowDef[1]));
    row.appendChild(label);
    const cells = make("div", "care-cells");
    const byHour = {};
    items
      .filter(function (item) {
        return item.kind === rowDef[0];
      })
      .forEach(function (item) {
        const minutes = Number(item.minutes);
        const hour = Math.max(0, Math.min(23, Math.floor((Number.isFinite(minutes) ? minutes : 0) / 60)));
        if (!byHour[hour]) byHour[hour] = [];
        byHour[hour].push(item.at || "");
      });
    for (let hour = 0; hour < 24; hour += 1) {
      const cell = make("span", "care-cell");
      const hourLabel = String(hour).padStart(2, "0") + ":00";
      const lines = [hourLabel];
      cell.title = hourLabel;
      if (byHour[hour]) {
        cell.classList.add("on", rowDef[0]);
        cell.title = hourLabel + " " + rowDef[1] + "（" + byHour[hour].join("、") + "）";
        byHour[hour].forEach(function (at) {
          lines.push(at + " " + rowDef[1]);
        });
      } else {
        lines.push("没有记录");
      }
      if (hour === nowHour) cell.classList.add("is-now");
      attachCareTip(cell, lines);
      cells.appendChild(cell);
    }
    row.appendChild(cells);
    host.appendChild(row);
  });
  if (!items.length) {
    host.appendChild(make("p", "empty", "最近 24 小时还没有记录。"));
  }
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

function renderSelfCare(care) {
  const host = el("selfCare");
  host.replaceChildren();
  const items = Array.isArray(care) ? care : [];
  const mealTimes = {};
  items
    .filter(function (item) {
      return item.kind === "meal";
    })
    .forEach(function (item) {
      const minutes = Number(item.minutes) || 0;
      const slot = minutes < 600 ? "早饭" : minutes < 960 ? "午饭" : "晚饭";
      if (!mealTimes[slot]) mealTimes[slot] = item.at;
    });
  const meals = make("div", "selfcare-line");
  ["早饭", "午饭", "晚饭"].forEach(function (name) {
    const chip = make("span", "selfcare-chip");
    chip.appendChild(make("b", "", name));
    chip.appendChild(make("span", "", mealTimes[name] || "还没"));
    meals.appendChild(chip);
  });
  host.appendChild(meals);

  const counts = { drink: 0, toilet: 0, shower: 0, brush: 0 };
  items.forEach(function (item) {
    if (counts[item.kind] !== undefined) counts[item.kind] += 1;
  });
  const stats = make("div", "selfcare-line selfcare-counts");
  [
    ["喝水", counts.drink],
    ["厕所", counts.toilet],
    ["洗澡", counts.shower],
    ["刷牙", counts.brush],
  ].forEach(function (pair) {
    const cell = make("span", "selfcare-chip");
    cell.appendChild(make("b", "", pair[0]));
    cell.appendChild(make("span", "", String(pair[1]) + " 次"));
    stats.appendChild(cell);
  });
  host.appendChild(stats);
}


function renderDetails(details) {
  const host = el("innerStats");
  host.replaceChildren();
  if (!details || !details.length) return;
  details.forEach(function (item) {
    const row = make("div", "inner-stat");
    const head = make("div", "inner-stat-head");
    head.appendChild(make("span", "", item.label));
    const value = Number(item.value) || 0;
    const display =
      item.key === "sleep_debt"
        ? (Math.round(value * 10) / 10) + " 小时"
        : Math.round(value) + "%";
    head.appendChild(make("b", "", (item.level || "") + " · " + display));
    row.appendChild(head);
    const bar = make("div", "inner-bar");
    const fill = make("i", item.tone || "ok");
    const percent =
      item.key === "sleep_debt"
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
  const rows = [
    ["下次", "约 " + cycle.days_to_next + " 天后"],
    ["易孕", cycle.fertile ? "是" : "否"],
    ["经量", cycle.flow_label || "—"],
    ["小腹", crampText(cycle.cramps)],
    ["周期", cycle.cycle_length + " 天"],
    ["分泌物", cycle.discharge_label || "—"],
    ["经前", symptomBits.length ? symptomBits.join("、") : "—"],
    ["用品", typeof cycle.pads === "number" ? cycle.pads + " 片" : "—"],
  ];
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

function startFertilityArt(data) {
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

  function spawnSperm(initial) {
    const outer = maxRadius();
    return {
      angle: Math.random() * Math.PI * 2,
      radius: initial
        ? anim.eggR * 1.3 + Math.random() * Math.max(16, outer - anim.eggR * 1.3)
        : outer + 12 + Math.random() * 28,
      speed: 4.2 + Math.random() * 5.6,
      size: 0.86 + Math.random() * 0.6,
      phase: Math.random() * Math.PI * 2,
      z: Math.random(),
    };
  }

  function seed() {
    anim.sperm = [];
    for (let index = 0; index < 14; index += 1) {
      const sperm = spawnSperm(true);
      sperm.z = Math.min(
        1,
        Math.max(0.04, 0.08 + (index / 13) * 0.84 + (Math.random() - 0.5) * 0.06),
      );
      anim.sperm.push(sperm);
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
    s.radius -= s.speed * dt;
    s.angle += (0.016 + (s.z - 0.5) * 0.008) * dt;
    if (s.radius < anim.eggR * 1.18) {
      const next = spawnSperm(false);
      s.angle = next.angle;
      s.radius = next.radius;
      s.speed = next.speed;
      s.size = next.size;
      s.phase = next.phase;
      s.z = next.z;
    }
    if (s.radius > maxRadius() + 60) s.radius = maxRadius() + 60;
    const x = anim.cx + Math.cos(s.angle) * s.radius;
    const y = anim.cy + Math.sin(s.angle) * s.radius;
    const fade = Math.min(1, Math.max(0, (s.radius - anim.eggR * 1.2) / 30));
    const alpha =
      (0.45 + s.z * 0.55) * fade * (anim.data.pregnant ? 0.12 : 1);
    if (alpha <= 0.01) return;
    const depthScale = 0.62 + s.z * 0.95;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(s.angle);
    ctx.scale(s.size * depthScale, s.size * depthScale);
    ctx.globalAlpha = alpha;

    const tailLength = 118;
    const samples = 26;
    const points = [];
    for (let index = 0; index <= samples; index += 1) {
      const t = index / samples;
      points.push({
        x: 8 + t * tailLength,
        y:
          Math.sin(t * 3.1 - anim.time * 2.9 + s.phase) *
          10 *
          (1 - t * 0.12),
        t: t,
      });
    }

    ctx.beginPath();
    points.forEach(function (point, index) {
      if (index === 0) ctx.moveTo(point.x, point.y);
      else ctx.lineTo(point.x, point.y);
    });
    ctx.strokeStyle = "rgba(220, 235, 255, 0.18)";
    ctx.lineWidth = 5.6;
    ctx.lineCap = "round";
    ctx.stroke();

    ctx.beginPath();
    points.forEach(function (point, index) {
      const width = 2.5 * Math.pow(1 - point.t, 0.78) + 0.22;
      const px = point.x;
      const py = point.y - width;
      if (index === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    for (let index = points.length - 1; index >= 0; index -= 1) {
      const point = points[index];
      const width = 2.5 * Math.pow(1 - point.t, 0.78) + 0.22;
      ctx.lineTo(point.x, point.y + width);
    }
    ctx.closePath();
    ctx.fillStyle = "#fffdfb";
    ctx.fill();

    ctx.beginPath();
    points.forEach(function (point, index) {
      const width = 2.5 * Math.pow(1 - point.t, 0.78) + 0.22;
      const px = point.x;
      const py = point.y - width * 0.22;
      if (index === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.strokeStyle = "rgba(255, 255, 255, 0.72)";
    ctx.lineWidth = 0.65;
    ctx.stroke();

    if (s.z > 0.58) {
      const aura = ctx.createRadialGradient(0, 0, 0, 0, 0, 24);
      aura.addColorStop(0, "rgba(215, 230, 255, " + (0.16 * s.z).toFixed(3) + ")");
      aura.addColorStop(1, "rgba(215, 230, 255, 0)");
      ctx.beginPath();
      ctx.fillStyle = aura;
      ctx.arc(0, 0, 24, 0, Math.PI * 2);
      ctx.fill();
    }

    const head = ctx.createRadialGradient(-3.4, -2.2, 0.4, 0, 0, 11);
    head.addColorStop(0, "#ffffff");
    head.addColorStop(0.5, "#fbf8ff");
    head.addColorStop(0.82, "#e8dcf3");
    head.addColorStop(1, "#cdbce0");
    ctx.shadowColor = "rgba(215, 230, 255, 0.55)";
    ctx.shadowBlur = 5 + s.z * 8;
    ctx.beginPath();
    ctx.moveTo(9, 0);
    ctx.bezierCurveTo(8, -3.6, 3, -5.8, -2, -5.4);
    ctx.bezierCurveTo(-7, -5, -9, -2.4, -9, 0);
    ctx.bezierCurveTo(-9, 2.4, -7, 5, -2, 5.4);
    ctx.bezierCurveTo(3, 5.8, 8, 3.6, 9, 0);
    ctx.closePath();
    ctx.fillStyle = head;
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.92)";
    ctx.lineWidth = 0.85;
    ctx.stroke();

    ctx.beginPath();
    ctx.fillStyle = "rgba(255, 255, 255, 0.86)";
    ctx.ellipse(-2.6, -2.2, 3.2, 1.35, -0.18, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.strokeStyle = "rgba(199, 183, 226, 0.5)";
    ctx.lineWidth = 0.8;
    ctx.arc(0, 0, 8.7, Math.PI * 1.08, Math.PI * 1.58);
    ctx.stroke();
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
    tab.hidden = true;
    stopFertilityArt();
    art.replaceChildren();
    info.replaceChildren();
    hint.textContent = "—";
    if (!el("panel-fertility").hidden) setTab("live");
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
  startFertilityArt(data);

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
    el("summary").textContent = "还没有读取到人格";
    el("profileLine").textContent = "先在 AstrBot 里配置一个人格，再刷新这一页。";
    el("subtitle").textContent = "未读取到人格";
    el("clock").textContent = "—";
    el("meters").replaceChildren(make("p", "empty", "没有可显示的身体指标。"));
    el("innerStats").replaceChildren();
    el("careRows").replaceChildren();
    el("selfCare").replaceChildren();
    el("lastRow").textContent = "";
    el("events").replaceChildren(make("li", "empty", "还没有值得记下的事。"));
    el("eventCount").textContent = "";
    el("cycleCard").hidden = true;
    el("maleCard").hidden = true;
    el("fertilityTab").hidden = true;
    stopFertilityArt();
    updateSettingsHint();
    return;
  }
  renderHero(persona, data.now);
  renderMeters(persona.meters);
  renderCare(persona.care);
  renderSelfCare(persona.care);
  renderDetails(persona.details);
  renderLast(persona.last);
  renderCycle(persona);
  renderFertility(persona);
  renderMale(persona);
  renderEvents(persona.events);
  updateSettingsHint();
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

async function boot() {
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
