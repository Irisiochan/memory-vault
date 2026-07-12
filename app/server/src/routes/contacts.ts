import { Router } from 'express';
import type { AgentManager } from '../agents/manager.js';
import type { Db, ContactRow } from '../db.js';
import type { SseHub } from '../sse.js';

/** apiKey never leaves the server in clear — mask to ••••+last4 for the UI. */
function maskConfig(config: Record<string, any>): Record<string, any> {
  if (typeof config.apiKey === 'string' && config.apiKey.length > 0) {
    return { ...config, apiKey: `••••${config.apiKey.slice(-4)}` };
  }
  return config;
}

function isMaskedKey(v: unknown): boolean {
  return typeof v === 'string' && (v === '' || v.startsWith('••••'));
}

export function contactsRouter(db: Db, sse: SseHub, manager: AgentManager): Router {
  const r = Router();

  const publicRow = (c: ContactRow) => ({
    ...c,
    config: maskConfig(JSON.parse(c.config || '{}')),
    state: manager.stateOf(c.id),
  });

  r.get('/', (_req, res) => {
    const rows = db
      .prepare(
        `SELECT c.*,
           (SELECT content FROM messages m WHERE m.contact_id = c.id AND m.kind = 'text'
              AND m.deleted = 0 ORDER BY m.id DESC LIMIT 1) AS last_content,
           (SELECT created_at FROM messages m WHERE m.contact_id = c.id AND m.deleted = 0
              ORDER BY m.id DESC LIMIT 1) AS last_at
         FROM contacts c WHERE c.enabled = 1 ORDER BY c.sort_order, c.created_at`
      )
      .all() as (ContactRow & { last_content: string | null; last_at: string | null })[];

    res.json({ contacts: rows.map((c) => publicRow(c)) });
  });

  r.post('/', async (req, res) => {
    const { id, name, avatar, color, backend, config } = req.body ?? {};
    if (typeof name !== 'string' || !name.trim()) {
      return res.status(400).json({ error: 'name required' });
    }
    let slug = (typeof id === 'string' && id ? id : name)
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9-]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 32);
    // 纯中文名字 slug 会变空——退化成随机 id，显示名不受影响
    if (!slug) slug = `c${Date.now().toString(36)}`;
    const existing = db.prepare('SELECT * FROM contacts WHERE id = ?').get(slug) as
      | ContactRow
      | undefined;
    if (existing?.enabled) {
      return res.status(409).json({ error: `联系人 ${slug} 已存在` });
    }

    const isRoom = req.body?.kind === 'room';
    const cfg = config && typeof config === 'object' ? { ...config } : {};
    if (isRoom) {
      const members: string[] = Array.isArray(cfg.members) ? cfg.members : [];
      const valid = members.filter((id) =>
        db.prepare("SELECT id FROM contacts WHERE id = ? AND enabled = 1 AND kind = 'dm'").get(id)
      );
      if (valid.length === 0)
        return res.status(400).json({ error: '群聊至少要拉一个现有联系人' });
      cfg.members = valid;
    }
    const backendKind = isRoom
      ? 'room'
      : ['claude-cli', 'codex', 'api'].includes(backend)
        ? backend
        : 'api';

    // 软删过的同名坑位：UPDATE 复活并覆盖（消息表有外键，不能 DELETE；历史正好延续）
    db.prepare(
      `INSERT INTO contacts (id, name, avatar, color, backend, kind, config, sort_order, enabled)
       VALUES (?, ?, ?, ?, ?, ?, ?, 50, 1)
       ON CONFLICT(id) DO UPDATE SET
         name = excluded.name, avatar = excluded.avatar, color = excluded.color,
         backend = excluded.backend, kind = excluded.kind, config = excluded.config,
         enabled = 1`
    ).run(
      slug,
      name.trim(),
      typeof avatar === 'string' && avatar ? avatar : isRoom ? '👥' : '🤖',
      typeof color === 'string' && color ? color : '#8888aa',
      backendKind,
      isRoom ? 'room' : 'dm',
      JSON.stringify(cfg)
    );

    const created = db.prepare('SELECT * FROM contacts WHERE id = ?').get(slug) as ContactRow;
    const payload = publicRow(created);
    sse.broadcast('contact', payload);
    res.status(201).json(payload);
  });

  r.patch('/:id', async (req, res) => {
    const contact = db.prepare('SELECT * FROM contacts WHERE id = ?').get(req.params.id) as
      | ContactRow
      | undefined;
    if (!contact) return res.status(404).json({ error: 'contact not found' });

    const { name, avatar, color, config } = req.body ?? {};
    let nextConfig = contact.config;
    if (config && typeof config === 'object') {
      const oldConfig = JSON.parse(contact.config || '{}');
      const merged = { ...config };
      // masked/empty key from the UI means "keep the stored one"
      if (isMaskedKey(merged.apiKey) && oldConfig.apiKey) merged.apiKey = oldConfig.apiKey;
      nextConfig = JSON.stringify(merged);
    }
    db.prepare('UPDATE contacts SET name = ?, avatar = ?, color = ?, config = ? WHERE id = ?').run(
      typeof name === 'string' && name.trim() ? name.trim() : contact.name,
      typeof avatar === 'string' && avatar ? avatar : contact.avatar,
      typeof color === 'string' && color ? color : contact.color,
      nextConfig,
      contact.id
    );
    const updated = db.prepare('SELECT * FROM contacts WHERE id = ?').get(contact.id) as ContactRow;
    await manager.notifyContactUpdated(updated);
    const payload = publicRow(updated);
    sse.broadcast('contact', payload);
    res.json(payload);
  });

  r.delete('/:id', async (req, res) => {
    const contact = db.prepare('SELECT * FROM contacts WHERE id = ?').get(req.params.id) as
      | ContactRow
      | undefined;
    if (!contact) return res.status(404).json({ error: 'contact not found' });
    db.prepare('UPDATE contacts SET enabled = 0 WHERE id = ?').run(contact.id);
    await manager.remove(contact.id);
    sse.broadcast('contact', { id: contact.id, enabled: 0 });
    res.json({ ok: true });
  });

  return r;
}
