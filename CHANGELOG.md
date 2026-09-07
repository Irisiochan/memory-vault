# Changelog

## Unreleased

## 0.8.0 - 2026-09-07

- Serialize every vault operation across server processes, not only threads:
  a reentrant OS file lock (`.vault-operation.lock` in the vault root) now
  guards the full read-modify-write-sync cycle, so a Docker HTTP instance and
  per-CLI stdio instances can safely share one vault. The OS releases the lock
  automatically if a server dies, so a crash cannot leave the vault stuck.
- Route fact-domain read-modify-write through the same cross-process lock
  instead of a separate in-process lock.
- Detach Git subprocesses from the server's stdin so a Git prompt can never
  swallow bytes from the live stdio MCP pipe.
- Run `git add` / `git ls-files` with `--literal-pathspecs`, and tolerate a
  retried archive whose source deletion was already committed: the sync still
  retries the pending push instead of failing on the missing file.
- Harden `read_file` path validation: reject backslashes, drive colons, hidden
  path segments and symlinked components, and preserve the caller's vault-root
  spelling after validating the canonical target so relative paths stay
  comparable on Windows.
- Add a cross-process lock test suite (`tests/vault_lock.py`) covering lost
  read-modify-write updates, nested reentrancy, crash recovery and lock-file
  acquisition failures.

## 0.7.1 - 2026-08-25

- Let the MCP `update_task` tool atomically change or clear `due` while it
  records the task note and status update.
- Return a structured `ok` / `code` / `message` / `data` result from the MCP
  task-update contract so callers cannot mistake ordinary error text for a
  successful write.
- Add a real stdio MCP write-read contract check for rescheduling and
  structured not-found handling.

## 0.7.0 - 2026-08-14

- Pull before checking existence in `read_file` and `get_related`, so a path
  created by another device is visible on its first direct read.
- Collapse no-due tasks that have not changed for more than 14 days into a
  title-only dormant summary in `get_task_context`; allow explicit
  `dormant: true|false` frontmatter overrides.
- Rank multi-term AND search matches deterministically by title, headings, body
  evidence, and vault-directory priority; return the best bounded snippet.
- Let `log_daily` backfill a past vault-local date and optional `HH:MM` time,
  while rejecting future dates and malformed timestamps.
- Report concrete Git add/commit/pull/push failures and retry previously
  unpushed commits on a later write even when that write creates no new diff.
- Clarify that phone clients such as Kelivo and RikkaHub cannot use the
  computer's `127.0.0.1`, document Tailscale URLs, Streamable HTTP `Accept`
  requirements, token errors, and Docker's loopback-only default.

- Make MCP instructions and context-tool descriptions aware of host-injected
  core and turn-time preload markers, avoiding duplicate context calls.
- Keep task snapshots independent from core preload state and document the
  exact cadence in the reusable templates and README.
- Serialize every MCP mutation across its full read-modify-write and Git-sync
  cycle so concurrent clients cannot overwrite one another's updates.
- Apply `valid_from` and `valid_until` to default active fact reads and compact
  context while retaining `status=all` as the unfiltered audit view.
- Align `_meta/rules.md` and bundled templates with preload-aware reads and
  structured-fact routing.
- Keep loopback HTTP smoke tests off ambient SOCKS/HTTP proxies by creating the
  local test client with `trust_env=False`.
- Support one scheduled future successor per fact key: keep the current value
  effective through the previous day, then switch reads automatically on
  `valid_from` without a background job.

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
