# Changelog

## 0.4.0 - 2026-07-21

- Split stable identity context from per-turn time and task snapshots with
  `get_context`, `get_turn_time`, and `get_task_context`.
- Add the compact `get_core_context` tool for token-sensitive gateways.
- Harden generated paths against traversal and isolate Git synchronization to
  the vault's own `.git` directory.
- Allow local-only operation without Git; initialize a blank vault when
  `MEMORY_VAULT_PATH` points to a new directory.
- Add shared `worker-tail` and `deploy-tail` ledger conventions.
- Add cross-CLI `AGENTS.md`/`CLAUDE.md` bootstraps and a shared workflow.
- Add an installable `memory-vault-mcp` command, Docker image, Compose service,
  and smoke tests.
- Clarify that raw chat exports and secrets must stay outside the vault.
