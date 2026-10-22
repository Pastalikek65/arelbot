# d2api: private primary text provider

The operator explicitly requested this integration on 2026-10-22 and accepted
the risks of using their own DeepSeek Web account. This records operating
authorization, **not** DeepSeek's permission or legal clearance. This is a Web
proxy, not the official DeepSeek API. Account restrictions, expired sessions,
protocol changes and upstream failures remain possible.

## Source and capabilities

`vendor/d2api/SOURCE.json` records the operator-provided source snapshot and
hashes. The upstream package declares MIT but contains no standalone license.
Only the server, libraries and mock tests are included. The original project is
left untouched. Its Telegram poller and login utility are not deployed.

The PoW WASM asset is not redistributed in this repository: its licensing is
UNKNOWN. The operator's existing copy is privately mounted read-only at runtime;
its expected hash is recorded in SOURCE.json. Account files never enter Git,
release archives or application images. Do not infer that the source's license
also covers DeepSeek's Web service or its assets.

The configured `deepseek-v4.1-flash` identifier is a proxy alias, not independent
verification of DeepSeek's underlying model. Tools use prompt-based emulation;
Arel's tool allowlist, argument checks and user permissions remain authoritative.
The proxy does not enforce OpenAI `response_format` or `max_tokens`. Arel adds
JSON instructions, validates JSON objects and bounds response characters; this
is not an exact upstream token budget.

With both D2API_ENABLED and D2API_KEY set, d2api is first for chat, fast,
analysis and summary, including explicit legacy routes. Existing keyed providers
remain fallback targets. Vision, sticker tagging, embeddings and transcription
retain their existing providers. Errors, timeout and invalid JSON use fallback;
cancellation propagates. Readiness is not a successful upstream request.

## Deployment contract

Use the normal Compose file with `-f deploy/compose.d2api.yml` under the same
project and production profile. The sidecar has no published port, requires a
random proxy key of at least 32 characters and runs as UID 1000 with a read-only
root filesystem, dropped capabilities and process/memory limits. Node uses
umask 0077 for refreshed account files. Only this container receives the account
directory. The bot and worker receive the proxy key, not the Web account token.

Protected host paths:

- `/opt/arel/shared/d2api` — root-owned, mode 0700.
- `accounts/` — UID/GID 1000, mode 0700; accounts.json mode 0600.
- `assets/deepseek_hash.wasm` — private runtime asset, read-only mount.
- `proxy.env` — root-owned mode 0600; PROXY_API_KEY only.

Bot configuration: D2API_ENABLED=true, D2API_URL=http://d2api:9655/v1,
D2API_KEY matching PROXY_API_KEY, and D2API_MODEL. No account credential belongs
in those variables. Disable D2API_ENABLED and recreate bot/worker to remove it
from routing; preserve existing fallback keys. Do not print Compose's resolved
environment, account files or unredacted response errors.

Before activation, validate wrong-key rejection, authenticated completion,
JSON/tool fixture behavior and a completion through Arel's actual router. Check
that container ports are unpublished and the account mount is absent from the
bot/worker. Back up the old environment, images and databases. If activation
fails, restore the old environment and images before recreating the old services.
Never restore a database backup over live data merely to roll back this change.

Mock tests use no account and require only Node: `node vendor/d2api/test/run.js`.
Live evidence and exact deployment status belong in the release evidence file.
No live Telegram player acceptance is implied by provider or automated tests.
