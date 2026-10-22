'use strict';

const crypto = require('crypto');

// ---------- message normalization ----------

function contentToText(content) {
  if (content === null || content === undefined) return '';
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === 'string') return part;
        if (!part || typeof part !== 'object') return '';
        if (part.type === 'text' && typeof part.text === 'string') return part.text;
        if (part.type === 'input_text' && typeof part.text === 'string') return part.text;
        if (part.type === 'image_url' || part.type === 'image') return '[image omitted: this proxy is text-only]';
        return '';
      })
      .filter(Boolean)
      .join('\n');
  }
  if (typeof content === 'object') return JSON.stringify(content);
  return String(content);
}

function isSystemRole(role) {
  return role === 'system' || role === 'developer';
}

// ---------- tool definitions ----------

const TOOL_CALL_FORMAT = '{"tool_call":{"name":"<tool_name>","arguments":{...}}}';

function normalizeTools(tools) {
  if (!Array.isArray(tools)) return [];
  const out = [];
  for (const tool of tools) {
    if (!tool || typeof tool !== 'object') continue;
    const fn = tool.function && typeof tool.function === 'object' ? tool.function : tool;
    const name = fn.name || tool.name;
    if (!name) continue;
    out.push({
      type: 'function',
      name: String(name),
      description: String(fn.description || tool.description || ''),
      parameters: fn.parameters || tool.input_schema || tool.parameters || { type: 'object', properties: {} },
    });
  }
  return out;
}

function compactSchema(schema, depth = 0) {
  if (!schema || typeof schema !== 'object' || depth > 6) return schema;
  if (Array.isArray(schema)) return schema.map((item) => compactSchema(item, depth + 1));
  const out = {};
  for (const [key, value] of Object.entries(schema)) {
    // Descriptions are already shown next to each tool; dropping them from the
    // parameter tree keeps the injected manual small.
    if (key === 'description' && depth > 0) continue;
    if (key === 'title') continue;
    out[key] = compactSchema(value, depth + 1);
  }
  return out;
}

function formatToolPrompt(tools) {
  if (!tools || tools.length === 0) return '';

  let text = '\n\n--- TOOL CALLING SYSTEM ---\n';
  text += 'You can request tools that run on the client machine. You cannot run them yourself.\n';
  text += `To request a tool, reply with ONLY this JSON object and nothing else:\n${TOOL_CALL_FORMAT}\n\n`;
  text += 'Rules:\n';
  text += '1. Output one tool call per response, as raw JSON (no markdown fence, no prose before or after).\n';
  text += '2. "arguments" must be a valid JSON object matching the tool schema.\n';
  text += '3. Never simulate, guess or fabricate tool output. Stop after the request and wait.\n';
  text += '4. The client executes the tool and sends the output back as a "[Tool Result: name]" message.\n';
  text += '5. When no tool is needed, answer normally in plain text without tool JSON.\n';
  text += '6. Escape backslashes, quotes and newlines inside string values.\n\n';
  text += 'Available tools:\n';

  for (const tool of tools) {
    text += `\n## ${tool.name}\n`;
    if (tool.description) text += `${tool.description.slice(0, 1200)}\n`;
    try {
      text += `Parameters: ${JSON.stringify(compactSchema(tool.parameters))}\n`;
    } catch (_) {
      text += 'Parameters: {}\n';
    }
  }

  text += '\n--- END TOOL CALLING SYSTEM ---';
  return text;
}

function toolCallAsPromptText(toolCall) {
  const name = toolCall && toolCall.function ? toolCall.function.name : '';
  let args = toolCall && toolCall.function ? toolCall.function.arguments : '{}';
  if (typeof args === 'string') {
    try {
      args = JSON.parse(args);
    } catch (_) {
      /* keep string */
    }
  }
  return JSON.stringify({ tool_call: { name, arguments: args } });
}

// ---------- prompt assembly ----------

function formatConversation(messages, toolNames = new Map()) {
  const parts = [];
  for (const msg of messages || []) {
    if (!msg || isSystemRole(msg.role)) continue;
    const text = contentToText(msg.content);

    if (msg.role === 'user') {
      if (text) parts.push(`User: ${text}`);
    } else if (msg.role === 'assistant') {
      if (text) parts.push(`Assistant: ${text}`);
      for (const tc of Array.isArray(msg.tool_calls) ? msg.tool_calls : []) {
        parts.push(`Assistant: ${toolCallAsPromptText(tc)}`);
      }
    } else if (msg.role === 'tool' || msg.role === 'function') {
      const name = msg.name || toolNames.get(msg.tool_call_id) || '';
      parts.push(`[Tool Result${name ? `: ${name}` : ''}]\n${text}`);
    }
  }
  return parts.join('\n\n').trim();
}

function buildToolNameIndex(messages) {
  const names = new Map();
  for (const msg of messages || []) {
    if (!msg || msg.role !== 'assistant' || !Array.isArray(msg.tool_calls)) continue;
    for (const tc of msg.tool_calls) {
      if (tc && tc.id && tc.function && tc.function.name) names.set(tc.id, tc.function.name);
    }
  }
  return names;
}

function buildPrompt(messages, tools) {
  let system = '';
  for (const msg of messages || []) {
    if (msg && isSystemRole(msg.role) && msg.content) {
      system += `${contentToText(msg.content)}\n`;
    }
  }
  system += formatToolPrompt(tools);

  const toolNames = buildToolNameIndex(messages);
  const conversation = formatConversation(messages, toolNames);

  let prompt = '';
  if (system.trim()) prompt += `System: ${system.trim()}\n\n`;
  prompt += conversation;

  // A long tool conversation can drift; the final reminder pins the protocol.
  if (tools && tools.length > 0) {
    const names = tools.map((t) => t.name).join(', ').slice(0, 1200);
    prompt += `\n\n[Tool reminder] Available tools: ${names}. To use one, reply with ONLY ${TOOL_CALL_FORMAT} and nothing else. Otherwise answer normally. Never invent tool results.`;
  }

  return prompt.trim();
}

// ---------- tool call detection ----------

function newCallId() {
  return `call_${crypto.randomBytes(8).toString('hex')}`;
}

function buildCall(name, args, index) {
  let argumentString;
  if (typeof args === 'string') {
    argumentString = args;
  } else if (args === undefined || args === null) {
    argumentString = '{}';
  } else {
    argumentString = JSON.stringify(args);
  }
  return {
    id: newCallId(),
    type: 'function',
    index,
    function: { name: String(name), arguments: argumentString },
  };
}

function callsFromObject(obj) {
  if (!obj || typeof obj !== 'object') return null;

  if (Array.isArray(obj)) {
    const calls = [];
    for (const item of obj) {
      const parsed = callsFromObject(item);
      if (parsed) calls.push(...parsed);
    }
    return calls.length ? calls : null;
  }

  if (Array.isArray(obj.tool_calls)) {
    const calls = [];
    for (const item of obj.tool_calls) {
      if (!item || typeof item !== 'object') continue;
      const fn = item.function && typeof item.function === 'object' ? item.function : item;
      const name = fn.name || item.name;
      if (!name) continue;
      const args = fn.arguments !== undefined ? fn.arguments : (item.arguments !== undefined ? item.arguments : item.input);
      calls.push({ name, args });
    }
    return calls.length ? calls : null;
  }

  if (obj.tool_call && typeof obj.tool_call === 'object') {
    const call = obj.tool_call;
    const name = call.name || (call.function && call.function.name);
    if (name) {
      const args = call.arguments !== undefined ? call.arguments : (call.input !== undefined ? call.input : call.function && call.function.arguments);
      return [{ name, args }];
    }
  }

  if (obj.function_call && typeof obj.function_call === 'object') {
    const call = obj.function_call;
    if (call.name) return [{ name: call.name, args: call.arguments !== undefined ? call.arguments : call.input }];
  }

  const name = obj.name || obj.tool;
  if (name) {
    const args = obj.arguments !== undefined ? obj.arguments : obj.input;
    if (args !== undefined || obj.arguments !== undefined) return [{ name, args }];
  }

  return null;
}

function balancedObjects(text) {
  const objects = [];
  let start = -1;
  let depth = 0;
  let inString = false;
  let escape = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (start === -1) {
      if (ch === '{') {
        start = i;
        depth = 1;
        inString = false;
        escape = false;
      }
      continue;
    }
    if (escape) {
      escape = false;
      continue;
    }
    if (ch === '\\' && inString) {
      escape = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
      continue;
    }
    if (inString) continue;
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) {
        objects.push({ text: text.slice(start, i + 1), start, end: i + 1 });
        start = -1;
        if (objects.length >= 24) break;
      }
    }
  }
  return objects;
}

/**
 * Extract tool calls from model output.
 * @param {string} content
 * @param {Iterable<string>|null} allowedNames only accept calls to known tools
 * @returns {{calls: Array|null, text: string}}
 */
function extractToolCalls(content, allowedNames = null) {
  const raw = String(content || '');
  if (!raw.trim()) return { calls: null, text: raw };
  const allowed = allowedNames ? new Set(allowedNames) : null;

  const accept = (found, candidateText, start, end) => {
    const usable = allowed ? found.filter((call) => allowed.has(String(call.name))) : found;
    if (!usable.length) return null;
    const calls = usable.map((c, i) => buildCall(c.name, c.args, i));
    const remaining = `${candidateText.slice(0, start)}${candidateText.slice(end)}`.replace(/\s+\n/g, '\n').trim();
    return { calls, text: remaining };
  };

  // Tolerate a fully fenced response.
  const fenced = raw.match(/^\s*```(?:json|tool_call|tool_calls)?\s*([\s\S]*?)\s*```\s*$/i);

  // 1) Strict envelope, possibly surrounded by prose.
  for (const candidateText of fenced ? [fenced[1], raw] : [raw]) {
    for (const candidate of balancedObjects(candidateText)) {
      let parsed;
      try {
        parsed = JSON.parse(candidate.text);
      } catch (_) {
        continue;
      }
      const found = callsFromObject(parsed);
      if (found) {
        const accepted = accept(found, candidateText, candidate.start, candidate.end);
        if (accepted) return accepted;
      }
    }

    // 2) Top-level JSON array of calls.
    const trimmed = candidateText.trim();
    if (trimmed.startsWith('[')) {
      try {
        const parsed = JSON.parse(trimmed);
        const found = callsFromObject(parsed);
        if (found) {
          const accepted = accept(found, candidateText, 0, candidateText.length);
          if (accepted) return accepted;
        }
      } catch (_) {
        /* try other formats */
      }
    }

    // 3) TOOL_CALL: name {json}
    const legacy = candidateText.match(/TOOL_CALL:\s*([A-Za-z0-9_.-]+)/i);
    if (legacy && (!allowed || allowed.has(legacy[1]))) {
      const braceStart = candidateText.indexOf('{', legacy.index + legacy[0].length);
      if (braceStart !== -1) {
        for (const candidate of balancedObjects(candidateText.slice(braceStart))) {
          try {
            const args = JSON.parse(candidate.text);
            const remaining = (candidateText.slice(0, legacy.index) + candidateText.slice(braceStart + candidate.end)).trim();
            return { calls: [buildCall(legacy[1], args, 0)], text: remaining };
          } catch (_) {
            /* keep scanning */
          }
        }
      }
    }

    // 4) XML-ish wrapper: <tool_call>{...}</tool_call>
    const wrapped = candidateText.match(/<tool_call[^>]*>\s*([\s\S]*?)\s*<\/tool_call>/i);
    if (wrapped) {
      try {
        const parsed = JSON.parse(wrapped[1]);
        const found = callsFromObject(parsed);
        if (found) {
          const accepted = accept(found, candidateText, wrapped.index, wrapped.index + wrapped[0].length);
          if (accepted) return accepted;
        }
      } catch (_) {
        /* not a tool call */
      }
    }
  }

  return { calls: null, text: raw };
}

// ---------- citation marker filter ----------

class CitationFilter {
  constructor() {
    this.buffer = '';
    this.indices = [];
  }

  feed(text) {
    if (!text) return '';
    this.buffer += text;
    let out = '';

    while (this.buffer.length > 0) {
      const open = this.buffer.indexOf('[');
      if (open === -1) {
        out += this.buffer;
        this.buffer = '';
        break;
      }

      out += this.buffer.slice(0, open);
      this.buffer = this.buffer.slice(open);

      const full = this.buffer.match(/^\[citation:(\d+)\]/);
      if (full) {
        this.indices.push(Number(full[1]));
        this.buffer = this.buffer.slice(full[0].length);
        continue;
      }

      if (isCitationPrefix(this.buffer)) break;

      out += '[';
      this.buffer = this.buffer.slice(1);
    }

    return out;
  }

  flush() {
    const rest = this.buffer;
    this.buffer = '';
    return rest;
  }
}

function isCitationPrefix(value) {
  const marker = '[citation:';
  if (marker.startsWith(value)) return true;
  if (!value.startsWith(marker)) return false;
  return /^\d*$/.test(value.slice(marker.length));
}

module.exports = {
  TOOL_CALL_FORMAT,
  contentToText,
  isSystemRole,
  normalizeTools,
  formatToolPrompt,
  buildPrompt,
  extractToolCalls,
  CitationFilter,
  buildToolNameIndex,
};
