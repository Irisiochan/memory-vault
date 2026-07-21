# Memory Vault — 多 AI 共享长期记忆

给 Claude、Codex、ChatGPT、自建 Agent 和手机端客户端一套共用记忆：
Markdown 存储、Obsidian 可读、MCP 读写、可选 Git 多设备同步。

仓库本身是空白模板，不包含作者的私人记忆。请用 **Use this template**
创建你自己的 **private repository**。

## v0.4.1 有什么新东西

- **Hub 远程访问默认安全**：非回环地址启动时强制要求 `HUB_ADMIN_TOKEN`；
  管理 API、Worker 配对、任务和 SSE 都需要认证，浏览器使用 HttpOnly 会话 Cookie。
- **发布链路补齐**：main/PR 自动执行 MCP、Hub、Web、Worker 和打包检查，Python
  依赖均有版本范围。

- **真正分离的上下文**：稳定身份用 `get_context`，每轮时间用
  `get_turn_time`，任务快照用 `get_task_context`；长会话不再抱着旧日期和旧待办。
- **更省 token**：网关可调用 `get_core_context` 只取核心文件，并限制每个文件长度。
- **路径与 Git 隔离**：所有生成路径都校验；只有 vault 自己有 `.git` 时才同步，
  不会误提交到父级源码仓库。
- **无 Git 也能用**：本地写入永远成立；Git 只是可选同步层。
- **共享任务账本**：加入 `worker-tail` / `deploy-tail`，让另一台机器或另一个 AI
  能从真实 Git 状态续接未完成工作。
- **跨 CLI 规则**：`AGENTS.md` 与 `CLAUDE.md` 共用一份工作流，避免不同客户端各写一套。
- **MCP 已封装**：提供 `memory-vault-mcp` 命令、Docker/Compose 和冒烟测试。

完整变化见 [CHANGELOG.md](CHANGELOG.md)。

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

安装后让 AI 依次调用 `get_context`、`get_turn_time`、`get_task_context`。
能读到你刚填写的核心记忆和当前任务快照，就接通了。

### 不 clone 源码，只安装命令

```bash
pipx install git+https://github.com/Irisiochan/memory-vault.git
memory-vault-mcp --vault <你的数据目录>
```

目标目录为空时会自动生成空白 vault；已有 vault 不会被空白核心文件覆盖。

## 一条命令跑 HTTP MCP（Docker）

```bash
docker compose up -d
```

- MCP 地址：`http://127.0.0.1:8900/mcp`
- 私有数据：`./vault-data/`（已 gitignore）
- 默认只绑定本机回环，不直接暴露到局域网或公网
- 停止：`docker compose down`；数据不会随容器删除

需要认证时，先设置 `VAULT_TOKEN` 再启动；客户端发送
`Authorization: Bearer <token>` 或 `X-Vault-Token: <token>`。

## MCP 工具

| 类别 | 工具 | 用途 |
|---|---|---|
| 稳定上下文 | `get_context` | 核心记忆全文 + 其余长期记忆索引 |
| 紧凑上下文 | `get_core_context` | 限长读取核心文件，适合网关注入 |
| 当前状态 | `get_turn_time` / `get_task_context` | 本轮时间 / 未完成任务快照 |
| 检索 | `search_vault` / `read_file` / `get_related` | 搜索、精读、沿链接与标签联想 |
| 长期记忆 | `write_memory` / `update_memory` / `archive_memory` | 写入、修正、软归档 |
| 低置信度 | `write_inbox` / `list_inbox` / `promote_to_memory` | 暂存推测，验证后升级 |
| 日常 | `log_daily` / `write_diary` | 生活流水 / 完整日记与阶段总结 |
| 任务 | `add_task` / `update_task` | 新建、完成或放弃待办 |

推荐调用节奏：

1. 新任务首轮：`get_context` + `get_turn_time` + `get_task_context`。
2. 后续每轮：只刷新 `get_turn_time`。
3. 跨日、上下文恢复、聊到截止日期或任务发生变化时，再刷新 `get_task_context`。
4. 涉及旧项目、人物、偏好或决策时，先搜索再精读。

这套节奏已写进 MCP instructions 和 `_meta/cli/global-agent-workflow.md`。

## 可选 Git 同步

如果 vault 自己是 Git 仓库，MCP 写入会在锁内完成 pull/rebase、显式提交和 push。
如果没有 `.git`，写入只保存在本地，不会向上寻找并误用父目录远端。

推荐方式：

- 每台设备 clone 同一个 **private** vault 仓库；
- 为自动写入配置独立、最小权限的 deploy key；
- 原始聊天、媒体、数据库、日志和密钥永远放在 vault 外。

## 手机与远程客户端

电脑和手机在同一 Tailscale 网络时：

```bash
memory-vault-mcp --vault <vault路径> --http
```

服务会优先绑定检测到的 Tailscale IP，默认端口 8900；客户端使用
`http://<Tailscale-IP>:8900/mcp`，传输类型选 `streamable-http`。

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
app/                 兼容保留的自托管聊天前端（新产品功能归 ai-hub-public）
```

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

## 可选聊天前端

[`app/`](app/README.md) 是兼容保留的开发者预览：多 AI 联系人、群聊、流式回复、
消息管理、记忆注入和 PC Worker。Memory Vault 后续聚焦记忆系统、MCP 和安全同步；
新的前端/Hub 产品能力归独立的
[`ai-hub-public`](https://github.com/Irisiochan/ai-hub-public) 仓库。只想要记忆库时完全不需要运行它。

## 开发与验证

```bash
python tests/smoke.py
python tests/protocol_smoke.py
python tests/http_smoke.py
python -m build
docker build -t memory-vault-mcp .
```

冒烟测试使用临时 vault，覆盖初始化、本地写入、记忆升级、分离上下文、
旧 vault 兼容、路径穿越防护，以及真实 stdio / streamable-http MCP 握手，
不会触碰你的真实数据。

## License

MIT
