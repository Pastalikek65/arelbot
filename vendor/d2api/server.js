'use strict';

const path = require('path');
require('./lib/env').loadDotEnv(path.join(__dirname, '.env'));

const http = require('http');
const crypto = require('crypto');

const { loadStore } = require('./lib/store');
const {
  AccountPool,
  DeepSeekStreamParser,
  UpstreamError,
  isAuthError,
  readLines,
} = require('./lib/deepseek');
const { modelConfig, isKnownModel, modelList, REAL_MODEL } = require('./lib/models');
const {
  buildPrompt,
  normalizeTools,
  extractToolCalls,
  CitationFilter,
  contentToText,
} = require('./lib/tools');

const PORT = Number(process.env.PORT || 9655);
const HOST = process.env.HOST || '127.0.0.1';
const PROXY_API_KEY = String(process.env.PROXY_API_KEY || process.env.API_KEY || '').trim();
const DEFAULT_MODEL = process.env.DEFAULT_MODEL || 'deepseek-v4.1-flash';
const MAX_PROMPT_CHARS = Number(process.env.MAX_PROMPT_CHARS || 220000);
const MAX_BODY_BYTES = Number(process.env.MAX_BODY_BYTES || 32 * 1024 * 1024);
const MAX_OUTPUT_CHARS = Math.max(1024, Number(process.env.MAX_OUTPUT_CHARS || 2 * 1024 * 1024));
const MAX_CONCURRENT = Math.max(1, Number(process.env.MAX_CONCURRENT || 8));
const KEEPALIVE_MS = Math.max(1000, Number(process.env.STREAM_KEEPALIVE_MS || 10000));
const DELETE_SESSIONS = process.env.DELETE_SESSIONS !== '0';
const LOG_REQUESTS = process.env.LOG_REQUESTS !== '0';

const store = loadStore();
const pool = new AccountPool(store);

// ---------------- small utilities ----------------

class Semaphore {
  constructor(limit) {
    this.limit = limit;
    this.active = 0;
    this.queue = [];
  }

  acquire(signal, timeoutMs = 120000) {
    if (signal && signal.aborted) return Promise.reject(signal.reason || new Error('client disconnected'));
    if (this.active < this.limit) {
      this.active++;
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      const entry = { resolve, reject, timer: null, onAbort: null, signal, settled: false };
      const remove = () => {
        const idx = this.queue.indexOf(entry);
        if (idx !== -1) this.queue.splice(idx, 1);
        clearTimeout(entry.timer);
        if (signal && entry.onAbort) signal.removeEventListener('abort', entry.onAbort);
      };
      entry.onAbort = () => {
        if (entry.settled) return;
        entry.settled = true;
        remove();
        reject(signal.reason || new Error('client disconnected'));
      };
      entry.timer = setTimeout(() => {
        if (entry.settled) return;
        entry.settled = true;
        remove();
        reject(new UpstreamError('Proxy is busy, try again shortly.', { status: 429, type: 'rate_limit_error' }));
      }, timeoutMs);
      this.queue.push(entry);
      if (signal) signal.addEventListener('abort', entry.onAbort, { once: true });
      if (signal && signal.aborted) entry.onAbort();
    });
  }

  release() {
    const next = this.queue.shift();
    if (next) {
      next.settled = true;
      clearTimeout(next.timer);
      // The abort listener belongs to the waiting promise, not the slot.
      // It is removed before ownership passes to the resumed handler.
      if (next.signal && next.onAbort) next.signal.removeEventListener('abort', next.onAbort);
      next.resolve();
      return;
    }
    this.active = Math.max(0, this.active - 1);
  }
}

const limiter = new Semaphore(MAX_CONCURRENT);

function id(prefix) {
  return `${prefix}${crypto.randomBytes(12).toString('hex')}`;
}

function estimateTokens(text) {
  return Math.max(0, Math.ceil(String(text || '').length / 4));
}

function nowSec() {
  return Math.floor(Date.now() / 1000);
}

function sendJson(res, status, body, extraHeaders = {}) {
  if (res.headersSent || res.writableEnded) return;
  const text = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(text),
    ...extraHeaders,
  });
  res.end(text);
}

function openaiError(res, status, message, type = 'invalid_request_error', code = null) {
  sendJson(res, status, {
    error: {
      message,
      type,
      param: null,
      code,
    },
  });
}

function anthropicError(res, status, message, type = 'invalid_request_error') {
  sendJson(res, status, {
    type: 'error',
    error: { type, message },
  });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new UpstreamError('Request body too large', { status: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw.trim()) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch (err) {
        reject(new UpstreamError(`Invalid JSON body: ${err.message}`, { status: 400 }));
      }
    });
    req.on('error', reject);
  });
}

function timingSafeEqual(a, b) {
  const ab = Buffer.from(String(a));
  const bb = Buffer.from(String(b));
  if (ab.length !== bb.length) return false;
  return crypto.timingSafeEqual(ab, bb);
}

function authorized(req) {
  if (!PROXY_API_KEY) return true;
  const auth = req.headers.authorization || '';
  const bearer = auth.startsWith('Bearer ') ? auth.slice(7) : '';
  return timingSafeEqual(bearer, PROXY_API_KEY) || timingSafeEqual(req.headers['x-api-key'] || '', PROXY_API_KEY);
}

function setCors(res) {
  res.setHeader('Access-Control-Allow-Origin', process.env.CORS_ORIGIN || '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, x-api-key, anthropic-version, anthropic-beta');
  res.setHeader('Access-Control-Expose-Headers', 'x-deepseek-model, x-deepseek-sources');
}

function escapeHtml(value) {
  return String(value === undefined || value === null ? '' : value).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

// ---------------- DeepSeek runner ----------------

function buildUpstreamPayload({ sessionId, prompt, cfg }) {
  const payload = {
    chat_session_id: sessionId,
    parent_message_id: null,
    prompt,
    ref_file_ids: [],
    thinking_enabled: Boolean(cfg.thinking),
    search_enabled: Boolean(cfg.search),
    action: null,
    preempt: false,
  };
  if (cfg.modelType) payload.model_type = cfg.modelType;
  return payload;
}

/**
 * Run one completion on one account and stream events to the handlers.
 * The caller owns account selection / retries.
 */
async function runOnAccount(account, { prompt, cfg, signal }, handlers) {
  let sessionId = null;
  let emittedAny = false;
  const parser = new DeepSeekStreamParser({ thinking: cfg.thinking });
  const filter = new CitationFilter();
  let thinking = '';
  let text = '';
  let finishReason = null;
  let upstreamError = null;
  const appendBounded = (kind, addition) => {
    if (text.length + thinking.length + addition.length > MAX_OUTPUT_CHARS) {
      throw new UpstreamError('DeepSeek response exceeded proxy limit', {
        status: 502, type: 'upstream_error', code: 'response_too_large',
      });
    }
    if (kind === 'thinking') thinking += addition;
    else text += addition;
  };

  try {
    sessionId = await account.createSession({ signal });
    const resp = await account.completion(buildUpstreamPayload({ sessionId, prompt, cfg }), { signal });

    for await (const line of readLines(resp, { signal })) {
      const events = parser.pushLine(line);
      for (const event of events) {
        if (event.type === 'thinking') {
          appendBounded('thinking', event.text);
          emittedAny = true;
          if (handlers.onThinking) handlers.onThinking(event.text);
        } else if (event.type === 'text') {
          const clean = filter.feed(event.text);
          if (clean) {
            appendBounded('text', clean);
            emittedAny = true;
            if (handlers.onText) handlers.onText(clean);
          }
        } else if (event.type === 'error') {
          upstreamError = new UpstreamError(event.message, { status: 502, type: 'upstream_error' });
        } else if (event.type === 'finish') {
          finishReason = 'stop';
        }
      }
      if (parser.done) break;
    }

    const tail = filter.flush();
    if (tail) {
      appendBounded('text', tail);
      if (handlers.onText) handlers.onText(tail);
    }

    if (upstreamError) throw upstreamError;
    if (!text && !thinking && !parser.done) {
      throw new UpstreamError('DeepSeek returned an empty response. The account may be rate limited or the session was rejected.', {
        status: 502,
        type: 'empty_response',
      });
    }

    return {
      thinking,
      text,
      completionTokens: parser.completionTokens,
      sources: parser.searchResults,
      finishReason: finishReason || 'stop',
    };
  } finally {
    if (DELETE_SESSIONS && sessionId) {
      await account.deleteSession(sessionId);
    }
  }
}

/**
 * Pick accounts, retry on auth / rate-limit before any output was emitted.
 */
async function complete({ prompt, cfg, signal }, handlers) {
  const tried = [];
  const maxAttempts = Math.max(1, Math.min(pool.size, Number(process.env.MAX_ACCOUNT_ATTEMPTS || 3)));
  let lastError = null;
  let emitted = false;

  const wrapped = {
    onThinking: (t) => {
      emitted = true;
      if (handlers.onThinking) handlers.onThinking(t);
    },
    onText: (t) => {
      emitted = true;
      if (handlers.onText) handlers.onText(t);
    },
  };

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    let account;
    try {
      account = pool.pick({ exclude: tried, prefer: handlers.preferAccountId });
    } catch (err) {
      throw lastError || err;
    }
    tried.push(account.id);

    try {
      const result = await runOnAccount(account, { prompt, cfg, signal }, wrapped);
      result.accountId = account.id;
      return result;
    } catch (err) {
      lastError = err;
      if (signal && signal.aborted) throw signal.reason || err;
      if (err && err.name === 'AbortError') throw err;

      if (isAuthError(err) && account.spec.email && account.spec.password) {
        try {
          await account.login();
          const result = await runOnAccount(account, { prompt, cfg, signal }, wrapped);
          result.accountId = account.id;
          return result;
        } catch (retryErr) {
          lastError = retryErr;
        }
      }

      pool.markFailure(account, lastError);

      // Once bytes went to the client, a retry would duplicate output.
      if (emitted) throw lastError;
      const retryable = lastError.type === 'rate_limit_error'
        || lastError.type === 'empty_response'
        || lastError.type === 'network_error'
        || isAuthError(lastError);
      if (!retryable) throw lastError;
    }
  }

  throw lastError || new UpstreamError('All DeepSeek attempts failed', { status: 502 });
}

// ---------------- OpenAI adapter ----------------

function pickOpenAiModel(requested) {
  const name = String(requested || DEFAULT_MODEL).trim().toLowerCase();
  if (isKnownModel(name)) return name;
  // Clients often send gpt-* / claude-* when pointed at a proxy. Fall back to
  // the default model instead of failing the whole request.
  return String(DEFAULT_MODEL).trim().toLowerCase();
}

function annotationsFromSources(sources) {
  if (!Array.isArray(sources) || sources.length === 0) return null;
  return sources.map((source) => ({
    type: 'url_citation',
    url_citation: {
      url: source.url,
      title: source.title || source.url,
      start_index: 0,
      end_index: 0,
    },
  }));
}

function sseData(res, payload) {
  if (res.writableEnded) return;
  res.write(`data: ${typeof payload === 'string' ? payload : JSON.stringify(payload)}\n\n`);
}

async function handleOpenAiChat(req, res, body) {
  const requestedModel = body.model || DEFAULT_MODEL;
  const model = pickOpenAiModel(requestedModel);
  const cfg = modelConfig(model);
  if (body.reasoning_effort && String(body.reasoning_effort).toLowerCase() !== 'none') cfg.thinking = true;
  if (body.web_search_options || body.search === true || body.enable_search === true) cfg.search = true;

  const messages = Array.isArray(body.messages) ? body.messages : [];
  if (messages.length === 0) {
    return openaiError(res, 400, "'messages' must be a non-empty array");
  }

  const tools = body.tool_choice === 'none' ? [] : normalizeTools(body.tools);
  const prompt = buildPrompt(messages, tools);
  if (prompt.length > MAX_PROMPT_CHARS) {
    return openaiError(res, 400, `Prompt is too long (${prompt.length} chars, limit ${MAX_PROMPT_CHARS}). Reduce the history or raise MAX_PROMPT_CHARS.`);
  }

  const stream = body.stream === true;
  const includeUsage = Boolean(body.stream_options && body.stream_options.include_usage);
  const completionId = id('chatcmpl-');
  const created = nowSec();
  const ac = new AbortController();
  res.on('close', () => {
    if (!res.writableEnded) ac.abort(new Error('client disconnected'));
  });

  let keepalive = null;
  if (stream) {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'x-deepseek-model': REAL_MODEL,
    });
    res.flushHeaders?.();
    sseData(res, {
      id: completionId,
      object: 'chat.completion.chunk',
      created,
      model,
      choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }],
    });
    keepalive = setInterval(() => {
      if (!res.writableEnded) res.write(': ping\n\n');
    }, KEEPALIVE_MS);
    keepalive.unref?.();
  }

  let release = null;
  try {
    await limiter.acquire(ac.signal);
    release = () => limiter.release();
    if (ac.signal.aborted) throw ac.signal.reason || new Error('client disconnected');

    const result = await complete({ prompt, cfg, signal: ac.signal }, {
      onThinking: stream ? (delta) => {
        sseData(res, {
          id: completionId,
          object: 'chat.completion.chunk',
          created,
          model,
          choices: [{ index: 0, delta: { reasoning_content: delta }, finish_reason: null }],
        });
      } : null,
      onText: stream && tools.length === 0 ? (delta) => {
        sseData(res, {
          id: completionId,
          object: 'chat.completion.chunk',
          created,
          model,
          choices: [{ index: 0, delta: { content: delta }, finish_reason: null }],
        });
      } : null,
    });

    // Tool calls are detected on the full answer (tool turns are buffered
    // upstream on purpose: partial JSON must never reach the client).
    const parsed = tools.length > 0 ? extractToolCalls(result.text, tools.map((t) => t.name)) : { calls: null, text: result.text };
    let calls = parsed.calls;
    if (!calls && tools.length > 0 && result.thinking) {
      const fromThinking = extractToolCalls(result.thinking, tools.map((t) => t.name));
      if (fromThinking.calls) calls = fromThinking.calls;
    }
    const text = calls ? parsed.text : result.text;
    const finishReason = calls ? 'tool_calls' : 'stop';
    const sources = annotationsFromSources(result.sources);

    const promptTokens = estimateTokens(prompt);
    const completionTokens = result.completionTokens || estimateTokens(`${result.thinking}${result.text}`);
    const usage = {
      prompt_tokens: promptTokens,
      completion_tokens: completionTokens,
      total_tokens: promptTokens + completionTokens,
    };

    if (!stream) {
      const message = { role: 'assistant', content: text || (calls ? null : '') };
      if (result.thinking) message.reasoning_content = result.thinking;
      if (calls) message.tool_calls = calls.map(({ index, ...call }) => call);
      if (sources) message.annotations = sources;

      return sendJson(res, 200, {
        id: completionId,
        object: 'chat.completion',
        created,
        model,
        choices: [{ index: 0, message, finish_reason: finishReason }],
        usage,
        system_fingerprint: 'deepseek-web',
      }, {
        'x-deepseek-model': REAL_MODEL,
        'x-deepseek-account': result.accountId,
      });
    }

    // Streaming: text already streamed live when no tools were requested.
    // When tools were requested the answer was buffered so partial JSON never
    // reaches the client; emit the parsed text now.
    if (calls) {
      // Preserve any prose the model emitted before the tool JSON.
      if (text) {
        const chunkSize = 400;
        for (let i = 0; i < text.length; i += chunkSize) {
          sseData(res, {
            id: completionId,
            object: 'chat.completion.chunk',
            created,
            model,
            choices: [{ index: 0, delta: { content: text.slice(i, i + chunkSize) }, finish_reason: null }],
          });
        }
      }
      calls.forEach((call, index) => {
        sseData(res, {
          id: completionId,
          object: 'chat.completion.chunk',
          created,
          model,
          choices: [{
            index: 0,
            delta: {
              tool_calls: [{
                index,
                id: call.id,
                type: 'function',
                function: { name: call.function.name, arguments: call.function.arguments },
              }],
            },
            finish_reason: null,
          }],
        });
      });
    } else if (text) {
      // Text already streamed live when no tools were requested. When tools
      // were requested the answer was buffered, so emit it now.
      if (tools.length > 0) {
        const chunkSize = 400;
        for (let i = 0; i < text.length; i += chunkSize) {
          sseData(res, {
            id: completionId,
            object: 'chat.completion.chunk',
            created,
            model,
            choices: [{ index: 0, delta: { content: text.slice(i, i + chunkSize) }, finish_reason: null }],
          });
        }
      }
    }

    if (sources) {
      sseData(res, {
        id: completionId,
        object: 'chat.completion.chunk',
        created,
        model,
        choices: [{ index: 0, delta: { annotations: sources }, finish_reason: null }],
      });
    }

    sseData(res, {
      id: completionId,
      object: 'chat.completion.chunk',
      created,
      model,
      choices: [{ index: 0, delta: {}, finish_reason: finishReason }],
    });

    if (includeUsage) {
      sseData(res, {
        id: completionId,
        object: 'chat.completion.chunk',
        created,
        model,
        choices: [],
        usage,
      });
    }

    sseData(res, '[DONE]');
    res.end();
  } catch (err) {
    const status = err && err.status ? err.status : 502;
    const message = err && err.message ? err.message : 'DeepSeek request failed';
    if (stream && res.headersSent) {
      sseData(res, { error: { message, type: err.type || 'upstream_error', code: err.code || null } });
      sseData(res, '[DONE]');
      res.end();
    } else {
      openaiError(res, status, message, err.type || 'upstream_error', err.code || null);
    }
  } finally {
    if (keepalive) clearInterval(keepalive);
    if (release) release();
  }
}

// ---------------- Anthropic adapter ----------------

function anthropicToOpenAi(body) {
  const messages = [];

  const systemText = contentToText(body.system);
  if (systemText) messages.push({ role: 'system', content: systemText });

  for (const msg of Array.isArray(body.messages) ? body.messages : []) {
    if (!msg || !msg.role) continue;
    if (msg.role === 'assistant') {
      const blocks = Array.isArray(msg.content) ? msg.content : [{ type: 'text', text: contentToText(msg.content) }];
      const textParts = [];
      const toolCalls = [];
      for (const block of blocks) {
        if (typeof block === 'string') {
          textParts.push(block);
        } else if (block && block.type === 'text') {
          textParts.push(block.text || '');
        } else if (block && block.type === 'tool_use') {
          toolCalls.push({
            id: block.id || id('call_'),
            type: 'function',
            function: {
              name: block.name,
              arguments: JSON.stringify(block.input || {}),
            },
          });
        }
      }
      messages.push({
        role: 'assistant',
        content: textParts.join('\n'),
        ...(toolCalls.length ? { tool_calls: toolCalls } : {}),
      });
    } else if (msg.role === 'user') {
      const blocks = Array.isArray(msg.content) ? msg.content : [{ type: 'text', text: contentToText(msg.content) }];
      const textParts = [];
      for (const block of blocks) {
        if (typeof block === 'string') {
          textParts.push(block);
        } else if (block && block.type === 'text') {
          textParts.push(block.text || '');
        } else if (block && block.type === 'tool_result') {
          messages.push({
            role: 'tool',
            tool_call_id: block.tool_use_id,
            content: contentToText(block.content),
          });
        }
      }
      if (textParts.length) messages.push({ role: 'user', content: textParts.join('\n') });
    } else {
      messages.push({ role: msg.role, content: contentToText(msg.content) });
    }
  }

  const tools = body.tool_choice && body.tool_choice.type === 'none' ? [] : normalizeTools(body.tools);
  return { messages, tools };
}

function anthropicModelCfg(requested) {
  const name = String(requested || '').trim().toLowerCase();
  if (isKnownModel(name)) return { model: name, cfg: modelConfig(name) };
  const fallback = String(process.env.ANTHROPIC_MODEL || DEFAULT_MODEL).trim().toLowerCase();
  const cfg = modelConfig(fallback);
  if (/think|reason|r1/.test(name)) cfg.thinking = true;
  return { model: fallback, cfg };
}

function anthropicBlocks(result, calls, text) {
  const blocks = [];
  if (text) blocks.push({ type: 'text', text });
  if (calls) {
    for (const call of calls) {
      let input = {};
      try {
        input = JSON.parse(call.function.arguments || '{}');
      } catch (_) {
        input = { _raw: call.function.arguments };
      }
      blocks.push({ type: 'tool_use', id: call.id, name: call.function.name, input });
    }
  }
  if (blocks.length === 0) blocks.push({ type: 'text', text: '' });
  return blocks;
}

async function handleAnthropicMessages(req, res, body) {
  const { model, cfg } = anthropicModelCfg(body.model);
  const responseModel = body.model || model;
  if (body.thinking && body.thinking.type === 'enabled') cfg.thinking = true;

  const { messages, tools } = anthropicToOpenAi(body);
  if (messages.length === 0) {
    return anthropicError(res, 400, "'messages' must be a non-empty array");
  }

  const prompt = buildPrompt(messages, tools);
  if (prompt.length > MAX_PROMPT_CHARS) {
    return anthropicError(res, 400, `Prompt is too long (${prompt.length} chars, limit ${MAX_PROMPT_CHARS}).`);
  }

  const stream = body.stream === true;
  const messageId = id('msg_');
  const ac = new AbortController();
  res.on('close', () => {
    if (!res.writableEnded) ac.abort(new Error('client disconnected'));
  });

  let release = null;
  let keepalive = null;
  try {
    await limiter.acquire(ac.signal);
    release = () => limiter.release();
    if (ac.signal.aborted) throw ac.signal.reason || new Error('client disconnected');

    const promptTokens = estimateTokens(prompt);
    let nextBlockIndex = 0;

    const sendEvent = (event, data) => {
      if (res.writableEnded) return;
      res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    };

    if (stream) {
      res.writeHead(200, {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache, no-transform',
        Connection: 'keep-alive',
        'x-deepseek-model': REAL_MODEL,
      });
      res.flushHeaders?.();

      sendEvent('message_start', {
        type: 'message_start',
        message: {
          id: messageId,
          type: 'message',
          role: 'assistant',
          model: responseModel,
          content: [],
          stop_reason: null,
          stop_sequence: null,
          usage: { input_tokens: promptTokens, output_tokens: 0 },
        },
      });
    }

    if (stream) {
      keepalive = setInterval(() => {
        if (!res.writableEnded) res.write(': ping\n\n');
      }, KEEPALIVE_MS);
      keepalive.unref?.();
    }

    const result = await complete({ prompt, cfg, signal: ac.signal }, {
      onText: stream ? null : null, // Anthropic streaming uses the buffered result
    });

    const parsed = tools.length > 0 ? extractToolCalls(result.text, tools.map((t) => t.name)) : { calls: null, text: result.text };
    let calls = parsed.calls;
    if (!calls && tools.length > 0 && result.thinking) {
      const fromThinking = extractToolCalls(result.thinking, tools.map((t) => t.name));
      if (fromThinking.calls) calls = fromThinking.calls;
    }
    const text = calls ? parsed.text : result.text;
    const outputTokens = result.completionTokens || estimateTokens(result.text);

    if (!stream) {
      return sendJson(res, 200, {
        id: messageId,
        type: 'message',
        role: 'assistant',
        model: responseModel,
        content: anthropicBlocks(result, calls, text),
        stop_reason: calls ? 'tool_use' : 'end_turn',
        stop_sequence: null,
        usage: { input_tokens: promptTokens, output_tokens: outputTokens },
      });
    }

    // Streamed Anthropic response (buffered text is fine here; tool input JSON
    // must be complete before input_json_delta is sent).
    if (text) {
      sendEvent('content_block_start', {
        type: 'content_block_start',
        index: nextBlockIndex,
        content_block: { type: 'text', text: '' },
      });
      const chunkSize = 400;
      for (let i = 0; i < text.length; i += chunkSize) {
        sendEvent('content_block_delta', {
          type: 'content_block_delta',
          index: nextBlockIndex,
          delta: { type: 'text_delta', text: text.slice(i, i + chunkSize) },
        });
      }
      sendEvent('content_block_stop', { type: 'content_block_stop', index: nextBlockIndex });
      nextBlockIndex++;
    }

    if (calls) {
      for (const call of calls) {
        const index = nextBlockIndex++;
        sendEvent('content_block_start', {
          type: 'content_block_start',
          index,
          content_block: { type: 'tool_use', id: call.id, name: call.function.name, input: {} },
        });
        sendEvent('content_block_delta', {
          type: 'content_block_delta',
          index,
          delta: { type: 'input_json_delta', partial_json: call.function.arguments || '{}' },
        });
        sendEvent('content_block_stop', { type: 'content_block_stop', index });
      }
    }

    if (!text && !calls) {
      sendEvent('content_block_start', {
        type: 'content_block_start',
        index: 0,
        content_block: { type: 'text', text: '' },
      });
      sendEvent('content_block_stop', { type: 'content_block_stop', index: 0 });
    }

    sendEvent('message_delta', {
      type: 'message_delta',
      delta: { stop_reason: calls ? 'tool_use' : 'end_turn', stop_sequence: null },
      usage: { input_tokens: promptTokens, output_tokens: outputTokens },
    });
    sendEvent('message_stop', { type: 'message_stop' });
    res.end();
  } catch (err) {
    const status = err && err.status ? err.status : 502;
    const message = err && err.message ? err.message : 'DeepSeek request failed';
    if (stream && res.headersSent) {
      res.write(`event: error\ndata: ${JSON.stringify({
        type: 'error',
        error: { type: err.type === 'rate_limit_error' ? 'rate_limit_error' : 'api_error', message },
      })}\n\n`);
      res.end();
    } else {
      anthropicError(res, status, message, err.type === 'rate_limit_error' ? 'rate_limit_error' : 'api_error');
    }
  } finally {
    if (keepalive) clearInterval(keepalive);
    if (release) release();
  }
}

// ---------------- HTTP server ----------------

function renderIndex() {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>d2api</title>
<style>
body{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;max-width:780px;margin:48px auto;padding:0 20px;color:#171717;background:#fafafa}
h1{font-size:28px;margin:0 0 4px} .sub{color:#666;margin-bottom:28px}
code,pre{background:#efefef;border-radius:6px;padding:2px 6px;font-size:13px}
pre{padding:14px;overflow:auto;line-height:1.5}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:7px 10px;border-bottom:1px solid #e5e5e5;text-align:left}
.ok{color:#0a7d33}.bad{color:#b42318}
</style></head><body>
<h1>d2api</h1>
<div class="sub">DeepSeek Web &rarr; OpenAI / Anthropic compatible API</div>
<p>Base URL: <code>http://127.0.0.1:${PORT}/v1</code> &middot; model: <code>deepseek-v4.1-flash</code></p>
<pre>curl http://127.0.0.1:${PORT}/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${PROXY_API_KEY ? '<PROXY_API_KEY>' : 'anything'}" \\
  -d '{"model":"deepseek-v4.1-flash","messages":[{"role":"user","content":"hello"}]}'</pre>
<h3>Accounts</h3>
<table><tr><th>id</th><th>label</th><th>status</th><th>cooldown</th><th>last error</th></tr>
${pool.status().map((a) => `<tr><td>${escapeHtml(a.id)}</td><td>${escapeHtml(a.label)}</td><td class="${a.ready ? 'ok' : 'bad'}">${a.ready ? (a.cooldown_sec ? 'cooling' : 'ready') : 'no auth'}</td><td>${escapeHtml(a.cooldown_sec || '')}</td><td>${escapeHtml(a.last_error ? String(a.last_error).slice(0, 80) : '')}</td></tr>`).join('')}
</table>
<p style="margin-top:28px;color:#777;font-size:13px">Add accounts with <code>node login.js add --token ...</code></p>
</body></html>`;
}

const server = http.createServer(async (req, res) => {
  setCors(res);
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  try {
    if (req.method === 'GET' && url.pathname === '/health') {
      return sendJson(res, 200, {
        ok: true,
        accounts: pool.size,
        ready: pool.status().filter((a) => a.ready && !a.cooldown_sec).length,
        real_model: REAL_MODEL,
      });
    }

    if (!authorized(req)) {
      if (url.pathname.startsWith('/v1/messages') || url.pathname.startsWith('/anthropic')) {
        return anthropicError(res, 401, 'Invalid API key', 'authentication_error');
      }
      return openaiError(res, 401, 'Invalid API key', 'authentication_error');
    }

    if (req.method === 'GET' && url.pathname === '/') {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(renderIndex());
      return;
    }

    if (req.method === 'GET' && url.pathname === '/v1/models') {
      return sendJson(res, 200, { object: 'list', data: modelList() });
    }

    if (req.method === 'GET' && url.pathname === '/v1/model-capabilities') {
      const data = {};
      for (const model of modelList()) {
        const cfg = modelConfig(model.id);
        data[model.id] = {
          real_model: cfg.realModel,
          model_type: cfg.modelType,
          thinking_enabled: cfg.thinking,
          search_enabled: cfg.search,
        };
      }
      return sendJson(res, 200, { object: 'list', data });
    }

    if (req.method === 'GET' && url.pathname === '/admin/status') {
      return sendJson(res, 200, {
        accounts: pool.status(),
        concurrency: { active: limiter.active, limit: limiter.limit, queued: limiter.queue.length },
      });
    }

    if (req.method === 'POST' && url.pathname === '/admin/reload') {
      openaiError(res, 501, 'Restart the process to reload accounts.json');
      return;
    }

    if (req.method === 'POST' && (url.pathname === '/v1/chat/completions' || url.pathname === '/chat/completions')) {
      const body = await readBody(req);
      const started = Date.now();
      if (LOG_REQUESTS) {
        console.log(`[d2api] chat model=${body.model || DEFAULT_MODEL} stream=${Boolean(body.stream)} tools=${Array.isArray(body.tools) ? body.tools.length : 0}`);
      }
      await handleOpenAiChat(req, res, body);
      if (LOG_REQUESTS) console.log(`[d2api] done in ${Date.now() - started}ms`);
      return;
    }

    if (req.method === 'POST' && (url.pathname === '/v1/messages/count_tokens' || url.pathname === '/anthropic/v1/messages/count_tokens')) {
      const body = await readBody(req);
      const { messages, tools } = anthropicToOpenAi(body);
      const prompt = buildPrompt(messages, tools);
      return sendJson(res, 200, { input_tokens: estimateTokens(prompt) });
    }

    if (req.method === 'POST' && (url.pathname === '/v1/messages' || url.pathname === '/anthropic/v1/messages' || url.pathname === '/messages')) {
      const body = await readBody(req);
      const started = Date.now();
      if (LOG_REQUESTS) {
        console.log(`[d2api] anthropic model=${body.model || '(default)'} stream=${Boolean(body.stream)} tools=${Array.isArray(body.tools) ? body.tools.length : 0}`);
      }
      await handleAnthropicMessages(req, res, body);
      if (LOG_REQUESTS) console.log(`[d2api] done in ${Date.now() - started}ms`);
      return;
    }

    sendJson(res, 404, { error: { message: `No route for ${req.method} ${url.pathname}`, type: 'not_found' } });
  } catch (err) {
    const status = err && err.status ? err.status : 500;
    const message = err && err.message ? err.message : 'Internal error';
    if (!res.headersSent) {
      if (url.pathname.startsWith('/v1/messages') || url.pathname.startsWith('/anthropic')) {
        anthropicError(res, status, message, err.type || 'api_error');
      } else {
        openaiError(res, status, message, err.type || 'api_error');
      }
    } else {
      try {
        res.end();
      } catch (_) {
        /* already closed */
      }
    }
  }
});

server.requestTimeout = Math.max(1000, Number(process.env.REQUEST_BODY_TIMEOUT_MS || 30000));
server.headersTimeout = 120000;

server.listen(PORT, HOST, () => {
  console.log(`[d2api] listening on http://${HOST}:${PORT}`);
  console.log(`[d2api] real upstream model: ${REAL_MODEL}`);
  console.log(`[d2api] accounts: ${pool.size} (${pool.status().filter((a) => a.ready).length} with credentials)`);
  if (pool.size === 0) {
    console.log('[d2api] no account yet -> node login.js add --token <token>');
  }
  if (!PROXY_API_KEY && HOST !== '127.0.0.1' && HOST !== 'localhost') {
    console.log('[d2api] warning: listening off-loopback without PROXY_API_KEY');
  }
});

function shutdown() {
  console.log('\n[d2api] shutting down');
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 3000).unref();
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
