# Migrating to Memory Vault v0.5

Memory Vault v0.5 makes the repository boundary explicit: this repository is
the reusable memory system, while chat interfaces are separate clients.

## Breaking removals

The following v0.4.1 paths are no longer present:

- `app/`: the legacy Node/React chat frontend and PC Worker
- `easy/`: the Windows launcher for that frontend
- `.github/workflows/build-windows-portable.yml`: the frontend portable build

The `Memory-Vault-Windows-x64.zip` frontend bundle is therefore not produced by
v0.5 releases. The old implementation and release artifact remain available
from the
[`v0.4.1` release](https://github.com/Irisiochan/memory-vault/releases/tag/v0.4.1)
and Git history.

## Before upgrading

If you edited anything under `app/` or `easy/`, copy or commit those changes in
a separate repository before switching to v0.5. A normal pull may otherwise
report delete/modify conflicts.

Private vault data should already live outside these paths. The Markdown vault
format, `_meta/` scripts, `memory-vault-mcp` command, Docker service, and MCP
tool contract remain compatible.

## Choose a client

- For Memory Vault alone, continue with `memory-vault-mcp`, `pipx`, or
  `docker compose` as documented in the main README.
- For a chat UI, contacts, groups, or PC Workers, use
  [AI Hub](https://github.com/Irisiochan/ai-hub-public). AI Hub consumes a
  versioned Memory Vault release over MCP instead of embedding this source tree.

When testing AI Hub against this release, set:

```dotenv
MEMORY_VAULT_VERSION=v0.5.0
```

Then rebuild its Compose service and run its Memory Vault contract smoke test.
