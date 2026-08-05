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

## date-event value 约定

用于「每年会回来」的日子（生日、纪念日），供 daily check-in 等确定性日期匹配读取。
**不要**用自由文本塞日期，也不要扫整条 fact 的任意 `YYYY-MM-DD`（会误命中 `valid_from` / `created`）。

### value 形态

```yaml
value:
  date: '2001-08-04'   # 首次/锚点日期 YYYY-MM-DD；匹配按 MM-DD（recurring=yearly）
  recurring: yearly    # 目前只约定 yearly；未声明 recurring 的不要当 date-event 扫
  label: Iris 生日     # 人读标签，注入线索时用
```

### 写入规则

- 同一语义只能有一条 active date-event。例如生日只写 `identity.birthday`；
  `identity.birth` 只保留出生地，**不要**再带 `date`。
- 纪念日放 `relationships` 域，key 形如 `anniversary.<slug>`
  （例：`anniversary.cheng_wedding`、`anniversary.cove_cohabitation`）。
- `priority` 建议 `pinned`（唯一关键日，如本人生日）或 `high`（关系纪念日），
  以便 compact 核心上下文也能看到；匹配器本身读 `get_facts` active 全集。
- `write_fact` 的 `value` 必须是上述对象，不要写成纯字符串日期。

### 示例

```yaml
# 生日
domain: identity
key: birthday
value:
  date: '2001-08-04'
  recurring: yearly
  label: Iris 生日
priority: pinned

# 纪念日
domain: relationships
key: anniversary.cheng_wedding
value:
  date: '2026-05-21'
  recurring: yearly
  label: 橙与 Iris 新婚纪念日
priority: high
```

关联方案：运行 vault 见 `memories/daily-checkin-context-aware-enhancement.md`（模板仓库可省略）。
