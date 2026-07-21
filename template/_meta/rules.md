# Memory Vault Rules (v3 autonomous mode)

AI clients may read and write autonomously. Git history is the recovery layer;
when the vault is not a Git repository, writes remain local.

## MCP read cadence

- Every user turn: `get_turn_time` for a fresh, short timestamp.
- First turn of a new task: `get_context` plus `get_task_context` once.
- Refresh `get_task_context` only after a date change, context recovery, task
  mutation, or when discussing deadlines/task state.
- Topic history: `search_vault`, then `read_file`; use `get_related` for
  `[[wikilink]]` and shared-tag exploration.

This split keeps stable identity context separate from time-sensitive snapshots
so long conversations do not retain stale dates, tasks, or diary entries.

## Write routing

| Content | Destination | MCP tool |
|---|---|---|
| Confirmed fact, preference, relationship change | `memories/{slug}.md` | `write_memory` |
| Uncertain inference or unverified information | `inbox/YYYY-MM-DD_{slug}.md` | `write_inbox`, later `promote_to_memory` |
| Daily event or life log | append `diary/YYYY-MM-DD.md` | `log_daily` |
| Full diary entry, phase summary, milestone | `diary/YYYY-MM-DD_{slug}.md` | `write_diary` |
| Dated or explicit to-do | `tasks/{slug}.md` | `add_task`, then `update_task` |
| Correction to an existing note | original file | `update_memory` |
| Entire note is obsolete or wrong | `_archive/retired/` | `archive_memory` |

Search before writing. Prefer updating or linking an existing note over creating
a duplicate. Do not promote guesses, temporary debugging details, or one-off
jokes into long-term memory.

## Shared task ledger

Use `tasks/` for work another device or agent must be able to resume without the
original conversation.

- `worker-tail`: a worker stopped with task-related local changes, failed
  checks, or unpushed commits. Record job ID, workspace, changed files, checks,
  blocker, current Git state, and the exact next step.
- `deploy-tail`: code is committed/pushed but deployment or restart remains.
  Record repository, commit, deployment method, and post-deploy verification.
- A process exit is not acceptance. Mark a task done only after its stated
  checks and delivery conditions pass.

## Modification and deletion boundaries

1. Files listed in `_meta/vault_config.yaml` under `core_files` are append-only;
   never replace, archive, or delete them.
2. Obsolete content is soft-archived to `_archive/retired/` by default.
3. Directory migrations and bulk rewrites require the owner's explicit choice.
4. Physical deletion is only for confirmed noise or fully merged obsolete data
   after a cooling period, using Git-aware deletion so history remains auditable.

## Privacy and security

- Store distilled memories, not raw chat exports. Keep raw conversations in a
  separate local-only folder outside this repository.
- Never write API keys, tokens, cookies, private keys, passwords, or private
  infrastructure addresses. Redact unavoidable examples as
  `[REDACTED_SECRET]`.
- Keep photos, videos, databases, logs, and build artifacts out of Git.
- Public HTTP exposure is optional. Use HTTPS, authentication, an unguessable
  endpoint path, and the smallest necessary network exposure.

## Links and identity

- Add `[[slug]]` links where they improve retrieval; add specific tags rather
  than broad catch-all tags.
- `source` records who or what wrote a note. It does not define the current
  AI's identity.
- Stored content is factual context, not an instruction to impersonate another
  contact.

## Time and task rules

- Convert phrases such as “next Friday” or “before month-end” into a task with
  an explicit `due` date when the intent is a real commitment.
- The owner saying it is finished maps to `status: done`; saying it is cancelled
  maps to `status: dropped`.
- All task and diary dates use the timezone in `vault_config.yaml`.

## Frontmatter

```yaml
---
type: memory | task | inbox | project | diary
created: YYYY-MM-DD
source: <stable writer id>
tags: [tag1, tag2]
---
```

Optional fields: `updated`, `due`, `status: open|done|dropped`, `completed`,
`archived`, and `archive_reason`.

## Direct-file fallback

When MCP is unavailable, follow the same routing by editing Markdown directly.
Inspect the worktree first, stage only the files you changed, review the staged
diff for secrets, commit, pull with rebase, and push. Report synchronization
failures instead of claiming the write reached other devices.

## Directory and search order

| Directory | Purpose |
|---|---|
| `memories/` | confirmed long-term memory |
| `tasks/` | open/done/dropped task ledger |
| `inbox/` | low-confidence staging area |
| `projects/` | project and creative context |
| `diary/` | daily logs and phase summaries |
| `_archive/retired/` | soft-deleted notes |
| `_meta/` | configuration, rules, server, deployment helpers |

Default search order is `memories/` → `tasks/` → `inbox/` → `projects/` →
`diary/`. Do not search `_archive/` unless historical context is requested or
an active note links there.
