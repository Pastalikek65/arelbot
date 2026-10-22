'use strict';

// Arel runs this proxy only as an authenticated, private sidecar. Account
// refresh writes must retain private permissions after atomic replacement.
process.umask(0o077);
if (String(process.env.PROXY_API_KEY || '').trim().length < 32) {
  console.error('D2API_PROXY_KEY_REQUIRED');
  process.exit(1);
}
require('./server');
