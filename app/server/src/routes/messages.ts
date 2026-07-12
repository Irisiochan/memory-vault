import { Router } from 'express';
import type { AgentManager } from '../agents/manager.js';
import type { ContactRow, Db, MessageRow } from '../db.js';
import type { SseHub } from '../sse.js';

export function messagesRouter(db: Db, sse: SseHub, manager: AgentManager): Router {
  const r = Router();

  const getContact = (id: string): ContactRow | undefined =>
    db.prepare('SELECT * FROM contacts WHERE id = ? AND enabled = 1').get(id) as
      | ContactRow
      | undefined;

  r.get('/:id/messages', (req, res) => {
    const contact = getContact(req.params.id);
    if (!contact) return res.status(404).json({ error: 'contact not found' });

    const limit = Math.min(Number(req.query.limit) || 50, 200);
    const before = req.query.before ? Number(req.query.before) : null;
    const after = req.query.after ? Number(req.query.after) : null;

    let rows: MessageRow[];
    if (after !== null) {
      rows = db
        .prepare(
          'SELECT * FROM messages WHERE contact_id = ? AND deleted = 0 AND id > ? ORDER BY id ASC LIMIT ?'
        )
        .all(contact.id, after, limit) as MessageRow[];
    } else if (before !== null) {
      rows = (
        db
          .prepare(
            'SELECT * FROM messages WHERE contact_id = ? AND deleted = 0 AND id < ? ORDER BY id DESC LIMIT ?'
          )
          .all(contact.id, before, limit) as MessageRow[]
      ).reverse();
    } else {
      rows = (
        db
          .prepare(
            'SELECT * FROM messages WHERE contact_id = ? AND deleted = 0 ORDER BY id DESC LIMIT ?'
          )
          .all(contact.id, limit) as MessageRow[]
      ).reverse();
    }
    res.json({ messages: rows });
  });

  /** 编辑提示词并重新生成：内容可选更新，其后的消息全部软删，CLI 上下文重置回放。 */
  r.post('/:id/messages/:mid/regenerate', async (req, res) => {
    const contact = getContact(req.params.id);
    if (!contact) return res.status(404).json({ error: 'contact not found' });
    const mid = Number(req.params.mid);
    const row = db
      .prepare('SELECT * FROM messages WHERE id = ? AND contact_id = ? AND deleted = 0')
      .get(mid, contact.id) as MessageRow | undefined;
    if (!row) return res.status(404).json({ error: 'message not found' });
    if (row.role !== 'user') return res.status(400).json({ error: '只能从你自己的消息重新生成' });
    if (contact.kind === 'room')
      return res.status(400).json({ error: '群聊里暂不支持重新生成（v1）' });

    let text = row.content;
    if (typeof req.body?.content === 'string' && req.body.content.trim()) {
      text = req.body.content.trim();
      db.prepare(
        `UPDATE messages SET content = ?, meta = json_set(COALESCE(meta,'{}'), '$.edited', 1) WHERE id = ?`
      ).run(text, mid);
      const updated = db.prepare('SELECT * FROM messages WHERE id = ?').get(mid) as MessageRow;
      sse.broadcast('message', updated);
    }

    const queued = await manager.get(contact).regenerateFrom(mid, text);
    if (queued === 'full') return res.status(429).json({ error: '排队太长了' });
    res.status(202).json({ ok: true, messageId: mid });
  });

  /** 删除单条消息：软删 + CLI 上下文重置（被删内容不再进入任何上下文）。 */
  r.delete('/:id/messages/:mid', async (req, res) => {
    const contact = getContact(req.params.id);
    if (!contact) return res.status(404).json({ error: 'contact not found' });
    const mid = Number(req.params.mid);
    const row = db
      .prepare('SELECT * FROM messages WHERE id = ? AND contact_id = ? AND deleted = 0')
      .get(mid, contact.id) as MessageRow | undefined;
    if (!row) return res.status(404).json({ error: 'message not found' });

    db.prepare('UPDATE messages SET deleted = 1 WHERE id = ?').run(mid);
    sse.broadcast('prune', { contactId: contact.id, ids: [mid] });
    await manager.invalidateConversation(contact);
    res.json({ ok: true });
  });

  /** token 消耗聚合（api/订阅通用，来自 done 消息的 meta.usage）。 */
  r.get('/:id/usage', (req, res) => {
    const contact = getContact(req.params.id);
    if (!contact) return res.status(404).json({ error: 'contact not found' });
    const rows = db
      .prepare(
        `SELECT meta, created_at FROM messages
         WHERE contact_id = ? AND deleted = 0 AND role = 'assistant' AND meta LIKE '%usage%'`
      )
      .all(contact.id) as { meta: string; created_at: string }[];
    const today = new Date().toISOString().slice(0, 10);
    const sum = { today: { input: 0, output: 0 }, total: { input: 0, output: 0 } };
    for (const r2 of rows) {
      try {
        const u = JSON.parse(r2.meta)?.usage;
        if (!u) continue;
        sum.total.input += u.input ?? 0;
        sum.total.output += u.output ?? 0;
        if (r2.created_at.startsWith(today)) {
          sum.today.input += u.input ?? 0;
          sum.today.output += u.output ?? 0;
        }
      } catch {}
    }
    res.json(sum);
  });

  r.post('/:id/messages', (req, res) => {
    const contact = getContact(req.params.id);
    if (!contact) return res.status(404).json({ error: 'contact not found' });

    const content = typeof req.body?.content === 'string' ? req.body.content.trim() : '';
    if (!content) return res.status(400).json({ error: 'content required' });

    const result = db
      .prepare(
        `INSERT INTO messages (contact_id, sender, role, kind, content, status)
         VALUES (?, 'user', 'user', 'text', ?, 'done')`
      )
      .run(contact.id, content);
    const row = db
      .prepare('SELECT * FROM messages WHERE id = ?')
      .get(Number(result.lastInsertRowid)) as MessageRow;
    sse.broadcast('message', row);

    if (contact.kind === 'room') {
      const targets = manager.dispatchRoomMessage(contact, content);
      return res.status(202).json({ messageId: row.id, queued: true, targets });
    }

    const queued = manager.get(contact).enqueue({ userMessageId: row.id, text: content });
    if (queued === 'full') {
      return res.status(429).json({ error: '排队太长了，等他喘口气', messageId: row.id });
    }
    res.status(202).json({ messageId: row.id, queued: true });
  });

  r.post('/:id/interrupt', (req, res) => {
    const contact = getContact(req.params.id);
    if (!contact) return res.status(404).json({ error: 'contact not found' });
    manager.interruptAll(contact);
    res.status(202).json({ ok: true });
  });

  r.post('/:id/session/reset', async (req, res) => {
    const contact = getContact(req.params.id);
    if (!contact) return res.status(404).json({ error: 'contact not found' });
    await manager.resetConversation(contact);
    res.json({ ok: true });
  });

  return r;
}
