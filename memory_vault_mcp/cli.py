from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    """Launch the server against an explicit vault or the current directory."""
    parser = argparse.ArgumentParser(description="Memory Vault MCP Server")
    parser.add_argument(
        "--vault",
        default=os.environ.get("MEMORY_VAULT_PATH"),
        help="vault data directory (default: current directory or MEMORY_VAULT_PATH)",
    )
    parser.add_argument("--http", action="store_true", help="use streamable HTTP instead of stdio")
    parser.add_argument("--host", default=None, help="HTTP bind address (default: detected Tailscale IP)")
    parser.add_argument("--port", type=int, default=8900, help="HTTP port (default: 8900)")
    parser.add_argument("--path", default="/mcp", help="MCP endpoint path (default: /mcp)")
    args = parser.parse_args(argv)

    vault = Path(args.vault).expanduser() if args.vault else Path.cwd()
    os.environ["MEMORY_VAULT_PATH"] = str(vault.resolve())

    # Import only after the vault path is fixed: the server initializes its
    # configuration and blank template at import time.
    from _meta.mcp_server import main as run_server

    server_args: list[str] = []
    if args.http:
        server_args.append("--http")
    if args.host:
        server_args.extend(["--host", args.host])
    server_args.extend(["--port", str(args.port), "--path", args.path])
    run_server(server_args)


if __name__ == "__main__":
    main()
