# VPS 部署笔记

把记忆库 MCP 服务跑在 VPS 上（24 小时在线），手机端等远程前端连它。
GitHub 私有仓库是同步中枢：VPS 克隆写入即 push，本地克隆读取时自动 pull。

## 架构

```
手机 App ──HTTP(tailnet)──> VPS (/opt/memory-vault, systemd 常驻)
ChatGPT ──HTTPS(funnel)──┘      │ git push/pull
                            GitHub 私有仓库（同步中枢）
                                │ git push/pull
桌面端 stdio / Obsidian ──> 本地电脑克隆
```

## 部署步骤（Ubuntu/Debian，需 Python ≥3.10）

```bash
# 1. 依赖
apt-get update && apt-get install -y python3-venv git
python3 -m venv /opt/memory-vault-env
/opt/memory-vault-env/bin/pip install "mcp[cli]" pyyaml

# 2. 部署密钥（公钥加到 GitHub 仓库 → Settings → Deploy keys，勾 Allow write access）
ssh-keygen -t ed25519 -N "" -f /root/.ssh/vault_deploy -C "vps-vault"
cat /root/.ssh/vault_deploy.pub
printf 'Host github.com\n  IdentityFile /root/.ssh/vault_deploy\n' >> /root/.ssh/config

# 3. 克隆（换成你的仓库）
git clone git@github.com:<你的用户名>/<你的仓库>.git /opt/memory-vault
cd /opt/memory-vault
git config user.name "vps"
git config user.email "<你的邮箱>"

# 4. Tailscale（手机端内网访问用）
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up

# 5. systemd 服务
cp _meta/deploy/memory-vault-mcp.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now memory-vault-mcp
systemctl status memory-vault-mcp
```

验证：`curl -s -X POST http://$(tailscale ip -4):8900/mcp -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'`

手机 App：添加 MCP，类型 streamable-http，URL `http://<VPS的Tailscale-IP>:8900/mcp`。

## 公网入口（ChatGPT 连接器专用，可选）

ChatGPT 的 MCP 连接器从 OpenAI 服务器发起连接，进不了 tailnet，且强制 HTTPS——
为它单独跑一个绑 127.0.0.1 的实例，秘密路径当密码，tailscale funnel 出公网：

```bash
# 1. 生成秘密路径（不要提交进 git！只存在 systemd unit 里）
SECRET=$(head -c16 /dev/urandom | xxd -p)

# 2. 第二个 systemd 实例：复制 memory-vault-mcp.service 为
#    memory-vault-mcp-public.service，ExecStart 改为：
#    ... mcp_server.py --http --host 127.0.0.1 --port 8901 --path /mcp-$SECRET/mcp
systemctl enable --now memory-vault-mcp-public

# 3. funnel 挂根路径（首次需按提示到管理台启用 Funnel；配置持久，重启不丢）
tailscale funnel --bg http://127.0.0.1:8901
tailscale funnel status
```

ChatGPT 连接器 URL：`https://<节点>.<tailnet>.ts.net/mcp-<SECRET>/mcp`，身份验证选"未授权"。

**安全模型，开之前想清楚**：

- 整个互联网都能碰到这个域名，唯一的门是路径里的随机串——**URL 即密钥，绝不外传**
- 怀疑泄露就换 SECRET 重启 public 实例，旧 URL 立即作废
- OpenAI 会代理并读到工具返回的全部记忆内容，这是接 ChatGPT 的固有代价
- tailnet 内的 8900 实例不受影响

## 运维

- 更新代码：`cd /opt/memory-vault && git pull && systemctl restart memory-vault-mcp`
- 看日志：`journalctl -u memory-vault-mcp -f`
- 完整备份：`git bundle create backup.bundle --all`
