import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export interface HubConfig {
  port: number;
  host: string;
  dbPath: string;
  agentsDir: string;
  webDist: string;
  claude: {
    cliPath: string;
    turnTimeoutMs: number;
  };
  codex: {
    cliPath: string;
    turnTimeoutMs: number;
  };
  memory: MemoryConfig;
}

export interface MemoryConfig {
  /** streamable-http MCP endpoint of the vault server; null disables the whole memory layer */
  mcpUrl: string | null;
  injectOnSpawn: boolean;
  searchPerTurn: boolean;
  capture: boolean;
  maxTurnChars: number;
  sessionMaxAgeHours: number;
}

const serverRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const defaults: HubConfig = {
  port: 3900,
  host: '127.0.0.1',
  dbPath: 'data/hub.db',
  agentsDir: 'agents',
  webDist: '../web/dist',
  claude: {
    cliPath: 'claude',
    turnTimeoutMs: 300_000,
  },
  codex: {
    cliPath: 'codex',
    turnTimeoutMs: 300_000,
  },
  memory: {
    mcpUrl: null,
    injectOnSpawn: true,
    searchPerTurn: true,
    capture: true,
    maxTurnChars: 1200,
    sessionMaxAgeHours: 12,
  },
};

export function loadConfig(): HubConfig {
  const file = path.join(serverRoot, 'config.json');
  let user: Partial<HubConfig> = {};
  if (fs.existsSync(file)) {
    user = JSON.parse(fs.readFileSync(file, 'utf-8'));
  }
  const cfg: HubConfig = {
    ...defaults,
    ...user,
    claude: { ...defaults.claude, ...(user.claude ?? {}) },
    codex: { ...defaults.codex, ...(user.codex ?? {}) },
    memory: { ...defaults.memory, ...(user.memory ?? {}) },
  };
  // resolve relative paths against server root so cwd doesn't matter
  cfg.dbPath = path.resolve(serverRoot, cfg.dbPath);
  cfg.agentsDir = path.resolve(serverRoot, cfg.agentsDir);
  cfg.webDist = path.resolve(serverRoot, cfg.webDist);
  return cfg;
}

export { serverRoot };
