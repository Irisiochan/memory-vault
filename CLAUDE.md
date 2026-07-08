# 记忆库使用规则（Claude Code 专用）

这个仓库是主人的 AI 共享记忆库。你没有 MCP 也能直接读写文件，规则如下。

## 自动注入（读）— 每次新对话第一轮回复前必须执行

1. 读 `_meta/vault_config.yaml` 拿主人名字和核心文件列表，读取核心记忆文件
2. 扫 `tasks/` 里 status: open 的事项，对照今天日期——过期了主动问主人完成没
3. 看 `diary/` 最近几天的日常流水
4. 按话题搜索其他相关记忆，顺 `[[链接]]` 联想

## 自动写入（写）— 自主模式，不需要主人审批

按 `_meta/rules.md` 的分流表：

- 确认的事实/偏好 → `memories/{slug}.md`
- 拿不准的推测 → `inbox/YYYY-MM-DD_{slug}.md`
- 日常发生的事 → 追加 `diary/YYYY-MM-DD.md`，格式 `- **HH:MM** [claude] 内容`
- 有时间节点的事 → `tasks/{slug}.md`（due + status: open）
- 修正 → 直接改原文件；作废 → 移到 `_archive/retired/`，不硬删除
- 核心身份文件只追加不重写

写完运行：`git pull --rebase && git add -A && git commit -m "auto: 写入记忆 <slug>" && git push`

## 不写入的情况

- 一次性情绪、玩笑（值得记的生活片段进 diary 除外）
- 已存在的重复信息（先搜再写）
- 无长期价值的临时调试细节
