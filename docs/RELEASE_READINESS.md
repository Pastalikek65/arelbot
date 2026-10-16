# Arelbot Production Readiness Report

This document separates deterministic repository evidence from checks that require a real Telegram, VDS, reverse proxy, or external provider environment. A green local or Docker test is not a live deployment acceptance.

Faz durumlarÄ± `docs/PHASE_STATUS.json` iÃ§inde makine okunur olarak tutulur.
`python scripts/phase_audit.py` dokuz deterministik fazÄ± release gate ile
doÄŸrular ve canlÄ± deployment fazÄ±nÄ± eksik kanÄ±t dosyalarÄ±yla birlikte
`deployment_pending` bÄ±rakÄ±r; bu komutun baÅŸarÄ±sÄ±z Ã§Ä±kmasÄ± mevcut durumda
beklenen ve gÃ¼venli davranÄ±ÅŸtÄ±r.

## Evidence summary

| Check | Result | Evidence |
|---|---:|---|
| Local Python tests | Passed (offline gate) | 857 passed, 10 skipped (Windows symlink capability and platform-specific archive fixtures) |
| Docker test profile | Passed | 865 passed, 2 environment-dependent skips |
| Architecture boundary check | Passed | `architecture_check.py --strict`: 0 issues |
| Security replay | Passed | 6/6: SSRF, private host, archive, prompt boundary, secret redaction |
| Provider fixture replay | Passed | 20 requests / 10 concurrency, p95 5.12 ms (archived deterministic sample) |
| Media delivery replay | Passed | 8/8, including SLO telemetry and 10-worker/100-chat queue isolation |
| Media platform replay matrix | Passed (network-free fixture replay) | 6/6: Instagram, TikTok, YouTube Shorts, X, Facebook/Reels and Reddit; artifact validation and cleanup passed |
| Media queue load harness | Passed (fixture replay) | 100 jobs / 10 workers / 100 active chats; max active workers 10; queue depth 0; p95 0.90 ms |
| Mongo/Redis/FFmpeg health | Passed | Docker health checks passed |
| Mongo migration | Passed | migration v14 forward/rollback smoke |
| Persistence restore smoke | Passed (Compose + VDS) | Mongo namespace remap, Redis RDB load, Chroma extraction and cleanup verified against disposable targets |
| Immutable release archive | Passed | Deployed commit `c029a43`; `arel-c029a43aed99.tar.gz`, SHA-256 `ab46450a2305febb8b3ec4e58423cdfa9e0495842599b24f87a58a90e629d921`; manifest extraction and checksum entry validation passed |
| VDS read-only readiness | Passed | SSH host-key, Docker/Compose, writable `/opt/arel`, shared env, three release directories and four target containers passed; 54 GB free disk. Safe report: `deploy/evidence/vds-readiness.json` |
| Prometheus rules | Passed | pinned `promtool`: 5 alert rules |
| Prometheus alert replay | Passed | pinned `promtool test rules`: all five alert conditions and annotations |
| Monitoring Compose graph | Passed | `docker compose --profile monitoring config --quiet` resolves bot/worker, Prometheus, Alertmanager and Grafana dependencies; pinned Prometheus config and five alert rules also pass `promtool` in Docker |
| Monitoring authentication replay | Passed | Prometheus Bearer token and operator header accepted; unauthenticated `/metrics` remains 401 |
| Webhook reverse-proxy contract | Passed | Production preflight checks public path, internal loopback upstream, TLS, Telegram secret forwarding, and default-deny route |
| Telegram API connection | Passed (read-only) | token shape and read-only `getMe`/`getWebhookInfo`/`getMyCommands` passed over verified TLS; no Telegram mutation occurred |
| Telegram API mutation replay | Passed (deterministic) | Real python-telegram-bot client replayed forum messages, `answerCallbackQuery`, `sendDocument`, `sendSticker`, `sendMessage`, `editMessageText`, `sendVideo` and `getFile` through `httpx.MockTransport`; live Telegram mutation remains deployment evidence |
| Public provider staging | Passed (bounded matrix) | weather, AQ, AFAD, currency 4/4; first-call p95 551.47 ms; repeat-cache p95 0.07 ms |
| Clean APIs live staging | Passed (bounded live completion) | `cleanapis` / `deepseek-v4-flash-0731` returned a successful bounded completion; only latency, response size and usage presence are archived in `deploy/evidence/cleanapis-staging.json` |
| Governance link health | Passed (reachability) / approval pending | 29 manifest links checked; 29 reachable with bounded GET fallback; legal/robots/attribution approval state unchanged |
| Live media staging | YouTube/TikTok passed latest SLO samples; broader matrix pending | 10-second YouTube Shorts fixture + TikTok 2/2, p95 3,882.00 ms; host 10-job/100-chat run 10/10 at p95 9,612.84 ms; latest Docker rerun 10/10 at p95 7,697.36 ms; one prior Docker run preserved a 15,472.15 ms jitter breach; the latest X public probe passed, but Instagram remains access-restricted and the full Instagram/X download matrix remains pending |

## Completed production infrastructure

- Typed `FeatureDefinition`, `FeatureResult`, `DataProvider`, provider registry, and source metadata contracts.
- Public HTML provider transport is bounded by HTTPS host allowlist, redirect refusal, content-type validation and a streamed 2 MiB response cap; policy failures have stable user-safe error codes.
- Production `FeatureRegistry` fail-closes external providers unless source, robots, terms and production sign-off metadata are all approved; development/replay keeps the pending inventory visible.
- Local tool operations use a typed `LocalToolProvider`; legacy adapters remain observable only when explicitly configured as migration fallbacks.
- Local 81-province Turkey snapshot with Unicode normalization, plate, phone, district, alias, and inline search.
- Pharmacy, fuel, currency/gold, crypto, earthquake, news/RSS, weather, air quality, BIM, prayer, flight, culture, and story provider surfaces.
- Timeout, retry, request coalescing, stale-while-revalidate, rate limiting, circuit breaker, latency/error score, and source freshness.
- Personal and group notification center; administrator-only group weather/earthquake/air-quality/news schedules; timezone, quiet hours, content deduplication, and idempotent delivery ledger.
- Notes, tasks, recurring reminders, daily/weekly plans, export, and bulk deletion.
- AI provider routing, vision/STT/TTS, RAG ingestion, Chroma persistence, tool allowlist, source boundary, and opt-in memory.
- Clean APIs / DeepSeek V4 Flash 0731 is wired as a separate typed provider route; credentials are deployment-only and provider terms/sign-off remain pending.
- Clean APIs live completion staging passed for `deepseek-v4-flash-0731`; `deploy/evidence/cleanapis-staging.json` is bounded and secret-free, while the remaining production governance approval is still fail-closed.
- AI router runtime telemetry records bounded fallback attempts, per-provider success/error counters, and p50/p95/p99 call latency in the observability snapshot.
- AI tool permissions are enforced centrally from the feature manifest; private-only tools fail closed in groups and group-admin tools require explicit admin context.
- Direct allowlisted tool errors are normalized to stable codes and generic messages before AI consumption.
- Public web reader and Tavily search use typed providers with bounded input/output, source attribution, untrusted-context metadata, SSRF/redirect policy and key rotation; manual `/ara` traffic goes through the feature executor.
- Lyrics search is a typed provider chain with permitted-domain allowlisting, shared public URL validation, bounded excerpts and source attribution.
- Provider health exceptions are summarized by type before entering `/health` responses or operational evidence.
- Provider health scoring uses the declared millisecond latency unit and a live health latency until request samples exist; RSS article links are validated as credential-free HTTP(S) before Telegram formatting.
- Yt-dlp/FFmpeg media pipeline, 45 MiB policy, fallback provider, Redis queue, Mongo recovery, Telegram file_id cache, and temp cleanup.
- Worker-based image, document, speech, quote, sticker, and chart jobs with bounded payloads, dead letters, and restart recovery.
- Quote themes/templates/batch rendering; PNG/WEBP output; persistent sticker FSM, package operations, and catalog.
- Group RBAC, welcome/goodbye, flood/link/word filters, progressive mute/ban, analytics, and weekly summaries.
- Quiz, blackjack, batak, roulette, text games, economy, seasons, achievements, leaderboard, and idempotent game ledger.
- Inline city/news/quote/sticker/crypto/market surfaces, pagination, and forum topic scope.
- Polling/webhook transport contract with safe full URL, reverse proxy prefix, and relative path normalization.
- Read-only Telegram staging probe with redundant live gates for bot identity, webhook status, and published commands; Windows uses the native system trust store and an explicitly supplied `SSL_CERT_FILE`/`ARELBOT_EXTRA_CA_CERT` remains available for corporate roots.
- `scripts/telegram_mutation_staging.py` provides the deployment-scoped mutation evidence runner. It requires an explicit disposable staging chat, forum/file/callback inputs, three live gates plus `--allow-mutation`, and archives only operation counters/types.
- Docker dev/test/prod/monitoring profiles; MongoDB/Redis volumes and health checks; preflight, backup/restore, migration, and rollback procedures.
- `scripts/vds_backup_restore_staging.py` and `scripts/vds_backup_restore_remote.py` provide four-signal, disposable-target backup/restore evidence runners; the live VDS run verified Mongo namespace remap, Redis RDB loading, Chroma extraction and cleanup with a secret-free artifact digest. `scripts/vds_migration_rollback_remote.py` verifies the disposable 14â†’3â†’14 migration path.
- Loopback/token-protected `/healthz` and Prometheus `/metrics`; Prometheus/Grafana dashboard and five operational alerts.
- Monitoring profile includes pinned Alertmanager `v0.34.1`, a secret-file-backed HTTPS webhook receiver, Prometheus routing, and a loopback-only canary delivery probe.
- `/healthz` exposes bounded dependency/provider groups with `healthy`, `degraded`, and `unhealthy` classification and a five-second cache.
- `/healthz` includes feature, media, and AI provider groups; AI provider dictionaries are normalized into the same bounded health envelope.

## Quality gates

- `scripts/architecture_check.py --strict`: 0 violations.
- No direct HTTP, Redis, MongoDB, or FFmpeg calls in handlers.
- Handler persistence is exposed through HandlerDataPort; architecture AST checks reject direct .db attributes.
- Orchestrator update/admin methods use the same port; the composition root remains the only DB-to-service wiring boundary.
- Durable game callbacks use the port capability contract; unsupported test/degraded ports fall back to bounded in-memory claims.
- Static architecture guard rejects TLS certificate bypasses (`verify=False`, `nocheckcertificate`, `CERT_NONE`).
- Production bot/worker roles fail closed when MongoDB or Redis is unavailable; memory fallback is development/test only.
- Production preflight fails closed if the legacy provider fallback is enabled; typed provider contracts are mandatory in production.
- Secrets, cookies, complete media URLs, private user data, and tokens are not logged.
- Telegram operational log-channel events use payload length/type summaries and do not copy user or assistant text.
- Private attachments are represented only by bounded metadata; they are never uploaded to the operational log channel.
- Exception tracebacks are suppressed before formatting so private exception messages cannot enter operational logs.
- Exception objects passed as structured logging arguments are reduced to bounded type summaries before formatter interpolation.
- Architecture CI rejects raw exception interpolation (`str(exc)`, `repr(exc)`, and f-string exception values) in logger calls; covered paths emit only bounded exception types.
- Staging provider reports retain stable error types and source-host mismatch codes; provider exception text and response URLs are excluded from archived reports.
- `scripts/evidence_audit.py` verifies deterministic evidence invariants and secret-free artifacts; `--require-live` additionally requires a passing read-only VDS readiness report, Telegram mutation, full media matrix, VDS backup/restore, monitoring delivery, provider approval and Clean APIs staging artifacts.
- `scripts/feature_catalog_audit.py` compares all 50 runtime `FeatureRegistry` keys with the Markdown catalog; missing, undocumented, or duplicate rows fail the release gate.
- `--require-live` validates all six deployment artifacts plus VDS readiness by scope and invariant: successful file presence alone cannot promote a deployment. Full media evidence must cover Instagram, TikTok, YouTube Shorts and X with a measured download p95 at or below 10 seconds across at least 100 chat slots and no more than 10 concurrent jobs.
- The release gate supports an atomic `--report` JSON artifact with redacted output tails and symlink-target protection.
- `scripts/staging_matrix.py --report` writes the bounded report atomically for later release audit.
- Source code and operational documents are UTF-8 and document headings are checked by architecture CI.
- Fixture/replay contracts are used instead of live sites for deterministic acceptance.
- `docs/media-replay-matrix.json` and `scripts/media_replay_matrix.py` exercise every supported media platform family without contacting external sites; this is contract evidence, not live platform availability.

## Required evidence before live opening

- Linux VDS with production environment, real owner id, HTTPS reverse proxy, Telegram setWebhook, and secret header verification.
- Separate bot and worker processes from one release manifest; Redis/Mongo restart, worker crash, and rollback drills.
- Live Instagram, TikTok, YouTube Shorts, and X media matrix; p95 measurement with 10 concurrent external jobs and about 100 chats. The deterministic six-platform replay matrix is complete, but it does not promote live availability. YouTube/TikTok passed the latest host and Docker samples under 10 seconds; the preceding Docker sample remains archived as a real 15,472.15 ms jitter breach. The latest X public probe passed while Instagram returned the stable `source_access_restricted` code, so full Instagram/X downloads and repeated SLO confidence are still required. Evidence: `deploy/evidence/media-staging-concurrency.json`, `deploy/evidence/media-staging-concurrency-docker.json`, and `deploy/evidence/media-staging-concurrency-docker-rerun.json`.
- Provider usage terms, license, attribution, and robots/terms sign-off for every source.
- Migration rollback drill on the VDS passed against a disposable database (14â†’3â†’14, index contract and cleanup); future schema changes still require an explicit operator rollback.
- Windows polling and Linux webhook Telegram E2E for messages, photos, videos, callbacks, and forum topics.
- Run the read-only Telegram staging probe with deployment credentials and preserve its safe report as release evidence.
- Deployment-configured replay of provider outage/recovery, stale cache, 429, redirect/SSRF, archive traversal, secret leakage, and prompt injection.
- Live evidence must use the stable scopes `vds-readiness`, `telegram-mutation-live`, `live-media-matrix`, `vds-backup-restore`, `live-monitoring-alert-delivery`, `provider-governance-approval`, and `cleanapis-staging`; each report is checked for bounded counters, checksums/approval IDs where applicable, truthful readiness state, and secret-free content.
- Immutable release archive from a clean committed Git checkout. The local baseline archive and VDS upload/installation/activation passed; migration rollback remains deployment evidence.

## Deliberately deferred surfaces

- Mini App is behind a feature flag; inline and callback surfaces are the default.
- External account login, user cookies/passwords, and account-dependent integrations are disabled.
- Monetization and payments require a separate product decision.

## Release decision

The codebase is at a professional production-candidate infrastructure level. Offline and Docker quality gates are green; the live external and VDS evidence above must be completed before calling it production-complete.

## Last verification: 2026-10-10

- Telegram transport is a pure testable contract in `src/telegram_transport.py`.
- 857 local tests (+10 environment-dependent skips), 865 Docker tests (+2 environment-dependent skips), architecture strict 0, feature catalog parity 50/50, security replay 6/6, provider fixture replay p95 5.12 ms and latest provider load p95 15.85 ms (100-request/10-concurrency run), media replay 8/8, monitoring alert replay 8/8, six-platform media replay matrix 6/6, media queue load 100/100 at 0.90 ms p95.
- Real python-telegram-bot `get_me`, `send_message`, `edit_message_text`, `send_video` and `get_file` calls are replayed through `httpx.MockTransport`; safe API errors are covered without a live token.
- Production preflight now exercises the same `TelegramTransport` object used by runtime startup, including unsafe path, listen, port and secret rejection.
- Monitoring preflight is fail-closed when enabled: it requires observability and a real permission-restricted Grafana password file instead of the Compose placeholder.
- Prometheus v3.13.4 configuration and five alert rules were accepted by `promtool`; `alerts.test.yml` replays every rule with bounded fixture series and exact labels/annotations.
- Webhook reverse-proxy preflight now validates the exact Nginx public location, loopback upstream, TLS listener, Telegram secret header, and default-deny route; the immutable SSH deployer passes the host config path explicitly.
- Production transport preflight accepts an explicit polling mode for no-domain bootstrap and keeps the full HTTPS/Nginx contract mandatory for webhook mode.
- Read-only Telegram staging probe passed over verified TLS; safe report is archived under `deploy/evidence/telegram-staging.json`. The latest read-only result reports webhook disabled with 0 pending updates; the production polling process is active and the probe itself performed no mutation.
- Public provider staging matrix passed 4/4 (weather, air quality, AFAD and currency); first-call p95 was 551.47 ms and repeat-cache p95 was 0.07 ms. The safe report is archived under `deploy/evidence/provider-staging.json`.
- Live media staging probes/downloads passed 2/2 (10-second YouTube Shorts fixture and TikTok); latest two-case p95 is 3,882.00 ms. A later bounded public probe passed for X and classified Instagram as `source_access_restricted`; this is diagnostic only and is not a full-matrix approval. The host 10-job/100-chat-slot run passed 10/10 at 9,612.84 ms p95 and the latest Docker rerun passed 10/10 at 7,697.36 ms p95. A preceding Docker run completed 10/10 but breached p95 at 15,472.15 ms; it remains archived for honest jitter tracking. No upstream response text or URL was archived; safe reports are under `deploy/evidence/media-staging-live.json`, `deploy/evidence/media-staging-download.json`, `deploy/evidence/media-staging-concurrency.json`, and `deploy/evidence/media-staging-concurrency-docker-rerun.json`.
- Media queue load harness passed 100/100 jobs with 10 workers and 100 active chats; safe report is archived under `deploy/evidence/media-queue-load.json`.
- Compose persistence smoke passed for Mongo namespace remap and Redis RDB load; safe report is archived under `deploy/evidence/persistence-staging.json`.
- Provider governance report covers 18 external features; all remain explicitly pending until source terms/robots/attribution sign-off. A fingerprint-bound, time-limited approval manifest is now supported for reviewed sources, but no approval has been fabricated. Live Telegram mutation, webhook/reverse-proxy validation, external media matrix and monitoring delivery remain deployment-stage evidence; polling activation and VDS backup/restore are healthy.
- The secret-free `staging-core` profile generator can explicitly disable all pending external features for a controlled staging run; it produces no approval evidence and does not change the live deployment gate.
- Immutable release archive generation has passed from a clean Git checkout with source manifest and secret exclusion; the deployed archive hash is recorded above. VDS upload and activation passed on the target; the disposable migration rollback evidence is archived for future release comparison.
