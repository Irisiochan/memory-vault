import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type { HubConfig, MemoryConfig } from '../config.js';
import {
  deactivateSession,
  getActiveSession,
  getLastSeen,
  saveSession,
  setLastSeen,
  type ContactRow,
  type Db,
  type MessageRow,
} from '../db.js';
import { maybeCapture } from '../memory/capture.js';
import {
  PREAMBLE_UNAVAILABLE,
  buildSessionPreamble,
  buildTurnBlock,
  wrapTurnText,
} from '../memory/inject.js';
import type { VaultClient } from '../memory/vaultClient.js';
import { getUserProfile } from '../routes/user.js';
import type { SseHub } from '../sse.js';
import { ClaudeCliBackend } from './claudeCli.js';
import { CodexAppServerBackend } from './codexAppServer.js';
import { DirectApiBackend } from './directApi.js';
import type { AgentBackend, TurnHandle } from './types.js';

export type RoomTurnOutcome = 'spoke' | 'silent' | 'error';

type QueueItem =
  | { kind: 'dm'; userMessageId: number; text: string }
  // 群聊回合：出队时才构建增量 transcript；reaction = 接话轮（可 [PASS] 沉默）
  | { kind: 'room-turn'; mode: 'normal' | 'reaction'; resolve: (r: RoomTurnOutcome) => void };

const PASS_RE = /^[\s（(【\[]*(pass|不接话|沉默|skip)[\s）)】\]。.!～~]*$/i;

const QUEUE_CAP = 5;
const CRASH_LOCKOUT = 3;
const CRASH_WINDOW_MS = 5 * 60_000;

interface Deps {
  db: Db;
  sse: SseHub;
  config: HubConfig;
  vault: VaultClient | null;
}

/**
 * 一个"某成员在某会话里"的运行时。DM 时 convo === agent；
 * 群聊时 convo 是 room 行、agent 是成员联系人（各成员独立会话互不拖累）。
 */
export class AgentRuntime {
  private queue: QueueItem[] = [];
  private running = false;
  private backend: AgentBackend | null = null;
  private backendStartedAt = 0;
  private currentHandle: TurnHandle | null = null;
  private crashes: number[] = [];
  private seenMemoryPaths = new Set<string>();
  state = 'idle';

  constructor(private convo: ContactRow, private agent: ContactRow, private deps: Deps) {}

  private get isRoom(): boolean {
    return this.convo.id !== this.agent.id;
  }

  private get memberId(): string {
    return this.isRoom ? this.agent.id : '';
  }

  /** 记忆配置：全局 < 成员自己的 < 群覆盖 */
  private memCfg(): MemoryConfig {
    const agentCfg = JSON.parse(this.agent.config || '{}');
    const convoCfg = JSON.parse(this.convo.config || '{}');
    return {
      ...this.deps.config.memory,
      ...(agentCfg.memory ?? {}),
      ...(this.isRoom ? convoCfg.memory ?? {} : {}),
    };
  }

  async updateAgent(row: ContactRow): Promise<void> {
    this.agent = row;
    if (!this.isRoom) this.convo = row;
    if (this.backend) {
      await this.backend.stop();
      this.backend = null;
    }
  }

  updateConvo(row: ContactRow): void {
    if (this.isRoom) this.convo = row;
  }

  enqueue(item: { userMessageId: number; text: string }): 'queued' | 'full' {
    if (this.queue.length >= QUEUE_CAP) return 'full';
    this.queue.push({ kind: 'dm', ...item });
    void this.run();
    return 'queued';
  }

  /** 群聊回合：编排器 await 结果（spoke/silent/error），实现顺序发言与接话轮。 */
  runRoomTurn(mode: 'normal' | 'reaction'): Promise<RoomTurnOutcome> {
    return new Promise((resolve) => {
      this.queue.push({ kind: 'room-turn', mode, resolve });
      void this.run();
    });
  }

  interrupt(): void {
    void this.currentHandle?.interrupt();
  }

  async reset(): Promise<void> {
    this.queue = [];
    await this.backend?.stop();
    this.backend = null;
    this.crashes = [];
    deactivateSession(this.deps.db, this.convo.id, this.isRoom ? this.memberId : undefined);
    this.setState('idle');
  }

  async stop(): Promise<void> {
    await this.backend?.stop();
    this.backend = null;
  }

  private log(msg: string): void {
    const tag = this.isRoom ? `${this.convo.name}·${this.agent.name}` : this.agent.name;
    console.log(`  [${tag}] ${msg}`);
  }

  private setState(state: string, detail?: string): void {
    this.state = state;
    this.deps.sse.broadcast('status', {
      contactId: this.convo.id,
      state,
      detail,
      member: this.isRoom ? this.agent.name : undefined,
    });
  }

  /** 发言人显示名：user → owner 的资料名，其余查联系人表。 */
  private nameOf(sender: string): string {
    if (sender === 'user') return getUserProfile(this.deps.db).name;
    if (sender === this.agent.id) return this.agent.name;
    const row = this.deps.db.prepare('SELECT name FROM contacts WHERE id = ?').get(sender) as
      | { name: string }
      | undefined;
    return row?.name ?? sender;
  }

  private insertMessage(fields: {
    role: string;
    kind: string;
    content: string;
    status: string;
    turnId: string | null;
    meta?: unknown;
  }): MessageRow {
    const { db } = this.deps;
    const r = db
      .prepare(
        `INSERT INTO messages (contact_id, sender, role, kind, content, status, turn_id, meta)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
      )
      .run(
        this.convo.id,
        this.agent.id,
        fields.role,
        fields.kind,
        fields.content,
        fields.status,
        fields.turnId,
        JSON.stringify(fields.meta ?? {})
      );
    return db
      .prepare('SELECT * FROM messages WHERE id = ?')
      .get(Number(r.lastInsertRowid)) as MessageRow;
  }

  private updateMessage(id: number, content: string, status: string, meta?: unknown): MessageRow {
    const { db } = this.deps;
    if (meta !== undefined) {
      db.prepare('UPDATE messages SET content = ?, status = ?, meta = ? WHERE id = ?').run(
        content,
        status,
        JSON.stringify(meta),
        id
      );
    } else {
      db.prepare('UPDATE messages SET content = ?, status = ? WHERE id = ?').run(content, status, id);
    }
    return db.prepare('SELECT * FROM messages WHERE id = ?').get(id) as MessageRow;
  }

  private async run(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      while (this.queue.length > 0) {
        const item = this.queue.shift()!;
        await this.processTurn(item);
      }
    } finally {
      this.running = false;
    }
  }

  private lockedOut(): boolean {
    const now = Date.now();
    this.crashes = this.crashes.filter((t) => now - t < CRASH_WINDOW_MS);
    return this.crashes.length >= CRASH_LOCKOUT;
  }

  private recordCrash(): void {
    this.crashes.push(Date.now());
  }

  /** 群聊身份框架：进 system prompt，讲清群规（@ 不召唤、带名字前缀等）。 */
  private roomFraming(): string {
    if (!this.isRoom) return '';
    const cfg = JSON.parse(this.convo.config || '{}');
    const memberIds: string[] = cfg.members ?? [];
    const names = memberIds.map((id) => this.nameOf(id));
    const userName = getUserProfile(this.deps.db).name;
    return [
      '',
      `# 群聊模式：「${this.convo.name}」`,
      `成员：${names.join('、')}；用户：${userName}。你是其中的「${this.agent.name}」。`,
      '- 你收到的群消息带「名字：」前缀标明发言人；你自己发言直接说内容，不要加前缀。',
      '- 群里 @某人 不会自动召唤对方。想让谁跟进就直接说出来，由用户决定叫谁。',
      '- 群聊节奏：简短、有自己观点、不复读别人说过的，不用每条都接。',
      '- 每轮发言后有"接话轮"：你会看到其他成员刚说的话，可以自然接话、反驳、补充；',
      '  没什么想说的就只回 [PASS]（会被网关静默处理，不丢人）。宁可 PASS 也别硬找话。',
      '- 其他成员的错误/掉线由网关处理，你不会看到，也不用分析。',
    ].join('\n');
  }

  private async ensureStarted(): Promise<void> {
    if (this.backend?.alive()) return;
    const cfg = JSON.parse(this.agent.config || '{}');
    const mem = this.memCfg();
    const resumeToken = getActiveSession(this.deps.db, this.convo.id, this.memberId);

    let preamble = '';
    if (this.deps.vault && mem.injectOnSpawn) {
      try {
        preamble = await buildSessionPreamble(this.deps.vault, {
          id: this.agent.id,
          name: this.agent.name,
          backend: this.agent.backend,
        });
        this.log(`memory preamble injected (${preamble.length} chars)`);
      } catch (e: any) {
        preamble = PREAMBLE_UNAVAILABLE;
        this.log(`memory preamble unavailable: ${e.message}`);
      }
    }
    this.seenMemoryPaths.clear();

    preamble = [this.roomFraming(), preamble].filter(Boolean).join('\n');

    if (!resumeToken) {
      const bridge = this.buildBridge();
      if (bridge) {
        preamble = [preamble, bridge].filter(Boolean).join('\n');
        this.log('conversation archive bridged into fresh session');
      }
    }

    if (this.agent.backend === 'claude-cli') {
      const cwd = path.resolve(this.deps.config.agentsDir, cfg.cwd ?? this.agent.id);
      fs.mkdirSync(cwd, { recursive: true });
      this.backend = new ClaudeCliBackend({
        cliPath: cfg.cliPath ?? this.deps.config.claude.cliPath,
        cwd,
        model: cfg.model ?? undefined,
        allowedTools: cfg.allowedTools ?? undefined,
        disallowedTools: cfg.disallowedTools ?? undefined,
        appendSystemPrompt:
          [cfg.appendSystemPrompt, preamble].filter(Boolean).join('\n') || undefined,
        permissionMode: cfg.permissionMode ?? undefined,
        mcpConfig: cfg.mcpConfig ?? undefined,
        turnTimeoutMs: this.deps.config.claude.turnTimeoutMs,
        log: (m) => this.log(m),
      });
    } else if (this.agent.backend === 'codex') {
      const cwd = path.resolve(this.deps.config.agentsDir, cfg.cwd ?? this.agent.id);
      fs.mkdirSync(cwd, { recursive: true });
      this.backend = new CodexAppServerBackend({
        cliPath: cfg.cliPath ?? this.deps.config.codex.cliPath,
        cwd,
        model: cfg.model ?? undefined,
        developerInstructions:
          [cfg.developerInstructions, preamble].filter(Boolean).join('\n') || undefined,
        turnTimeoutMs: this.deps.config.codex.turnTimeoutMs,
        log: (m) => this.log(m),
      });
    } else if (this.agent.backend === 'api') {
      const apiKey: string = cfg.apiKey || (cfg.apiKeyRef ? process.env[cfg.apiKeyRef] ?? '' : '');
      this.backend = new DirectApiBackend({
        provider: cfg.provider === 'anthropic' ? 'anthropic' : 'openai-compat',
        baseUrl: cfg.baseUrl ?? 'https://api.openai.com',
        apiKey,
        model: cfg.model ?? '',
        systemPrompt: [cfg.systemPrompt, preamble].filter(Boolean).join('\n') || undefined,
        maxHistoryMessages: cfg.maxHistoryMessages ?? 60,
        maxTokens: cfg.maxTokens ?? 4096,
        turnTimeoutMs: this.deps.config.claude.turnTimeoutMs,
        db: this.deps.db,
        contactId: this.convo.id,
        log: (m) => this.log(m),
        roomMode: this.isRoom
          ? { selfId: this.agent.id, nameOf: (s) => this.nameOf(s) }
          : undefined,
      });
    } else {
      throw new Error(`backend "${this.agent.backend}" 不认识`);
    }

    this.log(`starting backend${resumeToken ? ` (resume ${resumeToken.slice(0, 8)}…)` : ''}`);
    await this.backend.start(resumeToken);
    this.backendStartedAt = Date.now();
  }

  /** 编辑/删除后的对话存档回放：被删内容天然不在其中——上下文诚实。 */
  private buildBridge(): string {
    if (this.agent.backend === 'api') return ''; // api 每轮都从 DB 重建历史
    const rows = this.deps.db
      .prepare(
        `SELECT sender, content FROM messages
         WHERE contact_id = ? AND kind = 'text' AND status = 'done' AND deleted = 0
         ORDER BY id DESC LIMIT 30`
      )
      .all(this.convo.id) as { sender: string; content: string }[];
    if (rows.length === 0) return '';
    const lines = rows.reverse().map((r) => `${this.nameOf(r.sender)}：${r.content.slice(0, 400)}`);
    return [
      '',
      '# 对话存档回放（网关注入）',
      '此前的 CLI 会话已被重置（消息被编辑或删除）。以下是保留下来的近期对话，被删除的内容不在其中，请以此为准继续，别提"会话重置"这回事：',
      '',
      ...lines,
    ].join('\n');
  }

  /** 编辑/删除触及 CLI 上下文 → 重置会话，下次 spawn 用存档回放。 */
  async invalidateCliContext(): Promise<void> {
    if (this.agent.backend === 'api') return;
    deactivateSession(this.deps.db, this.convo.id, this.isRoom ? this.memberId : undefined);
    if (this.isRoom) {
      // 存档回放会覆盖历史，跳过重复的增量投递
      const max = this.deps.db
        .prepare('SELECT COALESCE(MAX(id), 0) AS m FROM messages WHERE contact_id = ?')
        .get(this.convo.id) as { m: number };
      setLastSeen(this.deps.db, this.convo.id, this.agent.id, max.m);
    }
    if (this.backend) {
      await this.backend.stop();
      this.backend = null;
    }
    this.log('CLI context invalidated (edit/delete) — will replay archive on next spawn');
  }

  /** 从某条 user 消息重新生成（仅 DM）。 */
  async regenerateFrom(userMessageId: number, text: string): Promise<'queued' | 'full'> {
    this.deps.db
      .prepare('UPDATE messages SET deleted = 1 WHERE contact_id = ? AND id > ?')
      .run(this.convo.id, userMessageId);
    this.deps.sse.broadcast('prune', { contactId: this.convo.id, afterId: userMessageId });
    await this.invalidateCliContext();
    return this.enqueue({ userMessageId, text });
  }

  private async maybeRecycleStale(): Promise<void> {
    const mem = this.memCfg();
    if (!this.deps.vault || !mem.injectOnSpawn) return;
    const maxAgeMs = mem.sessionMaxAgeHours * 3_600_000;
    if (this.backend?.alive() && maxAgeMs > 0 && Date.now() - this.backendStartedAt > maxAgeMs) {
      this.log(`backend older than ${mem.sessionMaxAgeHours}h — recycling for fresh memory context`);
      await this.backend.stop();
      this.backend = null;
    }
  }

  /** 群聊增量投递：把该成员未读的文本消息拼成带名字的 transcript。
   *  错误/工具消息永不进入（GPT 建议 #9）。 */
  private buildRoomDelivery(): { text: string; upToId: number } | null {
    const lastSeen = getLastSeen(this.deps.db, this.convo.id, this.agent.id);
    const rows = this.deps.db
      .prepare(
        `SELECT id, sender, content FROM messages
         WHERE contact_id = ? AND id > ? AND deleted = 0 AND kind = 'text' AND status = 'done'
           AND sender != ?
         ORDER BY id ASC LIMIT 50`
      )
      .all(this.convo.id, lastSeen, this.agent.id) as {
      id: number;
      sender: string;
      content: string;
    }[];
    if (rows.length === 0) return null;
    const upToId = rows[rows.length - 1].id;
    const lines = rows.map((r) => `${this.nameOf(r.sender)}：${r.content}`);
    return { text: lines.join('\n'), upToId };
  }

  private async processTurn(item: QueueItem): Promise<void> {
    const { sse } = this.deps;
    const convoId = this.convo.id;

    // 群回合结果只回传一次
    let settled = false;
    const settle = (r: RoomTurnOutcome) => {
      if (item.kind === 'room-turn' && !settled) {
        settled = true;
        item.resolve(r);
      }
    };

    if (this.lockedOut()) {
      const row = this.insertMessage({
        role: 'system',
        kind: 'error',
        content: `${this.isRoom ? `${this.agent.name} ` : ''}连续崩了好几次，先歇了。用会话重置（session/reset）再叫我。`,
        status: 'done',
        turnId: null,
      });
      sse.broadcast('message', row);
      this.setState('error', 'crash lockout');
      this.queue = [];
      settle('error');
      return;
    }

    // 群聊：出队时构建增量投递（合批天然完成）
    let delivery: { text: string; upToId: number } | null = null;
    if (item.kind === 'room-turn') {
      delivery = this.buildRoomDelivery();
      if (!delivery) {
        settle('silent'); // 没有新东西可回
        return;
      }
    }

    try {
      await this.maybeRecycleStale();
      await this.ensureStarted();
    } catch (e: any) {
      this.recordCrash();
      this.backend = null;
      const row = this.insertMessage({
        role: 'system',
        kind: 'error',
        content: `${this.isRoom ? `${this.agent.name} ` : ''}后端启动失败：${e.message}`,
        status: 'done',
        turnId: null,
      });
      sse.broadcast('message', row);
      this.setState('error', e.message);
      settle('error');
      return;
    }

    const turnId = crypto.randomUUID();
    this.setState('thinking');

    let textRow: MessageRow | null = null;
    let thinkingRow: MessageRow | null = null;
    let textBuf = '';
    let thinkingBuf = '';

    // 本轮实际投喂的文本
    const sourceText = item.kind === 'dm' ? item.text : delivery!.text;
    const reactionSuffix =
      '（接话机会：看完上面新发言，想接就简短接一句；没什么可补充就只回 [PASS]。）';
    const normalSuffix = '（轮到你了。实在没话说也可以只回 [PASS]。）';
    let turnText: string;
    if (item.kind === 'dm') {
      turnText = item.text;
    } else if (this.agent.backend === 'api') {
      // api 成员的群历史（含最新消息）由 roomMode history 携带，这里只需提示发言
      turnText = `（群里有新消息，见对话历史。）${item.kind === 'room-turn' && item.mode === 'reaction' ? reactionSuffix : normalSuffix}`;
    } else {
      turnText = `${delivery!.text}\n\n${item.kind === 'room-turn' && item.mode === 'reaction' ? reactionSuffix : normalSuffix}`;
    }

    const mem = this.memCfg();
    if (this.deps.vault && mem.searchPerTurn) {
      try {
        const block = await buildTurnBlock(
          this.deps.vault,
          sourceText,
          this.seenMemoryPaths,
          mem.maxTurnChars
        );
        if (block) {
          this.log(`memory search injected ${block.split('\n').length} entries`);
          turnText = wrapTurnText(turnText, block);
        }
      } catch {
        // best-effort — preamble is the guaranteed layer
      }
    }

    const handle = this.backend!.sendTurn({ text: turnText });
    this.currentHandle = handle;

    try {
      for await (const ev of handle.events) {
        switch (ev.type) {
          case 'session':
            saveSession(this.deps.db, convoId, ev.sessionId, this.memberId);
            break;

          case 'delta':
            if (!textRow) {
              textRow = this.insertMessage({
                role: 'assistant',
                kind: 'text',
                content: '',
                status: 'streaming',
                turnId,
              });
              sse.broadcast('message', textRow);
              this.setState('streaming');
            }
            textBuf += ev.text;
            sse.broadcast('delta', { contactId: convoId, messageId: textRow.id, text: ev.text });
            break;

          case 'thinking':
            if (!thinkingRow) {
              thinkingRow = this.insertMessage({
                role: 'assistant',
                kind: 'thinking',
                content: '',
                status: 'streaming',
                turnId,
              });
              sse.broadcast('message', thinkingRow);
            }
            thinkingBuf += ev.text;
            sse.broadcast('delta', { contactId: convoId, messageId: thinkingRow.id, text: ev.text });
            break;

          case 'tool_use': {
            const row = this.insertMessage({
              role: 'assistant',
              kind: 'tool_use',
              content: ev.name,
              status: 'done',
              turnId,
              meta: { name: ev.name, input: ev.inputSummary },
            });
            sse.broadcast('message', row);
            this.setState(`tool:${ev.name}`);
            break;
          }

          case 'tool_result':
            this.setState('thinking', `${ev.name}: ${ev.ok ? 'ok' : 'denied/failed'}`);
            break;

          case 'done': {
            if (thinkingRow) {
              sse.broadcast('message', this.updateMessage(thinkingRow.id, thinkingBuf, 'done'));
            }
            const finalText = ev.finalText || textBuf;
            const passed = this.isRoom && PASS_RE.test(finalText.trim());

            if (passed) {
              // 成员选择沉默：把已流出去的气泡收回（软删 + prune）
              if (textRow) {
                this.deps.db
                  .prepare('UPDATE messages SET deleted = 1, status = ? WHERE id = ?')
                  .run('done', textRow.id);
                sse.broadcast('prune', { contactId: convoId, ids: [textRow.id] });
              }
              if (thinkingRow) {
                this.deps.db.prepare('UPDATE messages SET deleted = 1 WHERE id = ?').run(thinkingRow.id);
                sse.broadcast('prune', { contactId: convoId, ids: [thinkingRow.id] });
              }
              this.log('passed (silent)');
            } else if (textRow) {
              sse.broadcast(
                'message',
                this.updateMessage(textRow.id, finalText, 'done', { usage: ev.usage })
              );
            } else if (finalText) {
              const row = this.insertMessage({
                role: 'assistant',
                kind: 'text',
                content: finalText,
                status: 'done',
                turnId,
                meta: { usage: ev.usage },
              });
              sse.broadcast('message', row);
            }
            if (item.kind === 'room-turn' && delivery) {
              setLastSeen(this.deps.db, convoId, this.agent.id, delivery.upToId);
            }
            this.crashes = [];
            this.setState('idle');
            // 自动捕捉只在 DM 里跑：群消息由派发层按"owner 原话、群级一次"捕捉，
            // 成员发言（带名字前缀的 transcript）永不参与——防记忆污染
            if (!this.isRoom && this.deps.vault && mem.capture) {
              void maybeCapture(
                this.deps.vault,
                { id: this.agent.id, name: this.agent.name },
                getUserProfile(this.deps.db).name,
                sourceText,
                finalText,
                (m) => this.log(m)
              ).catch(() => {});
            }
            settle(passed ? 'silent' : 'spoke');
            break;
          }

          case 'error': {
            if (thinkingRow) {
              sse.broadcast('message', this.updateMessage(thinkingRow.id, thinkingBuf, 'interrupted'));
            }
            if (textRow) {
              sse.broadcast('message', this.updateMessage(textRow.id, textBuf, 'interrupted'));
            }
            const row = this.insertMessage({
              role: 'system',
              kind: 'error',
              content: this.isRoom ? `${this.agent.name}：${ev.message}` : ev.message,
              status: 'done',
              turnId,
            });
            sse.broadcast('message', row);
            if (ev.fatal) {
              this.recordCrash();
              this.backend = null;
            }
            this.setState('error', ev.message);
            settle('error');
            break;
          }
        }
      }
    } finally {
      this.currentHandle = null;
      settle('error'); // 流意外结束的兜底
      if (this.state === 'streaming' || this.state === 'thinking' || this.state.startsWith('tool:')) {
        this.setState('idle');
      }
    }
  }
}

export class AgentManager {
  private runtimes = new Map<string, AgentRuntime>();

  constructor(private deps: Deps) {}

  /** DM runtime。 */
  get(contact: ContactRow): AgentRuntime {
    let rt = this.runtimes.get(contact.id);
    if (!rt) {
      rt = new AgentRuntime(contact, contact, this.deps);
      this.runtimes.set(contact.id, rt);
    }
    return rt;
  }

  /** 群成员 runtime。 */
  getRoomMember(room: ContactRow, member: ContactRow): AgentRuntime {
    const key = `${room.id}:${member.id}`;
    let rt = this.runtimes.get(key);
    if (!rt) {
      rt = new AgentRuntime(room, member, this.deps);
      this.runtimes.set(key, rt);
    }
    return rt;
  }

  private roomMembers(room: ContactRow): ContactRow[] {
    const cfg = JSON.parse(room.config || '{}');
    const ids: string[] = Array.isArray(cfg.members) ? cfg.members : [];
    if (ids.length === 0) return [];
    const placeholders = ids.map(() => '?').join(',');
    return this.deps.db
      .prepare(
        `SELECT * FROM contacts WHERE id IN (${placeholders}) AND enabled = 1 AND kind = 'dm'`
      )
      .all(...ids) as ContactRow[];
  }

  /** 点名解析：@名字/@id/@all；模型消息里的 @ 一律不算（只处理 user 消息）。 */
  parseTargets(room: ContactRow, content: string): ContactRow[] {
    const members = this.roomMembers(room);
    if (members.length === 0) return [];
    const mentions = [...content.matchAll(/@([^\s@，。！？、,!?：:；;]+)/g)].map((m) =>
      m[1].toLowerCase()
    );
    if (mentions.length === 0) return members;
    if (mentions.some((m) => m === 'all' || m === '所有人' || m === '大家')) return members;
    const hit = members.filter(
      (c) => mentions.includes(c.id.toLowerCase()) || mentions.includes(c.name.toLowerCase())
    );
    return hit.length > 0 ? hit : members;
  }

  private roomChains = new Map<string, Promise<void>>();

  /** 用户在群里发言 → 顺序点名轮 + 接话轮（输出不互相触发，轮数硬上限）。
   *  记忆捕捉在这里做且只做一次：只看 owner 的原话，成员发言永不参与。 */
  dispatchRoomMessage(room: ContactRow, content: string): string[] {
    const targets = this.parseTargets(room, content);

    const roomCfg = JSON.parse(room.config || '{}');
    const mem: MemoryConfig = { ...this.deps.config.memory, ...(roomCfg.memory ?? {}) };
    if (this.deps.vault && mem.capture) {
      void maybeCapture(
        this.deps.vault,
        { id: room.id, name: room.name },
        getUserProfile(this.deps.db).name,
        content,
        '',
        (m) => console.log(`  [${room.name}] ${m}`)
      ).catch(() => {});
    }

    // 同一个群的轮次串行：用户连发消息时排队，不交叉
    const prev = this.roomChains.get(room.id) ?? Promise.resolve();
    this.roomChains.set(
      room.id,
      prev
        .then(() => this.runRoomRound(room, targets))
        .catch((e) => console.error(`  [${room.name}] round error:`, e))
    );
    return targets.map((t) => t.id);
  }

  private shuffle<T>(arr: T[]): T[] {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  /** 一轮群聊：点名成员按随机顺序依次发言（后发言者看得见先发言者），
   *  然后跑至多 reactionRounds 轮接话——全体成员看过新发言后可接话或 [PASS]。 */
  private async runRoomRound(room: ContactRow, targets: ContactRow[]): Promise<void> {
    for (const member of this.shuffle(targets)) {
      await this.getRoomMember(room, member).runRoomTurn('normal');
    }

    const roomCfg = JSON.parse(room.config || '{}');
    const maxReactionRounds = Math.min(Math.max(Number(roomCfg.reactionRounds ?? 1), 0), 3);
    const everyone = this.roomMembers(room);

    for (let round = 0; round < maxReactionRounds; round++) {
      let anySpoke = false;
      for (const member of this.shuffle(everyone)) {
        const outcome = await this.getRoomMember(room, member).runRoomTurn('reaction');
        if (outcome === 'spoke') anySpoke = true;
      }
      if (!anySpoke) break; // 全员沉默，话题自然结束
    }
  }

  /** 会话状态聚合（列表小圆点用）：DM 直取；群取最忙成员。 */
  stateOf(contactId: string): string {
    const dm = this.runtimes.get(contactId);
    if (dm) return dm.state;
    let agg = 'idle';
    for (const [key, rt] of this.runtimes) {
      if (!key.startsWith(`${contactId}:`)) continue;
      if (rt.state === 'streaming' || rt.state.startsWith('tool:')) return rt.state;
      if (rt.state === 'thinking') agg = 'thinking';
      else if (rt.state === 'error' && agg === 'idle') agg = 'error';
    }
    return agg;
  }

  private runtimesOfRoom(roomId: string): AgentRuntime[] {
    return [...this.runtimes.entries()]
      .filter(([key]) => key.startsWith(`${roomId}:`))
      .map(([, rt]) => rt);
  }

  interruptAll(contact: ContactRow): void {
    if (contact.kind === 'room') {
      for (const rt of this.runtimesOfRoom(contact.id)) rt.interrupt();
    } else {
      this.runtimes.get(contact.id)?.interrupt();
    }
  }

  async resetConversation(contact: ContactRow): Promise<void> {
    if (contact.kind === 'room') {
      for (const rt of this.runtimesOfRoom(contact.id)) await rt.reset();
      deactivateSession(this.deps.db, contact.id); // 兜底：包括没有 runtime 的成员
    } else {
      await this.get(contact).reset();
    }
  }

  /** 删除消息后的诚实处理：DM 单 runtime；群里全体成员会话重置。 */
  async invalidateConversation(contact: ContactRow): Promise<void> {
    if (contact.kind === 'room') {
      for (const member of this.roomMembers(contact)) {
        await this.getRoomMember(contact, member).invalidateCliContext();
      }
    } else {
      await this.get(contact).invalidateCliContext();
    }
  }

  async notifyContactUpdated(contact: ContactRow): Promise<void> {
    if (contact.kind === 'room') {
      for (const rt of this.runtimesOfRoom(contact.id)) rt.updateConvo(contact);
      return;
    }
    const rt = this.runtimes.get(contact.id);
    if (rt) await rt.updateAgent(contact);
    // 该联系人作为群成员的 runtime 也要换新配置
    for (const [key, roomRt] of this.runtimes) {
      if (key.endsWith(`:${contact.id}`)) await roomRt.updateAgent(contact);
    }
  }

  async remove(contactId: string): Promise<void> {
    for (const [key, rt] of [...this.runtimes]) {
      if (key === contactId || key.startsWith(`${contactId}:`) || key.endsWith(`:${contactId}`)) {
        await rt.stop();
        this.runtimes.delete(key);
      }
    }
  }

  async stopAll(): Promise<void> {
    await Promise.all([...this.runtimes.values()].map((rt) => rt.stop()));
  }
}
