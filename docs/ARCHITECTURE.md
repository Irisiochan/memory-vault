# Memory Vault 架构

面向想读源码、二次开发或排查问题的人。README 讲怎么用，本文讲它是怎么搭的。
只描述本仓已实现的稳定结构；版本级变化看 [CHANGELOG.md](../CHANGELOG.md)。

## 1. 核心决策

1. **数据是 Markdown，不是数据库。** 全部记忆是带 YAML frontmatter 的 Markdown
   文件，Obsidian 可直接读写，AI 死了数据还在。检索、聚合、上下文都是运行时
   从文件现算的，没有第二份需要同步的索引状态。
2. **代码与数据同仓分层。** `_meta/` 放实现，其余目录放数据；模板仓不含任何
   真实记忆，用户从 `template/` 初始化自己的私有 vault。
3. **一把锁覆盖完整写事务。** 任何写入 = 「读取 → 修改 → 落盘 → Git 同步」
   整体持锁执行，锁是跨进程的，同机的 Docker HTTP 实例和各 CLI stdio 实例
   可安全共享同一 vault。
4. **Git 是可选同步层，不是依赖。** vault 自己是 Git 仓库时写入自动 commit /
   pull --rebase / push；没有 `.git` 就纯本地，绝不向上误用父目录远端。

## 2. 分层结构

```
客户端（Claude/Codex/ChatGPT/手机 App …）
 │  stdio 或 streamable-HTTP（可选 VAULT_TOKEN 认证）
 ▼
vault_transport.py     传输：stdio / HTTP 绑定与认证，Tailscale IP 优先
mcp_server.py          工具注册与入口，不含业务逻辑
 │
 ├─ vault_context.py   稳定上下文 / 本轮时间 / 任务快照（get_context 等）
 ├─ vault_facts.py     结构化事实：按 domain 聚合存储、版本收敛、有效期过滤
 ├─ vault_search.py    只读检索：search / list / get_related（目录加权）
 ├─ vault_writes.py    写入：inbox / memory / archive / diary / daily
 └─ vault_tasks.py     任务账本：快照分层（到期/活跃/冬眠）与原子改期
 │
vault_runtime.py       共享运行时：配置、路径校验、文件系统、Git 原语、
                       serialized_mutation 装饰器（写事务边界）
vault_lock.py          可重入跨进程文件锁（.vault-operation.lock）
```

外围：`memory_vault_mcp/` 是 pip/pipx 可安装的命令行包装（`memory-vault-mcp`），
自带一份 `template/` 用于向空目录生成空白 vault；`_meta/build_context.py`
给没有 MCP 的客户端生成可粘贴的 context prompt。

## 3. 数据布局

| 目录 | 内容 | 特殊语义 |
|---|---|---|
| `memories/` | 确认的长期记忆 | `core_files` 每次新任务注入；核心文件只追加 |
| `memories/facts/<domain>.md` | 结构化事实 | 由 `write_fact` 管理，一文件一 domain，人也可读 |
| `tasks/` | 共享任务账本 | frontmatter 驱动快照分层，done/dropped 自动归档 |
| `inbox/` | 低置信度暂存 | 验证后 `promote_to_memory` 升级 |
| `projects/` `diary/` | 项目脑洞 / 日常与日记 | |
| `_archive/retired/` | 软删除区 | `archive_memory` 落点，git 可回滚 |
| `_meta/` | 配置、规则、实现 | `vault_config.yaml` 是用户侧唯一必填配置 |

## 4. 关键机制

### 写事务与锁

所有写工具经 `serialized_mutation` 装饰：进入即获取 `.vault-operation.lock`
文件锁（可重入，进程崩溃由操作系统释放），锁内完成修改、原子落盘
（临时文件 + 替换）和 Git 同步。Git 子进程不继承 MCP 的 stdio 管道，
弹交互询问不会吞协议字节；push 失败如实返回错误摘要，后续写入自动补推
未同步的 commit，不把本地 commit 误报成远端成功。

### Fact 层

事实以 `{domain, key, value, priority, valid_from/valid_until}` 结构化存储：
同 key 新写入收敛版本而非堆积；读取默认只返回当天有效的 active facts；
写未来 `valid_from` 形成预约切换，到期读取层自动换版本。compact core
context 由 pinned/high 优先级的 active facts 实时渲染，供宿主预载注入。

### 任务快照分层

`get_task_context` 按当前日期分桶：已过期 / 今天 / 未来 7 天 / 无期限。
无期限任务超过 14 天未更新自动转「冬眠层」只列标题，防止长期积压占满
每轮上下文；frontmatter `dormant: true|false` 可手动覆盖。

### 预载感知

宿主可在用户消息前注入 `<VAULT_CORE_PRELOADED>` / `<TURN_TIME_PRELOADED>`
等标记；MCP instructions 与工具说明教会模型识别标记、跳过重复调用，
避免长会话反复堆叠同一份上下文。

## 5. 安全边界

- 路径校验拒绝反斜杠、盘符、隐藏段、`..` 和符号链接组件，且在 Git 拉取之后
  执行——同步带入的 vault 外符号链接第一次读取即被拒。检索类工具的目录扫描
  走同一套校验。
- Git 操作使用 `--literal-pathspecs`，防止 pathspec 展开越界。
- HTTP 传输默认只绑回环或 Tailscale IP；`VAULT_TOKEN` 提供 Bearer /
  X-Vault-Token 认证。裸公网暴露被文档明确劝阻。
- 密钥永不入 Markdown / 日志 / Git（规则见 `template/_meta/rules.md`）。

## 6. 测试与交付

`tests/` 全部使用临时 vault，不触碰真实数据：smoke（初始化与基本读写）、
task_snapshot_tiers（任务分层）、protocol_smoke / http_smoke（真实 stdio 与
streamable-HTTP 握手）、concurrent_writes / vault_lock（并发与锁）、
maintenance_regressions（历史回归）、repository_boundary（代码/数据边界）。
CI 验证 Python 包构建、MCP 协议、HTTP 传输、仓库边界与 Docker 镜像。

交付形态三种，同一代码路径：clone + `pip install -e .`（开发）、
`pipx install git+…`（只装命令）、`docker compose up -d`（HTTP 服务）。
