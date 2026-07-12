export interface Contact {
  id: string;
  name: string;
  avatar: string;
  color: string;
  backend: string;
  kind: string;
  config: Record<string, unknown>;
  state: string;
  last_content: string | null;
  last_at: string | null;
}

export interface Message {
  id: number;
  contact_id: string;
  sender: string;
  role: 'user' | 'assistant' | 'system';
  kind: 'text' | 'thinking' | 'tool_use' | 'error';
  content: string;
  status: 'streaming' | 'done' | 'error' | 'interrupted';
  turn_id: string | null;
  meta: string;
  created_at: string;
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { error?: string }).error ?? `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export interface UserProfile {
  name: string;
  avatar: string;
  color: string;
}

export interface Usage {
  today: { input: number; output: number };
  total: { input: number; output: number };
}

export interface QuotaWindow {
  remainingPct: number;
  resetsAt: string | null;
}

export interface ClaudeQuota {
  available: boolean;
  fiveHour?: QuotaWindow | null;
  sevenDay?: QuotaWindow | null;
}

export interface ContactPayload {
  id?: string;
  name?: string;
  avatar?: string;
  color?: string;
  backend?: string;
  kind?: string;
  config?: Record<string, unknown>;
}

export interface ContactStatus {
  state: string;
  member?: string;
}

export const api = {
  contacts: () => req<{ contacts: Contact[] }>('/api/contacts'),

  createContact: (data: ContactPayload) =>
    req<Contact>('/api/contacts', { method: 'POST', body: JSON.stringify(data) }),

  updateContact: (id: string, data: ContactPayload) =>
    req<Contact>(`/api/contacts/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),

  deleteContact: (id: string) =>
    req<{ ok: boolean }>(`/api/contacts/${id}`, { method: 'DELETE' }),

  messages: (contactId: string, opts: { before?: number; after?: number; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (opts.before) q.set('before', String(opts.before));
    if (opts.after) q.set('after', String(opts.after));
    if (opts.limit) q.set('limit', String(opts.limit));
    return req<{ messages: Message[] }>(`/api/contacts/${contactId}/messages?${q}`);
  },

  send: (contactId: string, content: string) =>
    req<{ messageId: number }>(`/api/contacts/${contactId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),

  interrupt: (contactId: string) =>
    req<{ ok: boolean }>(`/api/contacts/${contactId}/interrupt`, { method: 'POST' }),

  regenerate: (contactId: string, messageId: number, content?: string) =>
    req<{ ok: boolean }>(`/api/contacts/${contactId}/messages/${messageId}/regenerate`, {
      method: 'POST',
      body: JSON.stringify(content ? { content } : {}),
    }),

  deleteMessage: (contactId: string, messageId: number) =>
    req<{ ok: boolean }>(`/api/contacts/${contactId}/messages/${messageId}`, {
      method: 'DELETE',
    }),

  usage: (contactId: string) => req<Usage>(`/api/contacts/${contactId}/usage`),

  claudeQuota: () => req<ClaudeQuota>('/api/quota/claude'),

  getUser: () => req<UserProfile>('/api/user'),

  putUser: (p: Partial<UserProfile>) =>
    req<UserProfile>('/api/user', { method: 'PUT', body: JSON.stringify(p) }),

  resetSession: (contactId: string) =>
    req<{ ok: boolean }>(`/api/contacts/${contactId}/session/reset`, { method: 'POST' }),
};

export interface SseHandlers {
  onMessage(msg: Message): void;
  onDelta(d: { contactId: string; messageId: number; text: string }): void;
  onStatus(s: { contactId: string; state: string; detail?: string; member?: string }): void;
  onContact(c: Contact): void;
  onPrune(p: { contactId: string; ids?: number[]; afterId?: number }): void;
  onUser(u: UserProfile): void;
  onReconnect(): void;
}

export function connectEvents(handlers: SseHandlers): () => void {
  let es: EventSource | null = null;
  let closed = false;
  let hadError = false;

  const open = () => {
    if (closed) return;
    es?.close();
    es = new EventSource('/api/events');
    es.onopen = () => {
      if (hadError) {
        hadError = false;
        handlers.onReconnect();
      }
    };
    es.onerror = () => {
      hadError = true; // EventSource auto-retries; onopen will trigger resync
    };
    es.addEventListener('message', (e) => handlers.onMessage(JSON.parse(e.data)));
    es.addEventListener('delta', (e) => handlers.onDelta(JSON.parse(e.data)));
    es.addEventListener('status', (e) => handlers.onStatus(JSON.parse(e.data)));
    es.addEventListener('contact', (e) => handlers.onContact(JSON.parse(e.data)));
    es.addEventListener('prune', (e) => handlers.onPrune(JSON.parse(e.data)));
    es.addEventListener('user', (e) => handlers.onUser(JSON.parse(e.data)));
  };

  open();

  const onVisible = () => {
    if (document.visibilityState !== 'visible') return;
    // phone coming back from lock screen: EventSource may be silently dead
    if (!es || es.readyState === EventSource.CLOSED) open();
    handlers.onReconnect();
  };
  document.addEventListener('visibilitychange', onVisible);

  return () => {
    closed = true;
    document.removeEventListener('visibilitychange', onVisible);
    es?.close();
  };
}
