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

- `domain + key` 最多一个 `status: active` 版本；新版本会收敛旧 active 版本。
- `source_refs` 强制非空；没有来源或不确定的内容先进入 `inbox/`。
- 单域 active inferred facts 不得超过 active explicit facts 的 30%。
- `get_facts` 默认只返回 active；compact 核心上下文只返回 active pinned/high facts。
- 事实值、版本状态、有效期和收敛链只通过 `write_fact` 修改。
