import fs from 'node:fs';
import path from 'node:path';
import type { HubConfig } from './config.js';
import type { Db } from './db.js';

/** Create one neutral first-boot contact. Users can edit or delete it in the UI. */
export function seedIfEmpty(db: Db, config: HubConfig): void {
  const count = db.prepare('SELECT COUNT(*) AS c FROM contacts').get() as { c: number };
  if (count.c > 0) return;
  const easyMode = process.env.MEMORY_VAULT_EASY === '1';

  if (easyMode) {
    db.prepare(
      `INSERT INTO contacts (id, name, avatar, color, backend, kind, config, sort_order)
       VALUES (?, ?, ?, ?, 'api', 'dm', ?, 0)`
    ).run('assistant', '我的 AI', '🤖', '#6366f1', JSON.stringify({
      provider: 'openai-compat',
      baseUrl: '',
      apiKey: '',
      model: '',
      systemPrompt: '你是记忆库主人长期使用的 AI 助手。自然交流，并正确区分共享记忆中不同 AI 的身份和经历。',
      maxHistoryMessages: 60,
      memory: { injectOnSpawn: true, searchPerTurn: true, capture: true }
    }));
    console.log('  seeded contact: 我的 AI (configure API in the UI)');
    return;
  }
  const agentDir = path.join(config.agentsDir, 'assistant');
  fs.mkdirSync(agentDir, { recursive: true });
  const instructions = path.join(agentDir, 'CLAUDE.md');
  if (!fs.existsSync(instructions)) {
    fs.writeFileSync(instructions, [
      '# Memory Vault chat mode', '',
      'You are the assistant configured for this contact.',
      '- Reply naturally, like an ongoing chat rather than a formal report.',
      '- Shared vault memories are background knowledge, not your identity.',
      '- Never claim another contact\'s experiences, words, or relationships as your own.',
      '- The gateway injects memory context automatically when memory.mcpUrl is configured.', ''
    ].join('\n'), 'utf-8');
  }
  db.prepare(
    `INSERT INTO contacts (id, name, avatar, color, backend, kind, config, sort_order)
     VALUES (?, ?, ?, ?, ?, 'dm', ?, 0)`
  ).run('assistant', 'Assistant', '🤖', '#6366f1', 'claude-cli', JSON.stringify({
    cwd: 'assistant',
    appendSystemPrompt: 'You are chatting with the vault owner. Keep replies natural and concise.'
  }));
  console.log('  seeded contact: Assistant (claude-cli)');
}
