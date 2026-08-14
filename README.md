# Memory Vault — 通用 AI 记忆基础设施

给 Claude、Codex、ChatGPT 和自建 Agent 一套可替换前端的共享长期记忆：
Markdown 存储、Obsidian 可读、MCP 读写、Docker 部署、可选 Git 多设备同步。

仓库本身是空白模板，不包含作者的私人记忆。请用 **Use this template**
创建你自己的 **private repository**。

## 当前 main：预载感知、任务分层与写入一致性

有些客户端会在用户消息前自动注入 Memory Vault 的核心上下文或当前时间。
MCP instructions、工具说明和跨 CLI 工作流现在会识别
`<VAULT_CORE_PRELOADED>`、`<VAULT_CORE_PRELOAD_FALLBACK>` 与
`<TURN_TIME_PRELOADED>`，避免同一轮再次调用上下文或时间工具。

核心预载不包含任务快照；除非宿主明确说明任务快照也已预载，每个新任务仍调用一次
`get_task_context`。这样既保留截止事项提醒，也不会在长会话里反复堆叠同一份上下文。

`memory-vault-mcp` 的任务快照会完整展示有期限的任务和最近更新的无期限任务。
无期限任务超过 14 天没有更新时，默认进入“冬眠层”，只汇总标题，避免长期积压占满
每轮上下文；需要详情时仍可通过 `search_vault` / `read_file` 读取。任务 frontmatter
可用 `dormant: true` 强制冬眠，或用 `dormant: false` 保持完整展示。

当前主线也会串行化同一服务进程中的完整写事务，避免并发客户端覆盖彼此的更新；
Fact 的有效期会参与默认 active 读取和 compact context 过滤。

## v0.5.0：记忆系统与客户端正式解耦

- 仓库只保留 Markdown 记忆规范、MCP server/CLI、Docker、模板、同步规则与测试。
- 旧 `app/`、`easy/` 和 Windows 前端便携包已移出；旧实现仍可从
  [`v0.4.1`](https://github.com/Irisiochan/memory-vault/tree/v0.4.1) 追溯。
- 聊天 UI、多联系人、群聊和 Worker 由可选客户端
  [`ai-hub-public`](https://github.com/Irisiochan/ai-hub-public) 提供。
- CI 只验证 Memory Vault 的 Python 包、MCP 协议、HTTP 传输、仓库边界和 Docker 镜像。

从旧版升级前请读 [v0.5 迁移说明](docs/migration-v0.5.md)；完整变化见
[CHANGELOG.md](CHANGELOG.md)。

## 最短上手：本地 MCP

前置：Python 3.10+。Git 只有在你需要多设备同步时才必需。

```bash
# 1. 从模板创建自己的私有仓库后 clone
git clone <你的私有仓库地址> memory-vault
cd memory-vault

# 2. 建虚拟环境并安装本仓库提供的 MCP 命令
python -m venv .venv
python -m pip install -e .

# 3. 填 _meta/vault_config.yaml，并完善 memories/ 下两个 owner-*.md
```

Windows 若 `python -m pip install -e .` 没装进刚建的环境，可显式运行：

```powershell
.venv\Scripts\python.exe -m pip install -e .
```

然后在支持 stdio MCP 的桌面客户端中添加：

```json
{
  "mcpServers": {
    "memory-vault": {
      "command": "memory-vault-mcp",
      "args": ["--vault", "<memory-vault 的绝对路径>"]
    }
  }
}
```

兼容旧配置：仍可直接把 `python <绝对路径>/_meta/mcp_server.py` 当作 MCP
命令。完整示例见 [`_meta/client_config_example.json`](_meta/client_config_example.json)。

安装后，如果宿主没有预载上下文，让 AI 依次调用 `get_context`、
`get_turn_time`、`get_task_context`。能读到你刚填写的核心记忆和当前任务
快照，就接通了；宿主已经注入预载标记时按下面的节奏跳过重复调用。

### 不 clone 源码，只安装命令

```bash
pipx install git+https://github.com/Irisiochan/memory-vault.git
memory-vault-mcp --vault <你的数据目录>
```

目标目录为空时会自动生成空白 vault；已有 vault 不会被空白核心文件覆盖。

## 一条命令跑本机 HTTP MCP（Docker）

```bash
docker compose up -d
```

- 本机 MCP 地址：`http://127.0.0.1:8900/mcp`
- 私有数据：`./vault-data/`（已 gitignore）
- 默认只绑定电脑本机回环，不直接暴露到局域网或公网
- 停止：`docker compose down`；数据不会随容器删除

> **手机不能使用这个 `127.0.0.1` 地址。** 手机 App 里的 `127.0.0.1`
> 指手机自己，不是运行 Docker 的电脑；默认 `compose.yaml` 也只发布到电脑回环地址。
> Kelivo、RikkaHub 等手机客户端请使用后文的 Tailscale 接入方式，不要直接把 Docker
> 端口改成无认证的 `0.0.0.0` 暴露到局域网或公网。

需要认证时，先设置 `VAULT_TOKEN` 再启动；客户端发送
`Authorization: Bearer <token>` 或 `X-Vault-Token: <token>`。

## MCP 工具

| 类别 | 工具 | 用途 |
|---|---|---|
| 稳定上下文 | `get_context` | 核心记忆全文 + 其余长期记忆索引 |
| 核心上下文 | `get_core_context` | narrative 核心文件或 compact 高优先级事实 |
| 结构化事实 | `write_fact` / `get_facts` | 可追溯写入、版本收敛和筛选读取 |
| 当前状态 | `get_turn_time` / `get_task_context` | 本轮时间 / 分层的未完成任务快照 |
| 检索 | `search_vault` / `read_file` / `get_related` | 搜索、精读、沿链接与标签联想 |
| 长期记忆 | `write_memory` / `update_memory` / `archive_memory` | 写入、修正、软归档 |
| 低置信度 | `write_inbox` / `list_inbox` / `promote_to_memory` | 暂存推测，验证后升级 |
| 日常 | `log_daily` / `write_diary` | 生活流水 / 完整日记与阶段总结 |
| 任务 | `add_task` / `update_task` | 新建待办；完成或放弃时自动归档 |

所有 MCP 写工具在同一服务进程内共享一把可重入写锁，覆盖完整的
“读取 → 修改 → 落盘 → Git 同步”事务，避免多个 HTTP 客户端或 AI 同时更新同一
文件时互相覆盖。多个独立服务进程不要同时写同一个 vault。

Fact 的 `valid_from` / `valid_until` 会参与读取：默认 `get_facts` 和 compact
context 只返回 vault 当天有效的 active facts；`status=all` 仍保留完整版本用于审计。
同 key 写入未来 `valid_from` 时会建立预约切换：当前版本保持到生效日前一天，
到期后读取层自动切换；每个 key 同时最多保留一个待生效预约。

推荐调用节奏：

1. 新任务首轮：有 `<VAULT_CORE_PRELOADED>` 就直接使用；出现
   `<VAULT_CORE_PRELOAD_FALLBACK>` 或没有 core 预载标记时，调用一次
   `get_context`。
2. 每个用户回合：有 `<TURN_TIME_PRELOADED>` 就直接使用；没有时调用一次
   `get_turn_time`。
3. 每个新任务调用一次 `get_task_context`；仅当宿主明确说明任务快照也已预载
   时跳过。跨日、上下文恢复、聊到截止日期或任务发生变化时再刷新。无期限任务
   超过 14 天未更新会折叠到冬眠层，可用 `dormant: true|false` 手动覆盖。
4. 涉及旧项目、人物、偏好或决策时，先搜索再精读。

这套节奏已写进 MCP instructions 和 `_meta/cli/global-agent-workflow.md`。

## 可选 Git 同步

如果 vault 自己是 Git 仓库，MCP 写入会在锁内完成显式提交、pull/rebase 和 push。
如果没有 `.git`，写入只保存在本地，不会向上寻找并误用父目录远端。

同步失败时，工具返回 Git 的真实错误摘要，不会把本地 commit 误报成远端成功。
如果上一次 push 失败，后续写入即使没有产生新差异，也会重试补推尚未同步的 commit。

推荐方式：

- 每台设备 clone 同一个 **private** vault 仓库；
- 为自动写入配置独立、最小权限的 deploy key；
- 原始聊天、媒体、数据库、日志和密钥永远放在 vault 外。

## 手机与远程客户端

Kelivo、RikkaHub 等手机 App 不能连接电脑的 `127.0.0.1`。推荐让电脑和手机
登录同一个 Tailscale 网络，然后在电脑上启动：

```bash
memory-vault-mcp --vault <vault路径> --http
```

服务会优先绑定检测到的 Tailscale IP，默认端口 8900。按照启动输出在手机中配置：

- 传输类型：`streamable-http` / `Streamable HTTP`
- URL：`http://<电脑的Tailscale-IP>:8900/mcp`，例如 `http://100.x.x.x:8900/mcp`
- 自定义 `Accept`：优先留空，让客户端自动生成；若客户端要求手填，使用
  `application/json, text/event-stream`，不能只填 `text/event-stream`
- 认证：服务端设置了 `VAULT_TOKEN` 时，添加
  `Authorization: Bearer <token>` 或 `X-Vault-Token: <token>`

Streamable HTTP 的 POST 请求必须同时接受 JSON 和 event stream，详见
[MCP Transport specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)。

常见报错：

| 现象 | 优先检查 |
|---|---|
| 连接失败 / connection refused | 是否误填 `127.0.0.1`；电脑服务是否仍在运行；两端 Tailscale 是否在线 |
| `401 Unauthorized` | 是否漏填或填错 `VAULT_TOKEN` 请求头 |
| `406 Not Acceptable` / 初始化失败 | 删除自定义 `Accept`，或改为 `application/json, text/event-stream` |
| 能访问但提示 Host 不允许 | URL 是否使用服务实际绑定并打印出的 Tailscale IP |

需要 24 小时在线可部署到 VPS，见
[`_meta/deploy/vps_setup.md`](_meta/deploy/vps_setup.md)。

### ChatGPT 自定义 App

ChatGPT 不会直接启动你电脑上的 stdio MCP；它需要可达的远程 MCP 服务。
截至 2026-07，完整 MCP（含写入）仍属于 Business、Enterprise 与 Edu 的 web beta，
并受管理员、角色和工作区设置控制。个人版或移动端可能看不到同一入口。
请以 OpenAI 当前的
[Developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-apps-and-full-mcp-connectors-in-chatgpt-beta)
与 [Apps in ChatGPT](https://help.openai.com/en/articles/11487775-connectors-in-chatgpt)
说明为准。

优先使用受认证的 HTTPS 或官方 Secure MCP Tunnel；不要把无认证的记忆端点裸露到公网。
仓库保留 Tailscale Funnel + 秘密路径的自托管示例，但它是高级部署方案，URL 必须按密钥保护。

## 目录结构

```text
memories/            确认的长期记忆；core_files 每次新任务注入
tasks/               open / done / dropped 的共享任务账本
inbox/               低置信度暂存，验证后再升级
projects/            创作、项目、脑洞
diary/               日常流水与阶段总结
_archive/retired/    软删除区
_meta/               配置、规则、MCP 服务与部署辅助
template/            MCP 初始化新数据目录时使用的空白模板
memory_vault_mcp/    可安装命令的 Python 包装
```

`_meta/mcp_server.py` 只负责工具注册与入口；上下文、事实、检索、写入、任务和传输逻辑
分别位于 `_meta/vault_*.py`，便于独立测试和演进。

## 隐私边界

- 你创建的真实记忆仓库必须是 **private**。
- 只存蒸馏后的事实、偏好、日常和任务；原始聊天导出放库外本地目录。
- API key、token、cookie、私钥、密码、真实内网地址不进 Markdown、日志或 Git。
- 核心文件只追加，不替换、不归档；不确定内容先进 `inbox/`。
- 公网 MCP 会把工具返回内容交给远端平台处理，开放前先评估数据边界。

## 没有 MCP 的客户端

```bash
python _meta/build_context.py
```

它会生成 `_meta/context_prompt.md`，可粘贴到支持自定义指令的客户端。
这种方式只负责注入；写入仍需客户端直接编辑文件或由其他自动化完成。

## 可选客户端：AI Hub

需要聊天 UI、多联系人、群聊、流式回复或 PC Worker 时，使用独立的
[`ai-hub-public`](https://github.com/Irisiochan/ai-hub-public)。它通过固定版本的
Memory Vault Docker/MCP 依赖运行，不复制维护本仓库源码；只想要记忆基础设施时
完全不需要安装 AI Hub。

## 开发与验证

```bash
python tests/smoke.py
python tests/task_snapshot_tiers.py
python tests/protocol_smoke.py
python tests/http_smoke.py
python tests/concurrent_writes.py
python tests/maintenance_regressions.py
python tests/repository_boundary.py
python -m build
docker build -t memory-vault-mcp .
```

测试使用临时 vault，覆盖初始化、本地写入、记忆升级、分离上下文、任务冬眠分层、
旧 vault 兼容、路径穿越防护、仓库边界，以及真实 stdio / streamable-http MCP
握手，不会触碰你的真实数据。

## License

MIT
