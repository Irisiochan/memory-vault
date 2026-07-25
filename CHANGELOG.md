# Changelog

## 0.6.0 - 2026-07-25

- Split the MCP implementation into focused runtime, context, fact, search,
  write, task, and transport modules; keep `mcp_server.py` as a 101-line
  compatibility entrypoint.
- Add `write_fact` and `get_facts` with source requirements, idempotent writes,
  active-version convergence, compact context, and guarded fact-domain files.
- Preserve AI Hub `hub-auto` inbox provenance when promoting captures or
  converting them into tasks.
- Add protocol, HTTP, fact-layer, lifecycle, module-boundary, template-sync,
  repository-boundary, and wheel packaging coverage.

## 0.5.1 - 2026-07-25

- Make `update_task(..., status="done" | "dropped")` move the task into
  `_archive/retired/` in the same automatic Git commit.
- Preserve the original filename; on collision, append `-YYYYMMDD` and then a
  numeric suffix if needed.
- Add `archived: YYYY-MM-DD` frontmatter and regression coverage for active
  task removal, task-context exclusion, collisions, and staged Git paths.

## 0.5.0 - 2026-07-24

- Remove the legacy `app/` chat frontend, `easy/` Windows launcher, and portable
  frontend build workflow from the Memory Vault repository.
- Keep Memory Vault focused on the Markdown vault contract, installable MCP
  server/CLI, Docker image, templates, synchronization rules, and tests.
- Point users who need a chat UI, contacts, groups, or PC Workers to the
  separately versioned
  [AI Hub](https://github.com/Irisiochan/ai-hub-public) client.
- Add a repository-boundary regression test and a dedicated Python/Docker
  quality workflow.
- Document the breaking upgrade path in
  [docs/migration-v0.5.md](docs/migration-v0.5.md). The removed frontend remains
  available in the `v0.4.1` tag and Git history.

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
