# Arelbot Implementation Status

Last update: 2026-10-10

This file keeps a clean, verifiable current status. Detailed contracts are in `docs/ARCHITECTURE.md`, the feature matrix is in `docs/FEATURE_CATALOG.md`, source and license records are in `docs/DATA_SOURCES.md`, and operations procedures are in `docs/OPERATIONS.md`.

## Current level

Arelbot is a production-candidate Telegram bot. Typed provider contracts, Redis/Mongo job durability, worker separation, AI/RAG, media, notifications, productivity, group, game, quote, and sticker surfaces use shared domain boundaries.

## Completed phases

### Stability and quality gate

- Reproducible dependency constraints, UTF-8 source, central Settings, secret redaction, and production preflight.
- Strict architecture gate now checks every Python and Markdown source for common mojibake patterns as well as replacement/lost characters; legacy encoding exemptions were removed and the chat-engine sanitizer map was converted to explicit Unicode escapes.
- An AST-based audit now parses every Telegram handler module and blocks recurring ASCII regressions such as `Kullanim`, `Gecersiz`, `alinamadi`, malformed sticker-thumbnail text, and untranslated cookie/help messages; handler strings and affected callback expectations are UTF-8 tested.
- Docker Compose dev/test/prod/monitoring profiles with MongoDB, Redis, FFmpeg, and ffprobe health checks.
- Architecture check, security replay, provider matrix, compileall, and deterministic release gate.

### Typed city and public-data providers

- Checksummed 81-province local snapshot with province, plate, phone, district, alias, and diacritic search.
- Typed pharmacy, fuel, currency/gold, crypto, earthquake, news/RSS, weather, air quality, BIM, prayer, flight, culture, and story adapters.
- Local memory/game/image tools use a typed local provider envelope; the legacy adapter is reserved for explicitly enabled migration fallback chains.
- Public Spatula compatibility tools now delegate to typed providers; the former direct HTTP/BeautifulSoup scraper path is removed and covered by no-scraper regression tests.
- HTML-backed typed providers share a streamed 2 MiB response cap, configured HTTPS host allowlist, content-type validation and stable policy errors before cache/rendering.
- Weather, air-quality, crypto, culture, market, AFAD, RSS and Tavily structured providers now use the shared bounded transport for HTTPS/host/redirect/content-type/size policy; fixture tests cover success, redirect, private-host and oversized-body cases.
- Provider runtime retry policy now distinguishes deterministic policy/schema failures from transient timeout, 429 and 5xx failures; deterministic responses consume one attempt and transient failures retain bounded retry/circuit accounting.
- Source, license/terms, cache TTL, freshness SLA, timeout, retry, circuit breaker, rate limit, and stale result envelope.
- Administrator media cookie vault accepts browser JSON exports as well as Netscape files, validates platform domains and normalizes them before Fernet encryption; plaintext cookie values are never logged or stored in fixtures.
- Instagram media probing now retries with a temporary administrator cookie file when the public probe is blocked, only when the encrypted cookie vault is explicitly enabled; the temporary file is removed after probing. The production yt-dlp pin is updated to 2026.08.19.

### Notifications and productivity

- User-facing `/bildirim` center and inline planning from `/hava` and `/deprem` result screens; kur, akaryakıt, kripto, haber and BİM takipleri de artık kişisel merkezden tek tuşla kurulabiliyor.
- Group plans are administrator-only with daily/weekly time selection, multi-group support, and forum topic scope. The inline center now also schedules city-scoped air-quality alerts and deduplicated breaking-news digests.
- Notes, tasks, recurring reminders, timezone, quiet hours, daily/weekly plans, export, and bulk deletion.
- Main inline menu now exposes Türkiye/public-data tools, BİM catalogue pages, notification center and an opt-in timezone-aware daily digest. Photo uploads first ask whether to analyze, OCR, or render a sticker; the selected action is owner-scoped and queued.
- Mongo delivery ledger prevents duplicate notifications after restart.

### AI, documents, and worker jobs

- Task-based AI provider routing, token/cost/latency metrics, typed tool allowlist, and authenticated context override.
- ChatEngine system date context now uses the tested `Europe/Istanbul` conversion helper instead of host-local time, keeping Windows and UTC VDS responses consistent.
- Clean APIs is available as a first-class OpenAI-compatible `cleanapis` route with `CLEANAPIS_URL`, `CLEANAPIS_KEY`, `CLEANAPIS_MODEL` and bounded task allowlists; when the route is empty, configured Clean APIs keys default text/analysis/sticker tagging to `deepseek-v4-flash-0731`, while missing keys fall back safely. Production terms/sign-off remain fail-closed until reviewed.
- Clean APIs live staging probe passed with the configured `deepseek-v4-flash-0731` route; the evidence stores only provider/model identity, latency, usage presence and response size, never the key or model output.
- OpenAI-compatible AI requests enforce HTTPS/public endpoint policy, configured-model allowlists, bounded messages/responses/embeddings, explicit `max_tokens` limits and typed protocol errors; deterministic input failures do not cool down valid provider keys.
- AI tool execution now enforces the feature manifest permission scope before dispatch: private-only memory tools fail closed in groups, group-admin tools require explicit administrator context, and denials use a stable `TOOL_PERMISSION_DENIED` code without exposing arguments.
- Direct allowlisted tools also pass through a bounded error envelope; arbitrary provider messages, URLs and credentials are removed before the model sees the result.
- Public web reader and Tavily search now run as typed, source-attributed providers. SSRF/private-host/redirect and bounded-body checks protect web reading; search keys rotate through the KeyManager circuit and `/ara` uses the shared FeatureExecutor path.
- Manual `/ara` and `/search` now preserve the scoped personality mode, match commands case-insensitively, and pass provider text through the untrusted context boundary instead of concatenating it into the user prompt; replay tests cover prompt-injection text.
- ChatEngine now removes only the just-persisted matching Telegram user tail before appending the current turn, preventing duplicated context and token waste while preserving prior identical questions.
- Lyrics search now uses the typed search + bounded web-reader chain, follows only the permitted HTTPS lyrics-domain allowlist, and returns a short attributed excerpt instead of reproducing full copyrighted text.
- PDF/DOCX/TXT/image ingestion, MIME/path/archive checks, chunking, embeddings, Chroma persistence, and opt-in memory.
- Vision, STT, TTS, image, quote, sticker, and chart jobs run in workers with Redis queues and Mongo recovery/dead letters.

### Media, quote, sticker, group, and games

- Yt-dlp/FFmpeg, SSRF/redirect policy, 45 MiB limit, fallback provider, Telegram file_id cache, and temp cleanup.
- Media provider boundary now exposes an explicit `MediaArtifact` TypedDict for path, format, duration, size and platform; public cookie usage remains optional and observable.
- Legacy `VideoDownloader`/`TikTokDownloader` imports are compatibility facades over `YtDlpProvider`; direct Cobalt/TikWM HTTP bypasses were removed and statically guarded.
- Media URL replay now covers Instagram/instagr.am, TikTok, YouTube/Shorts, Twitter/X, and Reddit; provider registry tests verify platform-specific priority, alias routing, circuit opening, and wildcard fallback.
- Deterministic media replay matrix now covers Instagram, TikTok, YouTube Shorts, X, Facebook/Reels and Reddit with zero network calls, artifact validation, provider selection and temp cleanup; live platform acceptance remains separately gated.
- Quote themes/templates/batch/PNG/WEBP; sticker FSM, static/video render, package operations, thumbnail, and catalog.
- Persistent FSM payload tracking now observes nested dictionary and list mutations, including in-place append/pop operations, before Redis TTL persistence; concurrent user/chat wizard scopes use separate Redis keys and mismatched group callbacks fail closed.
- Group RBAC, welcome/goodbye, flood/link/word filters, mute/ban, and analytics.
- Administrator-scoped group personality modes (`serious`, `balanced`, `needle`) persist through chat settings and use the same sensitive-topic safety policy.
- Private users can persist their own `/persona` or `/uslup` preference; callbacks are owner-scoped, groups remain administrator-controlled, and sensitive or technical turns force a serious safety tone.
- ChatEngine now passes recent assistant-turn count into PersonalityPolicy; balanced mode permits playful tone on a four-turn cadence and needle mode on a two-turn cadence, reducing repetitive sarcasm without rewriting factual output.
- Runtime feature manifest now covers local and queue-owned public surfaces (notes, tasks, reminders, notification preferences, price alerts, media, group moderation/personality, leaderboard, and delivery deduplication) with typed permissions, timeouts, rate limits, audit policies, and kill-switch metadata.
- Quiz, blackjack, batak, roulette, economy, seasons, achievements, leaderboard, and callback idempotency.

### Operations and transport

- Polling/webhook settings are a pure contract in `src/telegram_transport.py`.
- Transport construction now independently rejects non-HTTPS/credentialed webhook URLs, unsafe path segments, invalid URL-safe secrets and out-of-range ports before python-telegram-bot startup.
- Read-only Telegram staging probe validates bot identity, webhook status and published commands behind redundant live gates.
- Private attachments are never uploaded to the operational log channel; only bounded presence and byte metadata are retained.
- Exception traces are suppressed at the log boundary; operational records retain only bounded exception types.
- Structured logger arguments containing exception objects are reduced to exception type summaries before formatting, preventing raw driver messages from bypassing URL/secret redaction.
- Architecture CI rejects `str(exc)`, `repr(exc)` and f-string exception interpolation in logger calls; retry, game, configuration, i18n, database, and media-store paths use bounded exception summaries.
- Telegram bootstrap no longer constructs a module-level orchestrator; handler registration receives the composition-root instance explicitly and lifecycle hooks resolve it from application-scoped data.
- Production preflight now constructs the same `TelegramTransport` contract used by `main.py`, so webhook path, listen address, port, secret and URL rules cannot drift between deployment validation and runtime startup.
- Bootstrap transport dispatch is now a separately tested seam: polling invokes only `run_polling`, webhook invokes only `run_webhook`, and neither path can silently consume the other mode's keyword set.
- Production monitoring is opt-in through `MONITORING_ENABLED`; preflight requires observability to be enabled and validates a real, permission-restricted Grafana admin password file before the monitoring Compose profile is opened.
- Staging matrix reports use stable failure codes and exception types, without copying provider response text or URLs into archived evidence.
- Staging matrix supports an atomic `--report` artifact for deployment evidence; live execution remains explicitly gated.
- `scripts/evidence_audit.py` validates deterministic evidence invariants and secret-free reports; `--require-live` is a separate fail-closed deployment check.
- `docs/PHASE_STATUS.json` and `scripts/phase_audit.py` provide a machine-readable phase contract; the current audit verifies phases 0–8 and leaves phase 9 explicitly `deployment_pending` until the VDS readiness gate and all six deployment evidence artifacts pass invariant checks.
- Live deployment evidence is schema-validated before promotion: read-only VDS readiness, Telegram mutation, full six-platform media/SLO, VDS backup/restore checksum, monitoring delivery, provider approval and Clean APIs staging each require stable scope, counters and invariant sets.
- Release gate reports can be persisted atomically with `--report`; symlink targets are rejected and output tails are centrally redacted.
- Provider feature flags are enforced before network work; `FEATURE_DISABLED` and `/features` status expose the emergency kill-switch state.
- Production registry construction now requires complete provider approval metadata; pending, disabled or partially reviewed external sources fail closed before any network call, even when a manual Compose start bypasses preflight.
- ProviderRuntime graceful shutdown now drains health telemetry, cancels detached stale-refresh tasks, and clears cache/inflight state before provider HTTP clients are closed.
- Production preflight rejects `LEGACY_PROVIDER_FALLBACK_ENABLED`; legacy adapters remain available only for controlled development/staging replay.
- Loopback/token-protected observability, Prometheus/Grafana dashboard, and five alert rules.
- Alertmanager is now part of the monitoring graph: Prometheus routes to a secret-file-backed HTTPS webhook, production preflight validates the receiver target, and `scripts/monitoring_alert_staging.py` proves delivery through the webhook integration counter without exposing the endpoint.
- Authenticated `/healthz` now returns bounded core dependency status plus feature/media provider health groups; core outages are `unhealthy`, optional provider outages are `degraded`, and the snapshot is cached for five seconds.
- `/healthz` now includes the AI router's observed circuit and provider health group; mapping-based AI health rows are summarized without exposing provider payloads.
- Provider health failures are now represented by stable exception-type summaries; provider credentials, URLs and driver messages cannot leak through `/health`.
- Backup/restore namespace remap, migration rollback, release manifest, and production preflight.
- `scripts/vds_backup_restore_staging.py` now turns the manual VDS backup/restore requirement into a fail-closed, disposable-target evidence workflow; it never fabricates the live artifact and requires separate destructive confirmation.
- `scripts/vds_backup_restore_remote.py` executes the same drill through the production Compose containers, verifies Mongo namespace remap, Redis RDB loading and Chroma extraction, then removes every temporary target without stopping production services.
- `scripts/vds_migration_rollback_remote.py` runs the registered 14→3→14 migration rollback contract against a disposable VDS database and verifies cleanup/isolation; live evidence is archived at `deploy/evidence/vds-migration-rollback.json`.
- Telegram log-channel events contain only safe payload length/type summaries; user names,
  message bodies, URLs and assistant output are excluded from operational sinks.

## Latest deterministic evidence

- Local offline release gate: **864 passed, 10 skipped** (Windows symlink capability and platform-specific archive fixtures).
- Real python-telegram-bot `get_me`, `send_message`, `edit_message_text`, `send_video` and `get_file` calls are replayed through `httpx.MockTransport`; safe API errors are covered without a live token.
- Docker release gate: **872 passed, 2 environment-dependent skips**.
- Architecture strict: **0 issues**.
- AI router telemetry: aggregate and registered-provider request/success/error counters plus p50/p95/p99 latency samples are exposed in `/metrics` without request payload or model labels.
- Deterministic AI request bounds now raise a typed `AIProviderRequestError`; router validation counters are separate and cannot consume provider fallback or circuit health.
- Telegram display names now pass through a bounded, delimiter-safe turn envelope shared by persistence and prompt assembly; regression coverage protects history deduplication from name-controlled delimiters.
- The legacy `UnifiedLogger` failure boundary now emits only typed exception summaries, with a regression test proving private exception text cannot enter its sink.
- Handler persistence boundary: all Telegram handlers use HandlerDataPort; AST guard rejects direct .db attributes and replay tests cover the port.
- User-facing exception boundary: generic handler and image-job failures use controlled messages; arbitrary exception text is not sent to Telegram.
- Media URL, cookie, download, and transcode failures now use a typed Telegram presentation boundary; provider URLs, paths, credentials, and arbitrary driver text are never echoed to users.
- Telegram-facing Orchestrator runtime methods also use HandlerDataPort; architecture AST checks allow DB wiring only in the composition methods.
- Durable game actions: HandlerDataPort advertises claim/release capability; GameActionLedger selects Mongo persistence only when the capability is present and tested.
- Security replay: **6/6**.
- Provider fixture replay: 20 requests, 10 concurrency, p95 **5.12 ms** (archived deterministic sample); latest 100-request load p95 **16.29 ms**.
- Media delivery replay: **8/8**, including completion-SLO telemetry and 10-worker/100-chat queue isolation.
- Media queue load harness: **100/100** fixture jobs, 10 workers, 100 active chats, queue depth 0, max active workers 10, p95 **2.53 ms** in the latest Docker release gate; safe evidence written to `deploy/evidence/media-queue-load.json`.
- Media replay matrix: **6/6** platform families passed with `network_calls=0`; safe evidence written to `deploy/evidence/media-replay-matrix.json`.
- Provider health scoring now uses the documented millisecond latency unit and includes live latency before the first request sample; RSS article links are restricted to credential-free HTTP(S) URLs before Telegram rendering.
- Mongo/Redis/FFmpeg/ffprobe/encryption health and migration smoke: passed.
- Telegram read-only staging: **passed** (`getMe`, webhook status, 92 published command names); the latest report records webhook disabled and 0 pending updates without mutating Telegram; safe evidence written to `deploy/evidence/telegram-staging.json`.
- Telegram mutation replay: **4/4** deterministic PTB client scenarios covering `sendMessage`, forum `message_thread_id`, `editMessageText`, `sendVideo`, `sendDocument`, `sendSticker`, `answerCallbackQuery` and `getFile`; safe API error handling remains covered, while live mutation remains deployment-scoped.
- Monitoring profile graph: **passed**; `docker compose --profile monitoring config --quiet` now resolves bot, worker, MongoDB, Redis, Prometheus and Grafana dependencies as one valid graph.
- Monitoring authentication replay: **passed**; `/metrics` accepts the configured Prometheus Bearer credential as well as the operator header, while unauthenticated requests remain 401.
- Monitoring alert replay: **passed**; pinned `promtool test rules` exercises all five alert rules with scrape, queue-capacity, backlog, and dropped-telemetry fixtures.
- Monitoring delivery replay: **passed 8/8** through the loopback canary client and secret-free counter assertions; live Alertmanager/webhook delivery remains deployment-scoped.
- Webhook reverse-proxy contract: **passed**; production preflight now verifies the public URL prefix, exact Nginx location, loopback upstream path, TLS listener, Telegram secret forwarding, and default-deny route. SSH release deploy passes the target Nginx path with `--nginx-config`.
- VDS read-only readiness: **passed**; Linux, Docker, Compose, writable `/opt/arel`, shared production env, three release directories, four healthy target containers and 54 GB free disk were verified without stopping existing workloads; safe evidence is archived at `deploy/evidence/vds-readiness.json`.
- Public provider staging: **passed 4/4** (weather, air quality, AFAD, currency); first-call p95 **551.47 ms**, repeated cache responses p95 **0.07 ms**; safe evidence written to `deploy/evidence/provider-staging.json`.
- Governance link health: **29/29 reachable**; no HTTP reachability blocker remains. Source terms/robots/attribution approval is still pending for the governed features; safe evidence is archived at `deploy/evidence/provider-governance-links.json`.
- Compose persistence smoke: **passed**; Mongo namespace-remap restore and Redis RDB load verified with disposable data; safe evidence written to `deploy/evidence/persistence-staging.json`.
- Immutable release build/deploy: **passed** from a clean Git checkout; active release `c029a43aed99` was uploaded and activated with archive SHA-256 `ab46450a2305febb8b3ec4e58423cdfa9e0495842599b24f87a58a90e629d921`. Local `.env`, runtime state, cookies, and credentials are excluded from the archive.
- Live media staging: **probe/download 2/2 passed** (10-second YouTube Shorts fixture and TikTok); latest two-case p95 **3,882.00 ms**; safe evidence written without URL or upstream error text.
- External media concurrency staging: the host run passed **10/10** across YouTube/TikTok with a 10-job limit and 100 synthetic chat slots at p95 **9,612.84 ms**. The latest Docker rerun also passed **10/10** at p95 **7,697.36 ms** (`deploy/evidence/media-staging-concurrency-docker-rerun.json`). One preceding Docker sample completed 10/10 but recorded a transient **15,472.15 ms** p95 (`deploy/evidence/media-staging-concurrency-docker.json`); the release evidence preserves that breach instead of hiding source jitter.

## Remaining work

1. Real Linux VDS webhook/reverse proxy and Windows polling E2E.
2. Live Instagram/TikTok/YouTube Shorts/X media matrix; URL normalization and provider fallback replay cover all four platform families plus Reddit, while YouTube/TikTok 10-job/100-chat samples currently pass the 10-second SLO. One Docker repetition breached it under source jitter; live Instagram/X fixtures and repeated SLO confidence remain open.
3. VDS persistence and migration rollback drills now pass: Mongo/Redis/Chroma backup, disposable restore, checksum and cleanup with archive digest `5e9dbf7ed8edfc4351fd340c48da48bdaaccff1f1bb31b6ea2197f07845d8ad2`, plus disposable 14→3→14 migration rollback.
4. Provider license/terms/robots production sign-off and source kill-switch decisions.
5. Live Telegram message, callback, forum topic, sticker, and file API E2E using deployment configuration; deterministic PTB mutation replay is now green.
6. Monitoring container startup and real alert delivery verification in staging; pinned alert-rule replay now passes in the release gate, while live delivery evidence must satisfy the deployment contract.
7. Repeat the read-only Telegram staging probe after each release; the current deployment report is archived and passed `getMe`, webhook status, and published command checks over verified TLS (no mutation).

## Status label

**Production-candidate / deployment validation pending.** Code and Docker quality gates are green; live environment evidence is still required.


- Provider source governance is now typed and machine-audited: each external feature exposes source governance, robots policy, terms review, and production sign-off state. The normal release gate reports pending sources safely; production preflight can fail closed with `--require-provider-approval`. A fingerprint-bound, time-limited `PROVIDER_APPROVAL_MANIFEST` can carry reviewed evidence without editing source metadata; stale, malformed or mismatched evidence remains fail-closed.
- A deterministic `staging-core` profile now converts the pending governance worksheet into an explicit `DISABLED_FEATURES` list without network access or approval claims. Runtime status reports `kill_switch` when an operator-disabled source is also governance-pending, so `/features` and audit output remain truthful.
