import crypto from 'node:crypto';
import path from 'node:path';
import { Router, type Request } from 'express';
import type { Db, JobRow, WorkerRow } from '../db.js';
import type { SseHub } from '../sse.js';

type Capabilities = {
  runners?: string[];
  workspaces?: string[];
  shell?: boolean;
  ssh?: boolean;
};

const ACTIVE = new Set(['claimed', 'running', 'pause_requested', 'cancel_requested']);
const LEASE_SECONDS = 45;

function json<T>(raw: string, fallback: T): T {
  try { return JSON.parse(raw) as T; } catch { return fallback; }
}

function hash(value: string): string {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function slug(value: unknown): string {
  return String(value ?? '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-|-$/g, '').slice(0, 48);
}

function publicWorker(row: WorkerRow) {
  const seen = row.last_seen_at ? new Date(`${row.last_seen_at}Z`).getTime() : 0;
  return {
    id: row.id,
    name: row.name,
    capabilities: json<Capabilities>(row.capabilities, {}),
    status: seen && Date.now() - seen < 70_000 ? row.status : 'offline',
    last_seen_at: row.last_seen_at,
    created_at: row.created_at,
  };
}

function publicJob(row: JobRow) {
  return { ...row, permissions: json(row.permissions, {}) };
}

function workspaceAllowed(workspace: string, roots: string[]): boolean {
  const target = path.resolve(workspace).toLowerCase();
  return roots.some((root) => {
    const base = path.resolve(root).toLowerCase();
    return target === base || target.startsWith(base + path.sep);
  });
}

function workerFrom(req: Request, db: Db): WorkerRow | null {
  const auth = req.header('authorization') ?? '';
  const token = auth.startsWith('Bearer ') ? auth.slice(7).trim() : '';
  const dot = token.indexOf('.');
  if (dot < 1) return null;
  const id = token.slice(0, dot);
  const worker = db.prepare('SELECT * FROM workers WHERE id = ?').get(id) as WorkerRow | undefined;
  if (!worker) return null;
  const actual = Buffer.from(hash(token));
  const expected = Buffer.from(worker.token_hash);
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected) ? worker : null;
}

export function workersRouter(db: Db, sse: SseHub): Router {
  const r = Router();

  const emitJob = (id: string) => {
    const row = db.prepare('SELECT * FROM jobs WHERE id = ?').get(id) as JobRow | undefined;
    if (row) sse.broadcast('job', publicJob(row));
  };
  const addMessage = (jobId: string, sender: string, kind: string, content: string, meta: unknown = {}) => {
    const result = db.prepare(
      'INSERT INTO job_messages (job_id, sender, kind, content, meta) VALUES (?, ?, ?, ?, ?)'
    ).run(jobId, sender, kind, content.slice(0, 200_000), JSON.stringify(meta));
    const row = db.prepare('SELECT * FROM job_messages WHERE id = ?').get(Number(result.lastInsertRowid));
    sse.broadcast('job-message', row);
    return row;
  };
  const reap = () => {
    db.prepare(
      `UPDATE jobs SET status = 'expired', updated_at = datetime('now')
       WHERE status = 'pending' AND ttl_at IS NOT NULL AND ttl_at <= datetime('now')`
    ).run();
    const stale = db.prepare(
      `SELECT id FROM jobs WHERE status IN ('claimed','running','pause_requested','cancel_requested')
       AND lease_until IS NOT NULL AND lease_until <= datetime('now')`
    ).all() as { id: string }[];
    for (const { id } of stale) {
      db.prepare(
        `UPDATE jobs SET status = 'interrupted', error = 'worker lease expired; manual resume required',
         updated_at = datetime('now') WHERE id = ?`
      ).run(id);
      addMessage(id, 'system', 'state', 'Worker 失联，任务已中断；不会自动重跑副作用。');
      emitJob(id);
    }
  };

  r.get('/workers', (_req, res) => {
    reap();
    const workers = db.prepare('SELECT * FROM workers ORDER BY created_at').all() as WorkerRow[];
    res.json({ workers: workers.map(publicWorker) });
  });

  r.post('/workers', (req, res) => {
    const id = slug(req.body?.id || req.body?.name);
    const name = typeof req.body?.name === 'string' ? req.body.name.trim() : '';
    if (!id || !name) return res.status(400).json({ error: 'worker id/name required' });
    const token = `${id}.${crypto.randomBytes(32).toString('base64url')}`;
    db.prepare(
      `INSERT INTO workers (id, name, token_hash) VALUES (?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET name = excluded.name, token_hash = excluded.token_hash,
       status = 'offline', last_seen_at = NULL`
    ).run(id, name, hash(token));
    const worker = db.prepare('SELECT * FROM workers WHERE id = ?').get(id) as WorkerRow;
    res.status(201).json({ worker: publicWorker(worker), token });
  });

  r.get('/jobs', (req, res) => {
    reap();
    const limit = Math.min(Math.max(Number(req.query.limit) || 100, 1), 300);
    const rows = db.prepare('SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?').all(limit) as JobRow[];
    res.json({ jobs: rows.map(publicJob) });
  });

  r.get('/jobs/:id', (req, res) => {
    reap();
    const job = db.prepare('SELECT * FROM jobs WHERE id = ?').get(req.params.id) as JobRow | undefined;
    if (!job) return res.status(404).json({ error: 'job not found' });
    const messages = db.prepare('SELECT * FROM job_messages WHERE job_id = ? ORDER BY id').all(job.id);
    res.json({ job: publicJob(job), messages });
  });

  r.post('/jobs', (req, res) => {
    const runner = req.body?.runner === 'claude' ? 'claude' : req.body?.runner === 'codex' ? 'codex' : '';
    const prompt = typeof req.body?.prompt === 'string' ? req.body.prompt.trim() : '';
    const workspace = typeof req.body?.workspace === 'string' ? req.body.workspace.trim() : '';
    if (!runner || !prompt || !workspace) return res.status(400).json({ error: 'runner/workspace/prompt required' });
    if (prompt.length > 100_000 || workspace.length > 1000) return res.status(400).json({ error: 'job too large' });
    const id = crypto.randomUUID();
    const idempotencyKey = typeof req.body?.idempotencyKey === 'string' && req.body.idempotencyKey
      ? req.body.idempotencyKey.slice(0, 200) : id;
    const ttlMinutes = Math.min(Math.max(Number(req.body?.ttlMinutes) || 1440, 5), 10080);
    const permissions = {
      write: req.body?.permissions?.write !== false,
      shell: req.body?.permissions?.shell === true,
      ssh: req.body?.permissions?.ssh === true,
    };
    if (runner === 'codex' && !permissions.shell) {
      return res.status(400).json({ error: 'Codex 的文件读取/编辑都通过 Shell 工具；Codex 任务必须显式开启 Shell' });
    }
    try {
      db.prepare(
        `INSERT INTO jobs
         (id, requested_by, worker_id, runner, workspace, prompt, priority, ttl_at, idempotency_key, permissions)
         VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', ?), ?, ?)`
      ).run(
        id,
        typeof req.body?.requestedBy === 'string' ? req.body.requestedBy : 'owner',
        typeof req.body?.workerId === 'string' && req.body.workerId ? req.body.workerId : null,
        runner,
        workspace,
        prompt,
        Math.min(Math.max(Number(req.body?.priority) || 0, -10), 10),
        `+${ttlMinutes} minutes`,
        idempotencyKey,
        JSON.stringify(permissions)
      );
    } catch (e: any) {
      if (String(e.message).includes('UNIQUE')) return res.status(409).json({ error: 'duplicate idempotency key' });
      throw e;
    }
    addMessage(id, 'owner', 'prompt', prompt, { runner, workspace, permissions });
    emitJob(id);
    const job = db.prepare('SELECT * FROM jobs WHERE id = ?').get(id) as JobRow;
    res.status(201).json(publicJob(job));
  });

  r.post('/jobs/:id/action', (req, res) => {
    reap();
    const job = db.prepare('SELECT * FROM jobs WHERE id = ?').get(req.params.id) as JobRow | undefined;
    if (!job) return res.status(404).json({ error: 'job not found' });
    const action = req.body?.action;
    let next: string | null = null;
    if (action === 'cancel' && job.status === 'pending') next = 'cancelled';
    else if (action === 'cancel' && ACTIVE.has(job.status)) next = 'cancel_requested';
    else if (action === 'pause' && ACTIVE.has(job.status)) next = 'pause_requested';
    else if (action === 'resume' && ['paused', 'interrupted', 'failed'].includes(job.status)) next = 'pending';
    if (!next) return res.status(409).json({ error: `cannot ${action} from ${job.status}` });
    db.prepare(
      `UPDATE jobs SET status = ?, lease_until = NULL, error = NULL, updated_at = datetime('now') WHERE id = ?`
    ).run(next, job.id);
    addMessage(job.id, 'owner', 'state', `${action}: ${job.status} → ${next}`);
    emitJob(job.id);
    res.json({ ok: true, status: next });
  });

  r.post('/worker/connect', (req, res) => {
    const worker = workerFrom(req, db);
    if (!worker) return res.status(401).json({ error: 'invalid worker token' });
    const caps = req.body?.capabilities && typeof req.body.capabilities === 'object' ? req.body.capabilities : {};
    db.prepare(
      `UPDATE workers SET capabilities = ?, status = 'online', last_seen_at = datetime('now') WHERE id = ?`
    ).run(JSON.stringify(caps), worker.id);
    const updated = db.prepare('SELECT * FROM workers WHERE id = ?').get(worker.id) as WorkerRow;
    sse.broadcast('worker', publicWorker(updated));
    res.json({ worker: publicWorker(updated), leaseSeconds: LEASE_SECONDS });
  });

  const tryClaim = (worker: WorkerRow): JobRow | null => {
    reap();
    const fresh = db.prepare('SELECT * FROM workers WHERE id = ?').get(worker.id) as WorkerRow;
    const caps = json<Capabilities>(fresh.capabilities, {});
    const runners = Array.isArray(caps.runners) ? caps.runners : [];
    const roots = Array.isArray(caps.workspaces) ? caps.workspaces : [];
    const candidates = db.prepare(
      `SELECT * FROM jobs WHERE status = 'pending' AND (worker_id IS NULL OR worker_id = ?)
       ORDER BY priority DESC, created_at ASC LIMIT 50`
    ).all(worker.id) as JobRow[];
    for (const job of candidates) {
      const perms = json<{ shell?: boolean; ssh?: boolean }>(job.permissions, {});
      if (!runners.includes(job.runner) || !workspaceAllowed(job.workspace, roots)) continue;
      if (perms.shell && !caps.shell || perms.ssh && !caps.ssh) continue;
      const result = db.prepare(
        `UPDATE jobs SET worker_id = ?, status = 'claimed', lease_until = datetime('now', ?),
         updated_at = datetime('now') WHERE id = ? AND status = 'pending'`
      ).run(worker.id, `+${LEASE_SECONDS} seconds`, job.id);
      if (result.changes) {
        addMessage(job.id, worker.id, 'state', 'Worker 已认领任务');
        emitJob(job.id);
        return db.prepare('SELECT * FROM jobs WHERE id = ?').get(job.id) as JobRow;
      }
    }
    return null;
  };

  r.get('/worker/claim', async (req, res) => {
    const worker = workerFrom(req, db);
    if (!worker) return res.status(401).json({ error: 'invalid worker token' });
    db.prepare("UPDATE workers SET status = 'online', last_seen_at = datetime('now') WHERE id = ?").run(worker.id);
    const deadline = Date.now() + Math.min(Math.max(Number(req.query.wait) || 20, 0), 25) * 1000;
    let closed = false;
    req.on('close', () => { closed = true; });
    while (!closed) {
      const job = tryClaim(worker);
      if (job) return res.json({ job: publicJob(job), leaseSeconds: LEASE_SECONDS });
      if (Date.now() >= deadline) return res.json({ job: null });
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  });

  r.post('/worker/jobs/:id/start', (req, res) => {
    const worker = workerFrom(req, db);
    if (!worker) return res.status(401).json({ error: 'invalid worker token' });
    const result = db.prepare(
      `UPDATE jobs SET status = 'running', lease_until = datetime('now', ?), updated_at = datetime('now')
       WHERE id = ? AND worker_id = ? AND status = 'claimed'`
    ).run(`+${LEASE_SECONDS} seconds`, req.params.id, worker.id);
    if (!result.changes) return res.status(409).json({ error: 'job is not claimed by this worker' });
    addMessage(req.params.id, worker.id, 'state', '开始执行');
    emitJob(req.params.id);
    res.json({ ok: true });
  });

  r.post('/worker/jobs/:id/heartbeat', (req, res) => {
    const worker = workerFrom(req, db);
    if (!worker) return res.status(401).json({ error: 'invalid worker token' });
    const job = db.prepare('SELECT * FROM jobs WHERE id = ? AND worker_id = ?').get(req.params.id, worker.id) as JobRow | undefined;
    if (!job) return res.status(404).json({ error: 'job not found' });
    db.prepare("UPDATE workers SET status = 'busy', last_seen_at = datetime('now') WHERE id = ?").run(worker.id);
    if (ACTIVE.has(job.status)) {
      db.prepare("UPDATE jobs SET lease_until = datetime('now', ?), updated_at = datetime('now') WHERE id = ?")
        .run(`+${LEASE_SECONDS} seconds`, job.id);
    }
    const action = job.status === 'cancel_requested' ? 'cancel' : job.status === 'pause_requested' ? 'pause' : 'continue';
    res.json({ action, status: job.status });
  });

  r.post('/worker/jobs/:id/events', (req, res) => {
    const worker = workerFrom(req, db);
    if (!worker) return res.status(401).json({ error: 'invalid worker token' });
    const job = db.prepare('SELECT * FROM jobs WHERE id = ? AND worker_id = ?').get(req.params.id, worker.id) as JobRow | undefined;
    if (!job || ['cancelled', 'expired'].includes(job.status)) return res.status(409).json({ error: 'job no longer accepts events' });
    const kind = typeof req.body?.kind === 'string' ? req.body.kind.slice(0, 40) : 'log';
    const content = typeof req.body?.content === 'string' ? req.body.content : JSON.stringify(req.body?.content ?? '');
    if (kind === 'session' && typeof req.body?.meta?.sessionId === 'string') {
      db.prepare("UPDATE jobs SET session_id = ?, updated_at = datetime('now') WHERE id = ?")
        .run(req.body.meta.sessionId, job.id);
    }
    const row = addMessage(job.id, worker.id, kind, content, req.body?.meta ?? {});
    res.status(201).json(row);
  });

  r.post('/worker/jobs/:id/complete', (req, res) => {
    const worker = workerFrom(req, db);
    if (!worker) return res.status(401).json({ error: 'invalid worker token' });
    const job = db.prepare('SELECT * FROM jobs WHERE id = ? AND worker_id = ?').get(req.params.id, worker.id) as JobRow | undefined;
    if (!job) return res.status(404).json({ error: 'job not found' });
    const requested = req.body?.status;
    let status = requested === 'done' ? 'done' : requested === 'paused' ? 'paused' : requested === 'interrupted' ? 'interrupted' : 'failed';
    if (job.status === 'cancel_requested') status = 'cancelled';
    if (job.status === 'pause_requested') status = 'paused';
    const result = typeof req.body?.result === 'string' ? req.body.result.slice(0, 500_000) : null;
    const error = typeof req.body?.error === 'string' ? req.body.error.slice(0, 20_000) : null;
    db.prepare(
      `UPDATE jobs SET status = ?, result = ?, error = ?, lease_until = NULL,
       updated_at = datetime('now') WHERE id = ?`
    ).run(status, result, error, job.id);
    db.prepare("UPDATE workers SET status = 'online', last_seen_at = datetime('now') WHERE id = ?").run(worker.id);
    addMessage(job.id, worker.id, status === 'done' ? 'result' : 'state', result || error || status);
    emitJob(job.id);
    res.json({ ok: true, status });
  });

  return r;
}
