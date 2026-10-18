# Mimari Kararlar

## ADR-001 — Yerel şehir snapshot'ı

Şehir sorguları dış API'ye bağlı olmadan deterministik ve hızlı çalışmalıdır.
Bu nedenle 81 il/973 ilçe verisi sürümlü snapshot olarak tutulur ve kontrollü
script ile güncellenir.

## ADR-002 — Typed feature registry

Komut, callback, inline ve AI yüzlerinin aynı iş kuralını kullanması için
feature metadata ve provider sözleşmesi Telegram katmanından ayrılmıştır.

## ADR-003 — Harici hesap yok

Kullanıcıdan ekstra hesap, parola veya cookie kurulumu istenmez. Kişisel not,
görev ve hatırlatıcılar Telegram kimliğiyle yerel MongoDB kaydında tutulur.

## ADR-004 — GPL kod kopyalanmaz

KekikSpatula ve TRSehirler özellik kapsamı referans alınır; kaynak kodu
doğrudan kopyalanmaz. Kullanılan veri ve paketler lisans envanterine eklenir.

## ADR-005 — Uçuşlarda doğrulanmış arama bağlantıları

Google Flights ve benzeri arama sayfalarının HTML yapısı kararsızdır; scraping
ile gösterilen fiyatlar eksik veya yanlış olabilir. `/ucak` bu nedenle canlı
fiyat uydurmaz ve harici hesap istemez. Typed local provider, route ve tarih
alanlarını doğrular, sabit HTTPS host'larına arama bağlantıları üretir ve
kullanıcıya fiyatın hedef sitede doğrulanması gerektiğini bildirir.


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

`main.py` yalnızca uygulama oluşturma, lifecycle callback'leri, komut kaydı ve çalışma modunu seçer. Kullanıcıya görünen start/help yüzeyleri ve yönetim iş kuralları `Orchestrator` içinde tutulur; böylece testler Telegram Application başlatmadan çalıştırılabilir ve handler katmanı repository/provider sınırlarını ihlal etmez.

## ADR-013 - Yönetim çıktılarında güvenli HTML

## ADR-048 - EVREN geçici hata dayanıklılığı

EVREN tekil birincil sohbet sağlayıcısı olarak kullanılır; geçici 429, bağlantı
kopması ve zaman aşımı yanıtları kota tükenmesiyle aynı şey değildir. OpenAI
uyumlu adapter bu hataları sınırlı geri çekilmeyle yeniden dener ve yalnızca
upstream açıkça kota sinyali verirse uzun cooldown uygular. Yanıt gövdesi
saklanmaz veya loglanmaz; kalıcı kota/kimlik hatası kullanıcıya güvenli genel
hata ile döner.

Yönetici duyuruları ve grup listeleri Telegram HTML parse moduna girmeden önce dinamik metinleri escape eder. Yetki reddi sessizce işlenir; başarılı/başarısız yönetim işlemleri maskeli audit kayıtlarıyla izlenir.


## ADR-014 - Local coordinate resolution for public geo providers

Hava kalitesi gibi konum tabanlı public provider'lar koordinatı Telegram metninden veya kullanıcının verdiği URL'den almaz. Şehir ve plaka yerel checksum'lı snapshot'tan çözülür; böylece Türkçe karakterler, cache anahtarları ve SSRF sınırları aynı domain kuralıyla korunur.

## ADR-015 - Observable nested FSM state

Redis'teki geçici sihirbaz payload'ları JSON uyumlu ve kullanıcı kapsamıdır.
İç içe sözlük ve liste mutasyonlarını izleyen tracked container'lar kullanılır;
aksi halde bir sticker/quote adımında yapılan `append` veya `pop` işlemi state
haritasında görünürken Redis'e yazılmayabilir ve yeniden başlatma sonrası akış
bozulurdu. Memory backend yalnızca test ve geliştirme içindir.

## ADR-016 - Kullanıcı odaklı bildirim yüzeyi

Bildirim merkezi `/bildirim` ve kısa işlem akışları `/takip` üzerinden sunulur.
Eski teknik `/abonelik` alias'ı komut menüsünden kaldırıldı; kullanıcıya aynı
işi anlatan iki eşdeğer olmayan kavram gösterilmez. Grup bildirimleri yine
yalnızca admin yetkisiyle ve inline panel üzerinden yönetilir.


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

OpenAI-compatible providers such as EVREN and NVIDIA use one adapter contract
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
healthy active AI route for unrelated users.

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

## ADR-040 - Clean APIs retirement is fail-closed

Clean APIs is retired from the active Arelbot provider route by product decision. The legacy adapter and historical evidence remain only for backwards-compatible fixture inspection. Runtime routing drops stale cleanapis entries and never sends credentials or requests to that gateway. EVREN is the replacement route and uses the bounded evren-staging evidence contract; no Clean APIs live probe is part of release validation.

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
entity (for example KPSS, ÖSYM, an exam or an application) with a date/time
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

## ADR-2026-10-17 Guardian as a sidecar provider

Decision: keep Kek Guardian as a separate server-side moderation engine and do not copy its code into Arel. Only Arel remains in Telegram groups and performs Telegram actions. The current VDS process is a Telegram polling bot without the Unix/RPC bridge required by Arel, so the integration remains disabled until a real evaluator-backed bridge is deployed.

Reasons: one action authority prevents duplicate bans/deletes, the Unix socket gives a bounded local trust boundary, and the separation allows independent Guardian upgrades and rollback. The rollout is disabled -> shadow -> owner canary -> active.

## ADR-2026-10-17 Guardian command surface inside Arel

The Telegram command surface is embedded in Arel through a typed
`GuardianCommandService`. Guardian's canonical moderation names are published
by Arel and the older Turkish/Arel names are compatibility aliases only. Every
destructive operation passes through the existing authorization, idempotency
ledger and Telegram action executor. The optional `/root/guardian` process
remains an evaluator/provider and is not required for Arel to expose the
commands.
## ADR-048 — Guardian full surface port

The complete Kek Guardian command vocabulary and its group-scoped operational
features are now owned by Arel's `GuardianSurface`. The port preserves the
Guardian command names and behavior categories (administration, anti-link,
anti-flood, captcha, locks, blocklists, greetings, notes, aliases, roles,
federation state, global bans and recurring settings) while translating them
to Arel's Mongo repository, typed Telegram action executor and callback
boundary. Legacy Guardian SQLAlchemy/Telethon modules, databases and login
sessions are not imported into the bot process.

The separate VDS `kek-guardian.service` is disabled for the Arel bot token.
Running both polling processes would create duplicate updates and two action
authorities. Arel remains the only Telegram bot in groups; a future external
Guardian evaluator may still be attached through the authenticated socket
contract, but it is optional and cannot override Arel's authorization or
idempotency ledger.

## ADR-049 — Guardian compatibility depth and Telegram limits

Guardian'ın kalan operasyon komutları typed, grup kapsamlı durum değişiklikleri
ve sınırlandırılmış transport işlemleri olarak uygulanır. Admin izin profilleri,
hayalet ban, reaksiyon/medya paketi korumaları, saklama tabanlı üye inceleme,
federasyon not/log/abonelikleri, risk özetleri ve yedek içe/dışa aktarma Arel'in
repository, yetkilendirme ve audit sınırlarını kullanır. Telegram işlemi
başarısızsa komut sahte başarı mesajı üretmez.

Telegram botları geçmiş üye dizinini veya sınırsız mesaj aramasını açmaz. Bu
nedenle zombie/member-log özellikleri Arel'in saklanan aktivite penceresini,
purge ise ardışık mesaj kimliklerinden oluşan sınırlı aralığı kullanır. Bu,
gizli bir kullanıcı hesabı veya sınırsız ikinci veri deposu eklememek için
bilinçli bir protokol kararıdır.

## ADR-050 - Shared game session and round-scoped actions

Interactive games use one versioned session contract instead of separate
callback-only state machines. Every session is scoped to a chat, has bounded
players and expiry, and is persisted with compare-and-set semantics. Telegram
buttons carry a round identifier and every mutation is claimed through the
idempotent game action ledger. This prevents stale buttons, duplicate rewards
and restart races while keeping the existing word, card and social game
surfaces backward compatible.

Connect Four is the first new board game on this contract: a two-player,
six-by-seven game with a lobby, turn ownership, column validation, win/draw
detection and owner/admin cancellation. It is exposed through `/connect4`, the
button-based game menu and the feature registry.

## ADR-051 - Expanded board-game surface with XOX

XOX uses the same persisted chat-scoped state and round-scoped callback contract as Connect Four. New rounds select 6x6, 7x7 or 8x8 and require four consecutive marks; the domain still reads legacy 3x3 snapshots during recovery. Its transitions stay in `src/services/tictactoe_ui.py`; Telegram handlers only validate scope, claim the action ledger, persist state and render the next panel. This keeps a restarted process, an old inline panel, or a repeated callback from changing another round.

## ADR-052 - Versioned board metadata and atomic game recovery

Game definitions own the durable state schema. XOX and Connect Four publish a
schema version, stable state key, board dimensions and connect length in both
the runtime feature registry and each `GameSession` record. The persistence
boundary validates these fields before accepting a snapshot. In-memory and
durable implementations must expose compare-and-set saves, chat/game scoped
state keys, defensive reads and all-or-nothing recovery. This prevents a stale
or malformed callback from mutating a different board and gives migrations a
safe compatibility check before a new schema is enabled.

## ADR-053 - Gomoku core and replayable progression

Gomoku is implemented as an immutable, dependency-free Connect-N core rather
than embedding board rules in Telegram callbacks. Arel exposes bounded 6–15
boards with five-in-a-row, canonical move replay and round/message/topic
callback binding. The same persistence and action-ledger contracts used by
XOX and Connect Four protect restart recovery and duplicate callbacks.

Game rewards are accepted as bounded progression events with an idempotency
key. Cooldown, rolling-window and daily caps prevent score farming; seasonal
leaderboards and achievements are deterministic projections that can be
rebuilt from the event ledger. The progression domain has no network or
Telegram dependency, so a provider or handler cannot bypass score policy.

## ADR-054 - Guardian target resolution

Guardian kullanıcı hedefleri reply, sayısal Telegram ID veya Arel'in daha
önce gözlemlediği kullanıcı adı ile çözümlenir. Bot API genel bir kullanıcı adı
arama uç noktası sunmadığı için kullanıcı adı önce repository profilinden
bulunur, ardından aynı grup için `get_chat_member` ile doğrulanır. Kayıtlı veya
aktif üyeliği doğrulanamayan hedeflerde işlem fail-closed olur ve kullanıcıdan
hedef mesaja yanıt vermesi istenir; böylece başka gruptaki aynı görünen isim
üzerinden moderasyon yapılamaz.
