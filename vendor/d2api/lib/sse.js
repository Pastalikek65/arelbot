'use strict';

/**
 * Async line reader for a fetch() Response body (SSE).
 * Yields lines without the trailing CR/LF. The final line is yielded even
 * when the body does not end with a newline.
 */
async function* readLines(response, { signal, idleTimeoutMs } = {}) {
  const body = response.body;
  if (!body) return;

  const idleMs = Math.max(1000, Number(idleTimeoutMs || process.env.DEEPSEEK_STREAM_IDLE_TIMEOUT_MS || 180000));
  const maxLineChars = Math.max(1024, Number(process.env.MAX_SSE_LINE_CHARS || 1024 * 1024));
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const readWithIdleTimeout = () => new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`DeepSeek stream was idle for ${idleMs}ms`));
    }, idleMs);
    reader.read().then(
      (result) => {
        clearTimeout(timer);
        resolve(result);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });

  try {
    while (true) {
      if (signal && signal.aborted) throw signal.reason || new Error('aborted');
      const { done, value } = await readWithIdleTimeout();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf('\n')) !== -1) {
        let line = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 1);
        if (line.length > maxLineChars) throw new Error('DeepSeek SSE line exceeded proxy limit');
        if (line.endsWith('\r')) line = line.slice(0, -1);
        yield line;
      }
      if (buffer.length > maxLineChars) throw new Error('DeepSeek SSE line exceeded proxy limit');
    }
    buffer += decoder.decode();
    if (buffer.length > 0) {
      for (const line of buffer.split('\n')) {
        if (line.length > maxLineChars) throw new Error('DeepSeek SSE line exceeded proxy limit');
        yield line.endsWith('\r') ? line.slice(0, -1) : line;
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch (_) {
      /* already closed */
    }
    try {
      reader.releaseLock();
    } catch (_) {
      /* already released */
    }
  }
}

module.exports = { readLines };
