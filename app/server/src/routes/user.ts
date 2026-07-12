import { Router } from 'express';
import type { Db } from '../db.js';
import type { SseHub } from '../sse.js';

const DEFAULT_PROFILE = { name: 'Owner', avatar: '👤', color: '#6366f1' };

export function getUserProfile(db: Db): typeof DEFAULT_PROFILE {
  const row = db.prepare("SELECT value FROM settings WHERE key = 'user_profile'").get() as
    | { value: string }
    | undefined;
  if (!row) return { ...DEFAULT_PROFILE };
  try {
    return { ...DEFAULT_PROFILE, ...JSON.parse(row.value) };
  } catch {
    return { ...DEFAULT_PROFILE };
  }
}

export function userRouter(db: Db, sse: SseHub): Router {
  const r = Router();

  r.get('/', (_req, res) => res.json(getUserProfile(db)));

  r.put('/', (req, res) => {
    const current = getUserProfile(db);
    const { name, avatar, color } = req.body ?? {};
    const next = {
      name: typeof name === 'string' && name.trim() ? name.trim() : current.name,
      avatar: typeof avatar === 'string' && avatar ? avatar : current.avatar,
      color: typeof color === 'string' && color ? color : current.color,
    };
    db.prepare(
      "INSERT INTO settings (key, value) VALUES ('user_profile', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    ).run(JSON.stringify(next));
    sse.broadcast('user', next);
    res.json(next);
  });

  return r;
}
