@echo off
REM 记忆库 MCP Server —— HTTP 模式（供 Tailscale 网络内的手机端连接）
REM 双击运行即可；开机自启可用任务计划程序指向本文件
REM 可选共享密钥：先 set VAULT_TOKEN=你的密钥 再启动

cd /d "%~dp0.."
python _meta\mcp_server.py --http
pause
