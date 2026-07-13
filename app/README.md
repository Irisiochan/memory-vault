# Memory Vault Hub

随 Memory Vault 一起提供的自托管聊天前端。它把多个 AI 联系人、共享记忆和聊天历史放进一个 IM 风格界面。

> 这里是保留 CLI 后端与源码调试能力的开发者版。Windows 用户如果只想下载即用，请到仓库 [Releases](https://github.com/Irisiochan/memory-vault/releases) 获取便携版。

## 已支持

- Claude CLI、Codex app-server、Anthropic API、OpenAI-compatible API
- 多联系人和点名式群聊
- SSE 流式回复、thinking 折叠、消息编辑/重生成/删除（含 thinking）
- SQLite 聊天历史和联系人配置
- 每次会话自动注入 `get_context`，每轮按关键词检索记忆
- 明显的计划、偏好和人生事件自动暂存到 vault inbox
- 共享记忆身份隔离：共享知识不会被模型冒认成自己的经历
- 联系人模型切换：Codex 动态读取当前账号模型目录，切换后安全重建底层会话
- 离线优先 PC Worker：VPS 持久队列、本机主动认领、过程可视化、暂停/取消/人工恢复

## 本地启动

前置：Node.js 20+、Python 3.10+。按仓库根目录 README 先安装 Memory Vault 的 Python 依赖。

```bash
# 终端 1：启动记忆库 HTTP MCP
python _meta/mcp_server.py --http --host 127.0.0.1 --port 8900

# 终端 2：后端
cd app/server
cp config.example.json config.json
npm install
npm run dev

# 终端 3：前端
cd app/web
npm install
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`。默认联系人使用 Claude CLI；若没安装或没登录 Claude CLI，可在联系人设置中新建 API 联系人。

## 单进程生产构建

```bash
cd app/web && npm install && npm run build
cd ../server && npm install && npm run build && npm start
```

默认只监听 `127.0.0.1:3900`。需要局域网或 Tailscale 访问时，在 `app/server/config.json` 修改 `host`；不要把未认证的服务直接暴露到公网。

## 配置

复制 `server/config.example.json` 为 `server/config.json`。常用字段：

- `memory.mcpUrl`：Memory Vault 的 streamable HTTP MCP 地址
- `memory.capture`：是否把高价值对话自动暂存到 inbox
- `memory.searchPerTurn`：是否逐轮检索相关记忆
- `memory.sessionMaxAgeHours`：刷新共享记忆快照的最长会话时间
- `claude.cliPath` / `codex.cliPath`：本机 CLI 路径

若 MCP 设置了 `VAULT_TOKEN`，启动 Hub 时也要设置同名环境变量。

## PC Worker

PC Worker 适合“Hub 在 VPS、代码和 CLI 在个人电脑”的场景。PC 只主动发出长轮询连接，
不需要开放入站端口；离线时任务留在 VPS，运行中失联会中断并等待人工决定，不会盲目重跑。

1. 前端左上角点 `🖥`，生成一次性设备令牌。
2. 复制 `worker/config.example.json` 为 `worker/config.json`，填令牌和 workspace allowlist。
3. 运行 `node worker/worker.mjs worker/config.json`；Windows 可运行 `worker/install-startup.ps1` 安装登录自启。

Shell、SSH、读写和 workspace 都按 Worker 能力与每条任务的权限快照匹配。Codex 的文件工具本身
依赖 Shell，因此 Codex 任务必须显式开启 Shell；`write=false` 时仍由 Codex read-only sandbox 禁止写入。

## 安全说明

- API key 和聊天历史保存在本机 `app/server/data/hub.db`，该目录已被 gitignore。
- `config.json`、agent 工作目录、构建产物均不会进入 git。
- `worker/config.json` 和本机断线补传状态不会进入 git；VPS 仅保存设备令牌哈希。
- 默认没有登录层，只适合 localhost、可信局域网或 Tailscale。
- 开源仓库是模板；真正填入个人记忆后，请使用 private 仓库。

## 便携版如何构建

`.github/workflows/build-windows-portable.yml` 会在版本标签或手动运行时自动构建 Windows x64 ZIP，内置 Node.js、Python、MCP 依赖和生产版前后端。便携版默认只开放 API 联系人；Claude/Codex CLI 能力仍保留在本开发者版中。
