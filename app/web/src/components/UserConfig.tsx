import { useState } from 'react';
import { api, type UserProfile } from '../api';

interface Props {
  user: UserProfile;
  onClose(): void;
}

export default function UserConfig({ user, onClose }: Props) {
  const [name, setName] = useState(user.name);
  const [avatar, setAvatar] = useState(user.avatar);
  const [color, setColor] = useState(user.color);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    try {
      await api.putUser({ name, avatar, color });
      onClose();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 380 }} onClick={(e) => e.stopPropagation()}>
        <header className="modal-header">
          <h2>我的资料</h2>
          <button className="modal-close" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="modal-body">
          <div className="field-row">
            <label className="field" style={{ flex: 2 }}>
              名字
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="field" style={{ flex: 1 }}>
              头像
              <input value={avatar} onChange={(e) => setAvatar(e.target.value)} />
            </label>
            <label className="field" style={{ flex: 1 }}>
              气泡色
              <input type="color" value={color} onChange={(e) => setColor(e.target.value)} />
            </label>
          </div>
          {error && <div className="modal-error">⚠ {error}</div>}
        </div>
        <footer className="modal-footer">
          <span style={{ flex: 1 }} />
          <button className="ghost-btn" onClick={onClose}>
            取消
          </button>
          <button className="primary-btn" onClick={() => void save()}>
            保存
          </button>
        </footer>
      </div>
    </div>
  );
}
