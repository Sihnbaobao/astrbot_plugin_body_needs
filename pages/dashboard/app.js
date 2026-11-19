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
  CARE_ROWS.forEach(function (rowDef) {
    const row = make("div", "care-row");
    row.appendChild(make("span", "care-row-label", rowDef[1]));
    const lane = make("div", "care-lane");
    items
      .filter(function (item) {
        return item.kind === rowDef[0];
      })
      .forEach(function (item) {
        const dot = make("span", "care-dot " + item.kind);
        const minutes = Number(item.minutes);
        dot.style.left =
          Math.max(0.5, Math.min(99.5, ((Number.isFinite(minutes) ? minutes : 0) / 1440) * 100)) + "%";
        dot.title = (item.at || "") + " " + (item.label || rowDef[1]);
        lane.appendChild(dot);
      });
    if (Number.isFinite(nowMinutes)) {
      const now = make("span", "care-now");
      now.style.left = Math.max(0, Math.min(100, (nowMinutes / 1440) * 100)) + "%";
      now.title = "现在";
      lane.appendChild(now);
    }
    row.appendChild(lane);
    host.appendChild(row);
  });
  if (!items.length) {
    host.appendChild(make("p", "empty", "最近 24 小时还没有记录。"));
  }
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
  const meals = make("div", "selfcare-meals");
  ["早饭", "午饭", "晚饭"].forEach(function (name) {
    const row = make("div", "selfcare-meal");
    row.appendChild(make("span", "selfcare-label", name));
    row.appendChild(
      make("strong", "", mealTimes[name] ? "吃过了 " + mealTimes[name] : "还没吃"),
    );
    meals.appendChild(row);
  });
  host.appendChild(meals);

  const counts = { drink: 0, toilet: 0, shower: 0, brush: 0 };
  items.forEach(function (item) {
    if (counts[item.kind] !== undefined) counts[item.kind] += 1;
  });
  const stats = make("div", "selfcare-stats");
  [
    ["喝水", counts.drink],
    ["厕所", counts.toilet],
    ["洗澡", counts.shower],
    ["刷牙", counts.brush],
  ].forEach(function (pair) {
    const cell = make("div", "selfcare-stat");
    cell.appendChild(make("b", "", String(pair[1])));
    cell.appendChild(make("span", "", pair[0]));
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
    const raceRow = cycle.race
      ? [["种族", cycle.race + (cycle.physio_age ? "（约人类 " + cycle.physio_age + " 岁）" : "")]]
      : [];
    if (raceRow.length) {
      const facts = make("dl", "facts");
      raceRow.forEach(function (pair) {
        const row = document.createElement("div");
        row.appendChild(make("dt", "", pair[0]));
        row.appendChild(make("dd", "", pair[1]));
        facts.appendChild(row);
      });
      host.appendChild(facts);
    }
    return;
  }

  el("cyclePhase").textContent =
    (cycle.phase_label || "周期") + " · 第 " + cycle.day + " 天";

  const hero = make("div", "cycle-hero");
  hero.appendChild(make("b", "", "第 " + cycle.day + " 天"));
  hero.appendChild(make("span", "", cycle.phase_label || ""));
  host.appendChild(hero);

  const facts = make("dl", "facts");
  const symptomBits = [];
  if (cycle.breast) symptomBits.push("乳房胀");
  if (cycle.headache) symptomBits.push("头痛");
  if (cycle.backache) symptomBits.push("腰酸");
  const rows = [
    ["下次", "约 " + cycle.days_to_next + " 天后"],
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

  const hormones = make("div", "hormones");
  [
    ["雌激素", "estrogen", "estrogen"],
    ["孕激素", "progesterone", "progesterone"],
    ["LH", "lh", "lh"],
  ].forEach(function (row) {
    const value = Number(cycle[row[1]]) || 0;
    const line = make("div", "hormone-row");
    line.appendChild(make("span", "", row[0]));
    const bar = make("div", "hormone-bar");
    const fill = make("i", row[2]);
    fill.style.width = Math.min(100, Math.max(0, value)) + "%";
    bar.appendChild(fill);
    line.appendChild(bar);
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
  if (!events || !events.length) {
    host.appendChild(make("li", "empty", "还没有值得记下的事。"));
    el("eventCount").textContent = "";
    return;
  }
  events.forEach(function (event) {
    const item = document.createElement("li");
    item.appendChild(make("time", "", event.time || ""));
    item.appendChild(make("span", "", event.text || ""));
    host.appendChild(item);
  });
  el("eventCount").textContent = events.length + " 条";
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
  document.querySelectorAll("#tabs [data-tab]").forEach(function (tab) {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  el("panel-live").hidden = name !== "live";
  el("panel-settings").hidden = name !== "settings";
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
  await refresh();
  await loadConfig();
  state.timer = setInterval(refresh, 15000);
}

boot();
