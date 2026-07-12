import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import type { Db } from '../db.js';

/**
 * MCP client for the memory vault's streamable-http server, hardened for
 * gateway use: reconnect-on-failure with retries, and a SQLite outbox so
 * writes survive vault downtime (flushed every 60s) — the "断线兜底" layer.
 */
export class VaultClient {
  private client: Client | null = null;
  private connecting: Promise<Client> | null = null;
  private flushTimer: NodeJS.Timeout;
  private flushing = false;

  constructor(
    private url: string,
    private db: Db,
    private log: (msg: string) => void,
    private token: string | null = null
  ) {
    this.flushTimer = setInterval(() => void this.flushOutbox(), 60_000);
    this.flushTimer.unref();
  }

  private async connect(): Promise<Client> {
    if (this.client) return this.client;
    if (!this.connecting) {
      this.connecting = (async () => {
        const c = new Client({ name: 'ai-hub-gateway', version: '0.1.0' });
        const transport = new StreamableHTTPClientTransport(new URL(this.url), {
          requestInit: this.token
            ? { headers: { Authorization: `Bearer ${this.token}` } }
            : undefined,
        });
        await c.connect(transport);
        c.onclose = () => {
          if (this.client === c) this.client = null;
        };
        this.client = c;
        return c;
      })().finally(() => {
        this.connecting = null;
      });
    }
    return this.connecting;
  }

  /** Call a vault tool, reconnecting between attempts. Throws after retries. */
  async call(
    name: string,
    args: Record<string, unknown> = {},
    retries = 2
  ): Promise<string> {
    let lastErr: unknown;
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const c = await this.connect();
        const res: any = await c.callTool({ name, arguments: args });
        const text = (res?.content ?? [])
          .filter((b: any) => b.type === 'text')
          .map((b: any) => b.text)
          .join('\n');
        if (res?.isError) throw new Error(text.slice(0, 200) || `${name} returned error`);
        return text;
      } catch (e) {
        lastErr = e;
        this.client = null; // force fresh transport next attempt
        if (attempt < retries) await new Promise((r) => setTimeout(r, 500 * (attempt + 1)));
      }
    }
    throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
  }

  /** Fire a write; on failure park it in the outbox. Never throws. */
  async write(name: string, args: Record<string, unknown>): Promise<'ok' | 'queued'> {
    try {
      await this.call(name, args, 1);
      return 'ok';
    } catch (e: any) {
      this.log(`vault write ${name} failed (${e.message}), parked in outbox`);
      this.db
        .prepare('INSERT INTO memory_outbox (tool, args) VALUES (?, ?)')
        .run(name, JSON.stringify(args));
      return 'queued';
    }
  }

  async flushOutbox(): Promise<void> {
    if (this.flushing) return;
    this.flushing = true;
    try {
      const rows = this.db
        .prepare('SELECT * FROM memory_outbox ORDER BY id LIMIT 20')
        .all() as { id: number; tool: string; args: string; attempts: number }[];
      for (const row of rows) {
        try {
          await this.call(row.tool, JSON.parse(row.args), 0);
          this.db.prepare('DELETE FROM memory_outbox WHERE id = ?').run(row.id);
          this.log(`outbox flushed: ${row.tool} #${row.id}`);
        } catch (e: any) {
          this.db
            .prepare('UPDATE memory_outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?')
            .run(String(e.message).slice(0, 200), row.id);
          break; // vault still down — retry the rest next tick
        }
      }
    } finally {
      this.flushing = false;
    }
  }

  async close(): Promise<void> {
    clearInterval(this.flushTimer);
    try {
      await this.client?.close();
    } catch {}
    this.client = null;
  }
}
