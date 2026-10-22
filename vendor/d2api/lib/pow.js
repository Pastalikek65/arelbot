'use strict';
// DeepSeekHashV1 proof-of-work solver.
//
// DeepSeek protects /api/v0/chat/completion (and file upload) with a challenge
// that must be answered with their sha3 wasm module. The module is bundled in
// assets/, downloaded once from the CDN if missing, compiled once per process
// and instantiated per solve (fresh linear memory).

const fs = require('fs');
const path = require('path');

const DEFAULT_WASM_URL = 'https://fe-static.deepseek.com/chat/static/sha3_wasm_bg.7b9ca65ddd.wasm';
const BUNDLED_WASM = path.join(__dirname, '..', 'assets', 'deepseek_hash.wasm');

let modulePromise = null;
let moduleSource = null;

async function fetchWasm(url, timeoutMs = 15000) {
  const resp = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
  if (!resp.ok) throw new Error(`PoW: could not download wasm (HTTP ${resp.status})`);
  return new Uint8Array(await resp.arrayBuffer());
}

async function loadModule({ wasmPath, wasmUrl } = {}) {
  const source = wasmPath || BUNDLED_WASM;
  const url = wasmUrl || process.env.DEEPSEEK_WASM_URL || '';

  if (modulePromise && moduleSource === `${source}|${url}`) return modulePromise;

  moduleSource = `${source}|${url}`;
  modulePromise = (async () => {
    let bytes = null;
    try {
      bytes = await fs.promises.readFile(source);
    } catch (err) {
      if (err.code !== 'ENOENT') throw err;
    }
    if (!bytes) bytes = await fetchWasm(url || DEFAULT_WASM_URL);
    return WebAssembly.compile(bytes);
  })();

  // A failed load must not be cached forever.
  modulePromise.catch(() => {
    if (moduleSource === `${source}|${url}`) {
      modulePromise = null;
      moduleSource = null;
    }
  });

  return modulePromise;
}

/**
 * Solve a DeepSeek PoW challenge.
 * @param {object} challenge {algorithm, challenge, salt, difficulty, expire_at, signature, target_path}
 * @param {object} opts {wasmPath, wasmUrl}
 * @returns {Promise<number>} the nonce answer
 */
async function solvePOW(challenge, opts = {}) {
  if (!challenge || typeof challenge !== 'object') throw new Error('PoW: missing challenge');
  if (challenge.algorithm && challenge.algorithm !== 'DeepSeekHashV1') {
    throw new Error(`PoW: unsupported algorithm ${challenge.algorithm}`);
  }

  const mod = await loadModule(opts);
  const instance = await WebAssembly.instantiate(mod, { wbg: {} });
  const e = instance.exports;

  const enc = new TextEncoder();
  const prefix = `${challenge.salt}_${challenge.expire_at}_`;
  const cBytes = enc.encode(String(challenge.challenge));
  const pBytes = enc.encode(prefix);

  const cP = e.__wbindgen_export_0(cBytes.length, 1) >>> 0;
  const pP = e.__wbindgen_export_0(pBytes.length, 1) >>> 0;
  new Uint8Array(e.memory.buffer, cP, cBytes.length).set(cBytes);
  new Uint8Array(e.memory.buffer, pP, pBytes.length).set(pBytes);

  const sp = e.__wbindgen_add_to_stack_pointer(-16);
  e.wasm_solve(sp, cP, cBytes.length, pP, pBytes.length, Number(challenge.difficulty));
  const dv = new DataView(e.memory.buffer);
  const code = dv.getInt32(sp, true);
  const answer = dv.getFloat64(sp + 8, true);
  e.__wbindgen_add_to_stack_pointer(16);

  if (code === 0 || !Number.isFinite(answer) || answer < 0) {
    throw new Error('PoW: solver returned no answer');
  }
  return Math.floor(answer);
}

module.exports = { solvePOW, DEFAULT_WASM_URL, BUNDLED_WASM };
