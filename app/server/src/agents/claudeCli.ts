import { JsonlProcess } from './jsonlProcess.js';
import { AsyncQueue, type AgentBackend, type TurnEvent, type TurnHandle } from './types.js';

export interface ClaudeCliBackendOpts {
  cliPath: string;
  cwd: string;
  model?: string;
  allowedTools?: string[];
  disallowedTools?: string[];
  appendSystemPrompt?: string;
  permissionMode?: string;
  mcpConfig?: string;
  turnTimeoutMs: number;
  log: (msg: string) => void;
}

/**
 * Drives a persistent `claude` CLI child over stream-json stdio
 * (subscription quota, session continuity via --resume).
 *
 * Protocol notes (mirrors cc-connect agent/claudecode/session.go):
 *  in : {"type":"user","message":{"role":"user","content":"..."}}
 *  out: {"type":"system","subtype":"init","session_id":...}
 *       {"type":"stream_event","event":{...content_block_delta...}}   (--include-partial-messages)
 *       {"type":"assistant","message":{"content":[blocks]}}
 *       {"type":"user",...}                                            (tool_result echoes)
 *       {"type":"result","result":"...","session_id":...,"usage":{...}}
 *       {"type":"control_request",...}                                 (permission asks → we deny)
 */
export class ClaudeCliBackend implements AgentBackend {
  readonly kind = 'claude-cli' as const;

  private proc: JsonlProcess | null = null;
  private sessionId: string | null = null;
  private sessionAnnounced = false;
  private turn: AsyncQueue<TurnEvent> | null = null;
  private turnTimer: NodeJS.Timeout | null = null;
  private sawStreamText = false;
  private sawStreamThinking = false;
  private accText = '';
  private toolNames = new Map<string, string>(); // tool_use_id → name
  private stderrTail: string[] = [];
  private controlSeq = 0;

  constructor(private opts: ClaudeCliBackendOpts) {}

  // ── lifecycle ──────────────────────────────────────────

  async start(resumeToken: string | null): Promise<void> {
    const ok = await this.spawn(resumeToken);
    if (!ok && resumeToken) {
      this.opts.log(`resume ${resumeToken} failed, starting fresh session`);
      const fresh = await this.spawn(null);
      if (!fresh) throw new Error(`claude CLI failed to start: ${this.stderrSnippet()}`);
    } else if (!ok) {
      throw new Error(`claude CLI failed to start: ${this.stderrSnippet()}`);
    }
  }

  private spawn(resumeToken: string | null): Promise<boolean> {
    const args = [
      '--output-format', 'stream-json',
      '--input-format', 'stream-json',
      '--verbose',
      '--include-partial-messages',
      '--permission-prompt-tool', 'stdio',
    ];
    if (resumeToken) args.push('--resume', resumeToken);
    if (this.opts.model) args.push('--model', this.opts.model);
    if (this.opts.permissionMode) args.push('--permission-mode', this.opts.permissionMode);
    if (this.opts.allowedTools?.length) args.push('--allowedTools', this.opts.allowedTools.join(','));
    if (this.opts.disallowedTools?.length)
      args.push('--disallowedTools', this.opts.disallowedTools.join(','));
    if (this.opts.appendSystemPrompt)
      args.push('--append-system-prompt', this.opts.appendSystemPrompt);
    if (this.opts.mcpConfig) args.push('--mcp-config', this.opts.mcpConfig, '--strict-mcp-config');

    // Force the subscription login: settings.json "env" blocks re-inject
    // ANTHROPIC_* even if we delete them here, so override with explicit
    // values instead (empty key → apiKeySource: none → OAuth).
    const env = { ...process.env };
    delete env.CLAUDECODE;
    delete env.CLAUDE_CODE_ENTRYPOINT;
    delete env.ANTHROPIC_AUTH_TOKEN;
    delete env.ANTHROPIC_MODEL;
    env.ANTHROPIC_BASE_URL = 'https://api.anthropic.com';
    env.ANTHROPIC_API_KEY = '';

    // dev fixture: a .mjs cliPath is a mock CLI run via node
    const command = this.opts.cliPath.endsWith('.mjs') ? process.execPath : this.opts.cliPath;
    const finalArgs = this.opts.cliPath.endsWith('.mjs') ? [this.opts.cliPath, ...args] : args;

    const proc = new JsonlProcess({ command, args: finalArgs, cwd: this.opts.cwd, env });
    this.proc = proc;
    this.sessionAnnounced = false;

    proc.on('line', (line: any) => this.route(line));
    proc.on('stderr', (s: string) => {
      this.stderrTail.push(s);
      if (this.stderrTail.length > 20) this.stderrTail.shift();
    });
    proc.on('exit', ({ code, signal }: { code: number | null; signal: string | null }) => {
      this.opts.log(`claude exited code=${code} signal=${signal}`);
      if (this.turn) {
        this.turn.push({
          type: 'error',
          message: `claude 进程退出了 (code=${code})${this.stderrSnippet(' — ')}`,
          fatal: true,
        });
        this.finishTurn();
      }
    });

    proc.start();

    // Consider the spawn good once system/init arrives; if the process dies
    // first (typical for a stale --resume), report failure so start() can retry.
    return new Promise<boolean>((resolve) => {
      let settled = false;
      const settle = (v: boolean) => {
        if (!settled) {
          settled = true;
          resolve(v);
        }
      };
      const onInit = (line: any) => {
        if (line?.type === 'system' && line?.subtype === 'init') settle(true);
      };
      proc.on('line', onInit);
      proc.once('exit', () => settle(false));
      // init not observed but process alive → proceed; init may arrive with first turn
      setTimeout(() => settle(proc.alive()), 10_000);
    });
  }

  alive(): boolean {
    return this.proc?.alive() ?? false;
  }

  async stop(): Promise<void> {
    await this.proc?.stop();
  }

  private stderrSnippet(prefix = ''): string {
    const tail = this.stderrTail.join('').trim().slice(-300);
    return tail ? `${prefix}${tail}` : '';
  }

  // ── turns ──────────────────────────────────────────────

  sendTurn(input: { text: string }): TurnHandle {
    const queue = new AsyncQueue<TurnEvent>();
    this.turn = queue;
    this.accText = '';
    this.sawStreamText = false;
    this.sawStreamThinking = false;

    if (this.sessionId && !this.sessionAnnounced) {
      this.sessionAnnounced = true;
      queue.push({ type: 'session', sessionId: this.sessionId });
    }

    const sent = this.proc?.send({
      type: 'user',
      message: { role: 'user', content: input.text },
    });
    if (!sent) {
      queue.push({ type: 'error', message: 'claude 进程不在线，发送失败', fatal: true });
      this.finishTurn();
    } else {
      this.turnTimer = setTimeout(() => {
        this.opts.log('turn timeout, interrupting');
        void this.interrupt();
        queue.push({ type: 'error', message: '这轮超时了，已打断', fatal: false });
        this.finishTurn();
      }, this.opts.turnTimeoutMs);
    }

    return {
      events: queue,
      interrupt: () => this.interrupt(),
    };
  }

  async interrupt(): Promise<void> {
    this.proc?.send({
      type: 'control_request',
      request_id: `hub_${++this.controlSeq}`,
      request: { subtype: 'interrupt' },
    });
  }

  private finishTurn(): void {
    if (this.turnTimer) {
      clearTimeout(this.turnTimer);
      this.turnTimer = null;
    }
    this.turn?.end();
    this.turn = null;
  }

  // ── line routing ───────────────────────────────────────

  private route(line: any): void {
    switch (line?.type) {
      case 'system':
        if (line.subtype === 'init' && line.session_id) this.captureSession(line.session_id);
        return;
      case 'control_request':
        this.handleControlRequest(line);
        return;
      case 'stream_event':
        this.handleStreamEvent(line.event);
        return;
      case 'assistant':
        this.handleAssistant(line.message);
        return;
      case 'user':
        this.handleToolResults(line.message);
        return;
      case 'result':
        this.handleResult(line);
        return;
      default:
        return;
    }
  }

  private captureSession(sessionId: string): void {
    if (sessionId === this.sessionId) return;
    this.sessionId = sessionId;
    if (this.turn) {
      this.sessionAnnounced = true;
      this.turn.push({ type: 'session', sessionId });
    } else {
      this.sessionAnnounced = false;
    }
  }

  private handleStreamEvent(event: any): void {
    if (!this.turn || event?.type !== 'content_block_delta') return;
    const delta = event.delta;
    if (delta?.type === 'text_delta' && delta.text) {
      this.sawStreamText = true;
      this.accText += delta.text;
      this.turn.push({ type: 'delta', text: delta.text });
    } else if (delta?.type === 'thinking_delta' && delta.thinking) {
      this.sawStreamThinking = true;
      this.turn.push({ type: 'thinking', text: delta.thinking });
    }
  }

  private handleAssistant(message: any): void {
    if (!this.turn || !Array.isArray(message?.content)) return;
    for (const block of message.content) {
      if (block.type === 'tool_use') {
        this.toolNames.set(block.id, block.name);
        let summary = '';
        try {
          summary = JSON.stringify(block.input).slice(0, 200);
        } catch {}
        this.turn.push({ type: 'tool_use', name: block.name, inputSummary: summary });
      } else if (block.type === 'text' && block.text && !this.sawStreamText) {
        // fallback when --include-partial-messages produced no deltas
        this.accText += block.text;
        this.turn.push({ type: 'delta', text: block.text });
      } else if (block.type === 'thinking' && block.thinking && !this.sawStreamThinking) {
        this.turn.push({ type: 'thinking', text: block.thinking });
      }
    }
  }

  private handleToolResults(message: any): void {
    if (!this.turn || !Array.isArray(message?.content)) return;
    for (const block of message.content) {
      if (block.type !== 'tool_result') continue;
      const name = this.toolNames.get(block.tool_use_id) ?? 'tool';
      let summary = '';
      if (typeof block.content === 'string') summary = block.content;
      else if (Array.isArray(block.content))
        summary = block.content
          .filter((c: any) => c.type === 'text')
          .map((c: any) => c.text)
          .join(' ');
      this.turn.push({
        type: 'tool_result',
        name,
        ok: !block.is_error,
        summary: summary.slice(0, 200),
      });
    }
  }

  private handleResult(line: any): void {
    if (line.session_id) this.captureSession(line.session_id);
    if (!this.turn) return;
    const usage = line.usage
      ? {
          input: line.usage.input_tokens ?? 0,
          output: line.usage.output_tokens ?? 0,
          cacheCreation: line.usage.cache_creation_input_tokens ?? 0,
          cacheRead: line.usage.cache_read_input_tokens ?? 0,
        }
      : undefined;
    if (line.is_error) {
      this.turn.push({
        type: 'error',
        message: typeof line.result === 'string' && line.result ? line.result : 'claude 返回了错误',
        fatal: false,
      });
    } else {
      const finalText =
        typeof line.result === 'string' && line.result.length > 0 ? line.result : this.accText;
      this.turn.push({ type: 'done', finalText, usage });
    }
    this.finishTurn();
  }

  /** Permission asks (can_use_tool). v1 policy: deny with a friendly note. */
  private handleControlRequest(line: any): void {
    const req = line?.request;
    if (req?.subtype !== 'can_use_tool') return;
    this.opts.log(`denying tool request: ${req.tool_name}`);
    this.proc?.send({
      type: 'control_response',
      response: {
        subtype: 'success',
        request_id: line.request_id,
        response: {
          behavior: 'deny',
          message: `ai-hub 聊天模式没开放 ${req.tool_name}，需要的话请在联系人配置里加入白名单。`,
        },
      },
    });
    this.turn?.push({
      type: 'tool_result',
      name: req.tool_name ?? 'tool',
      ok: false,
      summary: '被 hub 权限策略拒绝',
    });
  }
}
