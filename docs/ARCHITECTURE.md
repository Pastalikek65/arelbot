# Arelbot Mimari SÃ¶zleÅŸmesi

```text
Telegram update
  -> router / permission
  -> feature registry veya job queue
  -> domain service / provider
  -> FeatureResult
  -> Telegram formatter / AI response
```

`src/features` ortak typed Ã§ekirdektir. `FeatureDefinition` komut, kategori,
izin, provider, provider_kind, cache, freshness, timeout ve audit bilgilerini taÅŸÄ±r.
`provider_kind=data` typed `DataProvider` registry'sinde doÄŸrulanÄ±r; `provider_kind=ai`
model rotasÄ±nÄ± belirtir ve veri saÄŸlayÄ±cÄ±sÄ± sÃ¶zleÅŸmesine zorla sokulmaz.
`DataProvider` dÄ±ÅŸ kaynaÄŸÄ± soyutlar. `FeatureResult` source, fetched_at,
stale, cache_hit, correlation_id ve gÃ¼venli hata kodu verir.

Provider runtime cache veya fallback sonucu dÃ¶ndÃ¼rdÃ¼kten sonra
`FeatureExecutor`, payload'Ä± provider'Ä±n `normalize()` sÃ¶zleÅŸmesinden geÃ§irir.
Bu adÄ±m bÃ¼tÃ¼n Telegram, inline ve AI Ã§aÄŸrÄ±larÄ±nda ortak ÅŸemayÄ± garanti eder;
normalizasyon hatasÄ± gÃ¼venli bir `PROVIDER_SCHEMA_INVALID` sonucu ve health kaydÄ±
Ã¼retir, ham upstream exception kullanÄ±cÄ±ya taÅŸÄ±nmaz.

Telegram, AI, inline ve callback yÃ¼zleri aynÄ± domain servislerini Ã§aÄŸÄ±rÄ±r.

## ChatFeaturePolicy ve kapsam izolasyonu

Quote ve medya indirme anahtarlarÄ± `ChatFeaturePolicy` Ã¼zerinden Ã§Ã¶zÃ¼lÃ¼r.
`quotes`/`quote` takma adÄ± `q_enabled`, `media`/`video` takma adÄ±
`media_enabled` alanÄ±na normalize edilir. Bu alanlar MongoDB'de sohbetin
`_id` alanÄ± altÄ±nda tutulur; bir gruptaki toggle baÅŸka bir grup veya Ã¶zel
sohbetin davranÄ±ÅŸÄ±nÄ± deÄŸiÅŸtirmez. Komut, otomatik link, caption, callback ve
inline quote yollarÄ± aynÄ± policy kapÄ±sÄ±ndan geÃ§er. KapalÄ± bir feature komutu
sessizce yok sayÄ±lÄ±r; medya kuyruÄŸuna veya quote repository'sine iÅŸ bÄ±rakÄ±lmaz.
YÃ¶netici toggle'larÄ± bounded audit kaydÄ±yla izlenir ve Telegram callback'i
ikinci kez cevaplama hatasÄ± kullanÄ±cÄ±ya taÅŸÄ±nmaz.

`src/video_downloader.py` ve `src/tiktok_downloader.py` eski dÄ±ÅŸ entegrasyonlar
iÃ§in yalnÄ±zca uyumluluk yÃ¼zÃ¼dÃ¼r. Bu modÃ¼ller HTTP istemcisi, hardcoded
saÄŸlayÄ±cÄ± veya kendi limit politikasÄ±nÄ± iÃ§ermez; `YtDlpProvider` sÃ¶zleÅŸmesine
delege eder. Yeni Telegram akÄ±ÅŸlarÄ±nda doÄŸrudan bu sÄ±nÄ±f yerine kuyruklu
`MediaDownloadService` kullanÄ±lÄ±r. Architecture check bu sÄ±nÄ±rÄ± ve eski
Cobalt/TikWM bypasslarÄ±nÄ± otomatik olarak reddeder.

Bildirim teslimatÄ± `SubscriptionStore` iÃ§indeki atomik claim lease'i ile
yÃ¼rÃ¼tÃ¼lÃ¼r. AFAD, haber ve aktÃ¼el Ã¼rÃ¼n akÄ±ÅŸlarÄ± iÃ§in iÃ§erik fingerprint'i
`last_fingerprint` alanÄ±nda tutulur; aynÄ± provider payload'Ä± yeniden geldiÄŸinde
plan ileri alÄ±nÄ±r ama Telegram'a ikinci kez gÃ¶nderilmez. Hava ve namaz gibi
zaman bazlÄ± akÄ±ÅŸlar tekrar politikasÄ±nÄ± korur; eÅŸik alarmlarÄ± yalnÄ±zca gÃ¶zlenen
payload deÄŸiÅŸtiÄŸinde tetiklenir.
Grup analitiÄŸi de `GroupAnalyticsService` Ã¼zerinden Ã§alÄ±ÅŸÄ±r; Mongo aggregate
sorgularÄ± `Database.get_group_analytics` repository sÄ±nÄ±rÄ±nda tutulur. Komut
Ã¶ncesi grup ve yÃ¶netici yetkisi tekrar doÄŸrulanÄ±r, kullanÄ±cÄ± adlarÄ± escape edilir.
Provider baÅŸarÄ±sÄ±z olduÄŸunda cache policy stale sonucu aÃ§Ä±k uyarÄ±yla sunabilir
veya iÅŸi queue'ya devredebilir. Handler doÄŸrudan scraper Ã§alÄ±ÅŸtÄ±rmaz.

MongoDB kalÄ±cÄ± iÅŸ, kullanÄ±cÄ±, mesaj, not, gÃ¶rev, reminder, cache ve audit
kayÄ±tlarÄ±nÄ±n kaynaÄŸÄ±dÄ±r. Redis kÄ±sa durum, queue, rate limit ve sÄ±cak cache
iÃ§in kullanÄ±lÄ±r. Medya dosyasÄ± yalnÄ±zca iÅŸ sÃ¼resince disk Ã¼zerinde tutulur.

Sihirbaz durumlarÄ± `PersistentStateMap` Ã¼zerinden kullanÄ±cÄ± + sohbet kapsamÄ±nda ve
TTL'li state store'a yazÄ±lÄ±r. AynÄ± kullanÄ±cÄ±nÄ±n iki grup veya Ã¶zel sohbetteki
oturumlarÄ± ayrÄ± Redis anahtarlarÄ±nda tutulur; callback baÄŸlamÄ± eÅŸleÅŸmiyorsa
durum fail-closed olarak yok sayÄ±lÄ±r. Durum payload'larÄ± JSON ÅŸekillidir; iÃ§ iÃ§e sÃ¶zlÃ¼k ve liste
mutasyonlarÄ± da izlenir, bÃ¶ylece sticker/quote/cookie adÄ±mlarÄ±nda `append`,
`pop` veya alan gÃ¼ncellemesi restart Ã¶ncesi kaybolmaz. Redis Ã¼retimde tek
paylaÅŸÄ±lan kÄ±sa durum kaynaÄŸÄ±dÄ±r; memory store yalnÄ±zca geliÅŸtirme/test
Ã§alÄ±ÅŸtÄ±rmalarÄ±nda kullanÄ±labilir.

Feature provider results use a two-level cache: a process-local hot cache and,
when the production queue has a Redis connection, a namespaced Redis cache.
Cache payloads retain source and fetch timestamps so a shared hit remains
traceable and stale results can be labelled at the Telegram boundary.

Weather requests carry a bounded forecast horizon (`days` 1â€“7). The wttr.in
adapter emits a stable daily forecast list and compact derived rain/wind
summaries; Telegram callbacks only select a horizon and never alter the
notification subscription. Group forecast callbacks remain view-only while
group notification controls are re-authorized for administrators.
## Native provider geÃ§iÅŸi

Native provider geÃ§iÅŸi feature anahtarÄ±nÄ± deÄŸiÅŸtirmeden yapÄ±lÄ±r. Registry Ã¶nce
`DataProvider` uyumlu typed provider'Ä± seÃ§er; public Spatula araÃ§larÄ±nÄ±n eski
`BaseTool` yÃ¼zeyi de artÄ±k yalnÄ±zca typed provider delegasyonu yapar ve doÄŸrudan
HTTP/scraper iÃ§ermez. BÃ¶ylece kontrollÃ¼ migration fallback aÃ§Ä±ldÄ±ÄŸÄ±nda bile
ikinci bir veri Ã§ekme politikasÄ± oluÅŸmaz. `ProviderRuntime` timeout, retry,
circuit breaker, request coalescing ve stale-while-revalidate uygular. Provider
Ã§aÄŸrÄ± saÄŸlÄ±ÄŸÄ± repository Ã¼zerinden `provider_health` koleksiyonuna yazÄ±lÄ±r;
handler'lar MongoDB veya HTTP istemcisine eriÅŸemez.

Transport policy/schema failures are classified as deterministic and consume
one provider attempt only. Transient upstream statuses (timeouts, 429 and
5xx) retain bounded retry and circuit accounting. This distinction keeps a
broken source from receiving duplicate requests while still allowing a
short-lived outage to recover within the feature deadline.

Ãœretim varsayÄ±lanÄ±nda `LEGACY_PROVIDER_FALLBACK_ENABLED=false` olduÄŸu iÃ§in
baÅŸarÄ±sÄ±z typed Ã§aÄŸrÄ± sessiz bir scraper baÅŸlatmaz; kullanÄ±cÄ± kontrollÃ¼ hata veya
stale cache gÃ¶rÃ¼r. Fallback yalnÄ±zca development/staging replay iÃ§in aÃ§Ä±labilir
ve `legacy` metadata ile gÃ¶zlemlenir. Zincir, seÃ§ilen provider'Ä±n `normalize()`
adÄ±mÄ±nÄ± ortak zarfa almadan Ã¶nce Ã§alÄ±ÅŸtÄ±rÄ±r; doÄŸrudan komut, AI aracÄ± ve migration
yÃ¼zeyi aynÄ± typed payload sÃ¶zleÅŸmesini korur.

RSS haber Ã¶zeti de aynÄ± kurala uyar: `RssNewsProvider` XML ayrÄ±ÅŸtÄ±rmayÄ±,
`ProviderRuntime` cache/retry/circuit/stale-while-revalidate politikasÄ±nÄ±,
`FeatureExecutor` ise ortak `FeatureResult` zarfÄ±nÄ± saÄŸlar. `/digest` handler'Ä±
network istemcisi oluÅŸturmaz; yalnÄ±zca provider sonucunu AI Ã¶zetine veya Telegram
HTML gÃ¶rÃ¼nÃ¼mÃ¼ne biÃ§imlendirir.

## Shared structured-data transport

JSON, RSS and XML providers use `src/features/providers/transport.py` before
parsing any upstream body. `ProviderHttpTransport` validates HTTPS, rejects
credentials/private hosts and unexpected hosts, refuses redirects, checks
content type, enforces a streamed response-byte limit, and converts malformed
JSON into stable provider error codes. The client remains dependency-injected
for fixture/replay tests; provider parsers receive only bounded bytes or a
decoded JSON value. This keeps weather, air quality, crypto, culture, market,
AFAD, RSS and Tavily adapters under the same SSRF and memory budget without
putting network policy in Telegram handlers.

## Grup duyurularÄ±

PlanlÄ± grup duyurularÄ± `group_announcement` feature kaydÄ±yla tanÄ±mlanÄ±r.
Metin ve zaman `AnnouncementService` tarafÄ±ndan sÄ±nÄ±rlandÄ±rÄ±lÄ±r; yÃ¶netici
yetkisi her komutta ve callback'te Telegram'dan yeniden doÄŸrulanÄ±r. KayÄ±tlar
genel `SubscriptionStore` iÃ§inde `scope=group` olarak tutulur. Atomic claim,
lease expiry ve `next_run_at` hesaplamasÄ± worker restart'Ä±nda kopya duyuruyu
Ã¶nler. KullanÄ±cÄ± metni Telegram HTML parse moduna girmeden Ã¶nce escape edilir.

## Ã‡ok modlu AI

Sticker render da aynÄ± aÄŸÄ±r iÅŸ sÄ±nÄ±rÄ±nÄ± kullanÄ±r: `StickerRenderService`
static gÃ¶rÃ¼ntÃ¼yÃ¼ Pillow ile, video gÃ¶rÃ¼ntÃ¼yÃ¼ FFmpeg ile bounded semaphore
altÄ±nda iÅŸler. Handler iÃ§inde codec, boyut veya geÃ§ici dosya yÃ¶netimi yoktur;
artifact yalnÄ±zca doÄŸrulama sonrasÄ± Telegram'a gÃ¶nderilir.

TTS Ã§aÄŸrÄ±larÄ± `SpeechProvider` ve `TTSService` Ã¼zerinden ilerler. `edge-tts`
yalnÄ±zca adapter katmanÄ±nda import edilir; handler'lar saÄŸlayÄ±cÄ± SDK'sÄ±na
eriÅŸmez. Ãœretim dosyasÄ± sÄ±nÄ±rlÄ± spool dizinindeki gizli `.part` dosyasÄ±na
yazÄ±lÄ±r; boyut ve saÄŸlayÄ±cÄ± yolu doÄŸrulandÄ±ktan sonra atomik olarak yayÄ±nlanÄ±r.
`StickerPackService` paket yÃ¶netimini owner kapsamÄ±yla sÄ±nÄ±rlar; `/stpack`
komutu yalnÄ±zca bu servisi biÃ§imlendirir ve silme iÅŸlemi kullanÄ±cÄ± kimliÄŸine
baÄŸlÄ± inline onaydan sonra yapÄ±lÄ±r. Telegram API baÅŸarÄ±sÄ±z olursa MongoDB
kaydÄ± korunur.
Quote Ã¼retimi `QuoteRenderService` Ã¼zerinden bounded thread kuyruÄŸunda Ã§alÄ±ÅŸÄ±r;
handler yalnÄ±zca doÄŸrulanmÄ±ÅŸ artifact'i Telegram'a gÃ¶nderir. `.part` dosyalarÄ±
timeout ve gÃ¶nderim hatalarÄ±nda temizlenir, baÅŸarÄ±lÄ± Ã§Ä±ktÄ± atomik olarak yayÄ±nlanÄ±r.
Oyun puanlarÄ± `event_id` ile idempotent kaydedilir; aynÄ± idempotency key canonical
`game_progress_events` ledger'Ä±na tek kez eklenir. Sezon tablosu ve baÅŸarÄ±mlar
event ledger'Ä±ndan aggregate edilir, bÃ¶ylece ayrÄ± sayaÃ§ projeksiyonlarÄ±nÄ±n
restart sÄ±rasÄ±nda ayrÄ±ÅŸma riski azaltÄ±lÄ±r. Sezonlar UTC Ã§eyreklerine gÃ¶re
isimlendirilir, leaderboard chat kapsamÄ±yla sorgulanÄ±r.
Semaphore, timeout ve provider health kontrolleri aynÄ± serviste tutulur.

AIProviderRouter aynÄ± circuit/fallback sÃ¶zleÅŸmesiyle vision, transcribe ve
embedding gÃ¶revlerini de yÃ¶nlendirir. OpenAI-compatible adapter; JSON chat,
multipart transcription ve embedding response zarflarÄ±nÄ± normalize eder.
Clean APIs, `CLEANAPIS_*` ayarlarÄ±yla bu adapter'a baÄŸlanan ayrÄ± bir typed
provider kimliÄŸidir. `CLEANAPIS_KEY` mevcutsa boÅŸ route yapÄ±landÄ±rmasÄ±nda
`chat`, `fast`, `analysis`, `summary` ve `sticker_tagging` gÃ¶revleri
`deepseek-v4-flash-0731` modeline yÃ¶nlendirilir; aÃ§Ä±k `AI_PROVIDER_ROUTES`
allowlist'i bu varsayÄ±lanÄ± override eder. Key, model, latency, circuit ve usage
telemetrisi diÄŸer provider'lardan ayrÄ± tutulur. Provider terms ve production
sign-off tamamlanmadan Ã¼retim registry'si bu rotayÄ± fail-closed bÄ±rakÄ±r.
Adapter ayrÄ±ca provider URL'sini HTTPS, credential, private-host, query ve
fragment politikalarÄ±ndan geÃ§irir; mesaj, yanÄ±t, transkript, embedding ve
`max_tokens` payload'larÄ± bounded sÃ¶zleÅŸmeyle doÄŸrulanÄ±r. KullanÄ±cÄ± girdisi
olan deterministic limit hatalarÄ± key circuit'ini bozmaz; upstream protokol
ÅŸema hatalarÄ± health ve failover telemetrisi olarak kaydedilir.
Yerel medya dosyalarÄ± symlink, boÅŸ iÃ§erik ve boyut sÄ±nÄ±rÄ±ndan geÃ§meden dÄ±ÅŸ
saÄŸlayÄ±cÄ±ya gÃ¶nderilmez. Vision/STT servisleri typed yol baÅŸarÄ±sÄ±z olduÄŸunda
geriye dÃ¶nÃ¼k ChatEngine akÄ±ÅŸÄ±nÄ± kontrollÃ¼ olarak kullanÄ±r.

Handler boundary rule now also rejects `._db` access statically. Quote source
lookup and sticker-tag lookup go through repository methods, so Mongo collection
objects are not exposed to Telegram handlers.

User display names are treated as untrusted labels. `safe_user_label()`
removes control characters and turn-envelope delimiters and applies an 80
character bound; `format_user_turn()` is shared by persistence and the current
AI request so history deduplication cannot diverge from prompt assembly.

## Notification delivery ledger

`SubscriptionStore` subscription claim'ini Telegram gonderim claim'inden ayirir. Her calisma penceresi `subscription_id:next_run_at` anahtariyla `notification_deliveries` koleksiyonunda tutulur. Durumlar `pending`, `sending`, `sent`, `skipped`, `failed` ve `uncertain` olabilir. `sending` durumunda worker restart'i Telegram sonucunu kesinlestiremezse ayni anahtar replay edilmez; subscription satiri kontrollu bicimde tamamlanir. Unique index ve 30 gunluk TTL Mongo'da, async lock ve ayni durum makinesi memory backend'de uygulanir.

### Market chart render

`/borsa` provider sonucu iÃ§indeki sÄ±nÄ±rlÄ± 7 gÃ¼nlÃ¼k noktalar `MarketChartService` Ã¼zerinden bounded worker-thread ile PNG grafiÄŸe Ã§evrilebilir. Matplotlib yoksa feature metin Ã¶zeti gÃ¶nderilir; handler doÄŸrudan grafik kÃ¼tÃ¼phanesine baÄŸlanmaz. Renderer concurrency, timeout ve Ã§Ä±ktÄ± boyutu sÄ±nÄ±rlarÄ± servis sÃ¶zleÅŸmesindedir.


## Provider observation history

`FeatureExecutor` provider saglik yazimini non-blocking yapar. Repository son snapshot'i `provider_health` koleksiyonunda tutarken, kaynak/freshness gozlemini `provider_observations` koleksiyonunda `provider + fetched_at` unique anahtariyla saklar. Cache hit ayni fetched timestamp'i kullandigi icin duplicate tarihsel kayit olusmaz. `observed_at` alaninda 30 gunluk TTL, provider/tarih sorgu indexleri ve geri alinabilir ikinci migration vardir; observation payload'i allowlist disinda kullanici girdisi, secret veya ham provider govdesi tasimaz.


### Sticker katalog sÄ±rasÄ±

Telegram sticker sÄ±rasÄ± deÄŸiÅŸikliÄŸi remote API baÅŸarÄ±lÄ± olduktan sonra repositoryâ€™ye yazÄ±lÄ±r. `sticker_pack_position` indeksi pack kapsamÄ±nda sÄ±ralama sorgularÄ±nÄ± hÄ±zlandÄ±rÄ±r; migration v3 aÃ§Ä±k rollback iÅŸlemi taÅŸÄ±r.


### Quote seÃ§enek sÃ¶zleÅŸmesi

`QuoteRenderService` tema, renk, Ã¶lÃ§ek, emoji ve boolean seÃ§eneklerini render kuyruÄŸuna girmeden Ã¶nce normalize eder; bilinmeyen anahtarlar atÄ±lÄ±r ve Ã¶lÃ§ek `0.5-2.0` aralÄ±ÄŸÄ±nda tutulur. `qtemplate` kayÄ±tlarÄ± aynÄ± normalizer ve 32 karakterlik isim kuralÄ±ndan geÃ§er; bÃ¶ylece Mongo, bellek ve Telegram yÃ¼zeyleri aynÄ± davranÄ±ÅŸÄ± paylaÅŸÄ±r.


### KalÄ±cÄ± batch quote kaydÄ±

Quote kaydÄ± ilk mesajla sÄ±nÄ±rlÄ± kalmaz: en fazla 10 kaynak mesajÄ±, render seÃ§enekleri, mesaj sayÄ±sÄ± ve Ã§Ä±ktÄ± tÃ¼rÃ¼ bounded kayÄ±t olarak saklanÄ±r. Metin ve ad alanlarÄ± sÄ±rasÄ±yla 4000 ve 128 karakterle sÄ±nÄ±rlÄ±dÄ±r; bÃ¶ylece batch quote restart sonrasÄ± yeniden incelenebilirken sÄ±nÄ±rsÄ±z payload depolanmaz.


### Multimodal personality boundary

Typed vision requests carry a bounded prompt envelope that labels the user's image request as untrusted input and appends `PersonalityPolicy.modality_guidance("vision")`. This keeps the sensitive-topic rule (no sarcasm or needless emoji) and the requirement to separate visible facts from guesses on provider adapters that do not receive the full ChatEngine message list.

Speech-to-text is deliberately split into two stages: the provider returns a faithful, 4000-character bounded transcript; the transcript is then sent through `ChatEngine.get_response` with the normal personality, tool allowlist, memory and prompt-boundary policies. If that second stage is unavailable, the unmodified transcript is returned. This prevents speech recognition from silently changing user content while keeping voice replies consistent with text chat.

Group tone is a persisted administrator setting rather than a user-controlled
prompt. `serious`, `balanced`, and `needle` modes are normalized by
`PersonalityPolicy`; a scoped policy is created per response without mutating
the global environment configuration. Sensitive, operational, legal, health,
and crisis markers always disable sarcasm, and the needle may target only a
situation or typo, never a person.

The policy also receives the number of recent assistant turns. Balanced mode
allows a playful line only on a four-turn cadence; needle mode uses a
two-turn cadence. The prompt contract therefore limits repetition before the
model sees the request, while sensitive and operational branches still win
over cadence. This is a tone budget, not a license to be rude: the model must
still target a situation or typo, never a person.

Local and queue-owned capabilities use the same feature registry as network
providers. Their manifests still declare permission scope, timeout, rate limit,
audit policy and kill-switch metadata even when no external provider is
involved. This keeps `/features`, AI dispatch authorization and operational
visibility from drifting away from Telegram command registration.

## Media provider capability routing

`MediaProvider` adapters may declare a `platforms` capability set. The registry ranks an exact platform match before wildcard or legacy adapters, while keeping generic fallbacks eligible. `YtDlpAdapter` advertises Instagram, TikTok, YouTube, X, Facebook and Reddit and delegates health checks to the underlying provider, so a missing `yt-dlp` dependency is visible in `/health` and `/providers`. Configured HTTP fallbacks remain wildcard providers and are still protected by the common public-host and redirect policy.

The yt-dlp adapter applies one credential-free public-request policy to both
metadata probing and download: bounded browser-compatible language/accept
headers and a platform referer. YouTube's existing Android player selection is
kept in the same policy function so probe/download cannot drift. Upstream
login walls and rate limits are reduced to stable `source_access_restricted`
and `source_rate_limited` codes; extractor text, URLs and cookies never reach
Telegram or logs. Headers do not bypass authentication, private content or a
platform's access controls, and an administrator cookie remains an explicit,
disabled-by-default fallback.

A cached Telegram `file_id` is treated as an expiring transport reference. If Telegram reports a stale or invalid reference, the cache entry is evicted and the original media pipeline is retried once for that job; unrelated Telegram delivery errors still fail the job without hiding the operational signal.

Identical active media requests are coalesced by the normalized URL hash. In a
production worker Redis owns the short-lived cross-process lock; the combined
development/test backend uses an instance-scoped asyncio lock. The lock is
held through Telegram delivery so a waiting chat can reuse the first job's
`file_id` cache entry instead of downloading the same public asset again.

When `MEDIA_ARIA2_ENABLED=true` and the executable is present, the yt-dlp
adapter delegates direct HTTP byte transfer to aria2c with eight bounded
connections. Format selection, host policy, size limits and cleanup remain in
the provider; a Windows installation without aria2c automatically uses the
verified yt-dlp transport.



## Persistent moderation enforcement

Group moderation keeps the low-latency in-process decision engine, then records each denied message in the owner-scoped `moderation_violations` collection with a unique Telegram update/message key. The four-phase migration adds unique, actor/time and 30-day TTL indexes. A duplicate update cannot add a second strike; a fresh worker can continue a flood ban threshold from persisted strikes. Message text is never stored in the violation record or audit payload.

The same ledger exposes a bounded administrator summary through `/moderasyon
[1|7|30]`. The summary aggregates only reason and actor counts after the
retention cutoff; it never returns message content. The command is restricted
to Telegram group administrators and uses the repository/service boundary, so
reporting cannot read the raw Mongo collection from a handler.

Uploaded documents use a two-part owner-scoped record: Chroma stores vectors
and bounded chunk metadata, while Mongo `rag_documents` stores filename,
checksum, size, embedding mode and chunk count. The v5 migration adds unique
document/checksum indexes. `/belgeler` lists metadata only; `/belgeler sil ID`
requires an inline confirmation and deletes both vector chunks and metadata.
If Mongo metadata persistence fails after a Chroma write, the vectors are
rolled back so an unreachable orphan cannot become undeletable user memory.

### Durable document ingestion

Telegram document updates carry only bounded metadata into `DocumentIngestionService`. The handler never downloads bytes or invokes Chroma/RAG. A Redis `arel:document:queue` transports jobs while Mongo `document_jobs` is the recovery source of truth. Workers fetch the Telegram `file_id`, write a random temporary file under the configured spool, run owner-scoped validation and ingestion, then unlink the file in a `finally` block. The queue enforces user/chat concurrency, deduplicates by Telegram `file_unique_id`, recovers `queued`/`downloading`/`ingesting` jobs after restart, and dead-letters permanent failures. Production `bot` and `worker` roles require MongoDB and Redis; process-local memory is reserved for combined development/tests.

### Durable multimodal attachment processing

Photo analysis, voice transcription/TTS and sticker rendering follow the same split-role boundary. Telegram handlers acknowledge the update and persist only Telegram file identifiers and bounded metadata in `attachment_jobs`; workers download into a random spool file, execute typed vision/STT/render services and send the result. Redis transports `arel:attachment:queue`, Mongo is the recovery source of truth, and migration v7 creates status, owner, job and source indexes. Temporary files are removed in a finally block, provider output is sanitized before HTML send, and user/chat semaphores prevent one actor from monopolizing workers.

### Durable quote rendering

Quote handlers perform source selection and enqueue a bounded message/options payload. `QuoteProcessingService` handles avatar retrieval, rendering, Telegram delivery and quote persistence in the worker process. Redis uses `arel:quote:queue`, Mongo `quote_jobs` is the recovery source, migration v8 provides indexes, and artifact/avatar cleanup runs in `finally`. The handler never opens a local render file or downloads a profile photo.

### Durable speech synthesis

`/oku` and translation auto-speech are acknowledgement-only handler operations. The worker consumes `arel:speech:queue`, synthesizes bounded text through `TTSService`, sends Telegram voice and removes the MP3 in `finally`. Mongo `speech_jobs` is the recovery source and migration v9 provides indexes; deduplication uses a chat/user/language/text hash without storing audio bytes.

### Sticker tagging and market chart workers

AI sticker keyword extraction now persists a bounded job containing only a Telegram file identifier; the worker downloads to a random spool, calls the typed vision service, normalizes tags and removes the file. Market chart jobs persist bounded scalar rows and a sanitized caption; the worker renders with the bounded chart service and sends the photo. Redis namespaces are isolated (`arel:sticker_tag:*` and `arel:market_chart:*`), Mongo migrations v10 and v11 provide recovery indexes, and both queues expose retry/dead-letter metrics.

### Telegram output delivery ledger

Worker-produced outputs use a shared `job_deliveries` ledger. Each output claims `namespace:job_id:output_type`; a second worker cannot send a terminal `sent` or `uncertain` key. The send boundary is explicitly `pending -> sending -> sent`; exceptions after `sending` become `uncertain`, preventing silent duplicates. Migration v12 adds a unique key and seven-day TTL index. Migration v13 adds the forum-topic subscription scope index. The ledger stores transport metadata only and never stores media bytes or user-authored payloads.

### Worker-backed AI image generation

`/image` and the AI handover tag only validate and enqueue a bounded prompt in
the Telegram process. `ImageGenerationService` consumes `arel:image:queue` in
the worker, calls the typed `CloudflareImageProvider` with a timeout and byte limit,
sends the photo through the shared delivery ledger, and removes the temporary
file in `finally`. Mongo `image_jobs` is the recovery source; migration v14
adds status, user, unique job and deduplication indexes. Prompts are excluded
from logs and dead-letter records.
## Forum topic delivery scope

Group weather, earthquake and announcement schedules persist the Telegram
`message_thread_id` alongside the subscription. The scheduler includes that
topic id when sending a notification, so two topics in one supergroup can own
independent schedules. Legacy rows without a topic remain in the general
topic; callbacks re-authorize the current group administrator before changing
the schedule. Topic ids are transport metadata only and are never included in
provider requests or user-facing audit payloads.


## Production configuration tier

`APP_ENV` is explicit in Compose: `development`, `test` or `production`. Bot and worker production roles reject the fallback owner id during `Config.validate`, so administrative control cannot silently run with a development default. This guard is independent of Telegram transport (`polling` or `webhook`) and is covered by configuration tests.


## Operasyonel gÃ¶zlemlenebilirlik

`ObservabilityServer` bot ve worker sÃ¼reÃ§lerinde isteÄŸe baÄŸlÄ±, loopback'e baÄŸlÄ±
bir HTTP yÃ¼zeyi saÄŸlar. `/healthz` Ã§ekirdek baÄŸÄ±mlÄ±lÄ±klarÄ± ve feature/medya/AI
provider gruplarÄ±nÄ± bounded sayÄ±mla dÃ¶ndÃ¼rÃ¼r; Ã§ekirdek arÄ±zasÄ± `unhealthy`,
opsiyonel provider arÄ±zasÄ± `degraded` durumudur ve snapshot beÅŸ saniye cache'lenir.
Readiness istemcileri iÃ§in `unhealthy` snapshot HTTP `503`, `healthy` veya
`degraded` snapshot HTTP `200` dÃ¶ner; bÃ¶ylece opsiyonel kaynak kesintisi botu
trafikten Ã§Ä±karmazken MongoDB/Redis arÄ±zasÄ± reverse proxy ve orkestratÃ¶re
ulaÅŸÄ±r.
`/metrics` nested queue, typed feature ve AI provider router p50/p95/p99 latency
snapshot'larÄ±nÄ± Prometheus metriklerine dÃ¶nÃ¼ÅŸtÃ¼rÃ¼r. Ä°stek parser'Ä± bounded GET
kabul eder, token public bind iÃ§in zorunludur ve hiÃ§bir kullanÄ±cÄ±, sohbet, tam
URL veya provider payload'Ä±nÄ± label olarak Ã¼retmez.
Architecture CI ayrÄ±ca exception mesajlarÄ±nÄ±n logger f-string'i veya
`str(exc)` ile redaction filtresinden Ã¶nce birleÅŸtirilmesini reddeder.

`main.py` yalnÄ±zca composition root olarak `Orchestrator` Ã¶rneÄŸini oluÅŸturur;
modÃ¼l yÃ¼klenirken global mutable bot durumu yaratÄ±lmaz. Handler kayÄ±t fonksiyonu
Ã¶rneÄŸi aÃ§Ä±kÃ§a alÄ±r, lifecycle callback'leri ise Telegram application scope'undan
aynÄ± Ã¶rneÄŸi Ã§Ã¶zer.

## Monitoring profile

Prometheus ve Grafana ayrÄ± `monitoring` Compose profiliyle Ã§alÄ±ÅŸÄ±r. Bot ve
worker endpoint'leri aynÄ± secret token ile scrape edilir; Prometheus/Grafana
host portlarÄ± loopback'e bind edilir. Dashboard kuyruk, gecikme, iÅŸ sonuÃ§larÄ±
ve AI kullanÄ±mÄ±nÄ± gÃ¶sterir; kullanÄ±cÄ±, sohbet veya provider payload'Ä± metric
label'Ä± olarak Ã¼retilmez.

### Media latency budget and hard timeout

The media completion SLO is intentionally separate from the hard provider timeout. MEDIA_COMPLETION_SLO_SECONDS (10 seconds by default) records a breach and triggers a bounded progress message; MEDIA_JOB_TIMEOUT_SECONDS (60 seconds by default) is the cancellation ceiling. A slow external source therefore remains a durable job with a visible job ID instead of being discarded at the SLO boundary.


## Media artifact contract

Media providers share MediaRequest, MediaMetadata, and MediaArtifact contracts. MediaArtifact requires the local path, title, duration, byte size, extension, and platform; used_cookie is optional and is present only when the administrator cookie fallback was actually used. The mapping shape preserves the existing transcoder and Telegram delivery boundary while making provider output explicit for new adapters.


## Hedefli grup yanÄ±tlarÄ±

Grup konuÅŸmasÄ±nda Arel yalnÄ±zca bir mention veya kendi mesajÄ±na reply ile
uyandÄ±rÄ±ldÄ±ÄŸÄ±nda sÄ±nÄ±rlÄ± bir turn snapshot'Ä± oluÅŸturur. Snapshot aynÄ± `chat_id`
ve `thread_id` iÃ§inde son 300 saniye ve en fazla 8 mesajla sÄ±nÄ±rlÄ±dÄ±r; her
kullanÄ±cÄ± iÃ§in en yeni uygun metin seÃ§ilir. Komut, oyun, medya ve sistem
mesajlarÄ± aday deÄŸildir. `GroupReplyRouter` modelin yalnÄ±zca snapshot'ta
verilen Telegram `message_id` deÄŸerlerini hedeflemesine izin verir ve planÄ± en
fazla Ã¼Ã§ `ReplySegment` olarak doÄŸrular.

Model planÄ± kullanÄ±cÄ± adlarÄ±yla hedef seÃ§emez ve `Aysel: ... Kemal: ...` gibi
birleÅŸik Ã§Ä±ktÄ±lar geÃ§ersizdir. GeÃ§ersiz plan veya EVREN arÄ±zasÄ±nda yalnÄ±zca
mevcut tetikleyici mesaja gÃ¼venli fallback planlanÄ±r. Her segment ayrÄ±
`send_message` Ã§aÄŸrÄ±sÄ±dÄ±r; `reply_to_message_id` ve forum `message_thread_id`
birlikte gÃ¶nderilir.

Adaylar `RecentMessageIndex` Ã¼zerinden MongoDB'ye kalÄ±cÄ±, Redis'e TTL'li olarak
yazÄ±lÄ±r. `ReplyDeliveryLedger` hedef, konu, feature ve generation anahtarÄ±nÄ±
idempotent claim eder. Telegram gÃ¶nderimi baÅŸarÄ±lÄ± olduktan sonra assistant
mesajÄ± gerÃ§ek hedef ve kullanÄ±cÄ± attribution alanlarÄ±yla yazÄ±lÄ±r. GÃ¶nderim
sonucu belirsizse ledger otomatik yeniden gÃ¶nderim yapmaz; Telegram hedefinin
silindiÄŸi kesinleÅŸirse segment atlanÄ±r ve mevcut tetikleyici iÃ§in tek fallback
ÅŸansÄ± deÄŸerlendirilir.

Planlama ve gÃ¶nderim aynÄ± grup/forum konu kilidi altÄ±nda seri hale getirilir.
Bu kilit mesaj kayÄ±tlarÄ±nÄ± engellemez; ancak eÅŸzamanlÄ± A/B/C tetiklerinde bir
turn'Ã¼n aday snapshot'Ä± ve delivery ledger claim'leri birbirine karÄ±ÅŸmaz.
