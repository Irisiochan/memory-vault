import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { EventEmitter } from 'node:events';

export interface JsonlProcessOpts {
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
}

/**
 * Long-lived child process speaking newline-delimited JSON over stdio.
 * Owns the UTF-8 chunk-boundary problem: bytes are buffered and only
 * complete lines are decoded, so multi-byte CJK chars split across
 * chunks never corrupt.
 *
 * Events: 'line' (parsed object), 'stderr' (string), 'exit' ({code, signal}).
 */
export class JsonlProcess extends EventEmitter {
  private child: ChildProcessWithoutNullStreams | null = null;
  private buf: Buffer = Buffer.alloc(0);
  private exited = false;

  constructor(private opts: JsonlProcessOpts) {
    super();
  }

  start(): void {
    const child = spawn(this.opts.command, this.opts.args, {
      cwd: this.opts.cwd,
      env: this.opts.env,
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    this.child = child;
    this.exited = false;

    child.stdout.on('data', (chunk: Buffer) => this.feed(chunk));
    child.stderr.on('data', (chunk: Buffer) => this.emit('stderr', chunk.toString('utf8')));
    child.on('error', (err) => {
      this.exited = true;
      this.emit('exit', { code: null, signal: null, error: err });
    });
    child.on('exit', (code, signal) => {
      this.exited = true;
      this.emit('exit', { code, signal });
    });
  }

  private feed(chunk: Buffer): void {
    this.buf = this.buf.length === 0 ? chunk : Buffer.concat([this.buf, chunk]);
    let idx: number;
    while ((idx = this.buf.indexOf(0x0a)) !== -1) {
      const lineBuf = this.buf.subarray(0, idx);
      this.buf = this.buf.subarray(idx + 1);
      const line = lineBuf.toString('utf8').trim();
      if (!line) continue;
      try {
        this.emit('line', JSON.parse(line));
      } catch {
        this.emit('stderr', `[jsonl] unparseable line: ${line.slice(0, 200)}`);
      }
    }
  }

  send(obj: unknown): boolean {
    if (!this.child || this.exited || !this.child.stdin.writable) return false;
    return this.child.stdin.write(JSON.stringify(obj) + '\n', 'utf8');
  }

  alive(): boolean {
    return this.child !== null && !this.exited;
  }

  pid(): number | undefined {
    return this.child?.pid;
  }

  /** Graceful: close stdin → wait → SIGTERM → wait → SIGKILL. */
  async stop(graceMs = 8000): Promise<void> {
    const child = this.child;
    if (!child || this.exited) return;

    const exited = new Promise<void>((resolve) => {
      if (this.exited) return resolve();
      this.once('exit', () => resolve());
    });
    const wait = (ms: number) =>
      Promise.race([exited.then(() => true), new Promise<false>((r) => setTimeout(() => r(false), ms))]);

    try {
      child.stdin.end();
    } catch {}
    if (await wait(graceMs)) return;
    try {
      child.kill('SIGTERM');
    } catch {}
    if (await wait(4000)) return;
    try {
      child.kill('SIGKILL');
    } catch {}
    await exited;
  }
}
