import { useState } from 'react';
import { api, type Contact } from '../api';

interface Props {
  contact: Contact | null; // null = create new
  contacts: Contact[]; // 现有联系人（建群选成员用）
  onClose(): void;
}

export default function ContactConfig({ contact, contacts, onClose }: Props) {
  const creating = contact === null;
  const cfg = (contact?.config ?? {}) as Record<string, any>;
  const mem = (cfg.memory ?? {}) as Record<string, any>;
  const project = (cfg.projectAccess ?? {}) as Record<string, any>;
  const [createKind, setCreateKind] = useState<'api' | 'room'>('api');
  const isRoom = creating ? createKind === 'room' : contact?.kind === 'room';
  const isApi = !isRoom && (creating || contact?.backend === 'api');
  const dmContacts = contacts.filter((c) => c.kind !== 'room');
  const [members, setMembers] = useState<string[]>((cfg.members as string[]) ?? []);
  const [reactionRounds, setReactionRounds] = useState<number>(cfg.reactionRounds ?? 1);
  const [respondAllByDefault, setRespondAllByDefault] = useState<boolean>(cfg.respondAllByDefault ?? false);
  const toggleMember = (id: string) =>
    setMembers((prev) => (prev.includes(id) ? prev.filter((m) => m !== id) : [...prev, id]));

  const [name, setName] = useState(contact?.name ?? '');
  const [avatar, setAvatar] = useState(contact?.avatar ?? '🤖');
  const [color, setColor] = useState(contact?.color ?? '#8888aa');

  const [provider, setProvider] = useState<string>(cfg.provider ?? 'openai-compat');
  const [baseUrl, setBaseUrl] = useState<string>(cfg.baseUrl ?? '');
  const [apiKey, setApiKey] = useState<string>(cfg.apiKey ?? '');
  const [model, setModel] = useState<string>(cfg.model ?? '');
  const [systemPrompt, setSystemPrompt] = useState<string>(cfg.systemPrompt ?? '');
  const [maxHistory, setMaxHistory] = useState<number>(cfg.maxHistoryMessages ?? 60);

  const [memInject, setMemInject] = useState<boolean>(mem.injectOnSpawn ?? true);
  const [memSearch, setMemSearch] = useState<boolean>(mem.searchPerTurn ?? true);
  const [memCapture, setMemCapture] = useState<boolean>(mem.capture ?? true);
  const [projectEnabled, setProjectEnabled] = useState<boolean>(project.enabled ?? false);
  const [projectWorkspace, setProjectWorkspace] = useState<string>(project.workspace ?? '');
  const [projectShell, setProjectShell] = useState<boolean>(project.allowShell ?? false);
  const [sessionTokenLimit, setSessionTokenLimit] = useState<number>(cfg.maxSessionInputTokens ?? 120000);

  const [advanced, setAdvanced] = useState(false);
  const [rawJson, setRawJson] = useState(() => JSON.stringify(cfg, null, 2));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const buildConfig = (): Record<string, unknown> => {
    if (advanced) return JSON.parse(rawJson);
    if (isRoom) return { ...cfg, members, reactionRounds, respondAllByDefault };
    if (!isApi) return {
      ...cfg,
      projectAccess: {
        enabled: projectEnabled,
        workspace: projectWorkspace.trim(),
        allowShell: projectShell,
      },
      maxSessionInputTokens: sessionTokenLimit,
    };
    return {
      ...cfg,
      provider,
      baseUrl: baseUrl.trim(),
      apiKey: apiKey, // 打码值/空 = 服务端保留旧 key
      model: model.trim(),
      systemPrompt: systemPrompt.trim() || undefined,
      maxHistoryMessages: maxHistory,
      memory: { injectOnSpawn: memInject, searchPerTurn: memSearch, capture: memCapture },
    };
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const config = buildConfig();
      if (creating) {
        if (!name.trim()) throw new Error('得有个名字');
        if (isRoom && members.length === 0) throw new Error('群聊至少拉一个人');
        await api.createContact({
          name,
          avatar: avatar === '🤖' && isRoom ? '👥' : avatar,
          color,
          backend: isRoom ? 'room' : 'api',
          kind: isRoom ? 'room' : 'dm',
          config,
        });
      } else {
        await api.updateContact(contact!.id, { name, avatar, color, config });
      }
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!contact) return;
    if (!window.confirm(`确定删除 ${contact.name}？聊天记录保留在库里，联系人从列表消失。`)) return;
    try {
      await api.deleteContact(contact.id);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-header">
          <h2>{creating ? (isRoom ? '建群聊' : '新联系人（API 接入）') : `设置 · ${contact!.name}`}</h2>
          <button className="modal-close" onClick={onClose}>
            ✕
          </button>
        </header>

        <div className="modal-body">
          {creating && (
            <div className="field-row">
              <label className="field">
                类型
                <select value={createKind} onChange={(e) => setCreateKind(e.target.value as 'api' | 'room')}>
                  <option value="api">API 联系人</option>
                  <option value="room">群聊（拉现有联系人）</option>
                </select>
              </label>
            </div>
          )}
          <div className="field-row">
            <label className="field" style={{ flex: 2 }}>
              名称
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="比如 GLM" />
            </label>
            <label className="field" style={{ flex: 1 }}>
              头像
              <input value={avatar} onChange={(e) => setAvatar(e.target.value)} placeholder="🤖" />
            </label>
            <label className="field" style={{ flex: 1 }}>
              颜色
              <input type="color" value={color} onChange={(e) => setColor(e.target.value)} />
            </label>
          </div>

          {!creating && (
            <p className="field-hint">
              后端：<code>{contact!.backend}</code>
              {!isApi && ' — CLI 联系人的深层配置用下面的「高级 JSON」改'}
            </p>
          )}

          {isRoom && !advanced && (
            <fieldset className="mem-toggles">
              <legend>群成员</legend>
              {dmContacts.map((c) => (
                <label key={c.id}>
                  <input
                    type="checkbox"
                    checked={members.includes(c.id)}
                    onChange={() => toggleMember(c.id)}
                  />
                  {c.avatar} {c.name}
                  <span className="field-hint" style={{ marginLeft: 6 }}>
                    ({c.backend})
                  </span>
                </label>
              ))}
              <p className="field-hint">
                群里用 @名字 点名，@all 叫全员；默认无 @ 时不调用模型，避免无意消耗。
              </p>
              <label>
                <input
                  type="checkbox"
                  checked={respondAllByDefault}
                  onChange={(e) => setRespondAllByDefault(e.target.checked)}
                />
                无 @ 时默认全员响应（更热闹，也更耗 token）
              </label>
              <label className="field" style={{ maxWidth: 160 }}>
                接话轮数（0-3）
                <input
                  type="number"
                  min={0}
                  max={3}
                  value={reactionRounds}
                  onChange={(e) => setReactionRounds(Math.min(3, Math.max(0, Number(e.target.value) || 0)))}
                />
              </label>
              <p className="field-hint">
                每轮点名发言后，成员会看到彼此的新发言并可自然接话（或沉默）。0 = 关闭，回到纯点名制。
              </p>
            </fieldset>
          )}

          {isApi && !advanced && (
            <>
              <div className="field-row">
                <label className="field" style={{ flex: 1 }}>
                  协议
                  <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                    <option value="openai-compat">OpenAI 兼容（GLM/DeepSeek/…）</option>
                    <option value="anthropic">Anthropic</option>
                  </select>
                </label>
                <label className="field" style={{ flex: 1 }}>
                  模型
                  <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="glm-4-plus" />
                </label>
              </div>
              <label className="field">
                Base URL
                <input
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://open.bigmodel.cn/api/paas（会自动拼 /v1/…）"
                />
              </label>
              <label className="field">
                API Key
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={cfg.apiKey ? `已设置（${cfg.apiKey}），留空不改` : 'sk-…'}
                />
              </label>
              <label className="field">
                人设 / 系统提示
                <textarea
                  rows={3}
                  value={systemPrompt}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                  placeholder="这个 AI 是谁、怎么说话"
                />
              </label>
              <div className="field-row">
                <label className="field" style={{ flex: 1 }}>
                  历史条数
                  <input
                    type="number"
                    min={2}
                    max={200}
                    value={maxHistory}
                    onChange={(e) => setMaxHistory(Number(e.target.value) || 60)}
                  />
                </label>
              </div>
              <fieldset className="mem-toggles">
                <legend>记忆库</legend>
                <label>
                  <input type="checkbox" checked={memInject} onChange={(e) => setMemInject(e.target.checked)} />
                  开局注入核心记忆
                </label>
                <label>
                  <input type="checkbox" checked={memSearch} onChange={(e) => setMemSearch(e.target.checked)} />
                  每轮自动检索
                </label>
                <label>
                  <input type="checkbox" checked={memCapture} onChange={(e) => setMemCapture(e.target.checked)} />
                  触发词自动记录
                </label>
              </fieldset>
            </>
          )}

          {!isRoom && !isApi && !advanced && (
            <fieldset className="mem-toggles">
              <legend>项目权限</legend>
              <label>
                <input
                  type="checkbox"
                  checked={projectEnabled}
                  onChange={(e) => setProjectEnabled(e.target.checked)}
                />
                允许这个联系人修改指定项目
              </label>
              {projectEnabled && (
                <>
                  <label className="field">
                    项目工作区（必须已存在，不能填磁盘根目录）
                    <input
                      value={projectWorkspace}
                      onChange={(e) => setProjectWorkspace(e.target.value)}
                      placeholder="/opt/my-project 或 E:\\projects\\my-project"
                    />
                  </label>
                  {contact?.backend === 'claude-cli' && (
                    <label>
                      <input
                        type="checkbox"
                        checked={projectShell}
                        onChange={(e) => setProjectShell(e.target.checked)}
                      />
                      同时允许 Bash（可运行测试/构建，风险更高）
                    </label>
                  )}
                  <p className="field-hint">
                    默认仍只读。开启后 Claude 获得 Read/Write/Edit，Codex 使用 workspace-write；工具调用会保留在聊天审计记录中，可随时关闭。
                  </p>
                </>
              )}
              <label className="field" style={{ maxWidth: 240 }}>
                换新会话阈值（输入 token，0 = 关闭）
                <input
                  type="number"
                  min={0}
                  step={10000}
                  value={sessionTokenLimit}
                  onChange={(e) => setSessionTokenLimit(Math.max(0, Number(e.target.value) || 0))}
                />
              </label>
              <p className="field-hint">达到阈值后自动开启新 thread，并注入最近对话的压缩回放与最新记忆。</p>
            </fieldset>
          )}

          <label className="advanced-toggle">
            <input type="checkbox" checked={advanced} onChange={(e) => setAdvanced(e.target.checked)} />
            高级 JSON（直接编辑完整 config）
          </label>
          {advanced && (
            <textarea
              className="json-editor"
              rows={10}
              value={rawJson}
              onChange={(e) => setRawJson(e.target.value)}
              spellCheck={false}
            />
          )}

          {error && <div className="modal-error">⚠ {error}</div>}
        </div>

        <footer className="modal-footer">
          {!creating && (
            <button className="danger-btn" onClick={() => void remove()}>
              删除联系人
            </button>
          )}
          <span style={{ flex: 1 }} />
          <button className="ghost-btn" onClick={onClose}>
            取消
          </button>
          <button className="primary-btn" disabled={saving} onClick={() => void save()}>
            {saving ? '保存中…' : '保存'}
          </button>
        </footer>
      </div>
    </div>
  );
}
