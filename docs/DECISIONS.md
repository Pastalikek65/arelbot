# Mimari Kararlar

## ADR-001 â€” Yerel ÅŸehir snapshot'Ä±

Åehir sorgularÄ± dÄ±ÅŸ API'ye baÄŸlÄ± olmadan deterministik ve hÄ±zlÄ± Ã§alÄ±ÅŸmalÄ±dÄ±r.
Bu nedenle 81 il/973 ilÃ§e verisi sÃ¼rÃ¼mlÃ¼ snapshot olarak tutulur ve kontrollÃ¼
script ile gÃ¼ncellenir.

## ADR-002 â€” Typed feature registry

Komut, callback, inline ve AI yÃ¼zlerinin aynÄ± iÅŸ kuralÄ±nÄ± kullanmasÄ± iÃ§in
feature metadata ve provider sÃ¶zleÅŸmesi Telegram katmanÄ±ndan ayrÄ±lmÄ±ÅŸtÄ±r.

## ADR-003 â€” Harici hesap yok

KullanÄ±cÄ±dan ekstra hesap, parola veya cookie kurulumu istenmez. KiÅŸisel not,
gÃ¶rev ve hatÄ±rlatÄ±cÄ±lar Telegram kimliÄŸiyle yerel MongoDB kaydÄ±nda tutulur.

## ADR-004 â€” GPL kod kopyalanmaz

KekikSpatula ve TRSehirler Ã¶zellik kapsamÄ± referans alÄ±nÄ±r; kaynak kodu
doÄŸrudan kopyalanmaz. KullanÄ±lan veri ve paketler lisans envanterine eklenir.

## ADR-005 â€” UÃ§uÅŸlarda doÄŸrulanmÄ±ÅŸ arama baÄŸlantÄ±larÄ±

Google Flights ve benzeri arama sayfalarÄ±nÄ±n HTML yapÄ±sÄ± kararsÄ±zdÄ±r; scraping
ile gÃ¶sterilen fiyatlar eksik veya yanlÄ±ÅŸ olabilir. `/ucak` bu nedenle canlÄ±
fiyat uydurmaz ve harici hesap istemez. Typed local provider, route ve tarih
alanlarÄ±nÄ± doÄŸrular, sabit HTTPS host'larÄ±na arama baÄŸlantÄ±larÄ± Ã¼retir ve
kullanÄ±cÄ±ya fiyatÄ±n hedef sitede doÄŸrulanmasÄ± gerektiÄŸini bildirir.


## ADR-006 - User notification quiet hours

Private notification subscriptions honor a user-scoped, timezone-aware quiet window. Group schedules are independent and remain controlled by group administrators. Due private jobs are deferred without claiming so provider retries and atomic delivery claims remain separate concerns.

## ADR-007 - Typed creative generation boundary

Story requests are validated by a typed provider and passed to the configured AI only as untrusted generation briefs. The provider does not scrape, store prompts, estimate external facts, or bypass the AI tool allowlist. Validation failures are deterministic and do not consume provider retry or circuit budget.

## ADR-008 - User-isolated document ingestion

Document analysis is bounded before parsing: the configured upload root,
maximum bytes, MIME/signature and DOCX archive policy are enforced by one
domain policy. Chroma records carry both user id and a SHA-256 fingerprint for
isolation and deduplication. Retrieved document text is explicitly untrusted
when passed to AI so document instructions cannot become model instructions.


## ADR-009 - Admin-only group policy controls

Group moderation controls are shared by the chat, but only Telegram creator or
administrator members may change them. The inline settings surface is backed
by the same persistent chat settings used by message moderation; callback
payloads do not carry mutable authority and are re-authorized on every click.


## ADR-010 - Idempotent game score events

Game score writes accept a bounded score and an optional immutable event id.
Mongo uses a sparse unique index for event ids; duplicate callback delivery
returns without incrementing player statistics. Quiz callbacks derive the event
from chat, message and user scope, so retries cannot create points.

## ADR-011 - Durable sticker add claims

Sticker pack callbacks may be delivered more than once and Telegram may return
an ambiguous network error after accepting a request. The bot therefore claims
each `(pack_id, file_id)` add in a unique Mongo operation record before calling
Telegram. A pending claim fails closed, while a completed claim is treated as
already applied. The catalog write is best effort after the remote success and
the operation records whether catalog reconciliation is still needed.


## ADR-012 - Telegram bootstrap boundary

`main.py` yalnÄ±zca uygulama oluÅŸturma, lifecycle callback'leri, komut kaydÄ± ve Ã§alÄ±ÅŸma modunu seÃ§er. KullanÄ±cÄ±ya gÃ¶rÃ¼nen start/help yÃ¼zeyleri ve yÃ¶netim iÅŸ kurallarÄ± `Orchestrator` iÃ§inde tutulur; bÃ¶ylece testler Telegram Application baÅŸlatmadan Ã§alÄ±ÅŸtÄ±rÄ±labilir ve handler katmanÄ± repository/provider sÄ±nÄ±rlarÄ±nÄ± ihlal etmez.

## ADR-013 - YÃ¶netim Ã§Ä±ktÄ±larÄ±nda gÃ¼venli HTML

## ADR-048 - EVREN geÃ§ici hata dayanÄ±klÄ±lÄ±ÄŸÄ±

EVREN tekil birincil sohbet saÄŸlayÄ±cÄ±sÄ± olarak kullanÄ±lÄ±r; geÃ§ici 429, baÄŸlantÄ±
kopmasÄ± ve zaman aÅŸÄ±mÄ± yanÄ±tlarÄ± kota tÃ¼kenmesiyle aynÄ± ÅŸey deÄŸildir. OpenAI
uyumlu adapter bu hatalarÄ± sÄ±nÄ±rlÄ± geri Ã§ekilmeyle yeniden dener ve yalnÄ±zca
upstream aÃ§Ä±kÃ§a kota sinyali verirse uzun cooldown uygular. YanÄ±t gÃ¶vdesi
saklanmaz veya loglanmaz; kalÄ±cÄ± kota/kimlik hatasÄ± kullanÄ±cÄ±ya gÃ¼venli genel
hata ile dÃ¶ner.

YÃ¶netici duyurularÄ± ve grup listeleri Telegram HTML parse moduna girmeden Ã¶nce dinamik metinleri escape eder. Yetki reddi sessizce iÅŸlenir; baÅŸarÄ±lÄ±/baÅŸarÄ±sÄ±z yÃ¶netim iÅŸlemleri maskeli audit kayÄ±tlarÄ±yla izlenir.


## ADR-014 - Local coordinate resolution for public geo providers

Hava kalitesi gibi konum tabanlÄ± public provider'lar koordinatÄ± Telegram metninden veya kullanÄ±cÄ±nÄ±n verdiÄŸi URL'den almaz. Åehir ve plaka yerel checksum'lÄ± snapshot'tan Ã§Ã¶zÃ¼lÃ¼r; bÃ¶ylece TÃ¼rkÃ§e karakterler, cache anahtarlarÄ± ve SSRF sÄ±nÄ±rlarÄ± aynÄ± domain kuralÄ±yla korunur.

## ADR-015 - Observable nested FSM state

Redis'teki geÃ§ici sihirbaz payload'larÄ± JSON uyumlu ve kullanÄ±cÄ± kapsamÄ±dÄ±r.
Ä°Ã§ iÃ§e sÃ¶zlÃ¼k ve liste mutasyonlarÄ±nÄ± izleyen tracked container'lar kullanÄ±lÄ±r;
aksi halde bir sticker/quote adÄ±mÄ±nda yapÄ±lan `append` veya `pop` iÅŸlemi state
haritasÄ±nda gÃ¶rÃ¼nÃ¼rken Redis'e yazÄ±lmayabilir ve yeniden baÅŸlatma sonrasÄ± akÄ±ÅŸ
bozulurdu. Memory backend yalnÄ±zca test ve geliÅŸtirme iÃ§indir.

## ADR-016 - KullanÄ±cÄ± odaklÄ± bildirim yÃ¼zeyi

Bildirim merkezi `/bildirim` ve kÄ±sa iÅŸlem akÄ±ÅŸlarÄ± `/takip` Ã¼zerinden sunulur.
Eski teknik `/abonelik` alias'Ä± komut menÃ¼sÃ¼nden kaldÄ±rÄ±ldÄ±; kullanÄ±cÄ±ya aynÄ±
iÅŸi anlatan iki eÅŸdeÄŸer olmayan kavram gÃ¶sterilmez. Grup bildirimleri yine
yalnÄ±zca admin yetkisiyle ve inline panel Ã¼zerinden yÃ¶netilir.


## ADR-017 - Global media inflight coalescing

Per-chat queue deduplication is insufficient when several chats request the same
public URL at once. The media service therefore locks the normalized URL hash
through provider work and Telegram delivery. Redis supplies the production
cross-process lock; the memory backend uses an instance-scoped asyncio lock.
Once the first delivery stores a Telegram `file_id`, waiters reuse it and do
not repeat the upstream download.


## ADR-018 - Optional multi-connection media transfer

The 10-second media target is dominated by upstream byte transfer, so the
Docker runtime includes aria2c as an optional yt-dlp downloader. It is enabled
only when explicitly allowed and the binary is discoverable; Windows and
minimal installations safely fall back to yt-dlp. The provider still owns
format, TLS, public-host, size and cleanup policies, so acceleration cannot
become a certificate or SSRF bypass.

## ADR-019 - Separate media SLO from cancellation timeout

The 10-second media target is a completion SLO, not a hard cancellation deadline. Public providers can exceed that budget because of source throttling or network variance; cancelling at exactly ten seconds would turn a measurable latency breach into an avoidable failed job. MEDIA_COMPLETION_SLO_SECONDS records the breach and sends bounded progress updates, while MEDIA_JOB_TIMEOUT_SECONDS provides a separate hard ceiling (60 seconds by default). The job ID remains the recovery and support reference throughout the extended attempt.

## ADR-020 - Bounded public HTML provider input

Public HTML sources are treated as untrusted transport input even when their
URL is configured by Arelbot. Shared provider transport accepts only the
configured HTTPS host, does not follow redirects, checks the content type, and
streams at most 2 MiB of decoded response bytes into BeautifulSoup. A rejected
body produces a stable policy error, is excluded from cache and is rendered to
users only as a safe availability message. Fixture tests cover host, content
type and size failures so source changes cannot turn a scraper into an
unbounded memory or SSRF surface.

## ADR-021 - First-class EVREN provider identity

EVREN is OpenAI-compatible and is kept as a distinct `evren` provider instead
of being aliased to another gateway. This preserves provider-level key
rotation, circuit state, health telemetry, usage accounting and explicit task
routing. The default text task allowlist selects `deepseek-v4.1-flash`; an
explicit `AI_PROVIDER_ROUTES` value can override it. No credential is committed
or written to a report.

## ADR-022 - One bounded transport for structured public data

Typed JSON, RSS and XML providers share `ProviderHttpTransport`. Each adapter
still owns its endpoint allowlist and schema, while the transport owns HTTPS,
credential/private-host rejection, redirect refusal, content-type validation,
streaming response limits and stable parse errors. Keeping those invariants in
one injected seam makes replay tests representative and prevents a new public
provider from accidentally reintroducing an unbounded or redirect-following
HTTP call.

## ADR-023 - Retry only recoverable provider failures

Provider runtime retries are reserved for connection failures and transient
upstream statuses such as 429 and 5xx. Content-type, redirect, host-policy,
size, JSON and schema failures are deterministic for the same request and stop
after the first attempt. Typed transport errors carry the status and retry
classification, while compatibility adapters can use stable error-code suffixes
without exposing upstream response bodies.

## ADR-024 - Bounded OpenAI-compatible AI transport

OpenAI-compatible providers, including Clean APIs, use one adapter contract
that rejects non-HTTPS, credential-bearing, private or query-bearing base URLs
before a request is made. Completion messages, response text, transcription
output, embeddings and token budgets are bounded and validated. Deterministic
caller input errors do not cool down a valid API key; malformed upstream
protocol responses do contribute to provider health and router failover.

## ADR-025 - Explicitly disabled providers may coexist with production approval

Production still requires source governance for every enabled external
provider. A deployment may list a pending feature in `DISABLED_FEATURES` while
its terms, robots and attribution review is outstanding; the preflight passes
that exact list to the governance checker and records the feature as
`disabled`. Unknown names fail closed, disabled features are blocked before
network work by the runtime registry, and the mechanism never marks a source
approved. This permits a staged core deployment without turning the approval
gate into an opt-out switch.

## ADR-026 - User-facing failures use controlled error messages

Telegram and AI tool boundaries must never echo arbitrary exception text.
Provider, filesystem and upstream messages can contain URLs, paths or
credentials even when an exception looks harmless. Handlers log only the
redacted exception type and return a bounded fallback; controlled job errors
may select a message from an explicit stable-code map. Validation messages
remain owned by their parser and are escaped before HTML delivery.

## ADR-027 - Polling is an explicit no-domain production transport

The production contract supports both webhook and polling, but never infers
one from missing values. Webhook requires the HTTPS URL, secret, transport
contract and Nginx proxy check. Polling requires no public domain and skips
those proxy checks; the deployer selects this path only when the environment
sets `TELEGRAM_MODE=polling`. This keeps first boot possible on a private VDS
without weakening the hardened webhook path.
## ADR-028 - One renderer for typed feature result metadata

All Telegram surfaces that display a `FeatureResult` use
`src/services/feature_presentation.py`. The formatter sanitizes provider content,
accepts only credential-free HTTP(S) source links, and appends stale/freshness
status plus Turkey-localized fetch time from the same envelope. Command,
callback and scheduled-delivery paths therefore cannot silently disagree about
source attribution or stale-cache warnings.
## ADR-029 - Explicit owner configuration is tracked separately from value

Production owner validation checks whether `OWNER_ID` was supplied in the
runtime environment, not whether its numeric value differs from a historical
development fallback. This prevents rejecting a legitimate operator whose
Telegram ID happens to equal that old fallback while still failing closed when
production starts without an explicit owner setting.
## ADR-030 - Fresh VDS env bootstrap is explicit and non-overwriting

The SSH deployer can receive a local production env file only through the
explicit `--bootstrap-env-file` option. It validates a regular UTF-8 file,
uploads it to a temporary remote path, and the remote installer installs it
with mode 0600 only when the shared env is absent. Existing remote state wins;
secrets are never printed, archived, or embedded in the release. The temporary
remote copy is removed on every exit path.

## ADR-031 - The feature catalog is a release-gated projection

The runtime `FeatureRegistry` remains the source of truth for public feature
metadata. `docs/FEATURE_CATALOG.md` is the operator-facing projection and must
contain exactly one row for every runtime key. `scripts/feature_catalog_audit.py`
compares the live registry definitions with the Markdown table without calling
external services; missing, undocumented, or duplicate rows fail
`scripts/release_gate.py`. This keeps command, permission, provider and cache
documentation from drifting silently as the bot grows.


## ADR-032 - User-facing Turkish text is regression-tested

Telegram handlers are a user-facing localization boundary, so common ASCII-only
Turkish regressions are treated as release defects rather than cosmetic cleanup.
`src/tests/test_turkish_user_text.py` parses every Telegram handler module's
string constants and rejects known lost-diacritic fragments, including malformed
variants that can pass a visual review. The audit is intentionally scoped to
display phrases and leaves protocol identifiers, callback payloads and aliases
ASCII-compatible. Any new handler message must pass this test before release.


## ADR-033 - AI system time is host-independent

The system timestamp included in ChatEngine prompts is product data, not a
property of the machine running the bot. `turkey_now()` converts an aware UTC
instant to `Europe/Istanbul` and treats naive test values as UTC. ChatEngine uses
that helper instead of `datetime.now()`, so a UTC VDS and a Windows developer
machine produce the same Turkish date and weekday context. User-specific
reminders keep their own persisted timezone rules; this decision only governs
the default assistant context.


## ADR-034 - Manual web search uses an explicit untrusted boundary

The `/ara` and `/search` Telegram path keeps the user's query as the model
request and passes provider output through ChatEngine's `context` boundary.
That context is escaped and marked untrusted before it reaches the model, so a
search result cannot become a system or user instruction. The path resolves the
same private or group personality mode as normal chat, and command matching uses
the raw text case-insensitively so Turkish-aware display normalization cannot
turn `/ara` into an ordinary chat message.


## ADR-035 - The current Telegram turn is not duplicated in AI history

The message handler records user messages before dispatching them to ChatEngine,
while ChatEngine appends the current request as a fresh user turn.
`history_without_current_user_turn()` removes only a matching tail entry (and
understands `/ara` and `/search` prefixes), preserving earlier identical
questions and assistant tails. This keeps context windows honest, avoids
double-counting tokens, and makes manual search and normal chat follow the same
conversation contract.

## ADR-036 - Deterministic AI request errors do not consume provider failover

`AIProviderRequestError` represents caller-controlled bounds and schema inputs
that cannot become valid on another provider. The AI router raises this typed
error immediately, records a separate validation metric, and leaves circuit
health and fallback order unchanged. Upstream protocol or transport failures
remain provider failures and continue to participate in health scoring and
failover. This prevents one oversized or malformed request from disabling a
healthy Clean APIs/DeepSeek route for unrelated users.

## ADR-037 - User labels use one bounded turn envelope

Telegram display names are untrusted presentation data. `safe_user_label()`
removes control characters and envelope delimiters, collapses whitespace, and
limits the label to 80 characters. `format_user_turn()` is used both when a
message is persisted and when the current model turn is appended. This keeps
conversation deduplication deterministic and prevents a display name from
creating a second-looking role boundary or an oversized AI request.

## ADR-038 - Legacy log sink uses exception summaries

The compatibility `UnifiedLogger` is still reachable during operational
logging, so its failure path must follow the same privacy boundary as the
structured logger. It records only `summarize_exception()` output; raw driver,
URL, path and user payload text never enters the log message.

## ADR-039 - Media boundary errors use stable public codes

Media URL validation, cookie management, download and transcoding exceptions can
carry provider URLs, paths or driver text. Telegram-facing media handlers now
pass these failures through `src.media.error_messages`, which maps only bounded
error codes to Turkish user messages. Cookie parser feedback is retained only
when it passes the shared validation boundary. Unknown provider text falls back
to a generic message, while the original typed exception remains available to
internal retry and telemetry paths.

## ADR-040 - Clean APIs live checks archive metadata, never model content

The configured Clean APIs/DeepSeek route is a production dependency, so a
successful unit fixture is not enough to prove its deployment configuration.
`scripts/cleanapis_staging.py` provides an explicit three-signal live gate and
sends one bounded fixed prompt. Its evidence records only provider identity,
model identity, latency, response size and whether usage metadata exists. API
keys, prompts, completions and upstream exception text are excluded. The probe
is a connectivity/configuration signal and does not imply the separate source
terms or production-governance approval required by the release preflight.

## ADR-041 - Provider approval reviews use a non-loadable worksheet

Operators need a repeatable way to compare a human source review with the
exact feature/provider identity that production will execute. The
`--review-template` command emits a URL-free worksheet with a source
fingerprint and current governance fields, but deliberately omits approval
IDs, reviewer identity and validity dates. The worksheet therefore cannot be
loaded as an approval manifest, and it cannot weaken the fail-closed
production gate. A completed, separately reviewed manifest remains the only
artifact that can promote a provider.

## ADR-042 - Personality uses a bounded turn cadence

Prompt instructions alone cannot reliably stop a model from adding a joke to
every answer. `ChatEngine` therefore supplies the number of recent assistant
turns to `PersonalityPolicy`. Balanced mode permits a playful line every four
turns and needle mode every two turns; serious, sensitive and operational
branches bypass the playful path entirely. The cadence is advisory guidance,
not response rewriting, so factual content and user intent remain untouched
while repetitive sarcasm is discouraged before provider routing.

## ADR-043 - Current-information chat uses governed web evidence

Natural-language questions that combine a high-signal current-information
entity (for example KPSS, Ã–SYM, an exam or an application) with a date/time
request are routed through the typed `web_search` feature. The chat engine
prefetches one bounded result when possible, labels it as untrusted external
context, and still exposes the allowlisted search tool for clarification.
The system instruction requires source and retrieval time in the answer and
forbids guessing when fresh evidence is unavailable. Domain-specific typed
features such as weather, earthquake, currency and pharmacy remain the
authoritative route for their own commands.

## ADR-044 - Personal daily digest is opt-in and claim-based

The daily digest is exposed from the main inline menu and `/gunluk_ozet`.
Users choose the city and local delivery time, and the feature remains off by
default. A MongoDB atomic local-date claim prevents duplicate delivery after a
restart; quiet hours defer the digest. The message combines typed weather,
earthquake and news results with the existing productivity summary, while a
provider failure degrades only that section.

## ADR-045 - Photo handling requires an explicit action

Incoming photos no longer select a processing mode implicitly. A scoped,
short-lived state presents analysis, OCR, sticker and cancel actions; only the
owner can consume it, and the selected work is submitted to the attachment
queue. This avoids surprising sticker generation and keeps Telegram handlers
free of downloads or vision work.


## ADR-046 - Group replies are message-id targeted fan-out

Group context is not a single conversational stream. When Arel is triggered,
the application builds a bounded, chat/topic-scoped snapshot and selects at
most the newest eligible message for three users. EVREN returns a JSON plan
whose targets must be Telegram message IDs from that snapshot. The router
rejects unknown, duplicate, stale and multi-address targets, so a model cannot
redirect a reply by inventing a username or Telegram ID. Each accepted segment
is delivered as its own Telegram reply and persisted only after delivery.

The Mongo message attribution fields and migration v15 remain backward
compatible. Redis is only the short-lived recent-message index; Mongo remains
the recovery source of truth. A delivery ledger claim is the idempotency
boundary across update retries and worker restarts. Uncertain Telegram sends
are not replayed automatically, while a confirmed missing reply target is
skipped with a bounded fallback opportunity for the current trigger.

## ADR-047 - Passive GIFs and group media failures stay quiet

Telegram animations are treated as passive attachments. A GIF does not enter
vision, document or sticker processing unless the user explicitly replies with
the relevant command. A caption containing a supported social URL remains an
explicit media download request. Media jobs carry the chat scope into the
worker; group failures receive bounded provider retries and a neutral progress
update, then the status message is removed after the final attempt. Private
chats retain the safe, mapped error message. This keeps provider diagnostics
out of group timelines without weakening SSRF, file-size or public-source
policies.
