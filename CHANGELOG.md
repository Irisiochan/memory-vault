# Changelog

## 0.4.1 - 2026-07-21

- Protect Hub contacts, messages, jobs, Worker pairing, and SSE with a Hub admin
  token while retaining per-device Worker bearer tokens.
- Refuse non-loopback Hub binds unless `HUB_ADMIN_TOKEN` is configured; provide
  an HttpOnly, SameSite=Strict browser session and direct bearer authentication.
- Add Hub authentication regression tests and run MCP, Hub, Web, Worker, audit,
  and packaging checks on main pushes and pull requests.
- Bound Python dependencies, switch package metadata to SPDX `MIT`, and replace
  personalized Worker examples with neutral placeholders.
- Clarify the repository boundary: Memory Vault focuses on the memory system;
  new UI and Hub product work belongs in the separate AI Hub repository.

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
