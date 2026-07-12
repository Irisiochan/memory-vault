import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Contact, Message, UserProfile } from '../api';

interface Props {
  message: Message;
  contact: Contact; // 发言人（群里是成员，DM 里就是会话联系人）
  showName?: string; // 群聊里气泡上方的发言人名字
  allowRegen?: boolean; // 群聊 v1 不支持编辑/重新生成
  user: UserProfile;
  selected: boolean;
  onSelect(id: number | null): void;
  onEdit(m: Message): void;
  onResend(m: Message): void;
  onDelete(m: Message): void;
}

export default function MessageBubble({
  message,
  contact,
  showName,
  allowRegen = true,
  user,
  selected,
  onSelect,
  onEdit,
  onResend,
  onDelete,
}: Props) {
  const [thinkingOpen, setThinkingOpen] = useState(false);
  const mine = message.sender === 'user';
  const edited = message.meta?.includes('"edited"');

  const actions = selected && (
    <div className={`msg-actions ${mine ? 'mine' : ''}`}>
      {mine && message.kind === 'text' && allowRegen && (
        <>
          <button onClick={() => onEdit(message)}>✎ 编辑</button>
          <button onClick={() => onResend(message)}>🔄 重新生成</button>
        </>
      )}
      <button className="del" onClick={() => onDelete(message)}>
        🗑 删除
      </button>
    </div>
  );

  if (message.kind === 'tool_use') {
    return (
      <div className="msg-group">
        <div className="tool-chip" title={safeMeta(message.meta)} onClick={() => onSelect(selected ? null : message.id)}>
          🔧 {message.content}
        </div>
        {actions}
      </div>
    );
  }

  if (message.kind === 'error') {
    return (
      <div className="msg-group center">
        <div className="error-note" onClick={() => onSelect(selected ? null : message.id)}>
          ⚠ {message.content}
        </div>
        {actions}
      </div>
    );
  }

  if (message.kind === 'thinking') {
    if (!message.content && message.status !== 'streaming') return null;
    return (
      <div className="msg-group">
        <div className="thinking-block">
          <button
            className="thinking-toggle"
            onClick={() => {
              setThinkingOpen(!thinkingOpen);
              onSelect(selected ? null : message.id);
            }}
          >
            💭 {thinkingOpen ? '收起想法' : '想法'}
            {message.status === 'streaming' && <span className="cursor">▍</span>}
          </button>
          {thinkingOpen && <div className="thinking-content">{message.content}</div>}
        </div>
        {actions}
      </div>
    );
  }

  return (
    <div className="msg-group">
      {showName && <span className="sender-name">{showName}</span>}
      <div className={`bubble-row ${mine ? 'mine' : 'theirs'}`}>
        {!mine && (
          <span className="avatar bubble-avatar" style={{ background: contact.color + '33' }}>
            {contact.avatar}
          </span>
        )}
        <div
          className={`bubble ${mine ? 'bubble-mine' : 'bubble-theirs'} ${
            message.status === 'interrupted' ? 'interrupted' : ''
          }`}
          style={mine ? { background: user.color } : undefined}
          onClick={() => onSelect(selected ? null : message.id)}
        >
          <div className="markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          </div>
          {message.status === 'streaming' && <span className="cursor">▍</span>}
          {message.status === 'interrupted' && <span className="interrupted-tag">（被打断）</span>}
          {edited && <span className="edited-tag">（已编辑）</span>}
        </div>
        {mine && (
          <span className="avatar bubble-avatar" style={{ background: user.color + '33' }}>
            {user.avatar}
          </span>
        )}
      </div>
      {actions}
    </div>
  );
}

function safeMeta(meta: string): string {
  try {
    const m = JSON.parse(meta);
    return m.input ?? '';
  } catch {
    return '';
  }
}
