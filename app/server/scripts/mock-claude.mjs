#!/usr/bin/env node
/**
 * Mock claude CLI speaking the stream-json protocol. Dev fixture for
 * verifying the gateway pipeline without burning quota / needing login.
 * Honors --resume <id> so session persistence can be tested.
 */
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';

const argv = process.argv.slice(2);
const resumeIdx = argv.indexOf('--resume');
const sessionId = resumeIdx !== -1 ? argv[resumeIdx + 1] : randomUUID();

const out = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');

out({ type: 'system', subtype: 'init', session_id: sessionId, model: 'mock' });

const rl = createInterface({ input: process.stdin });
let turn = 0;

rl.on('line', async (line) => {
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    return;
  }
  if (msg.type === 'control_request' && msg.request?.subtype === 'interrupt') {
    out({
      type: 'result',
      subtype: 'success',
      is_error: false,
      result: '（mock：被打断了）',
      session_id: sessionId,
    });
    return;
  }
  if (msg.type !== 'user') return;

  turn++;
  const text = typeof msg.message?.content === 'string' ? msg.message.content : '';
  const reply = text.includes('安静')
    ? '[PASS]'
    : `mock 收到第 ${turn} 条：「${text}」——中文✅ emoji🍊✅ 换行✅\n\n**markdown** 也 \`ok\``;

  // stream in small chunks with CJK-hostile boundaries
  for (const chunk of reply.match(/[\s\S]{1,7}/g) ?? []) {
    out({
      type: 'stream_event',
      event: { type: 'content_block_delta', delta: { type: 'text_delta', text: chunk } },
    });
    await new Promise((r) => setTimeout(r, 120));
  }

  if (text.includes('工具')) {
    out({
      type: 'assistant',
      message: {
        content: [{ type: 'tool_use', id: 'tu_1', name: 'mcp__memory-vault__search_vault', input: { query: 'test' } }],
      },
    });
    out({
      type: 'user',
      message: { content: [{ type: 'tool_result', tool_use_id: 'tu_1', content: 'mock result' }] },
    });
  }

  out({
    type: 'result',
    subtype: 'success',
    is_error: false,
    result: reply,
    session_id: sessionId,
    usage: { input_tokens: 10, output_tokens: 25 },
  });
});
