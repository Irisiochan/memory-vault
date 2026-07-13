import { useCallback, useEffect, useRef, useState } from 'react';
import { api, connectEvents, type Contact, type ContactStatus, type Message, type UserProfile } from './api';
import ChatPane from './components/ChatPane';
import ContactConfig from './components/ContactConfig';
import ContactList from './components/ContactList';
import UserConfig from './components/UserConfig';
import WorkerPanel from './components/WorkerPanel';

export default function App() {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Record<string, Message[]>>({});
  const [statuses, setStatuses] = useState<Record<string, ContactStatus>>({});
  const [unread, setUnread] = useState<Record<string, number>>({});
  const [configFor, setConfigFor] = useState<{ contact: Contact | null } | null>(null);
  const [user, setUser] = useState<UserProfile>({ name: 'Owner', avatar: '👤', color: '#6366f1' });
  const [userConfigOpen, setUserConfigOpen] = useState(false);
  const [workerPanelOpen, setWorkerPanelOpen] = useState(false);

  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  const upsertMessage = useCallback((msg: Message) => {
    setMessages((prev) => {
      const list = prev[msg.contact_id] ?? [];
      const idx = list.findIndex((m) => m.id === msg.id);
      const next =
        idx >= 0
          ? [...list.slice(0, idx), msg, ...list.slice(idx + 1)]
          : [...list, msg].sort((a, b) => a.id - b.id);
      return { ...prev, [msg.contact_id]: next };
    });
    setContacts((prev) =>
      prev.map((c) =>
        c.id === msg.contact_id && msg.kind === 'text'
          ? { ...c, last_content: msg.content, last_at: msg.created_at }
          : c
      )
    );
    if (msg.contact_id !== selectedRef.current && msg.sender !== 'user') {
      setUnread((prev) => ({ ...prev, [msg.contact_id]: (prev[msg.contact_id] ?? 0) + 1 }));
    }
  }, []);

  const loadMessages = useCallback(async (contactId: string) => {
    const { messages: rows } = await api.messages(contactId, { limit: 50 });
    setMessages((prev) => {
      const existing = prev[contactId] ?? [];
      const byId = new Map(existing.map((m) => [m.id, m]));
      for (const r of rows) byId.set(r.id, r);
      return { ...prev, [contactId]: [...byId.values()].sort((a, b) => a.id - b.id) };
    });
  }, []);

  const loadEarlier = useCallback(async (contactId: string) => {
    const list = messagesRef.current[contactId] ?? [];
    if (list.length === 0) return;
    const { messages: rows } = await api.messages(contactId, { before: list[0].id, limit: 50 });
    if (rows.length === 0) return;
    setMessages((prev) => {
      const existing = prev[contactId] ?? [];
      const byId = new Map([...rows, ...existing].map((m) => [m.id, m]));
      return { ...prev, [contactId]: [...byId.values()].sort((a, b) => a.id - b.id) };
    });
  }, []);

  const resync = useCallback(async () => {
    const { contacts: list } = await api.contacts();
    setContacts(list);
    void api.getUser().then(setUser).catch(() => {});
    setStatuses((prev) => {
      const next = { ...prev };
      for (const c of list) next[c.id] = { state: c.state };
      return next;
    });
    if (selectedRef.current) await loadMessages(selectedRef.current);
  }, [loadMessages]);

  useEffect(() => {
    void resync();
    const disconnect = connectEvents({
      onMessage: upsertMessage,
      onDelta: ({ contactId, messageId, text }) => {
        setMessages((prev) => {
          const list = prev[contactId];
          if (!list) return prev;
          return {
            ...prev,
            [contactId]: list.map((m) =>
              m.id === messageId ? { ...m, content: m.content + text } : m
            ),
          };
        });
      },
      onStatus: ({ contactId, state, member }) =>
        setStatuses((prev) => ({ ...prev, [contactId]: { state, member } })),
      onPrune: ({ contactId, ids, afterId }) =>
        setMessages((prev) => {
          const list = prev[contactId];
          if (!list) return prev;
          const keep = list.filter((m) => {
            if (ids && ids.includes(m.id)) return false;
            if (afterId !== undefined && m.id > afterId) return false;
            return true;
          });
          return { ...prev, [contactId]: keep };
        }),
      onUser: setUser,
      onContact: (c: Contact & { enabled?: number }) =>
        setContacts((prev) => {
          if (c.enabled === 0) {
            if (selectedRef.current === c.id) setSelectedId(null);
            return prev.filter((p) => p.id !== c.id);
          }
          return prev.some((p) => p.id === c.id)
            ? prev.map((p) => (p.id === c.id ? { ...p, ...c } : p))
            : [...prev, c];
        }),
      onReconnect: () => void resync(),
    });
    return disconnect;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const select = useCallback(
    (id: string | null) => {
      setSelectedId(id);
      if (id) {
        setUnread((prev) => ({ ...prev, [id]: 0 }));
        void loadMessages(id);
      }
    },
    [loadMessages]
  );

  const selected = contacts.find((c) => c.id === selectedId) ?? null;

  return (
    <div className={`app ${selected ? 'chat-open' : ''}`}>
      <ContactList
        contacts={contacts}
        statuses={statuses}
        unread={unread}
        selectedId={selectedId}
        onSelect={select}
        onAdd={() => setConfigFor({ contact: null })}
        user={user}
        onUserClick={() => setUserConfigOpen(true)}
        onWorkers={() => setWorkerPanelOpen(true)}
      />
      {selected ? (
        <ChatPane
          contact={selected}
          contacts={contacts}
          messages={messages[selected.id] ?? []}
          status={statuses[selected.id] ?? { state: 'idle' }}
          user={user}
          onBack={() => select(null)}
          onLoadEarlier={() => void loadEarlier(selected.id)}
          onSettings={() => setConfigFor({ contact: selected })}
        />
      ) : (
        <div className="chat-empty">选个人开聊 🍊</div>
      )}
      {configFor && (
        <ContactConfig
          contact={configFor.contact}
          contacts={contacts}
          onClose={() => setConfigFor(null)}
        />
      )}
      {userConfigOpen && <UserConfig user={user} onClose={() => setUserConfigOpen(false)} />}
      {workerPanelOpen && <WorkerPanel onClose={() => setWorkerPanelOpen(false)} />}
    </div>
  );
}
