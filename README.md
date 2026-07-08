# Memory Vault — 多 AI 共享记忆库模板

给你的所有 AI（Claude / ChatGPT / 手机端 App / Claude Code…）一个共用的长期记忆：
纯 Markdown + git 存储，Obsidian 可视化，MCP 协议接入，全平台自动同步。

**它能做什么：**

- 🧠 **自动注入**：每次新会话 AI 先调 `get_context`，直接"记得你是谁"
- ✍️ **自主读写**：AI 自己写记忆、改记忆、归档过时内容，不用你审批（git 历史兜底，改错随时回滚）
- 📅 **记住日常**：生活流水按天记进 `diary/`，最近几天自动带进上下文
- ⏰ **时间感知**：带截止日期的事进 `tasks/`，过期了 AI 会主动问你办完没
- 🔗 **联想**：记忆之间用 `[[wikilink]]` 互连，`get_related` 顺藤摸瓜
- 🔄 **全平台同步**：写入即 git push，读取自动 pull，多设备/多 AI 并发不打架

## 快速开始（10 分钟，纯本地）

前置：Python 3.10+、git。

```bash
# 1. 用这个模板建你自己的【私有】仓库（记忆是隐私，别用公开仓库！），clone 到本地
git clone <你的私有仓库地址> memory-vault && cd memory-vault

# 2. 装依赖
pip install "mcp[cli]" pyyaml

# 3. 个性化：填 _meta/vault_config.yaml（你的名字、时区），
#    再把 memories/ 里两个 owner-*.md 模板填成你自己的
```

然后把 MCP 接给桌面 AI（以 Claude Desktop 为例，配置文件 `%APPDATA%\Claude\claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "memory-vault": {
      "command": "python",
      "args": ["<绝对路径>/memory-vault/_meta/mcp_server.py"]
    }
  }
}
```

重启客户端，对 AI 说"调一下 get_context"——它能背出你写的核心记忆就通了。

## 进阶一：手机端（Tailscale 内网）

手机 App（如 RikkaHub）接 MCP 需要 HTTP 端点：

1. 电脑和手机装 [Tailscale](https://tailscale.com)，登录同一账号
2. 电脑上跑 `python _meta/mcp_server.py --http`（自动绑定 Tailscale IP，端口 8900）
3. 手机 App 添加 MCP：类型 streamable-http，URL `http://<电脑的Tailscale-IP>:8900/mcp`

想 24 小时在线？把整套部署到 VPS，见 `_meta/deploy/vps_setup.md`。

## 进阶二：ChatGPT（公网入口）

ChatGPT 连接器从 OpenAI 服务器发起连接，进不了内网，且强制 HTTPS。
需要 VPS + `tailscale funnel` + 秘密路径，详见 `_meta/deploy/vps_setup.md` 的"公网入口"一节。

⚠️ 读完那节的安全模型再开：URL 即密钥，且 OpenAI 会读到工具返回的记忆内容。

## 目录结构

```
memories/     确认的长期记忆（core_files 每次会话自动注入）
tasks/        有时间节点的事项（自动到期提醒）
inbox/        低置信度暂存（AI 拿不准的先放这，验证后自己升级）
projects/     创作、项目、脑洞
diary/        日常流水（YYYY-MM-DD.md）
_archive/     软删除区 + 归档
_meta/        配置、规则、MCP 服务端
```

写入规则和 AI 行为约定见 `_meta/rules.md`（AI 通过 MCP instructions 自动获得这套规则）。

## 隐私须知（认真读）

- **仓库必须是 private**——这是你的人生记录
- **原始聊天记录不要进库**：库里只放蒸馏后的记忆；原文备份放库外本地文件夹。原文进 git 历史后删除需要重写历史，很麻烦
- 公网入口（funnel）是可选的，开了就意味着"URL 泄露 = 记忆库泄露"，并且经由第三方（OpenAI）中转
- 照片视频默认被 .gitignore 挡住，不会上云

## 无 MCP 的前端

跑 `python _meta/build_context.py` 生成 `_meta/context_prompt.md`，
粘贴到 ChatGPT 自定义指令 / Gemini 等任何支持自定义提示词的地方（半自动，写入靠口述转达）。

## Claude Code 用户

仓库根目录的 `CLAUDE.md` 已包含自动读写规则——在库目录里启动 Claude Code 即自动生效，无需 MCP。

## License

MIT
