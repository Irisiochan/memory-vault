# Memory Vault Hub

随 Memory Vault 一起提供的自托管聊天前端。它把多个 AI 联系人、共享记忆和聊天历史放进一个 IM 风格界面。

## 已支持

- Claude CLI、Codex app-server、Anthropic API、OpenAI-compatible API
- 多联系人和点名式群聊
- SSE 流式回复、thinking 折叠、消息编辑/重生成/删除（含 thinking）
- SQLite 聊天历史和联系人配置
- 每次会话自动注入 `get_context`，每轮按关键词检索记忆
- 明显的计划、偏好和人生事件自动暂存到 vault inbox
- 共享记忆身份隔离：共享知识不会被模型冒认成自己的经历

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

## 安全说明

- API key 和聊天历史保存在本机 `app/server/data/hub.db`，该目录已被 gitignore。
- `config.json`、agent 工作目录、构建产物均不会进入 git。
- 默认没有登录层，只适合 localhost、可信局域网或 Tailscale。
- 开源仓库是模板；真正填入个人记忆后，请使用 private 仓库。
