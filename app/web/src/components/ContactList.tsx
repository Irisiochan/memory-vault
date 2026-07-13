import type { Contact, ContactStatus, UserProfile } from '../api';

interface Props {
  contacts: Contact[];
  statuses: Record<string, ContactStatus>;
  unread: Record<string, number>;
  selectedId: string | null;
  user: UserProfile;
  onSelect(id: string): void;
  onAdd(): void;
  onUserClick(): void;
  onWorkers(): void;
}

function stateLabel(state: string): string {
  if (state === 'thinking') return '思考中…';
  if (state === 'streaming') return '正在输入…';
  if (state.startsWith('tool:')) return `🔧 ${state.slice(5)}`;
  if (state === 'error') return '出错了';
  return '';
}

export default function ContactList({ contacts, statuses, unread, selectedId, user, onSelect, onAdd, onUserClick, onWorkers }: Props) {
  return (
    <aside className="contact-list">
      <header className="contact-list-header">
        <h1>ai-hub</h1>
        <span className="header-btns">
          <button className="add-btn" title="PC Worker 任务" onClick={onWorkers}>🖥</button>
          <button
            className="user-btn avatar"
            title={`${user.name} · 改我的资料`}
            style={{ background: user.color + '33' }}
            onClick={onUserClick}
          >
            {user.avatar}
          </button>
          <button className="add-btn" title="接入新 AI（API）" onClick={onAdd}>
            ＋
          </button>
        </span>
      </header>
      <div className="contact-scroll">
        {contacts.map((c) => {
          const st = statuses[c.id] ?? { state: 'idle' };
          const state = st.state;
          const base = stateLabel(state);
          const label = base && st.member ? `${st.member} ${base}` : base;
          return (
            <button
              key={c.id}
              className={`contact-item ${c.id === selectedId ? 'selected' : ''}`}
              onClick={() => onSelect(c.id)}
            >
              <span className="avatar" style={{ background: c.color + '33' }}>
                {c.avatar}
              </span>
              <span className="contact-info">
                <span className="contact-name" style={{ color: c.color }}>
                  {c.name}
                  {state !== 'idle' && (
                    <span className={`state-dot ${state === 'error' ? 'err' : 'busy'}`} />
                  )}
                </span>
                <span className="contact-preview">
                  {label || c.last_content?.slice(0, 40) || '还没聊过'}
                </span>
              </span>
              {(unread[c.id] ?? 0) > 0 && <span className="unread-badge">{unread[c.id]}</span>}
            </button>
          );
        })}
      </div>
    </aside>
  );
}
