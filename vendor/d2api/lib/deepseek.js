'use strict';

const { solvePOW } = require('./pow');
const { readLines } = require('./sse');

const BASE_URL = String(process.env.DEEPSEEK_BASE_URL || 'https://chat.deepseek.com').replace(/\/+$/, '');
const CLIENT_VERSION = process.env.DEEPSEEK_CLIENT_VERSION || '2.0.0';
const REQUEST_TIMEOUT_MS = Number(process.env.DEEPSEEK_TIMEOUT_MS || 60000);
const STREAM_CONNECT_TIMEOUT_MS = Number(process.env.DEEPSEEK_STREAM_TIMEOUT_MS || 60000);

const AUTH_CODE_RE = /token|login|log in|unauthor|auth|session expired|not logged/i;

class UpstreamError extends Error {
  constructor(message, { status = 502, type = 'upstream_error', retryAfter = null, code = null } = {}) {
    super(message);
    this.name = 'UpstreamError';
    this.status = status;
    this.type = type;
    this.retryAfter = retryAfter;
    this.code = code;
  }
}

function isAuthError(err) {
  if (!err) return false;
  if (err.status === 401 || err.status === 403) return true;
  if (err.code !== null && err.code !== undefined && Number.isFinite(Number(err.code))) {
    const code = Number(err.code);
    if (code === 40001 || code === 40002 || code === 40003 || code === 40100) return true;
  }
  return AUTH_CODE_RE.test(String(err.message || ''));
}

function combineSignals(...signals) {
  const list = signals.filter(Boolean);
  if (list.length === 0) return undefined;
  if (list.length === 1) return list[0];
  const controller = new AbortController();
  for (const signal of list) {
    if (signal.aborted) {
      controller.abort(signal.reason);
      break;
    }
    signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true });
  }
  return controller.signal;
}

function bizData(payload) {
  return payload && payload.data && payload.data.biz_data ? payload.data.biz_data : null;
}

class DeepSeekAccount {
  constructor(spec, deviceId, onUpdate) {
    this.spec = spec;
    this.deviceId = deviceId;
    this.onUpdate = onUpdate || (() => {});
    this.id = spec.id || 'acc';
    this.cooldownUntil = 0;
    this.failures = 0;
    this.lastUsedAt = 0;
    this.refreshing = null;
    this.lastError = null;
  }

  get token() {
    return this.spec.token || '';
  }

  get ready() {
    return Boolean(this.spec.token) || Boolean(this.spec.cookie) || Boolean(this.spec.email && this.spec.password);
  }

  get coolingDown() {
    return this.cooldownUntil > Date.now();
  }

  cooldown(ms) {
    this.cooldownUntil = Math.max(this.cooldownUntil, Date.now() + ms);
  }

  headers({ auth = true, json = true } = {}) {
    const headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
      Accept: '*/*',
      'x-client-platform': 'web',
      'x-client-version': CLIENT_VERSION,
      'x-app-version': CLIENT_VERSION,
      'x-client-locale': process.env.DEEPSEEK_LOCALE || 'en_US',
      'x-client-timezone-offset': '0',
      Origin: BASE_URL,
      Referer: `${BASE_URL}/`,
    };
    if (json) headers['Content-Type'] = 'application/json';
    if (auth && this.token) headers.Authorization = `Bearer ${this.token}`;
    if (this.spec.cookie) headers.Cookie = this.spec.cookie;
    if (this.spec.hif_dliq) headers['x-hif-dliq'] = this.spec.hif_dliq;
    if (this.spec.hif_leim) headers['x-hif-leim'] = this.spec.hif_leim;
    return headers;
  }

  async login({ signal } = {}) {
    if (!this.spec.email || !this.spec.password) {
      throw new UpstreamError('No token and no email/password for this account. Run: node login.js add ...', {
        status: 503,
        type: 'no_auth',
      });
    }
    if (this.refreshing) return this.refreshing;

    this.refreshing = (async () => {
      const payload = {
        email: this.spec.email,
        password: this.spec.password,
        device_id: this.spec.deviceId || this.deviceId,
        os: 'android',
      };

      let resp;
      try {
        resp = await fetch(`${BASE_URL}/api/v0/users/login`, {
          method: 'POST',
          headers: this.headers({ auth: false }),
          body: JSON.stringify(payload),
          signal: combineSignals(signal, AbortSignal.timeout(REQUEST_TIMEOUT_MS)),
        });
      } catch (err) {
        throw new UpstreamError(`DeepSeek login failed: ${err.message}`, { type: 'network_error' });
      }

      const text = await resp.text();
      let body = null;
      try {
        body = text ? JSON.parse(text) : null;
      } catch (_) {
        /* handled below */
      }

      // Current web login answers 202 with an empty body when it wants browser
      // verification / captcha. There is no token to read in that case.
      if (resp.status === 202 || !String(text).trim()) {
        throw new UpstreamError(
          'DeepSeek login requires browser verification (HTTP 202/captcha). Copy a token from DevTools instead: node login.js add --token ...',
          { status: 401, type: 'authentication_error', code: 'login_verification' },
        );
      }

      if (!resp.ok) {
        throw new UpstreamError(`DeepSeek login HTTP ${resp.status}: ${String(text).slice(0, 300)}`, {
          status: resp.status,
          type: 'authentication_error',
        });
      }
      if (!body || (body.code !== undefined && body.code !== 0)) {
        throw new UpstreamError(`DeepSeek login rejected: ${(body && (body.msg || body.message)) || String(text).slice(0, 300)}`, {
          status: 401,
          type: 'authentication_error',
          code: body && body.code,
        });
      }

      const user = bizData(body) && bizData(body).user;
      if (!user || !user.token) {
        throw new UpstreamError('DeepSeek login returned no token (captcha or new login flow may be required). Use: node login.js add --token ...', {
          status: 401,
          type: 'authentication_error',
        });
      }

      this.spec.token = user.token;
      this.spec.lastLoginAt = Date.now();
      this.failures = 0;
      this.onUpdate(this.spec);
      return user.token;
    })();

    try {
      return await this.refreshing;
    } finally {
      this.refreshing = null;
    }
  }

  async ensureToken() {
    if (this.spec.token) return;
    if (this.spec.cookie) {
      const token = await this.fetchCurrentToken();
      if (token) return;
    }
    if (this.spec.email && this.spec.password) {
      await this.login();
      return;
    }
    throw new UpstreamError(
      'No token and no usable cookie for this account. Run: node login.js add --token ... (or --cookie ...)',
      { status: 503, type: 'no_auth' },
    );
  }

  /**
   * Exchange the browser session cookie for the bearer token the API expects.
   * Newer web builds keep the token out of localStorage; /users/current still
   * returns it when a valid ds_session_id cookie is presented.
   */
  async fetchCurrentToken({ signal } = {}) {
    let json;
    try {
      json = await this.requestJson('/api/v0/users/current', {
        method: 'GET',
        body: undefined,
        signal,
        ensure: false,
      });
    } catch (_) {
      return null;
    }

    const biz = bizData(json) || {};
    const token = biz.token || (biz.user && biz.user.token) || '';
    if (!token) return null;

    this.spec.token = token;
    this.failures = 0;
    this.onUpdate(this.spec);
    return token;
  }

  async requestJson(pathname, { method = 'POST', body = {}, signal, auth = true, timeout = REQUEST_TIMEOUT_MS, ensure = true } = {}) {
    if (ensure) await this.ensureToken();

    let resp;
    try {
      resp = await fetch(`${BASE_URL}${pathname}`, {
        method,
        headers: this.headers({ auth }),
        body: method === 'GET' || body === undefined ? undefined : JSON.stringify(body),
        signal: combineSignals(signal, AbortSignal.timeout(timeout)),
      });
    } catch (err) {
      throw new UpstreamError(`DeepSeek request failed (${pathname}): ${err.message}`, {
        type: 'network_error',
        status: 502,
      });
    }

    const text = await resp.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch (_) {
      /* handled below */
    }

    if (!resp.ok) {
      const retryAfter = resp.headers.get('retry-after');
      const rateLimited = resp.status === 429 || /too many|rate.?limit|频繁|稍后|later/i.test(text);
      throw new UpstreamError(
        `DeepSeek HTTP ${resp.status} on ${pathname}: ${String(text).slice(0, 400)}`,
        {
          status: rateLimited ? 429 : resp.status,
          type: rateLimited ? 'rate_limit_error' : ((resp.status === 401 || resp.status === 403) ? 'authentication_error' : 'upstream_error'),
          retryAfter: retryAfter ? Number(retryAfter) || retryAfter : null,
          code: json && json.code,
        },
      );
    }

    if (json && json.code !== undefined && json.code !== 0) {
      const code = Number(json.code);
      const authInvalid = code === 40001 || code === 40002 || code === 40003 || code === 40100;
      throw new UpstreamError(`DeepSeek ${pathname} code ${json.code}: ${json.msg || json.message || 'unknown error'}`, {
        status: authInvalid ? 401 : 502,
        type: authInvalid ? 'authentication_error' : 'upstream_error',
        code: json.code,
      });
    }

    // Some errors arrive as HTTP 200 with a business code (e.g. throttling).
    const data = json && json.data;
    if (data && data.biz_code !== undefined && data.biz_code !== null && Number(data.biz_code) !== 0) {
      const message = data.biz_msg || data.biz_message || data.message || data.msg || `business code ${data.biz_code}`;
      const rateLimited = /too many|rate.?limit|频繁|稍后|later/i.test(message);
      throw new UpstreamError(`DeepSeek ${pathname} business error ${data.biz_code}: ${message}`, {
        status: rateLimited ? 429 : 502,
        type: rateLimited ? 'rate_limit_error' : 'upstream_error',
        code: data.biz_code,
      });
    }

    return json;
  }

  async createSession({ signal } = {}) {
    // The current web protocol takes an empty object here. The old "agent"
    // field is gone.
    const json = await this.requestJson('/api/v0/chat_session/create', { body: {}, signal });
    const biz = bizData(json) || {};
    const id = (biz.chat_session && biz.chat_session.id) || biz.id;
    if (!id) throw new UpstreamError('DeepSeek returned no chat_session id', { status: 502 });
    return id;
  }

  async createPow(targetPath = '/api/v0/chat/completion', { signal } = {}) {
    const json = await this.requestJson('/api/v0/chat/create_pow_challenge', {
      body: { target_path: targetPath },
      signal,
      timeout: 30000,
    });
    const challenge = (bizData(json) && bizData(json).challenge) || (json && json.data && json.data.challenge);
    if (!challenge) throw new UpstreamError('DeepSeek returned no PoW challenge (bad token?)', { status: 401, type: 'authentication_error' });

    const answer = await solvePOW(challenge, {
      wasmUrl: this.spec.wasmUrl || process.env.DEEPSEEK_WASM_URL,
    });

    const header = {
      algorithm: challenge.algorithm,
      challenge: challenge.challenge,
      salt: challenge.salt,
      answer,
      signature: challenge.signature,
      target_path: challenge.target_path || targetPath,
    };
    return Buffer.from(JSON.stringify(header), 'utf8').toString('base64');
  }

  async completion(payload, { signal } = {}) {
    const pow = await this.createPow('/api/v0/chat/completion', { signal });

    // The connect timeout must cover only the response headers. Keeping an
    // AbortSignal.timeout() on the fetch would kill long thinking streams.
    const connectController = new AbortController();
    const connectTimer = setTimeout(() => {
      connectController.abort(new Error(`DeepSeek did not start a stream within ${STREAM_CONNECT_TIMEOUT_MS}ms`));
    }, STREAM_CONNECT_TIMEOUT_MS);

    let resp;
    try {
      resp = await fetch(`${BASE_URL}/api/v0/chat/completion`, {
        method: 'POST',
        headers: {
          ...this.headers(),
          Accept: 'text/event-stream',
          'X-DS-PoW-Response': pow,
        },
        body: JSON.stringify(payload),
        signal: combineSignals(signal, connectController.signal),
      });
    } catch (err) {
      throw new UpstreamError(`DeepSeek completion failed: ${err.message}`, { type: 'network_error', status: 502 });
    } finally {
      clearTimeout(connectTimer);
    }

    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      const retryAfter = resp.headers.get('retry-after');
      const rateLimited = resp.status === 429 || /too many|rate.?limit|频繁|稍后|later/i.test(text);
      throw new UpstreamError(
        `DeepSeek completion HTTP ${resp.status}: ${String(text).slice(0, 400)}`,
        {
          status: rateLimited ? 429 : resp.status,
          type: rateLimited ? 'rate_limit_error' : ((resp.status === 401 || resp.status === 403) ? 'authentication_error' : 'upstream_error'),
          retryAfter: retryAfter ? Number(retryAfter) || retryAfter : null,
        },
      );
    }

    // A 200 JSON envelope is an error, not a stream.
    const contentType = String(resp.headers.get('content-type') || '').toLowerCase();
    if (contentType.includes('application/json')) {
      const text = await resp.text().catch(() => '');
      let message = text;
      try {
        const parsed = JSON.parse(text);
        message = (parsed.data && (parsed.data.biz_msg || parsed.data.biz_message)) || parsed.msg || parsed.message || text;
      } catch (_) {
        /* keep raw */
      }
      throw new UpstreamError(`DeepSeek did not start a stream: ${String(message).slice(0, 400)}`, {
        status: 502,
        type: 'upstream_error',
      });
    }

    return resp;
  }

  async deleteSession(sessionId) {
    if (!sessionId) return;
    try {
      await this.requestJson('/api/v0/chat_session/delete', {
        body: { chat_session_id: sessionId },
        timeout: 8000,
      });
    } catch (_) {
      /* cleanup is best-effort */
    }
  }
}

class AccountPool {
  constructor(store) {
    this.store = store;
    this.accounts = store.accounts.map((spec) => new DeepSeekAccount(
      spec,
      store.deviceId,
      (updated) => {
        const original = this.store.accounts.find((a) => a.id === updated.id);
        if (original && !original.env) {
          original.token = updated.token;
          original.lastLoginAt = updated.lastLoginAt;
          try {
            this.persist();
          } catch (_) {
            /* non-fatal */
          }
        }
      },
    ));
    this.cursor = 0;
  }

  persist() {
    const { saveStore } = require('./store');
    saveStore(this.store);
  }

  get size() {
    return this.accounts.length;
  }

  status() {
    return this.accounts.map((a) => ({
      id: a.id,
      label: a.spec.label || a.id,
      ready: a.ready,
      has_token: Boolean(a.spec.token),
      has_credentials: Boolean(a.spec.email && a.spec.password),
      cooldown_sec: Math.max(0, Math.ceil((a.cooldownUntil - Date.now()) / 1000)),
      failures: a.failures,
      last_error: a.lastError,
    }));
  }

  /**
   * Pick a usable account. Sticky accounts (passed in) are preferred so a
   * conversation stays on one login.
   */
  pick({ exclude = [], prefer = null } = {}) {
    const usable = this.accounts.filter((a) => a.ready && !a.coolingDown && !exclude.includes(a.id));
    if (usable.length === 0) {
      const cooling = this.accounts
        .filter((a) => a.ready && !exclude.includes(a.id))
        .sort((a, b) => a.cooldownUntil - b.cooldownUntil)[0];
      if (cooling) {
        const wait = Math.max(1, Math.ceil((cooling.cooldownUntil - Date.now()) / 1000));
        throw new UpstreamError(`All DeepSeek accounts are cooling down. Retry in ~${wait}s.`, {
          status: 429,
          type: 'rate_limit_error',
          retryAfter: wait,
        });
      }
      throw new UpstreamError('No DeepSeek account configured. Run: node login.js add --token ...', {
        status: 503,
        type: 'no_auth',
      });
    }

    if (prefer) {
      const sticky = usable.find((a) => a.id === prefer);
      if (sticky) return sticky;
    }

    const account = usable[this.cursor % usable.length];
    this.cursor++;
    account.lastUsedAt = Date.now();
    return account;
  }

  markFailure(account, err) {
    account.failures++;
    account.lastError = err && err.message ? err.message : String(err);
    if (err && err.type === 'rate_limit_error') {
      const seconds = Number(err.retryAfter) || 60;
      account.cooldown(Math.max(1000, seconds * 1000));
    } else if (err && err.type === 'authentication_error') {
      account.cooldown(5 * 60 * 1000);
    } else {
      account.cooldown(5 * 1000);
    }
  }
}

// ---------------- SSE protocol parser ----------------

class DeepSeekStreamParser {
  constructor(options = {}) {
    this.kind = options.thinking || process.env.DEEPSEEK_DEFAULT_REASONING === '1' ? 'thinking' : 'text';
    this.lastPath = null;
    this.done = false;
    this.finished = false;
    this.completionTokens = null;
    this.searchResults = [];
    this.responseMessageId = null;
  }

  static classify(fragment) {
    const type = String((fragment && fragment.type) || '').toUpperCase();
    return type === 'THINK' || type === 'REASONING' ? 'thinking' : 'text';
  }

  static textOf(fragment) {
    if (!fragment || typeof fragment.content !== 'string') return '';
    return fragment.content;
  }

  pushResults(list) {
    if (!Array.isArray(list)) return;
    for (const item of list) {
      if (!item || typeof item !== 'object') continue;
      const url = typeof item.url === 'string' ? item.url.trim() : '';
      if (!/^https?:\/\//i.test(url)) continue;
      const citeIndex = item.cite_index !== undefined ? Number(item.cite_index) : null;
      const existing = this.searchResults.find((r) => r.url === url);
      if (existing) {
        if (citeIndex !== null && (existing.cite_index === null || citeIndex < existing.cite_index)) {
          existing.cite_index = citeIndex;
        }
        if (!existing.title && item.title) existing.title = item.title;
      } else {
        this.searchResults.push({
          url,
          title: typeof item.title === 'string' ? item.title : '',
          cite_index: Number.isFinite(citeIndex) ? citeIndex : null,
        });
      }
    }
  }

  /** Parse one SSE line. Returns an array of events. */
  pushLine(rawLine) {
    const events = [];
    if (rawLine === null || rawLine === undefined || this.done) return events;

    const line = String(rawLine).trim();
    if (!line || line.startsWith(':') || line.startsWith('event:')) return events;
    if (!line.startsWith('data:')) return events;

    const data = line.slice(5).trim();
    if (!data) return events;
    if (data === '[DONE]') {
      this.done = true;
      events.push({ type: 'finish' });
      return events;
    }

    let chunk;
    try {
      chunk = JSON.parse(data);
    } catch (_) {
      return events;
    }

    const path = chunk.p !== undefined ? chunk.p : this.lastPath;
    if (chunk.p !== undefined) this.lastPath = chunk.p;
    const op = chunk.o;
    const value = chunk.v;

    if (chunk.type === 'error' || chunk.error) {
      const message = chunk.error?.message || chunk.content || chunk.message || 'DeepSeek stream error';
      events.push({ type: 'error', message: String(message) });
      this.done = true;
      return events;
    }

    if (chunk.code === 'content_filter') {
      events.push({ type: 'error', message: 'DeepSeek content filter triggered' });
      this.done = true;
      return events;
    }

    if (chunk.accumulated_token_usage !== undefined) {
      this.completionTokens = Number(chunk.accumulated_token_usage) || this.completionTokens;
    }

    // Full initial response frame.
    if (value && typeof value === 'object' && value.response) {
      const response = value.response;
      if (response.message_id) this.responseMessageId = response.message_id;
      const fragments = Array.isArray(response.fragments) ? response.fragments : [];
      for (const fragment of fragments) {
        const text = DeepSeekStreamParser.textOf(fragment);
        if (!text) continue;
        this.kind = DeepSeekStreamParser.classify(fragment);
        events.push({ type: this.kind, text });
      }
      return events;
    }

    // A bare string append has no path. If the last path is a fragment or
    // /results frame, the text belongs to the active fragment; do not drop it
    // as a malformed results payload.
    if (chunk.p === undefined && typeof value === 'string' && value
        && typeof path === 'string' && path.startsWith('response/fragments')) {
      events.push({ type: this.kind, text: value });
      return events;
    }

    // Appended fragments.
    if (path === 'response/fragments' && op === 'APPEND') {
      const fragments = Array.isArray(value) ? value : [value];
      for (const fragment of fragments) {
        if (!fragment || typeof fragment !== 'object') continue;
        if (fragment.results) this.pushResults(fragment.results);
        const text = DeepSeekStreamParser.textOf(fragment);
        if (!text) continue;
        this.kind = DeepSeekStreamParser.classify(fragment);
        events.push({ type: this.kind, text });
      }
      return events;
    }

    // Any fragment content append (also handles -1 which is the active one).
    if (typeof path === 'string' && path.startsWith('response/fragments/')) {
      if (path.includes('/results')) {
        const results = value && !Array.isArray(value) && Array.isArray(value.results) ? value.results : value;
        this.pushResults(results);
        return events;
      }
      if (path.endsWith('/content') && typeof value === 'string') {
        if (value) events.push({ type: this.kind, text: value });
        return events;
      }
      return events;
    }

    // Batch metadata: usage, quasi_status, title, etc.
    if (path === 'response' && op === 'BATCH' && Array.isArray(value)) {
      for (const item of value) {
        if (!item || typeof item !== 'object') continue;
        if (item.p === 'accumulated_token_usage') {
          this.completionTokens = Number(item.v) || this.completionTokens;
        }
        if (item.p === 'results') this.pushResults(item.v);
      }
      return events;
    }

    if (path === 'response/status' && String(value).toUpperCase() === 'FINISHED') {
      if (!this.finished) {
        this.finished = true;
        events.push({ type: 'finish' });
      }
      return events;
    }

    // Legacy simple payload: {"v":"text"} with no path.
    if (typeof value === 'string' && value) {
      // Skip paths that are known metadata.
      if (typeof path === 'string' && /\/(?:status|elapsed_secs|title|quasi_status)$/.test(path)) return events;
      events.push({ type: this.kind, text: value });
    }

    return events;
  }
}

module.exports = {
  BASE_URL,
  DeepSeekAccount,
  AccountPool,
  DeepSeekStreamParser,
  UpstreamError,
  isAuthError,
  combineSignals,
  readLines,
};
