import express from 'express';
import fs from 'node:fs';
import path from 'node:path';
import { AgentManager } from './agents/manager.js';
import { loadConfig } from './config.js';
import { openDb } from './db.js';
import { VaultClient } from './memory/vaultClient.js';
import { ClaudeQuotaPoller } from './quota/claudeQuota.js';
import { contactsRouter } from './routes/contacts.js';
import { messagesRouter } from './routes/messages.js';
import { userRouter } from './routes/user.js';
import { workersRouter } from './routes/workers.js';
import { seedIfEmpty } from './seed.js';
import { SseHub } from './sse.js';

const config = loadConfig();
const db = openDb(config.dbPath);
seedIfEmpty(db, config);

const sse = new SseHub();
const vault = config.memory.mcpUrl
  ? new VaultClient(
      config.memory.mcpUrl,
      db,
      (m) => console.log(`  [vault] ${m}`),
      process.env.VAULT_TOKEN ?? null
    )
  : null;
const manager = new AgentManager({ db, sse, config, vault });

const app = express();
app.use(express.json({ limit: '2mb' }));

app.get('/api/health', (_req, res) => {
  const count = db.prepare('SELECT COUNT(*) AS c FROM messages').get() as { c: number };
  res.json({ status: 'ok', messageCount: count.c });
});

app.get('/api/events', (req, res) => {
  sse.addClient(res);
  req.on('close', () => {});
});

const quotaPoller = new ClaudeQuotaPoller((m) => console.log(`  [quota] ${m}`));
quotaPoller.start();

app.use('/api/contacts', contactsRouter(db, sse, manager, config));
app.use('/api/contacts', messagesRouter(db, sse, manager));
app.use('/api/user', userRouter(db, sse));
app.use('/api', workersRouter(db, sse));
app.get('/api/quota/claude', (_req, res) => {
  const q = quotaPoller.get();
  res.json({ available: q !== null, ...(q ?? {}) });
});

// serve built frontend if present (prod single-process mode)
if (fs.existsSync(config.webDist)) {
  app.use(express.static(config.webDist));
  app.get(/^\/(?!api\/).*/, (_req, res) => {
    res.sendFile(path.join(config.webDist, 'index.html'));
  });
}

const server = app.listen(config.port, config.host, () => {
  console.log('');
  console.log('  🧠 memory-vault hub');
  console.log(`  http://${config.host}:${config.port}`);
  console.log(`  db: ${config.dbPath}`);
  console.log(`  web: ${fs.existsSync(config.webDist) ? config.webDist : '(dev — run vite separately)'}`);
  console.log('');
});

let shuttingDown = false;
async function shutdown(signal: string): Promise<void> {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`\n  ${signal} → graceful shutdown`);
  server.close();
  sse.close();
  await manager.stopAll();
  await vault?.close();
  db.close();
  process.exit(0);
}
process.on('SIGINT', () => void shutdown('SIGINT'));
process.on('SIGTERM', () => void shutdown('SIGTERM'));

// async 路由里的漏网 rejection 不许带崩整个网关
process.on('unhandledRejection', (reason) => {
  console.error('  [unhandledRejection]', reason);
});
process.on('uncaughtException', (err) => {
  console.error('  [uncaughtException]', err);
});
