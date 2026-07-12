import type { Db, MessageRow } from '../db.js';
import { AsyncQueue, type AgentBackend, type TurnEvent, type TurnHandle } from './types.js';

export interface DirectApiBackendOpts {
  provider: 'anthropic' | 'openai-compat';
  baseUrl: string;
  apiKey: string;
  model: string;
  systemPrompt?: string; // persona + 网关注入的记忆 preamble
  maxHistoryMessages: number;
  maxTokens: number;
  turnTimeoutMs: number;
  db: Db;
  contactId: string;
  log: (msg: string) => void;
  /** 群聊模式：自己的历史发言→assistant，其他人（含用户）→带名字前缀的 user */
  roomMode?: {
    selfId: string;
    nameOf: (sender: string) => string;
  };
}

/**
 * Stateless streaming backend over raw HTTP APIs. Conversation state lives in
 * the gateway DB (last N text messages become the messages array), so there is
 * no resume token — every turn is a full request.
 */
export class DirectApiBackend implements AgentBackend {
  readonly kind = 'api' as const;
  private stopped = false;

  constructor(private opts: DirectApiBackendOpts) {}

  async start(_resumeToken: string | null): Promise<void> {
    if (!this.opts.apiKey) throw new Error('这个联系人还没配 API key（联系人设置里填）');
    if (!this.opts.model) throw new Error('这个联系人还没配 model');
  }

  alive(): boolean {
    return !this.stopped;
  }

  async stop(): Promise<void> {
    this.stopped = true;
  }

  private history(currentText: string): { role: 'user' | 'assistant'; content: string }[] {
    const rows = this.opts.db
      .prepare(
        `SELECT * FROM messages
         WHERE contact_id = ? AND kind = 'text' AND status = 'done' AND deleted = 0
           AND role IN ('user','assistant')
         ORDER BY id DESC LIMIT ?`
      )
      .all(this.opts.contactId, this.opts.maxHistoryMessages) as MessageRow[];

    const room = this.opts.roomMode;
    const msgs = rows.reverse().map((r) => {
      if (room) {
        return r.sender === room.selfId
          ? { role: 'assistant' as const, content: r.content }
          : { role: 'user' as const, content: `${room.nameOf(r.sender)}：${r.content}` };
      }
      return { role: r.role as 'user' | 'assistant', content: r.content };
    });

    // 相邻同角色合并（群聊里连续多条 user 很常见，anthropic 要求交替）
    const merged: { role: 'user' | 'assistant'; content: string }[] = [];
    for (const m of msgs) {
      const last = merged[merged.length - 1];
      if (last && last.role === m.role) last.content += `\n${m.content}`;
      else merged.push({ ...m });
    }

    if (room) {
      // 群聊：全部内容都在历史里（含最新消息），currentText 只是提示发言
      merged.push({ role: 'user', content: currentText });
    } else {
      // DM：当前这条已落库，但注入检索块后的版本以参数为准
      if (merged.length > 0 && merged[merged.length - 1].role === 'user') merged.pop();
      merged.push({ role: 'user', content: currentText });
    }

    while (merged.length > 0 && merged[0].role === 'assistant') merged.shift();
    return merged;
  }

  sendTurn(input: { text: string }): TurnHandle {
    const queue = new AsyncQueue<TurnEvent>();
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), this.opts.turnTimeoutMs);

    void (async () => {
      try {
        const messages = this.history(input.text);
        if (this.opts.provider === 'anthropic') {
          await this.streamAnthropic(messages, queue, abort.signal);
        } else {
          await this.streamOpenAi(messages, queue, abort.signal);
        }
      } catch (e: any) {
        queue.push({
          type: 'error',
          message: abort.signal.aborted ? '请求超时/被打断' : `API 请求失败：${e.message}`,
          fatal: false,
        });
      } finally {
        clearTimeout(timer);
        queue.end();
      }
    })();

    return {
      events: queue,
      interrupt: async () => abort.abort(),
    };
  }

  private async *sseEvents(res: Response): AsyncGenerator<string> {
    const decoder = new TextDecoder();
    let buf = '';
    for await (const chunk of res.body as any) {
      buf += decoder.decode(chunk, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf('\n')) !== -1) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (line.startsWith('data:')) yield line.slice(5).trim();
      }
    }
  }

  private async streamAnthropic(
    messages: { role: string; content: string }[],
    queue: AsyncQueue<TurnEvent>,
    signal: AbortSignal
  ): Promise<void> {
    const url = `${this.opts.baseUrl.replace(/\/+$/, '')}/v1/messages`;
    const res = await fetch(url, {
      method: 'POST',
      signal,
      headers: {
        'content-type': 'application/json',
        'x-api-key': this.opts.apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: this.opts.model,
        max_tokens: this.opts.maxTokens,
        stream: true,
        ...(this.opts.systemPrompt ? { system: this.opts.systemPrompt } : {}),
        messages,
      }),
    });
    if (!res.ok || !res.body) {
      const body = await res.text().catch(() => '');
      throw new Error(`${res.status} ${body.slice(0, 200)}`);
    }

    let acc = '';
    let usage: { input: number; output: number } | undefined;
    for await (const data of this.sseEvents(res)) {
      if (!data || data === '[DONE]') continue;
      let ev: any;
      try {
        ev = JSON.parse(data);
      } catch {
        continue;
      }
      if (ev.type === 'content_block_delta') {
        if (ev.delta?.type === 'text_delta' && ev.delta.text) {
          acc += ev.delta.text;
          queue.push({ type: 'delta', text: ev.delta.text });
        } else if (ev.delta?.type === 'thinking_delta' && ev.delta.thinking) {
          queue.push({ type: 'thinking', text: ev.delta.thinking });
        }
      } else if (ev.type === 'message_start' && ev.message?.usage) {
        usage = { input: ev.message.usage.input_tokens ?? 0, output: 0 };
      } else if (ev.type === 'message_delta' && ev.usage) {
        usage = { input: usage?.input ?? 0, output: ev.usage.output_tokens ?? 0 };
      } else if (ev.type === 'error') {
        throw new Error(ev.error?.message ?? 'stream error');
      }
    }
    queue.push({ type: 'done', finalText: acc, usage });
  }

  private async streamOpenAi(
    messages: { role: string; content: string }[],
    queue: AsyncQueue<TurnEvent>,
    signal: AbortSignal
  ): Promise<void> {
    const url = `${this.opts.baseUrl.replace(/\/+$/, '')}/v1/chat/completions`;
    const res = await fetch(url, {
      method: 'POST',
      signal,
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${this.opts.apiKey}`,
      },
      body: JSON.stringify({
        model: this.opts.model,
        stream: true,
        stream_options: { include_usage: true },
        messages: [
          ...(this.opts.systemPrompt
            ? [{ role: 'system', content: this.opts.systemPrompt }]
            : []),
          ...messages,
        ],
      }),
    });
    if (!res.ok || !res.body) {
      const body = await res.text().catch(() => '');
      throw new Error(`${res.status} ${body.slice(0, 200)}`);
    }

    let acc = '';
    let usage: { input: number; output: number } | undefined;
    for await (const data of this.sseEvents(res)) {
      if (!data) continue;
      if (data === '[DONE]') break;
      let ev: any;
      try {
        ev = JSON.parse(data);
      } catch {
        continue;
      }
      const delta = ev.choices?.[0]?.delta;
      if (delta?.content) {
        acc += delta.content;
        queue.push({ type: 'delta', text: delta.content });
      }
      if (delta?.reasoning_content) {
        queue.push({ type: 'thinking', text: delta.reasoning_content });
      }
      if (ev.usage) {
        usage = {
          input: ev.usage.prompt_tokens ?? ev.usage.input_tokens ?? 0,
          output: ev.usage.completion_tokens ?? ev.usage.output_tokens ?? 0,
        };
      }
    }
    queue.push({ type: 'done', finalText: acc, usage });
  }
}
