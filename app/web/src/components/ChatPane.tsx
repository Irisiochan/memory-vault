import { useEffect, useRef, useState } from 'react';
import {
  api,
  type ClaudeQuota,
  type Contact,
  type ContactStatus,
  type Message,
  type Usage,
  type UserProfile,
} from '../api';
import MessageBubble from './MessageBubble';

interface Props {
  contact: Contact;
  contacts: Contact[];
  messages: Message[];
  status: ContactStatus;
  user: UserProfile;
  onBack(): void;
  onLoadEarlier(): void;
  onSettings(): void;
}

function statusText(status: ContactStatus): string {
  const who = status.member ? `${status.member} ` : '';
  if (status.state === 'thinking') return `${who}思考中…`;
  if (status.state === 'streaming') return `${who}正在输入…`;
  if (status.state.startsWith('tool:')) return `${who}正在用 ${status.state.slice(5)}`;
  if (status.state === 'error') return '出错了，可以再试一次或重置会话';
  return '';
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export default function ChatPane({ contact, contacts, messages, status, user, onBack, onLoadEarlier, onSettings }: Props) {
  const isRoom = contact.kind === 'room';
  const senderContactOf = (m: Message): Contact =>
    m.sender === 'user' ? contact : contacts.find((c) => c.id === m.sender) ?? contact;
  const [draft, setDraft] = useState('');
  const [sendError, setSendError] = useState<string | null>(null);
  const [selectedMsg, setSelectedMsg] = useState<number | null>(null);
  const [editing, setEditing] = useState<{ id: number; draft: string } | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [quota, setQuota] = useState<ClaudeQuota | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages, contact.id]);

  // usage/quota：切联系人时拉一次，回合结束（idle）再刷新
  useEffect(() => {
    setUsage(null);
    setQuota(null);
    void api.usage(contact.id).then(setUsage).catch(() => {});
    if (contact.backend === 'claude-cli') {
      void api.claudeQuota().then(setQuota).catch(() => {});
    }
    setSelectedMsg(null);
    setEditing(null);
  }, [contact.id, contact.backend]);

  useEffect(() => {
    if (status.state === 'idle') {
      void api.usage(contact.id).then(setUsage).catch(() => {});
      if (contact.backend === 'claude-cli') {
        void api.claudeQuota().then(setQuota).catch(() => {});
      }
    }
  }, [status.state, contact.id, contact.backend]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const send = async () => {
    const content = draft.trim();
    if (!content) return;
    setDraft('');
    setSendError(null);
    stickToBottom.current = true;
    try {
      await api.send(contact.id, content);
    } catch (e) {
      setSendError((e as Error).message);
      setDraft(content);
    }
  };

  const saveEdit = async () => {
    if (!editing || !editing.draft.trim()) return;
    const { id, draft: content } = editing;
    setEditing(null);
    setSelectedMsg(null);
    stickToBottom.current = true;
    try {
      await api.regenerate(contact.id, id, content);
    } catch (e) {
      setSendError((e as Error).message);
    }
  };

  const resend = async (m: Message) => {
    setSelectedMsg(null);
    stickToBottom.current = true;
    try {
      await api.regenerate(contact.id, m.id);
    } catch (e) {
      setSendError((e as Error).message);
    }
  };

  const remove = async (m: Message) => {
    setSelectedMsg(null);
    try {
      await api.deleteMessage(contact.id, m.id);
    } catch (e) {
      setSendError((e as Error).message);
    }
  };

  const busy =
    status.state === 'thinking' || status.state === 'streaming' || status.state.startsWith('tool:');
  const st = statusText(status);

  const quotaBits: string[] = [];
  if (contact.backend === 'claude-cli' && quota?.available) {
    if (quota.fiveHour) quotaBits.push(`5h剩${quota.fiveHour.remainingPct}%`);
    if (quota.sevenDay) quotaBits.push(`周剩${quota.sevenDay.remainingPct}%`);
  }
  if (usage && (usage.today.input > 0 || usage.today.output > 0)) {
    quotaBits.push(`今日 ${fmtTokens(usage.today.input)}↑ ${fmtTokens(usage.today.output)}↓`);
  }

  return (
    <main className="chat-pane">
      <header className="chat-header">
        <button className="back-btn" onClick={onBack}>
          ←
        </button>
        <span className="avatar" style={{ background: contact.color + '33' }}>
          {contact.avatar}
        </span>
        <div className="chat-title">
          <span style={{ color: contact.color }}>{contact.name}</span>
          <span className="chat-status">{st || quotaBits.join(' · ')}</span>
        </div>
        {busy && (
          <button className="interrupt-btn" onClick={() => void api.interrupt(contact.id)}>
            ⏹ 打断
          </button>
        )}
        <button className="gear-btn" title="联系人设置" onClick={onSettings}>
          ⚙
        </button>
      </header>

      <div className="message-scroll" ref={scrollRef} onScroll={onScroll}>
        {messages.length >= 50 && (
          <button className="load-earlier" onClick={onLoadEarlier}>
            加载更早的
          </button>
        )}
        {messages.map((m) =>
          editing && editing.id === m.id ? (
            <div key={m.id} className="edit-box">
              <textarea
                autoFocus
                rows={3}
                value={editing.draft}
                onChange={(e) => setEditing({ ...editing, draft: e.target.value })}
              />
              <div className="edit-actions">
                <button className="ghost-btn" onClick={() => setEditing(null)}>
                  取消
                </button>
                <button className="primary-btn" onClick={() => void saveEdit()}>
                  保存并重新生成
                </button>
              </div>
            </div>
          ) : (
            <MessageBubble
              key={m.id}
              message={m}
              contact={senderContactOf(m)}
              showName={isRoom && m.sender !== 'user' ? senderContactOf(m).name : undefined}
              allowRegen={!isRoom}
              user={user}
              selected={selectedMsg === m.id}
              onSelect={setSelectedMsg}
              onEdit={(msg) => {
                setEditing({ id: msg.id, draft: msg.content });
                setSelectedMsg(null);
              }}
              onResend={(msg) => void resend(msg)}
              onDelete={(msg) => void remove(msg)}
            />
          )
        )}
        {status.state === 'thinking' && (
          <div className="typing-hint">
            {status.member ?? contact.name} 思考中…
          </div>
        )}
      </div>

      {sendError && <div className="send-error">操作失败：{sendError}</div>}

      <footer className="composer">
        <textarea
          value={draft}
          placeholder={`发给 ${contact.name}…`}
          rows={1}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              if (window.matchMedia('(min-width: 768px)').matches) {
                e.preventDefault();
                void send();
              }
            }
          }}
        />
        <button className="send-btn" onClick={() => void send()} disabled={!draft.trim()}>
          ➤
        </button>
      </footer>
    </main>
  );
}
