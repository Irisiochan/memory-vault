import type { Response } from 'express';

export type SseEvent =
  | 'message' // full persisted message row
  | 'delta' // { contactId, messageId, text } streaming append
  | 'status' // { contactId, state, detail? }
  | 'contact' // contact config changed
  | 'prune' // { contactId, ids?: number[], afterId?: number } messages removed
  | 'user'; // user profile changed

export class SseHub {
  private clients = new Set<Response>();
  private eventId = 0;
  private heartbeat: NodeJS.Timeout;

  constructor() {
    this.heartbeat = setInterval(() => {
      for (const res of this.clients) res.write(': ping\n\n');
    }, 25_000);
    this.heartbeat.unref();
  }

  addClient(res: Response): void {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      'X-Accel-Buffering': 'no',
      Connection: 'keep-alive',
    });
    res.write('retry: 3000\n\n');
    this.clients.add(res);
    res.on('close', () => this.clients.delete(res));
  }

  broadcast(event: SseEvent, data: unknown): void {
    const payload = `id: ${++this.eventId}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
    for (const res of this.clients) res.write(payload);
  }

  close(): void {
    clearInterval(this.heartbeat);
    for (const res of this.clients) res.end();
    this.clients.clear();
  }
}
