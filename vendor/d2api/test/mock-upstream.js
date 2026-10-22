'use strict';

// Minimal DeepSeek Web upstream used by test/run.js. It speaks the same
// fragment/OT SSE protocol as chat.deepseek.com so the proxy can be tested
// end to end without a real account.

const http = require('http');

const PORT = Number(process.env.MOCK_PORT || 9801);
const prompts = [];
const deletedSessions = [];

// Valid DeepSeekHashV1 challenge for salt=testsalt, expire=1700000000,
// difficulty=1000, answer=42. Generated with the reference Go implementation.
const CHALLENGE = {
  algorithm: 'DeepSeekHashV1',
  challenge: 'd4a2ea58c89e40887c933484868380c6f803eaa8dc53a3b9df8e431b921a4f09',
  salt: 'testsalt',
  expire_at: 1700000000,
  difficulty: 1000,
  signature: 'mock-signature',
  target_path: '/api/v0/chat/completion',
};

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'));
      } catch (_) {
        resolve({});
      }
    });
  });
}

function json(res, body) {
  const text = JSON.stringify(body);
  res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(text) });
  res.end(text);
}

function sseLine(res, payload) {
  res.write(`data: ${typeof payload === 'string' ? payload : JSON.stringify(payload)}\n\n`);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const body = await readBody(req);

  if (url.pathname === '/__state') {
    return json(res, { prompts, deletedSessions });
  }

  if (url.pathname === '/api/v0/users/login') {
    return json(res, { code: 0, data: { biz_code: 0, biz_data: { user: { token: 'mock-token' } } } });
  }

  if (url.pathname === '/api/v0/users/current') {
    const auth = req.headers.authorization || '';
    if (auth.startsWith('Bearer ')) {
      const token = auth.slice(7);
      return json(res, { code: 0, data: { biz_code: 0, biz_data: { token, email: 'mock@example.com' } } });
    }
    if (String(req.headers.cookie || '').includes('ds_session_id=')) {
      return json(res, { code: 0, data: { biz_code: 0, biz_data: { token: 'mock-cookie-token', email: 'mock@example.com' } } });
    }
    return json(res, { code: 40003, msg: 'INVALID_TOKEN', data: null });
  }

  if (url.pathname === '/api/v0/chat_session/create') {
    return json(res, { code: 0, data: { biz_code: 0, biz_data: { chat_session: { id: `sess_${Date.now()}` } } } });
  }

  if (url.pathname === '/api/v0/chat/create_pow_challenge') {
    return json(res, { code: 0, data: { biz_code: 0, biz_data: { challenge: CHALLENGE } } });
  }

  if (url.pathname === '/api/v0/chat_session/delete') {
    await new Promise((resolve) => setTimeout(resolve, 150));
    deletedSessions.push(body.chat_session_id);
    return json(res, { code: 0, data: { biz_code: 0 } });
  }

  if (url.pathname === '/api/v0/chat/completion') {
    const prompt = String(body.prompt || '');
    prompts.push(prompt);
    if (prompt.includes('SLOW_FIRST')) {
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
    const thinking = Boolean(body.thinking_enabled);
    const search = Boolean(body.search_enabled);
    const toolResultTurn = /^\[Tool Result/m.test(prompt);
    const wantsTool = !toolResultTurn && /get_weather/.test(prompt) && /weather|hava/i.test(prompt);

    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });

    sseLine(res, { v: { response: { message_id: Date.now(), parent_id: 1, fragments: [], status: 'WIP' } } });

    if (thinking) {
      sseLine(res, { p: 'response/fragments', o: 'APPEND', v: [{ type: 'THINK', content: 'Let me think about it. ' }] });
      sseLine(res, { v: 'thinking continues. ' });
    }

    if (search) {
      sseLine(res, { p: 'response/fragments', o: 'APPEND', v: [{ type: 'RESPONSE', content: 'According to Example [citation:0], ' }] });
      sseLine(res, { p: 'response/fragments/-1/results', v: [{ url: 'https://example.com/page', title: 'Example page', cite_index: 0 }] });
      sseLine(res, { v: 'it is sunny. [citation:0]' });
    } else if (wantsTool) {
      sseLine(res, {
        p: 'response/fragments',
        o: 'APPEND',
        v: [{ type: 'RESPONSE', content: '{"tool_call":{"name":"get_weather","arguments":{"city":"Istanbul"}}}' }],
      });
    } else if (toolResultTurn) {
      sseLine(res, { p: 'response/fragments', o: 'APPEND', v: [{ type: 'RESPONSE', content: 'Tool result received.' }] });
    } else {
      const answer = prompt.includes('LONG_OUTPUT') ? 'x'.repeat(1500) : 'Hello from mock.';
      sseLine(res, { p: 'response/fragments', o: 'APPEND', v: [{ type: 'RESPONSE', content: answer }] });
    }

    sseLine(res, { p: 'response', o: 'BATCH', v: [{ p: 'accumulated_token_usage', v: 17 }, { p: 'quasi_status', v: 'FINISHED' }] });
    sseLine(res, { p: 'response/status', v: 'FINISHED' });
    sseLine(res, '[DONE]');
    res.end();
    return;
  }

  res.writeHead(404, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ code: 404, msg: `no mock route ${url.pathname}` }));
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`mock upstream on http://127.0.0.1:${PORT}`);
});
