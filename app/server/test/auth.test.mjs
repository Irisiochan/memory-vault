import assert from 'node:assert/strict';
import test from 'node:test';
import express from 'express';
import { createHubAuth, isWorkerDeviceRoute } from '../dist/auth.js';

const ADMIN_TOKEN = 'test-admin-token-at-least-24-chars';

async function start(token = ADMIN_TOKEN, host = '127.0.0.1', now = Date.now) {
  const auth = createHubAuth(host, token, now);
  const app = express();
  app.use(express.json());
  app.get('/api/health', (_req, res) => res.json({ status: 'ok' }));
  app.use('/api/auth', auth.router);
  app.use('/api', (req, res, next) => {
    if (isWorkerDeviceRoute(req.path)) return next();
    return auth.requireAdmin(req, res, next);
  });
  app.get('/api/workers', (_req, res) => res.json({ protected: true }));
  app.get('/api/events', (_req, res) => res.json({ protected: true }));
  app.post('/api/worker/connect', (_req, res) => res.json({ workerRoute: true }));
  app.get('/api/worker/not-a-device-route', (_req, res) => res.json({ unsafe: true }));

  const server = await new Promise((resolve) => {
    const listener = app.listen(0, '127.0.0.1', () => resolve(listener));
  });
  const address = server.address();
  const base = `http://127.0.0.1:${address.port}`;
  return { base, server };
}

test('remote binds fail closed without a Hub admin token', () => {
  assert.throws(() => createHubAuth('0.0.0.0', ''), /HUB_ADMIN_TOKEN is required/);
  assert.throws(() => createHubAuth('192.168.1.20', 'too-short'), /at least 24 characters/);
});

test('loopback remains zero-config when no token is set', async (t) => {
  const { base, server } = await start('', '127.0.0.1');
  t.after(() => server.close());
  assert.equal((await fetch(`${base}/api/workers`)).status, 200);
  assert.deepEqual(await (await fetch(`${base}/api/auth/status`)).json(), {
    required: false,
    authenticated: true,
  });
});

test('admin API, SSE and pairing require bearer or an HttpOnly session', async (t) => {
  let now = Date.now();
  const { base, server } = await start(ADMIN_TOKEN, '127.0.0.1', () => now);
  t.after(() => server.close());

  assert.equal((await fetch(`${base}/api/health`)).status, 200);
  assert.equal((await fetch(`${base}/api/workers`)).status, 401);
  assert.equal((await fetch(`${base}/api/events`)).status, 401);
  assert.equal((await fetch(`${base}/api/worker/connect`, { method: 'POST' })).status, 200);
  assert.equal((await fetch(`${base}/api/worker/not-a-device-route`)).status, 401);

  const direct = await fetch(`${base}/api/workers`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
  assert.equal(direct.status, 200);

  assert.equal((await fetch(`${base}/api/auth/session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: 'wrong-token' }),
  })).status, 401);

  const login = await fetch(`${base}/api/auth/session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: ADMIN_TOKEN }),
  });
  assert.equal(login.status, 204);
  const cookie = login.headers.get('set-cookie') ?? '';
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /SameSite=Strict/);
  assert.doesNotMatch(cookie, new RegExp(ADMIN_TOKEN));

  const session = await fetch(`${base}/api/workers`, { headers: { Cookie: cookie } });
  assert.equal(session.status, 200);

  now += 8 * 24 * 60 * 60 * 1000;
  const expired = await fetch(`${base}/api/workers`, { headers: { Cookie: cookie } });
  assert.equal(expired.status, 401);
});
