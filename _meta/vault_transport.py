"""Stdio and secured streamable-HTTP transport helpers."""

from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import subprocess
import sys


def detect_tailscale_ip() -> str | None:
    """Detect an IPv4 address in the Tailscale CGNAT range."""
    tailnet = ipaddress.ip_network("100.64.0.0/10")
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            address = result.stdout.strip().splitlines()[0].strip()
            if address and ipaddress.ip_address(address) in tailnet:
                return address
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except socket.gaierror:
        return None
    for info in infos:
        address = info[4][0]
        try:
            if ipaddress.ip_address(address) in tailnet:
                return address
        except ValueError:
            continue
    return None


def run_http(mcp, host: str | None, port: int, path: str = "/mcp") -> None:
    host = host or detect_tailscale_ip()
    if not host:
        sys.exit(
            "未检测到 Tailscale IP（100.x 段）。请先启动 Tailscale，"
            "或用 --host 手动指定绑定地址。（为安全起见不会默认绑定 0.0.0.0）"
        )
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = path
    from mcp.server.transport_security import TransportSecuritySettings

    if ipaddress.ip_address(host).is_loopback:
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )
    else:
        extra_hosts = [
            item.strip()
            for item in os.environ.get("VAULT_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        ]
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{host}:*", "localhost:*", "127.0.0.1:*", *extra_hosts],
            allowed_origins=[
                f"http://{host}:*",
                "http://localhost:*",
                "http://127.0.0.1:*",
            ],
        )

    token = os.environ.get("VAULT_TOKEN")
    if token:
        import uvicorn
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import PlainTextResponse

        class TokenAuth(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                authorized = (
                    request.headers.get("authorization") == f"Bearer {token}"
                    or request.headers.get("x-vault-token") == token
                )
                if not authorized:
                    return PlainTextResponse("unauthorized", status_code=401)
                return await call_next(request)

        app = mcp.streamable_http_app()
        app.add_middleware(TokenAuth)
        print(f"Memory Vault MCP (HTTP + token): http://{host}:{port}{path}")
        uvicorn.run(app, host=host, port=port)
        return
    print(f"Memory Vault MCP (HTTP): http://{host}:{port}{path}")
    mcp.run(transport="streamable-http")


def main(mcp, argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Memory Vault MCP Server")
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument("--path", default="/mcp")
    args = parser.parse_args(argv)
    if args.http:
        run_http(mcp, args.host, args.port, args.path)
    else:
        mcp.run()
