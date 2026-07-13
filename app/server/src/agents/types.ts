export type TurnEvent =
  | { type: 'session'; sessionId: string }
  | { type: 'delta'; text: string }
  | { type: 'thinking'; text: string }
  | { type: 'tool_use'; name: string; inputSummary: string }
  | { type: 'tool_result'; name: string; ok: boolean; summary: string }
  | { type: 'done'; finalText: string; usage?: TokenUsage }
  | { type: 'error'; message: string; fatal: boolean };

export interface TokenUsage {
  input: number;
  output: number;
  cacheCreation?: number;
  cacheRead?: number;
}

export interface TurnHandle {
  events: AsyncIterable<TurnEvent>;
  interrupt(): Promise<void>;
}

export interface AgentBackend {
  readonly kind: 'claude-cli' | 'codex' | 'api';
  /** Spawn/connect. resumeToken = claude session_id / codex threadId / null.
   *  Must not throw on a stale token — fall back to fresh and emit a new 'session'. */
  start(resumeToken: string | null): Promise<void>;
  /** One turn. Caller guarantees serialization — never called concurrently. */
  sendTurn(input: { text: string }): TurnHandle;
  alive(): boolean;
  stop(): Promise<void>;
}

/** Unbounded async FIFO used to bridge callback-style stdio events into AsyncIterable turns. */
export class AsyncQueue<T> implements AsyncIterable<T> {
  private items: T[] = [];
  private waiters: ((r: IteratorResult<T>) => void)[] = [];
  private ended = false;

  push(item: T): void {
    if (this.ended) return;
    const waiter = this.waiters.shift();
    if (waiter) waiter({ value: item, done: false });
    else this.items.push(item);
  }

  end(): void {
    if (this.ended) return;
    this.ended = true;
    for (const w of this.waiters.splice(0)) w({ value: undefined, done: true });
  }

  [Symbol.asyncIterator](): AsyncIterator<T> {
    return {
      next: (): Promise<IteratorResult<T>> => {
        if (this.items.length > 0) {
          return Promise.resolve({ value: this.items.shift()!, done: false });
        }
        if (this.ended) return Promise.resolve({ value: undefined, done: true });
        return new Promise((resolve) => this.waiters.push(resolve));
      },
    };
  }
}
