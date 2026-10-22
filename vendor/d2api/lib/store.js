'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const DATA_DIR = process.env.D2API_DATA_DIR || path.join(__dirname, '..', 'data');
const ACCOUNTS_FILE = path.join(DATA_DIR, 'accounts.json');

function ensureDataDir() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (err) {
    if (err.code === 'ENOENT') return fallback;
    throw new Error(`Could not read ${file}: ${err.message}`);
  }
}

function writeJsonAtomic(file, value) {
  ensureDataDir();
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(value, null, 2), 'utf8');
  fs.renameSync(tmp, file);
}

function newDeviceId() {
  return `d2api-${crypto.randomBytes(8).toString('hex')}`;
}

/**
 * accounts.json shape:
 * {
 *   "device_id": "d2api-...",
 *   "accounts": [
 *     {"id": "acc_1", "label": "...", "token": "...", "cookie": "...",
 *      "email": "...", "password": "...", "wasmUrl": "..."}
 *   ]
 * }
 * A plain array is accepted too.
 */
function loadStore() {
  const raw = readJson(ACCOUNTS_FILE, null);
  let deviceId;
  let accounts;

  if (Array.isArray(raw)) {
    accounts = raw;
    deviceId = null;
  } else if (raw && typeof raw === 'object') {
    accounts = Array.isArray(raw.accounts) ? raw.accounts : [];
    deviceId = raw.device_id || null;
  } else {
    accounts = [];
    deviceId = null;
  }

  deviceId = deviceId || process.env.DEEPSEEK_DEVICE_ID || newDeviceId();

  // Environment-provided tokens always participate, even without a file.
  const envTokens = String(process.env.DEEPSEEK_TOKENS || process.env.DEEPSEEK_TOKEN || '')
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean);

  const envCookie = String(process.env.DEEPSEEK_COOKIE || '').trim();

  for (const token of envTokens) {
    if (accounts.some((a) => a && a.token === token)) continue;
    accounts.push({
      id: `env_${accounts.length + 1}`,
      label: 'env',
      token,
      cookie: envCookie,
      env: true,
    });
  }

  // A cookie alone is a valid bootstrap: /api/v0/users/current exchanges it
  // for the bearer token.
  if (envCookie && envTokens.length === 0) {
    accounts.push({
      id: `env_cookie_${accounts.length + 1}`,
      label: 'env-cookie',
      token: '',
      cookie: envCookie,
      env: true,
    });
  }

  return { deviceId, accounts: accounts.filter((a) => a && (a.token || a.cookie || (a.email && a.password))) };
}

function saveStore(store) {
  const clean = {
    device_id: store.deviceId,
    accounts: (store.accounts || [])
      .filter((a) => !a.env)
      .map((a) => {
        const copy = { ...a };
        delete copy.env;
        delete copy.cooldownUntil;
        delete copy.failures;
        delete copy.lastUsedAt;
        return copy;
      }),
  };
  writeJsonAtomic(ACCOUNTS_FILE, clean);
}

function nextAccountId(accounts) {
  let n = accounts.length + 1;
  const ids = new Set(accounts.map((a) => a.id));
  while (ids.has(`acc_${n}`)) n++;
  return `acc_${n}`;
}

module.exports = {
  DATA_DIR,
  ACCOUNTS_FILE,
  ensureDataDir,
  readJson,
  writeJsonAtomic,
  loadStore,
  saveStore,
  nextAccountId,
  newDeviceId,
};
