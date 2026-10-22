'use strict';

const assert = require('assert');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { readLines } = require('../lib/sse');

const ROOT = path.resolve(__dirname, '..');
const MOCK_PORT = 9801;
const PROXY_PORT = 9802;
const COOKIE_PROXY_PORT = 9804;
const PRIVATE_PROXY_PORT = 9805;
const PRIVATE_BASE = `http://127.0.0.1:${PRIVATE_PROXY_PORT}`;
const PRIVATE_KEY = 'fixture-proxy-key-32-characters-long';
const BASE = `http://127.0.0.1:${PROXY_PORT}`;
const COOKIE_BASE = `http://127.0.0.1:${COOKIE_PROXY_PORT}`;

const dataDir = path.join(os.tmpdir(), `d2api-test-${process.pid}`);
fs.mkdirSync(dataDir, { recursive: true });

const children = [];

function start(script, env) {
  const child = spawn(process.execPath, [script], {
    cwd: ROOT,
    env: { ...process.env, ...env },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  children.push(child);
  child.stdout.on('data', (d) => process.env.VERBOSE ? process.stdout.write(`[${path.basename(script)}] ${d}`) : null);
  child.stderr.on('data', (d) => process.stderr.write(`[${path.basename(script)}] ${d}`));
  return child;
}

async function waitFor(url, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(url);
      if (resp.ok || resp.status === 404) return;
    } catch (err) {
      last = err;
    }
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error(`timed out waiting for ${url}${last ? `: ${last.message}` : ''}`);
}

async function postJson(pathname, body, headers = {}) {
  const resp = await fetch(`${BASE}${pathname}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });
  const text = await resp.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch (_) {
    /* stream */
  }
  return { status: resp.status, json, text, resp };
}

async function postRaw(pathname, body, headers = {}) {
  return fetch(`${BASE}${pathname}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });
}

async function readSse(resp) {
  const text = await resp.text();
  const events = [];
  for (const block of text.split('\n\n')) {
    const lines = block.split('\n');
    let data = null;
    let event = null;
    for (const line of lines) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      if (line.startsWith('data:')) data = line.slice(5).trim();
    }
    if (data !== null) {
      if (data === '[DONE]') events.push({ event, done: true });
      else {
        try {
          events.push({ event, data: JSON.parse(data) });
        } catch (_) {
          events.push({ event, raw: data });
        }
      }
    }
  }
  return events;
}

async function main() {
  start(path.join('test', 'mock-upstream.js'), { MOCK_PORT });
  start('server.js', {
    PORT: PROXY_PORT,
    HOST: '127.0.0.1',
    DEEPSEEK_BASE_URL: `http://127.0.0.1:${MOCK_PORT}`,
    DEEPSEEK_TOKEN: 'mock-token',
    D2API_DATA_DIR: dataDir,
    LOG_REQUESTS: '0',
    PROXY_API_KEY: '',
    MAX_CONCURRENT: '4',
  });

  start('arel-start.js', {
    PORT: PRIVATE_PROXY_PORT,
    HOST: '127.0.0.1',
    DEEPSEEK_BASE_URL: `http://127.0.0.1:${MOCK_PORT}`,
    DEEPSEEK_TOKEN: 'mock-token',
    D2API_DATA_DIR: dataDir,
    LOG_REQUESTS: '0',
    PROXY_API_KEY: PRIVATE_KEY,
    MAX_CONCURRENT: '1',
    MAX_OUTPUT_CHARS: '1024',
  });

  // Second instance boots from a cookie only; it must exchange it for a
  // bearer via /api/v0/users/current before creating a session.
  start('server.js', {
    PORT: COOKIE_PROXY_PORT,
    HOST: '127.0.0.1',
    DEEPSEEK_BASE_URL: `http://127.0.0.1:${MOCK_PORT}`,
    DEEPSEEK_TOKEN: '',
    DEEPSEEK_COOKIE: 'ds_session_id=mock-cookie; d_id=mock-device',
    D2API_DATA_DIR: dataDir,
    LOG_REQUESTS: '0',
    PROXY_API_KEY: '',
  });

  await waitFor(`${BASE}/health`);
  await waitFor(`${COOKIE_BASE}/health`);
  await waitFor(`${PRIVATE_BASE}/health`);
  await waitFor(`http://127.0.0.1:${MOCK_PORT}/api/v0/chat_session/create`);
  console.log('servers up');

  // Only the minimal health probe is public on the private sidecar.
  {
    const unauthenticated = await fetch(`${PRIVATE_BASE}/`);
    assert.equal(unauthenticated.status, 401);
    const authenticated = await fetch(`${PRIVATE_BASE}/`, { headers: { Authorization: `Bearer ${PRIVATE_KEY}` } });
    assert.equal(authenticated.status, 200);
    console.log('ok private status authentication');
  }

  // A disconnected waiter must leave the semaphore queue before it is granted.
  {
    const body = (content) => JSON.stringify({ model: 'deepseek-v4.1-flash', messages: [{ role: 'user', content }] });
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${PRIVATE_KEY}` };
    const first = fetch(`${PRIVATE_BASE}/v1/chat/completions`, {
      method: 'POST', headers, body: body('SLOW_FIRST'),
    });
    const deadline = Date.now() + 3000;
    let firstStarted = false;
    while (Date.now() < deadline) {
      const state = await (await fetch(`http://127.0.0.1:${MOCK_PORT}/__state`)).json();
      if (state.prompts.some((prompt) => prompt.includes('SLOW_FIRST'))) {
        firstStarted = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert(firstStarted, 'first request occupied the proxy slot');
    const controller = new AbortController();
    const cancelled = fetch(`${PRIVATE_BASE}/v1/chat/completions`, {
      method: 'POST', headers, body: body('CANCELLED_WAITER'), signal: controller.signal,
    }).catch(() => null);
    await new Promise((resolve) => setTimeout(resolve, 30));
    controller.abort();
    await cancelled;
    const third = fetch(`${PRIVATE_BASE}/v1/chat/completions`, {
      method: 'POST', headers, body: body('THIRD_REQUEST'),
    });
    assert.equal((await first).status, 200);
    assert.equal((await third).status, 200);
    const state = await (await fetch(`http://127.0.0.1:${MOCK_PORT}/__state`)).json();
    assert(!state.prompts.some((prompt) => prompt.includes('CANCELLED_WAITER')));
    assert(state.prompts.some((prompt) => prompt.includes('THIRD_REQUEST')));
    console.log('ok disconnected waiter cleanup');
  }

  {
    const stateBefore = await (await fetch(`http://127.0.0.1:${MOCK_PORT}/__state`)).json();
    const resp = await fetch(`${PRIVATE_BASE}/v1/chat/completions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${PRIVATE_KEY}` },
      body: JSON.stringify({ model: 'deepseek-v4.1-flash', messages: [{ role: 'user', content: 'CLEANUP_CHECK' }] }),
    });
    assert.equal(resp.status, 200);
    const stateAfter = await (await fetch(`http://127.0.0.1:${MOCK_PORT}/__state`)).json();
    assert.equal(stateAfter.deletedSessions.length, stateBefore.deletedSessions.length + 1,
      'session cleanup finished before the proxy response');
    console.log('ok session cleanup accounting');
  }

  {
    const resp = await fetch(`${PRIVATE_BASE}/v1/chat/completions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${PRIVATE_KEY}` },
      body: JSON.stringify({ model: 'deepseek-v4.1-flash', messages: [{ role: 'user', content: 'LONG_OUTPUT' }] }),
    });
    assert.equal(resp.status, 502);
    assert.equal((await resp.json()).error.code, 'response_too_large');
    console.log('ok bounded output');
  }

  {
    const previous = process.env.MAX_SSE_LINE_CHARS;
    process.env.MAX_SSE_LINE_CHARS = '1024';
    try {
      await assert.rejects(async () => {
        for await (const _line of readLines(new Response('x'.repeat(1025)))) {
          /* the overlong line must never be yielded */
        }
      }, /SSE line exceeded proxy limit/);
    } finally {
      if (previous === undefined) delete process.env.MAX_SSE_LINE_CHARS;
      else process.env.MAX_SSE_LINE_CHARS = previous;
    }
    console.log('ok bounded SSE line');
  }

  // cookie-only bootstrap
  {
    const resp = await fetch(`${COOKIE_BASE}/v1/chat/completions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: 'deepseek-v4.1-flash',
        messages: [{ role: 'user', content: 'hi' }],
      }),
    });
    const json = await resp.json();
    assert.equal(resp.status, 200);
    assert.equal(json.choices[0].message.content, 'Hello from mock.');
    console.log('ok cookie bootstrap');
  }

  // models
  const models = await (await fetch(`${BASE}/v1/models`)).json();
  assert(models.data.some((m) => m.id === 'deepseek-v4.1-flash'), 'default model listed');
  assert(models.data.some((m) => m.id === 'deepseek-v4.1-flash-think-search'), 'think+search model listed');
  console.log('ok models');

  // plain non-stream
  {
    const { status, json } = await postJson('/v1/chat/completions', {
      model: 'deepseek-v4.1-flash',
      messages: [{ role: 'user', content: 'hi' }],
    });
    assert.equal(status, 200);
    assert.equal(json.choices[0].message.content, 'Hello from mock.');
    assert.equal(json.choices[0].finish_reason, 'stop');
    assert(json.usage.total_tokens > 0);
    console.log('ok non-stream');
  }

  // thinking + stream
  {
    const resp = await postRaw('/v1/chat/completions', {
      model: 'deepseek-v4.1-flash-think',
      stream: true,
      messages: [{ role: 'user', content: 'think please' }],
    });
    assert.equal(resp.status, 200);
    assert(resp.headers.get('content-type').includes('text/event-stream'));
    const events = await readSse(resp);
    const chunks = events.filter((e) => e.data && e.data.choices && e.data.choices[0]);
    const reasoning = chunks.map((e) => (e.data.choices[0].delta || {}).reasoning_content || '').join('');
    const content = chunks.map((e) => (e.data.choices[0].delta || {}).content || '').join('');
    assert(reasoning.includes('think about it'), `reasoning streamed, got: ${reasoning}`);
    assert.equal(content, 'Hello from mock.');
    const finish = chunks.find((e) => e.data.choices[0].finish_reason === 'stop');
    assert(finish, 'finish chunk present');
    console.log('ok stream+thinking');
  }

  // tools
  {
    const { status, json } = await postJson('/v1/chat/completions', {
      model: 'deepseek-v4.1-flash',
      messages: [{ role: 'user', content: 'what is the weather in Istanbul?' }],
      tools: [{
        type: 'function',
        function: {
          name: 'get_weather',
          description: 'Get the weather for a city',
          parameters: { type: 'object', properties: { city: { type: 'string' } }, required: ['city'] },
        },
      }],
    });
    assert.equal(status, 200);
    const choice = json.choices[0];
    assert.equal(choice.finish_reason, 'tool_calls');
    assert.equal(choice.message.tool_calls[0].function.name, 'get_weather');
    const args = JSON.parse(choice.message.tool_calls[0].function.arguments);
    assert.equal(args.city, 'Istanbul');
    console.log('ok tools');
  }

  // streaming tools
  {
    const resp = await postRaw('/v1/chat/completions', {
      model: 'deepseek-v4.1-flash',
      stream: true,
      messages: [{ role: 'user', content: 'what is the weather in Istanbul?' }],
      tools: [{
        type: 'function',
        function: {
          name: 'get_weather',
          description: 'Get the weather for a city',
          parameters: { type: 'object', properties: { city: { type: 'string' } } },
        },
      }],
    });
    assert.equal(resp.status, 200);
    const events = await readSse(resp);
    const chunks = events.filter((e) => e.data && e.data.choices && e.data.choices[0]);
    const toolDelta = chunks.flatMap((e) => (e.data.choices[0].delta || {}).tool_calls || []);
    assert.equal(toolDelta.length, 1, 'one tool delta');
    assert.equal(toolDelta[0].function.name, 'get_weather');
    assert.equal(JSON.parse(toolDelta[0].function.arguments).city, 'Istanbul');
    assert(chunks.some((e) => e.data.choices[0].finish_reason === 'tool_calls'), 'tool_calls finish');
    console.log('ok stream tools');
  }

  // tool result round-trip
  {
    const { status, json } = await postJson('/v1/chat/completions', {
      model: 'deepseek-v4.1-flash',
      messages: [
        { role: 'user', content: 'what is the weather in Istanbul?' },
        { role: 'assistant', content: null, tool_calls: [{ id: 'call_1', type: 'function', function: { name: 'get_weather', arguments: '{"city":"Istanbul"}' } }] },
        { role: 'tool', tool_call_id: 'call_1', content: '{"temp":25}' },
      ],
      tools: [{
        type: 'function',
        function: { name: 'get_weather', description: 'Get the weather', parameters: { type: 'object', properties: { city: { type: 'string' } } } },
      }],
    });
    assert.equal(status, 200);
    assert.equal(json.choices[0].message.content, 'Tool result received.');
    assert.equal(json.choices[0].finish_reason, 'stop');
    console.log('ok tool result turn');
  }

  // search + citations
  {
    const { status, json } = await postJson('/v1/chat/completions', {
      model: 'deepseek-v4.1-flash-search',
      messages: [{ role: 'user', content: 'weather today' }],
    });
    assert.equal(status, 200);
    const message = json.choices[0].message;
    assert(!message.content.includes('[citation:'), `citation markers stripped: ${message.content}`);
    assert(message.content.includes('According to Example'), 'search text preserved');
    assert(message.content.includes('it is sunny'), `search text after results frame preserved: ${message.content}`);
    assert(message.annotations.some((a) => a.url_citation.url === 'https://example.com/page'), 'annotation added');
    console.log('ok search+citations');
  }

  // anthropic non-stream + tool
  {
    const { status, json } = await postJson('/v1/messages', {
      model: 'claude-sonnet-4-5',
      max_tokens: 100,
      system: 'You are a helpful assistant.',
      messages: [{ role: 'user', content: 'what is the weather in Istanbul?' }],
      tools: [{ name: 'get_weather', description: 'Get weather', input_schema: { type: 'object', properties: { city: { type: 'string' } } } }],
    });
    assert.equal(status, 200);
    assert.equal(json.stop_reason, 'tool_use');
    const tool = json.content.find((b) => b.type === 'tool_use');
    assert(tool, 'tool_use block');
    assert.equal(tool.name, 'get_weather');
    assert.equal(tool.input.city, 'Istanbul');
    console.log('ok anthropic tools');
  }

  // anthropic stream text
  {
    const resp = await postRaw('/v1/messages', {
      model: 'claude-sonnet-4-5',
      max_tokens: 100,
      stream: true,
      messages: [{ role: 'user', content: 'hi' }],
    });
    assert.equal(resp.status, 200);
    const events = await readSse(resp);
    const deltas = events.filter((e) => e.event === 'content_block_delta').map((e) => e.data.delta.text || '').join('');
    assert.equal(deltas, 'Hello from mock.');
    assert(events.some((e) => e.event === 'message_stop'), 'message_stop');
    console.log('ok anthropic stream');
  }

  console.log('\nall tests passed');
}

main()
  .catch((err) => {
    console.error('\nTEST FAILED:', err && err.stack ? err.stack : err);
    process.exitCode = 1;
  })
  .finally(() => {
    for (const child of children) {
      try {
        child.kill();
      } catch (_) {
        /* ignore */
      }
    }
  });
