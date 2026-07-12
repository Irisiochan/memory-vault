import type { VaultClient } from './vaultClient.js';

/**
 * Write path of the memory layer: after each completed turn the gateway runs
 * cheap trigger heuristics over the exchange. On a hit, the exchange is parked
 * in inbox/ (tagged hub-auto) — 按库规矩，拿不准的进 inbox，之后由人/AI 整理晋升。
 * 不追求聪明，追求"绝不漏掉明显该记的"。
 */

interface Trigger {
  re: RegExp;
  reason: string;
}

const TRIGGERS: Trigger[] = [
  { re: /\d{1,2}月\d{1,2}[日号]|明天|今晚|下+周|大?后天|周[一二三四五六日天]|下个?月|月底|年底|[一二三四五六七八九十两\d]{1,2}点半?(见|集合|出发|开始|叫|喊)/, reason: '时间与计划' },
  { re: /答应|约好|说好|敲定|定好|要记得|记一下|帮我记|别忘|提醒我|待办/, reason: '承诺与待办' },
  { re: /最喜欢|超喜欢|最讨厌|过敏|不吃|不能吃|爱吃|雷点|口味|尺码|偏好是/, reason: '偏好' },
  { re: /生日|纪念日|周年|第一次|搬家|离职|入职|面试|offer|录取|考试|体检|医院|确诊|受伤|分手|表白/, reason: '人生事件' },
  { re: /以后都|从今以后|往后|长期|每次都要|定个规矩|咱们约定|新习惯/, reason: '长期约定' },
];

const RATE_LIMIT_MS = 10 * 60_000;
const lastCapture = new Map<string, number>();

export function detectTrigger(text: string): string | null {
  for (const t of TRIGGERS) {
    if (t.re.test(text)) return t.reason;
  }
  return null;
}

export async function maybeCapture(
  vault: VaultClient,
  contact: { id: string; name: string },
  ownerName: string,
  userText: string,
  replyText: string,
  log: (msg: string) => void
): Promise<void> {
  const reason = detectTrigger(userText) ?? detectTrigger(replyText);
  if (!reason) return;

  const last = lastCapture.get(contact.id) ?? 0;
  if (Date.now() - last < RATE_LIMIT_MS) return;
  lastCapture.set(contact.id, Date.now());

  const slug = `hub-auto-${contact.id}-${new Date().toISOString().slice(11, 16).replace(':', '')}`;
  const title = `[hub-auto] ${reason}：${userText.replace(/\s+/g, ' ').slice(0, 24)}`;
  const content = [
    `网关自动捕捉（触发类别：${reason}，联系人：${contact.name}）。`,
    '内容未经确认——整理时采纳则 promote 或改写进 memories/，误报直接删。',
    '',
    `**${ownerName || 'Owner'}**：${userText.slice(0, 500)}`,
    '',
    `**${contact.name}**：${replyText.slice(0, 800)}`,
  ].join('\n');

  const result = await vault.write('write_inbox', {
    slug,
    title,
    content,
    tags: ['hub-auto', contact.id, reason],
    source: 'hub-auto',
  });
  log(`memory capture (${reason}) → inbox ${result === 'ok' ? '✓' : '(outbox queued)'}`);
}
