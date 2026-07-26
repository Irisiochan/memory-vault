# Memory Vault fact schema

Fact 层只保存稳定、可结构化、需要版本收敛的核心事实。项目事件、日记和需要完整语境的内容继续留在
普通 Markdown 记忆中。每个主题域对应 `memories/facts/<domain>.md`，每条 fact 是一个带边界标记的
YAML block，由 MCP 的 `write_fact` 写入和取代。

## 字段

```yaml
id: pets.household--0123456789ab
domain: pets
key: household
value:
  cats: 3
type: explicit
confidence: high
priority: pinned
status: active
valid_from: '2026-07-23'
valid_until: null
source_refs:
  - memories/owner-core.md
superseded_by: null
note: 背景说明
source: assistant-name
created: '2026-07-23T17:00:00+08:00'
updated: '2026-07-23T17:00:00+08:00'
```

## 不变量

- `domain + key` 在任一日期最多一个有效版本；当前版本与一个未来预约可以同时保持
  `status: active`，但有效期不得重叠。
- 写入 `valid_from` 晚于 vault 当天的同 key 新版本时，`write_fact` 会把当前版本的
  `valid_until` 设为生效日前一天并建立 `superseded_by` 链；到期后读取层自动切换，
  不依赖后台任务。
- 同一 key 最多保留一个待生效预约；重复预约幂等，冲突预约会被拒绝。
- `source_refs` 强制非空；没有来源或不确定的内容先进入 `inbox/`。
- 单域当前有效的 inferred facts 不得超过当前有效 explicit facts 的 30%。
- `get_facts` 默认只返回当天有效的 active；compact 核心上下文只返回当天有效的
  active pinned/high facts。
- 事实值、版本状态、有效期和收敛链只通过 `write_fact` 修改。
