import crypto from 'node:crypto';
import { Router, type NextFunction, type Request, type Response } from 'express';

const COOKIE_NAME = 'memory_vault_hub_session';
const SESSION_CONTEXT = 'memory-vault-hub-session-v1';
const SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60;

function safeEqual(actual: string, expected: string): boolean {
  const a = Buffer.from(actual);
  const b = Buffer.from(expected);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function bearer(req: Request): string {
  const header = req.header('authorization') ?? '';
  return header.startsWith('Bearer ') ? header.slice(7).trim() : '';
}

function cookies(req: Request): Record<string, string> {
  const values: Record<string, string> = {};
  for (const item of (req.header('cookie') ?? '').split(';')) {
    const index = item.indexOf('=');
    if (index < 1) continue;
    const name = item.slice(0, index).trim();
    const value = item.slice(index + 1).trim();
    try { values[name] = decodeURIComponent(value); } catch { /* ignore malformed cookie */ }
  }
  return values;
}

function isSecure(req: Request): boolean {
  const forwarded = (req.header('x-forwarded-proto') ?? '').split(',')[0].trim().toLowerCase();
  return req.secure || forwarded === 'https';
}

function sessionValue(token: string, expiresAt: number): string {
  const signature = crypto.createHmac('sha256', token)
    .update(`${SESSION_CONTEXT}.${expiresAt}`)
    .digest('base64url');
  return `${expiresAt}.${signature}`;
}

function validSession(value: string, token: string, now: number): boolean {
  const dot = value.indexOf('.');
  if (dot < 1) return false;
  const rawExpiry = value.slice(0, dot);
  if (!/^\d+$/.test(rawExpiry)) return false;
  const expiresAt = Number(rawExpiry);
  if (!Number.isSafeInteger(expiresAt) || expiresAt <= Math.floor(now / 1000)) return false;
  return safeEqual(value, sessionValue(token, expiresAt));
}

function sessionCookie(value: string, secure: boolean, maxAge: number): string {
  return [
    `${COOKIE_NAME}=${encodeURIComponent(value)}`,
    'Path=/api',
    'HttpOnly',
    'SameSite=Strict',
    `Max-Age=${maxAge}`,
    secure ? 'Secure' : '',
  ].filter(Boolean).join('; ');
}

export function isLoopbackHost(host: string): boolean {
  const normalized = host.trim().toLowerCase().replace(/^\[|\]$/g, '');
  return normalized === 'localhost' || normalized === '::1' || /^127(?:\.\d{1,3}){3}$/.test(normalized);
}

export function isWorkerDeviceRoute(requestPath: string): boolean {
  return /^\/worker\/(?:connect|claim|jobs\/[^/]+\/(?:start|heartbeat|events|complete))\/?$/.test(requestPath);
}

export interface HubAuth {
  required: boolean;
  router: Router;
  requireAdmin(req: Request, res: Response, next: NextFunction): void;
}

export function createHubAuth(
  host: string,
  configuredToken = process.env.HUB_ADMIN_TOKEN ?? '',
  now: () => number = Date.now
): HubAuth {
  const token = configuredToken.trim();
  if (!token && !isLoopbackHost(host)) {
    throw new Error('HUB_ADMIN_TOKEN is required when Hub host is not loopback');
  }
  if (token && token.length < 24) {
    throw new Error('HUB_ADMIN_TOKEN must contain at least 24 characters');
  }

  const authenticated = (req: Request): boolean => {
    if (!token) return true;
    const direct = bearer(req);
    if (direct && safeEqual(direct, token)) return true;
    const session = cookies(req)[COOKIE_NAME] ?? '';
    return Boolean(session) && validSession(session, token, now());
  };

  const router = Router();
  router.get('/status', (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.json({ required: Boolean(token), authenticated: authenticated(req) });
  });
  router.post('/session', (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    if (!token) return res.status(204).end();
    const supplied = bearer(req) || (typeof req.body?.token === 'string' ? req.body.token : '');
    if (!supplied || !safeEqual(supplied, token)) {
      return res.status(401).json({ error: 'invalid Hub admin token' });
    }
    const expiresAt = Math.floor(now() / 1000) + SESSION_MAX_AGE_SECONDS;
    res.setHeader('Set-Cookie', sessionCookie(sessionValue(token, expiresAt), isSecure(req), SESSION_MAX_AGE_SECONDS));
    return res.status(204).end();
  });
  router.delete('/session', (_req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Set-Cookie', sessionCookie('', false, 0));
    res.status(204).end();
  });

  return {
    required: Boolean(token),
    router,
    requireAdmin(req, res, next) {
      if (authenticated(req)) return next();
      res.setHeader('Cache-Control', 'no-store');
      res.setHeader('WWW-Authenticate', 'Bearer realm="memory-vault-hub"');
      res.status(401).json({ error: 'Hub authentication required' });
    },
  };
}
