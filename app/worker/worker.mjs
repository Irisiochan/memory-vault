import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const configPath = path.resolve(process.argv[2] ?? 'config.json');
if (!fs.existsSync(configPath)) {
  console.error(`Missing ${configPath}; copy config.example.json to config.json first.`);
  process.exit(1);
}
const cfg = JSON.parse(fs.readFileSync(configPath, 'utf8'));
const statePath = path.resolve(path.dirname(configPath), cfg.stateFile ?? 'worker-state.json');
const base = String(cfg.serverUrl ?? '').replace(/\/$/, '');
if (!base || !cfg.token) throw new Error('serverUrl/token required');

const auth = { Authorization: `Bearer ${cfg.token}`, 'Content-Type': 'application/json' };
let stopping = false;
let activeChild = null;
let spool = { active: null, events: [] };
try { spool = JSON.parse(fs.readFileSync(statePath, 'utf8')); } catch {}

function saveSpool() {
  const tmp = `${statePath}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(spool, null, 2), 'utf8');
  fs.renameSync(tmp, statePath);
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function request(url, init = {}) {
  const res = await fetch(`${base}${url}`, { ...init, headers: { ...auth, ...(init.headers ?? {}) } });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `${res.status} ${res.statusText}`);
  }
  return res.json();
}

function allowedWorkspace(value) {
  const target = path.resolve(value).toLowerCase();
  return (cfg.workspaces ?? []).some((root) => {
    const basePath = path.resolve(root).toLowerCase();
    return target === basePath || target.startsWith(basePath + path.sep);
  });
}

async function event(job, kind, content, meta = {}) {
  const payload = { kind, content: String(content).slice(0, 200000), meta };
  try {
    await request(`/api/worker/jobs/${job.id}/events`, {
      method: 'POST', body: JSON.stringify(payload),
    });
  } catch (e) {
    console.error(`[${job.id.slice(0, 8)}] event upload failed: ${e.message}`);
    spool.events.push({ jobId: job.id, payload });
    if (spool.events.length > 2000) spool.events.splice(0, spool.events.length - 2000);
    saveSpool();
  }
}

async function recoverSpool() {
  const remaining = [];
  for (const item of spool.events) {
    try {
      await request(`/api/worker/jobs/${item.jobId}/events`, { method: 'POST', body: JSON.stringify(item.payload) });
    } catch { remaining.push(item); }
  }
  spool.events = remaining;
  if (!spool.active) { saveSpool(); return; }
  const { job, outcome } = spool.active;
  const final = outcome ?? { status: 'interrupted', error: 'PC Worker restarted; manual resume required' };
  try {
    await request(`/api/worker/jobs/${job.id}/complete`, { method: 'POST', body: JSON.stringify(final) });
    spool.active = null;
    spool.events = spool.events.filter((e) => e.jobId !== job.id);
  } catch {}
  saveSpool();
}

function parseLine(job, line, state) {
  if (!line.trim()) return;
  let data;
  try { data = JSON.parse(line); } catch { void event(job, 'log', line); return; }
  const sessionId = data.session_id ?? data.sessionId ?? data.thread_id ?? data.threadId;
  if (typeof sessionId === 'string' && sessionId !== state.sessionId) {
    state.sessionId = sessionId;
    void event(job, 'session', `session ${sessionId}`, { sessionId });
  }
  if (data.type === 'result' && typeof data.result === 'string') state.result = data.result;
  if (data.type === 'item.completed' && data.item?.type === 'agent_message') {
    const text = data.item.text ?? data.item.content;
    if (typeof text === 'string') state.result = text;
  }
  const kind = /tool|command/.test(String(data.type ?? '')) ? 'tool' : /thinking|reason/.test(String(data.type ?? '')) ? 'thinking' : 'log';
  const content = data.message?.content ?? data.message ?? data.error?.message ?? data.error ?? data.delta?.text ?? data.item?.text ?? data.result ?? (['error', 'turn.failed'].includes(data.type) ? line : data.type) ?? line;
  void event(job, kind, typeof content === 'string' ? content : JSON.stringify(content), { type: data.type });
}

function runner(job) {
  const perms = job.permissions ?? {};
  const prompt = [
    `ai-hub worker job ${job.id}.`,
    'Work only inside the assigned workspace. Do not delegate to other agents.',
    perms.ssh ? 'SSH/VPS operations are explicitly allowed for this job.' : 'Do not use SSH or operate remote machines.',
    job.prompt,
  ].join('\n\n');
  const sessionId = typeof job.session_id === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(job.session_id)
    ? job.session_id : null;
  if (job.runner === 'claude') {
    const tools = ['Read', 'Grep', 'Glob'];
    if (perms.write) tools.push('Write', 'Edit');
    if (perms.shell) tools.push('Bash');
    const args = ['-p', '--verbose', '--output-format', 'stream-json', '--allowedTools', tools.join(',')];
    if (!perms.shell) args.push('--disallowedTools', 'Bash');
    if (sessionId) args.push('--resume', sessionId);
    return { command: cfg.claudeCommand ?? (process.platform === 'win32' ? 'claude.cmd' : 'claude'), args, stdin: prompt };
  }
  const sandbox = perms.write ? 'workspace-write' : 'read-only';
  const command = cfg.codexCommand ?? (process.platform === 'win32' ? 'codex.cmd' : 'codex');
  const model = typeof cfg.codexModel === 'string' && /^[a-zA-Z0-9._-]{1,100}$/.test(cfg.codexModel)
    ? ['--model', cfg.codexModel] : [];
  const windowsSandbox = process.platform === 'win32'
    && ['elevated', 'unelevated'].includes(cfg.codexWindowsSandbox ?? 'unelevated')
    ? ['--config', `windows.sandbox="${cfg.codexWindowsSandbox ?? 'unelevated'}"`]
    : [];
  if (sessionId) {
    return { command, args: ['exec', 'resume', '--json', ...windowsSandbox, ...model, sessionId, '-'], stdin: prompt };
  }
  return { command, args: ['exec', '--json', ...windowsSandbox, '--sandbox', sandbox, '--skip-git-repo-check', ...model, '-'], stdin: prompt };
}

async function execute(job) {
  if (!allowedWorkspace(job.workspace)) throw new Error(`workspace is outside allowlist: ${job.workspace}`);
  if (job.permissions?.shell && !cfg.allowShell) throw new Error('job requires shell but worker disallows it');
  if (job.permissions?.ssh && !cfg.allowSsh) throw new Error('job requires SSH but worker disallows it');
  if (!fs.existsSync(job.workspace)) throw new Error(`workspace does not exist: ${job.workspace}`);

  await request(`/api/worker/jobs/${job.id}/start`, { method: 'POST', body: '{}' });
  spool.active = { job, outcome: null };
  saveSpool();
  const spec = runner(job);
  await event(job, 'state', `启动 ${job.runner}: ${spec.command}`);
  const state = { result: '', sessionId: job.session_id ?? null, action: 'continue' };

  const child = spawn(spec.command, spec.args, {
    cwd: job.workspace,
    windowsHide: true,
    shell: process.platform === 'win32',
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, NO_COLOR: '1' },
  });
  activeChild = child;
  child.stdin.end(spec.stdin);
  let stdout = '';
  const consume = (chunk, stream) => {
    const text = chunk.toString('utf8');
    if (stream === 'stderr') void event(job, 'stderr', text);
    if (stream === 'stdout') {
      stdout += text;
      const lines = stdout.split(/\r?\n/);
      stdout = lines.pop() ?? '';
      for (const line of lines) parseLine(job, line, state);
    }
  };
  child.stdout.on('data', (c) => consume(c, 'stdout'));
  child.stderr.on('data', (c) => consume(c, 'stderr'));

  const heartbeat = setInterval(async () => {
    try {
      const response = await request(`/api/worker/jobs/${job.id}/heartbeat`, { method: 'POST', body: '{}' });
      state.action = response.action;
      if (response.action === 'cancel' || response.action === 'pause') child.kill('SIGTERM');
    } catch (e) {
      console.error(`[${job.id.slice(0, 8)}] heartbeat failed: ${e.message}`);
    }
  }, 12_000);

  const exit = await new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', (code, signal) => resolve({ code, signal }));
  }).finally(() => clearInterval(heartbeat));
  activeChild = null;
  if (stdout.trim()) parseLine(job, stdout, state);
  if (state.action === 'pause') return { status: 'paused', result: state.result };
  if (state.action === 'cancel') return { status: 'interrupted', result: state.result };
  if (exit.code === 0) return { status: 'done', result: state.result || `runner exited successfully` };
  return { status: 'failed', result: state.result, error: `${spec.command} exited code=${exit.code} signal=${exit.signal}` };
}

async function main() {
  console.log(`ai-hub PC Worker → ${base}`);
  while (!stopping) {
    try {
      await request('/api/worker/connect', {
        method: 'POST',
        body: JSON.stringify({ capabilities: {
          runners: cfg.runners ?? ['codex'], workspaces: cfg.workspaces ?? [],
          shell: cfg.allowShell === true, ssh: cfg.allowSsh === true,
        } }),
      });
      await recoverSpool();
      const { job } = await request('/api/worker/claim?wait=25');
      if (!job) continue;
      console.log(`[${job.id.slice(0, 8)}] claimed ${job.runner} @ ${job.workspace}`);
      try {
        const outcome = await execute(job);
        spool.active = { job, outcome };
        saveSpool();
        await request(`/api/worker/jobs/${job.id}/complete`, { method: 'POST', body: JSON.stringify(outcome) });
        spool.active = null;
        spool.events = spool.events.filter((e) => e.jobId !== job.id);
        saveSpool();
      } catch (e) {
        const outcome = { status: 'failed', error: e.stack ?? e.message };
        spool.active = { job, outcome };
        saveSpool();
        await request(`/api/worker/jobs/${job.id}/complete`, { method: 'POST', body: JSON.stringify(outcome) })
          .then(() => { spool.active = null; saveSpool(); }).catch(() => {});
      }
    } catch (e) {
      if (!stopping) console.error(`worker loop: ${e.message}; retrying…`);
      await wait(3000);
    }
  }
}

for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => {
  stopping = true;
  activeChild?.kill('SIGTERM');
});

await main();
