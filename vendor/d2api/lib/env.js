'use strict';

const fs = require('fs');

/**
 * Tiny .env loader (no dependency). Real environment variables win.
 * Supports KEY=value, # comments, and single/double quoted values.
 */
function loadDotEnv(file) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (err) {
    if (err.code === 'ENOENT') return false;
    throw err;
  }

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.*)$/);
    if (!match) continue;

    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (value === '\\n') value = '\n';

    if (process.env[match[1]] === undefined) process.env[match[1]] = value;
  }

  return true;
}

module.exports = { loadDotEnv };
