import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export interface QuotaWindow {
  remainingPct: number;
  resetsAt: string | null;
}

export interface ClaudeQuota {
  fiveHour: QuotaWindow | null;
  sevenDay: QuotaWindow | null;
  sevenDayOpus: QuotaWindow | null;
  fetchedAt: string;
}

/**
 * Best-effort subscription quota for claude-cli contacts, polled from the
 * OAuth usage endpoint the Claude Code /usage screen uses. Undocumented —
 * parsed defensively; when it breaks the UI simply shows token counts only.
 */
export class ClaudeQuotaPoller {
  private data: ClaudeQuota | null = null;
  private timer: NodeJS.Timeout | null = null;
  private failures = 0;
  private skipUntil = 0;

  constructor(private log: (msg: string) => void) {}

  start(intervalMs = 300_000): void {
    void this.poll();
    this.timer = setInterval(() => void this.poll(), intervalMs);
    this.timer.unref();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
  }

  get(): ClaudeQuota | null {
    return this.data;
  }

  private token(): string | null {
    // credentials 文件优先：usage 端点只认完整 /login 的 access token，
    // 拒绝 setup-token 生成的 sk-ant-oat（403）。
    try {
      const creds = JSON.parse(
        fs.readFileSync(path.join(os.homedir(), '.claude', '.credentials.json'), 'utf-8')
      );
      const t = creds?.claudeAiOauth?.accessToken;
      if (t) return t;
    } catch {}
    return process.env.CLAUDE_CODE_OAUTH_TOKEN ?? null;
  }

  private async poll(): Promise<void> {
    const token = this.token();
    if (!token) return;
    if (Date.now() < this.skipUntil) return; // 指数退避中

    try {
      const res = await fetch('https://api.anthropic.com/api/oauth/usage', {
        headers: {
          authorization: `Bearer ${token}`,
          'anthropic-beta': 'oauth-2025-04-20',
          // 端点校验客户端身份：没有这两个头会 401
          'user-agent': 'claude-cli/2.1.207 (external, cli)',
          'x-app': 'cli',
        },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const j: any = await res.json();

      const pick = (o: any): QuotaWindow | null =>
        o && typeof o.utilization === 'number'
          ? {
              remainingPct: Math.max(0, Math.round(100 - o.utilization)),
              resetsAt: o.resets_at ?? null,
            }
          : null;

      this.data = {
        fiveHour: pick(j.five_hour),
        sevenDay: pick(j.seven_day),
        sevenDayOpus: pick(j.seven_day_opus),
        fetchedAt: new Date().toISOString(),
      };
      this.failures = 0;
      this.skipUntil = 0;
    } catch (e: any) {
      this.failures++;
      // 退避：5min → 10 → 20 → … 封顶 2h，永不放弃（端点恢复就恢复）
      const backoffMs = Math.min(300_000 * 2 ** this.failures, 7_200_000);
      this.skipUntil = Date.now() + backoffMs;
      this.log(`claude quota poll failed (${this.failures}): ${e.message}, backing off ${Math.round(backoffMs / 60_000)}min`);
    }
  }
}
