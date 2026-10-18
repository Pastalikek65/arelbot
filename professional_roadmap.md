# Arelbot Üretim Yol Haritası

Bu belge Arelbot'un uzun vadeli ürün ve mühendislik sözleşmesidir. Uygulama
fazları sırayla ilerler; bir fazın çıkış kriteri karşılanmadan ilgili özellik
üretimde açılmaz.

## Uygulama ilkeleri

- Telegram komutu, callback, inline arama ve AI aynı typed feature çekirdeğini kullanır.
- Handler'lar yalnızca Telegram giriş/çıkışını yönetir; iş kuralları domain servislerindedir.
- Public veri ve Telegram içi not/görev/hatırlatıcı sistemi varsayılandır.
- Harici hesap, parola ve kullanıcı cookie'si toplanmaz.
- Dış kaynaklarda lisans, attribution, freshness, timeout ve fallback kaydı bulunur.
- AI yalnızca izinli araçları çağırır ve güncel veriyi kaynaksız üretmez.
- Mesaj saklama süresi 30 gün, Telegram `file_id` cache süresi 24 saattir.

## Fazlar

### Faz 0 — Kalite kapısı ve yönetişim

`AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/DATA_SOURCES.md`, `LICENSES.md` ve
`scripts/architecture_check.py` ile kod sınırları, secret politikası, lisans
envanteri ve release kontrolleri kurulacak.

### Faz 1 — Typed feature platformu

`src/features` altındaki `FeatureDefinition`, `FeatureResult`, `DataProvider`
ve `FeatureRegistry` bütün özelliklerin ortak sözleşmesi olacak. Spatula araçlarının
uyumluluk yüzeyi typed provider delegasyonuna taşındı; doğrudan scraper yolu
kapatıldı.

### Faz 2 — Türkiye şehir kataloğu

81 il ve 973 ilçe sürümlü yerel snapshot olarak gelir. `/sehir`, `/plaka`,
`/telefon`, `/ilce` ve `/sehirler` komutları dış servis olmadan çalışır.

### Faz 3 — Public bilgi sağlayıcıları

Eczane, akaryakıt, döviz/altın, deprem, haber, hava, ezan, uçuş, aktüel ürün
ve kültür sağlayıcıları ortak cache, health, circuit breaker, attribution ve
stale veri politikasıyla çalışır.

### Faz 4 — Kişisel üretkenlik

Kullanıcının Telegram içinde tuttuğu notlar, görevler, listeler, tekrar eden
hatırlatıcılar, sessiz saatler, günlük özet ve bildirim tercihleri MongoDB ve
Redis tabanlı kalıcı iş akışıyla tamamlanır.

### Faz 5 — AI, RAG ve kaynaklı yanıt

Provider routing, tool allowlist, dosya ingestion, Chroma, kullanıcı hafızası
opt-in, prompt injection koruması, kaynak ve maliyet metrikleri tamamlanır.

### Faz 6 — Grup, medya, quote, sticker ve oyun

Kalıcı grup ayarları, moderasyon, medya worker'ları, quote/sticker kuyrukları,
oyun state recovery, leaderboard ve ekonomi sistemi üretim kriterleriyle
etkinleştirilir.

### Faz 7 — Operasyon ve dağıtım sertleştirmesi

Windows polling, Linux webhook, Docker Compose profilleri, backup/restore,
migration, rollback, yük, güvenlik ve provider replay kontrolleri çalıştırılır.

## Kabul hedefleri

- İlk kabul mesajı p95 < 1 saniye.
- Yerel şehir sorgusu p95 < 100 ms.
- Cache hit public veri sorgusu p95 < 500 ms.
- Uygun medya işinde p95 ≤ 10 saniye.
- 10 eşzamanlı medya işi ve yaklaşık 100 aktif sohbet.
- 45 MiB üstü dosya Telegram'a gönderilmez.
- Worker yeniden başlatıldığında iş kaybı yaşanmaz.
- Secret, cookie, tam URL veya kişisel veri loglanmaz.

## Current execution checkpoint -- 2026-10-09

- Handler persistence is now explicit through HandlerDataPort; handler AST boundaries are enforced and covered by regression tests.
- Production deploy now supports an explicit no-domain polling bootstrap while preserving the fail-closed HTTPS/Nginx webhook contract.
- Telegram-facing orchestrator persistence is port-based; architecture checks permit DB access only in dependency composition.
- Durable game action claims are capability-gated through the same port, preserving callback idempotency after restart.
- Runtime feature manifest now registers local and queue-owned public surfaces (productivity, media, group moderation/personality, leaderboard and notification delivery) with the same typed permission, audit, rate-limit, timeout and kill-switch contract used by external providers.
- Private /persona and /uslup preferences now persist per user with strict private callback ownership; group personality remains administrator-scoped and safety overrides stay active.
- Personality cadence is now bounded by recent assistant turns: balanced tone receives a four-turn playful budget and needle tone a two-turn budget, while sensitive and operational topics always force a serious response.
- Provider runtime shutdown now cancels stale-while-revalidate tasks and clears in-memory cache/inflight state before HTTP transports close; a dedicated regression test covers restart-safe cleanup.
- Sticker, cookie, and pack-delete wizard state now uses user/chat-scoped Redis keys; two simultaneous chats for one user remain isolated and legacy unscoped records are accepted only in private chats.
- Bootstrap transport dispatch now has explicit polling/webhook integration tests, closing the gap between validated configuration and the actual python-telegram-bot start method.
- Latest offline+Docker gate: 857 local tests (+10 environment-dependent skips), 865 Docker tests (+2 environment-dependent skips), architecture strict 0, security replay 6/6, provider fixture load p95 5.12 ms (20 requests / 10 concurrency), latest provider load p95 15.85 ms (100 requests / 10 concurrency), deterministic media load 100/100 at 10 workers across 100 chat slots (0.90 ms p95), monitoring delivery replay 8/8, and exact five-rule Prometheus alert replay. Full Python and Markdown mojibake detection is included in the architecture gate. Clean APIs routing is now a first-class typed provider with a default `deepseek-v4-flash-0731` text-task allowlist when configured.
- Public Spatula compatibility tools now delegate to typed providers; direct scraper imports are forbidden by regression coverage and no longer exist in the compatibility module.
- Personal notification center now installs currency, fuel, crypto, news, and BİM tracking through owner-scoped inline callbacks; threshold alerts remain explicit and auditable through `/alarm`.
- Media URL replay now covers Instagram/instagr.am, TikTok, YouTube/Shorts, Twitter/X, and Reddit; registry replay verifies platform aliases, provider priority, circuit breaking, and wildcard fallback.
- Deterministic media replay matrix now covers Instagram, TikTok, YouTube Shorts, X, Facebook/Reels and Reddit; all six families pass provider selection, artifact validation and cleanup without network calls. Live availability remains deployment evidence.
- Production feature registry now fails closed for external providers whose source, robots, terms or production sign-off metadata is incomplete; manual Compose starts cannot bypass the governance guard.
- AI tool dispatch now enforces the feature manifest permission scope before execution; group AI receives a bounded chat context and cannot invoke private-only memory or group-admin tools without explicit scope.
- Hardened Telegram operational logging so private user and assistant payloads are represented only by bounded length/type summaries.
- Operational logging excludes private attachments and keeps only bounded presence/byte metadata.
- Exception logging drops traceback payloads at the redaction boundary; logs retain only stable error categories and exception types.
- Telegram polling/webhook behavior is isolated in `src/telegram_transport.py`; real python-telegram-bot `get_me` and `send_message` calls are covered by an `httpx.MockTransport` replay.
- Production preflight reuses `TelegramTransport` directly, keeping webhook URL/path/secret/listen/port validation identical to runtime startup and adding regression coverage for unsafe transport settings.
- Monitoring deployment is explicit: `MONITORING_ENABLED=true` now requires the Prometheus-facing observability endpoint and a 0600 Grafana admin password file, so the Compose placeholder cannot become a production credential.
- Monitoring delivery is now explicit: Prometheus routes to pinned Alertmanager `v0.34.1`, whose receiver URL is an external HTTPS secret file; the loopback-only canary runner proves delivery counters before a live artifact is accepted.
- A read-only Telegram staging probe now verifies identity, webhook state and published commands only after redundant live confirmation.
- Dev/prod/monitoring Compose profiles are now validated together by the release gate.
- Monitoring Compose graph is now validated with the real Compose parser; selecting only the monitoring profile resolves bot, worker, MongoDB, Redis, Prometheus and Grafana dependencies. Production readiness remains deployment-gated by real VDS webhook/reverse proxy, Instagram/X live media fixtures, external media SLO remediation, provider terms sign-off, backup/restore drill, monitoring startup, and Windows/Linux E2E.
- Telegram API replay now covers forum topic messages, callback acknowledgement, document/sticker uploads and file retrieval through the real PTB client; only the deployment-scoped live Telegram mutation remains open.
- Observability replay now covers Prometheus' Bearer authentication path; the monitoring endpoint no longer rejects the credential format used by the pinned scrape configuration.
- Webhook deployment is now fail-closed: `production_preflight.py --nginx-config` checks URL prefix, exact public location, loopback upstream path, TLS listener, secret header forwarding, and default-deny routing; the SSH release script passes the real host path.
- External media concurrency staging now covers 10 jobs over 100 synthetic chat slots; the latest host run passed 10/10 at 9,612.84 ms p95 and the latest Docker rerun passed 10/10 at 7,697.36 ms p95. A preceding Docker sample recorded a real 15,472.15 ms source-jitter breach and remains archived. Instagram/X fixtures and repeated SLO confidence remain open. Earthquake/AFAD feature freshness is now explicitly bounded at 60-second cache and 300-second freshness SLA. Every future feature must update its typed manifest, provider/source record, tests, audit policy, metrics, operations notes, and this checkpoint when the release gate changes. AI provider fallback attempts now emit bounded aggregate and provider latency/error counters alongside token/cost usage telemetry.
- Public provider staging was rerun against the bounded weather, air-quality, AFAD and currency matrix: 4/4 passed, first-call p95 551.47 ms and repeat-cache p95 0.07 ms. The report contains only source hosts and bounded latency metadata.
- Governance link health was checked with the explicit live gate: 29/29 manifest links were reachable after bounded HEAD/GET checks. No source was legally approved; terms/robots/attribution review remains pending and the bounded report is `deploy/evidence/provider-governance-links.json`.
- VDS backup/restore staging now has a gated evidence runner with namespace separation, checksum digest, symlink-safe targets, and secret-free reports; the real VDS drill remains deployment-scoped and is not represented by the local Compose smoke.
- Public HTML providers now share a streamed 2 MiB response cap, configured HTTPS host allowlist, content-type validation and stable policy error mapping; malformed or oversized upstream bodies cannot enter cache or Telegram rendering.
- Structured JSON/RSS/XML providers now share the same injected bounded transport, so redirects, private or credentialed hosts, unexpected content types and oversized bodies fail before parsing; replay coverage includes the common transport and provider adapters.
- Provider retries now classify typed transport status: malformed/policy/schema responses stop immediately while timeout, 429 and 5xx paths remain retryable and observable in circuit metrics.


### Provider governance checkpoint — 2026-10-07

Typed providers now carry explicit source-governance metadata and an offline
report (`scripts/provider_governance.py`). All current external sources remain
`pending` until terms, robots, attribution, rate-limit and production sign-off
are completed. Production runtime and preflight fail closed by default when
provider approval is missing; development and replay environments can still
exercise pending providers explicitly without treating them as approved.
