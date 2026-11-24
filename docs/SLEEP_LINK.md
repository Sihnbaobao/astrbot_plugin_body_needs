# 睡眠契约（供其它插件读取）

睡眠由身体插件（astrbot_plugin_body_needs）独占：什么时候困、什么时候上床、什么时候睡着、什么时候醒、白天要不要午睡，都由它决定。其它插件只在行动前读一次状态，并把自己的活动写回一份心跳。

## 身体发布：睡眠状态

### 1. 同进程调用

\x60\x60\x60python
snapshot = plugin.sleep_snapshot()
# {
#   "asleep": True,          # 真的睡着了
#   "in_bed": False,         # 躺在床上但还没睡着
#   "enabled": True,
#   "persona": "璃月",
#   "kind": "night",         # night | nap | ""
#   "since": 1790000000.0,   # 睡着的时刻
#   "bed_at": 0.0,           # 上床的时刻（in_bed 时有效）
#   "wake_at": 1790028000.0, # 预计醒
#   "bedtime": 0.0,          # 下一次习惯就寝
#   "nap_at": 0.0,           # 今天预计午睡的时刻，0 表示没有
#   "slept_hours": 1.5,
#   "line": "睡下了（23:10 睡的，大概 07:20 醒）",
#   "debt": 8.4,
#   "busy_until": 1790030000.0,
#   "busy_kind": "看B站",
# }
\x60\x60\x60

### 2. 文件契约

\x60data/plugin_data/astrbot_plugin_body_needs/sleep.json\x60，结构：

\x60\x60\x60json
{
  "version": 1,
  "current": "璃月",
  "personas": {
    "璃月": {
      "persona_id": "璃月",
      "asleep": true,
      "in_bed": false,
      "kind": "night",
      "since": 1790000000.0,
      "bed_at": 0.0,
      "wake_at": 1790028000.0,
      "bedtime": 1790040000.0,
      "nap_at": 0.0,
      "awake_since": 1789980000.0,
      "busy_until": 1790030000.0,
      "busy_kind": "看B站",
      "enabled": true,
      "gate": true,
      "line": "睡下了（23:10 睡的，大概 07:20 醒）",
      "debt": 8.4,
      "updated_at": "2026-11-21T03:00:00+08:00"
    }
  }
}
\x60\x60\x60

读取规则：\x60enabled == false\x60 或 \x60wake_at\x60 已经过去都按醒着算；\x60in_bed\x60 不算睡着，只有 \x60asleep\x60 才静默。

## 插件写回：活动心跳

B站、听歌、QQ空间在做自己的事时会写 \x60data/plugin_data/<插件名>/activity.json\x60：

\x60\x60\x60json
{"active": true, "kind": "看B站", "persona": "璃月", "since": 1790000000, "until": 1790002400, "focus": 0.8}
\x60\x60\x60

身体插件读所有心跳，把最晚的 \x60until\x60 当作“她现在正忙着”：忙的时候上床时间会往后推（最多锚点后 3 小时），睡眠债 ≥17 时才压过忙。因此睡前看 B 站看上瘾会晚睡，第二天自然也起得晚。

心跳过期（\x60until\x60 过去）就当作空闲，不需要手动清除。

## 各插件怎么排时间

所有插件都从睡眠状态里排自己的时间，而不是写死钟点：

- 醒着的时间窗：\x60awake_since\x60 到 \x60bedtime\x60；午睡窗口是 \x60nap_at\x60 前后。
- 尽量不要落在午睡和上床后的时间；同一时段最多让一件事占着（\x60activity.json\x60），允许偶尔重叠，但不做固定重合。
- 睡着时一律不行动；醒来后按自己的节奏接上。
