# Arelbot Mimari Sözleşmesi

```text
Telegram update
  -> router / permission
  -> feature registry veya job queue
  -> domain service / provider
  -> FeatureResult
  -> Telegram formatter / AI response
```

`src/features` ortak typed çekirdektir. `FeatureDefinition` komut, kategori,
izin, provider, provider_kind, cache, freshness, timeout ve audit bilgilerini taşır.
`provider_kind=data` typed `DataProvider` registry'sinde doğrulanır; `provider_kind=ai`
model rotasını belirtir. `provider_kind=moderation`, Guardian gibi ayrı bir
moderation service sözleşmesiyle dependency injection üzerinden bağlanan provider
sınırını belirtir; bu provider data registry'sine zorla sokulmaz.
`DataProvider` dış kaynağı soyutlar. `FeatureResult` source, fetched_at,
stale, cache_hit, correlation_id ve güvenli hata kodu verir.

Provider runtime cache veya fallback sonucu döndürdükten sonra
`FeatureExecutor`, payload'ı provider'ın `normalize()` sözleşmesinden geçirir.
Bu adım bütün Telegram, inline ve AI çağrılarında ortak şemayı garanti eder;
normalizasyon hatası güvenli bir `PROVIDER_SCHEMA_INVALID` sonucu ve health kaydı
üretir, ham upstream exception kullanıcıya taşınmaz.

Telegram, AI, inline ve callback yüzleri aynı domain servislerini çağırır.

## ChatFeaturePolicy ve kapsam izolasyonu

Quote ve medya indirme anahtarları `ChatFeaturePolicy` üzerinden çözülür.
`quotes`/`quote` takma adı `q_enabled`, `media`/`video` takma adı
`media_enabled` alanına normalize edilir. Bu alanlar MongoDB'de sohbetin
`_id` alanı altında tutulur; bir gruptaki toggle başka bir grup veya özel
sohbetin davranışını değiştirmez. Komut, otomatik link, caption, callback ve
inline quote yolları aynı policy kapısından geçer. Kapalı bir feature komutu
sessizce yok sayılır; medya kuyruğuna veya quote repository'sine iş bırakılmaz.
Yönetici toggle'ları bounded audit kaydıyla izlenir ve Telegram callback'i
ikinci kez cevaplama hatası kullanıcıya taşınmaz.

`src/video_downloader.py` ve `src/tiktok_downloader.py` eski dış entegrasyonlar
için yalnızca uyumluluk yüzüdür. Bu modüller HTTP istemcisi, hardcoded
sağlayıcı veya kendi limit politikasını içermez; `YtDlpProvider` sözleşmesine
delege eder. Yeni Telegram akışlarında doğrudan bu sınıf yerine kuyruklu
`MediaDownloadService` kullanılır. Architecture check bu sınırı ve eski
Cobalt/TikWM bypasslarını otomatik olarak reddeder.

Bildirim teslimatı `SubscriptionStore` içindeki atomik claim lease'i ile
yürütülür. AFAD, haber ve aktüel ürün akışları için içerik fingerprint'i
`last_fingerprint` alanında tutulur; aynı provider payload'ı yeniden geldiğinde
plan ileri alınır ama Telegram'a ikinci kez gönderilmez. Hava ve namaz gibi
zaman bazlı akışlar tekrar politikasını korur; eşik alarmları yalnızca gözlenen
payload değiştiğinde tetiklenir.
Grup analitiği de `GroupAnalyticsService` üzerinden çalışır; Mongo aggregate
sorguları `Database.get_group_analytics` repository sınırında tutulur. Komut
öncesi grup ve yönetici yetkisi tekrar doğrulanır, kullanıcı adları escape edilir.
Provider başarısız olduğunda cache policy stale sonucu açık uyarıyla sunabilir
veya işi queue'ya devredebilir. Handler doğrudan scraper çalıştırmaz.

MongoDB kalıcı iş, kullanıcı, mesaj, not, görev, reminder, cache ve audit
kayıtlarının kaynağıdır. Redis kısa durum, queue, rate limit ve sıcak cache
için kullanılır. Medya dosyası yalnızca iş süresince disk üzerinde tutulur.

Sihirbaz durumları `PersistentStateMap` üzerinden kullanıcı + sohbet kapsamında ve
TTL'li state store'a yazılır. Aynı kullanıcının iki grup veya özel sohbetteki
oturumları ayrı Redis anahtarlarında tutulur; callback bağlamı eşleşmiyorsa
durum fail-closed olarak yok sayılır. Durum payload'ları JSON şekillidir; iç içe sözlük ve liste
mutasyonları da izlenir, böylece sticker/quote/cookie adımlarında `append`,
`pop` veya alan güncellemesi restart öncesi kaybolmaz. Redis üretimde tek
paylaşılan kısa durum kaynağıdır; memory store yalnızca geliştirme/test
çalıştırmalarında kullanılabilir.

Feature provider results use a two-level cache: a process-local hot cache and,
when the production queue has a Redis connection, a namespaced Redis cache.
Cache payloads retain source and fetch timestamps so a shared hit remains
traceable and stale results can be labelled at the Telegram boundary.

Weather requests carry a bounded forecast horizon (`days` 1–7). The wttr.in
adapter emits a stable daily forecast list and compact derived rain/wind
summaries; Telegram callbacks only select a horizon and never alter the
notification subscription. Group forecast callbacks remain view-only while
group notification controls are re-authorized for administrators.
## Native provider geçişi

Native provider geçişi feature anahtarını değiştirmeden yapılır. Registry önce
`DataProvider` uyumlu typed provider'ı seçer; public Spatula araçlarının eski
`BaseTool` yüzeyi de artık yalnızca typed provider delegasyonu yapar ve doğrudan
HTTP/scraper içermez. Böylece kontrollü migration fallback açıldığında bile
ikinci bir veri çekme politikası oluşmaz. `ProviderRuntime` timeout, retry,
circuit breaker, request coalescing ve stale-while-revalidate uygular. Provider
çağrı sağlığı repository üzerinden `provider_health` koleksiyonuna yazılır;
handler'lar MongoDB veya HTTP istemcisine erişemez.

Transport policy/schema failures are classified as deterministic and consume
one provider attempt only. Transient upstream statuses (timeouts, 429 and
5xx) retain bounded retry and circuit accounting. This distinction keeps a
broken source from receiving duplicate requests while still allowing a
short-lived outage to recover within the feature deadline.

Üretim varsayılanında `LEGACY_PROVIDER_FALLBACK_ENABLED=false` olduğu için
başarısız typed çağrı sessiz bir scraper başlatmaz; kullanıcı kontrollü hata veya
stale cache görür. Fallback yalnızca development/staging replay için açılabilir
ve `legacy` metadata ile gözlemlenir. Zincir, seçilen provider'ın `normalize()`
adımını ortak zarfa almadan önce çalıştırır; doğrudan komut, AI aracı ve migration
yüzeyi aynı typed payload sözleşmesini korur.

RSS haber özeti de aynı kurala uyar: `RssNewsProvider` XML ayrıştırmayı,
`ProviderRuntime` cache/retry/circuit/stale-while-revalidate politikasını,
`FeatureExecutor` ise ortak `FeatureResult` zarfını sağlar. `/digest` handler'ı
network istemcisi oluşturmaz; yalnızca provider sonucunu AI özetine veya Telegram
HTML görünümüne biçimlendirir.

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

## Grup duyuruları

Planlı grup duyuruları `group_announcement` feature kaydıyla tanımlanır.
Metin ve zaman `AnnouncementService` tarafından sınırlandırılır; yönetici
yetkisi her komutta ve callback'te Telegram'dan yeniden doğrulanır. Kayıtlar
genel `SubscriptionStore` içinde `scope=group` olarak tutulur. Atomic claim,
lease expiry ve `next_run_at` hesaplaması worker restart'ında kopya duyuruyu
önler. Kullanıcı metni Telegram HTML parse moduna girmeden önce escape edilir.

## Çok modlu AI

Sticker render da aynı ağır iş sınırını kullanır: `StickerRenderService`
static görüntüyü Pillow ile, video görüntüyü FFmpeg ile bounded semaphore
altında işler. Handler içinde codec, boyut veya geçici dosya yönetimi yoktur;
artifact yalnızca doğrulama sonrası Telegram'a gönderilir.

TTS çağrıları `SpeechProvider` ve `TTSService` üzerinden ilerler. `edge-tts`
yalnızca adapter katmanında import edilir; handler'lar sağlayıcı SDK'sına
erişmez. Üretim dosyası sınırlı spool dizinindeki gizli `.part` dosyasına
yazılır; boyut ve sağlayıcı yolu doğrulandıktan sonra atomik olarak yayınlanır.
`StickerPackService` paket yönetimini owner kapsamıyla sınırlar; `/stpack`
komutu yalnızca bu servisi biçimlendirir ve silme işlemi kullanıcı kimliğine
bağlı inline onaydan sonra yapılır. Telegram API başarısız olursa MongoDB
kaydı korunur.
Quote üretimi `QuoteRenderService` üzerinden bounded thread kuyruğunda çalışır;
handler yalnızca doğrulanmış artifact'i Telegram'a gönderir. `.part` dosyaları
timeout ve gönderim hatalarında temizlenir, başarılı çıktı atomik olarak yayınlanır.
Oyun puanları `event_id` ile idempotent kaydedilir; aynı idempotency key canonical
`game_progress_events` ledger'ına tek kez eklenir. Sezon tablosu ve başarımlar
event ledger'ından aggregate edilir, böylece ayrı sayaç projeksiyonlarının
restart sırasında ayrışma riski azaltılır. Sezonlar UTC çeyreklerine göre
isimlendirilir, leaderboard chat kapsamıyla sorgulanır.
Semaphore, timeout ve provider health kontrolleri aynı serviste tutulur.

AIProviderRouter aynı circuit/fallback sözleşmesiyle vision, transcribe ve
embedding görevlerini de yönlendirir. OpenAI-compatible adapter; JSON chat,
multipart transcription ve embedding response zarflarını normalize eder.
Clean APIs eski kurulumlarla uyumluluk için enum/adapter seviyesinde tutulur ancak üretim routerına
kaydedilmez ve hiçbir canlı route tarafından seçilmez. Eski AI_PROVIDER_ROUTES değerleri bellek içinde
temizlenir; uygun durumda EVRENe, aksi durumda aktif varsayılan rotaya düşer. Clean APIs anahtarı
yeni kurulumlarda gerekli bir AI sağlayıcısı sayılmaz ve Faz 9 kanıt kapısına dahil değildir.
Adapter ayrıca provider URL'sini HTTPS, credential, private-host, query ve
fragment politikalarından geçirir; mesaj, yanıt, transkript, embedding ve
`max_tokens` payload'ları bounded sözleşmeyle doğrulanır. Kullanıcı girdisi
olan deterministic limit hataları key circuit'ini bozmaz; upstream protokol
şema hataları health ve failover telemetrisi olarak kaydedilir.
Yerel medya dosyaları symlink, boş içerik ve boyut sınırından geçmeden dış
sağlayıcıya gönderilmez. Vision/STT servisleri typed yol başarısız olduğunda
geriye dönük ChatEngine akışını kontrollü olarak kullanır.

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

`/borsa` provider sonucu içindeki sınırlı 7 günlük noktalar `MarketChartService` üzerinden bounded worker-thread ile PNG grafiğe çevrilebilir. Matplotlib yoksa feature metin özeti gönderilir; handler doğrudan grafik kütüphanesine bağlanmaz. Renderer concurrency, timeout ve çıktı boyutu sınırları servis sözleşmesindedir.


## Provider observation history

`FeatureExecutor` provider saglik yazimini non-blocking yapar. Repository son snapshot'i `provider_health` koleksiyonunda tutarken, kaynak/freshness gozlemini `provider_observations` koleksiyonunda `provider + fetched_at` unique anahtariyla saklar. Cache hit ayni fetched timestamp'i kullandigi icin duplicate tarihsel kayit olusmaz. `observed_at` alaninda 30 gunluk TTL, provider/tarih sorgu indexleri ve geri alinabilir ikinci migration vardir; observation payload'i allowlist disinda kullanici girdisi, secret veya ham provider govdesi tasimaz.


### Sticker katalog sırası

Telegram sticker sırası değişikliği remote API başarılı olduktan sonra repository’ye yazılır. `sticker_pack_position` indeksi pack kapsamında sıralama sorgularını hızlandırır; migration v3 açık rollback işlemi taşır.


### Quote seçenek sözleşmesi

`QuoteRenderService` tema, renk, ölçek, emoji ve boolean seçeneklerini render kuyruğuna girmeden önce normalize eder; bilinmeyen anahtarlar atılır ve ölçek `0.5-2.0` aralığında tutulur. `qtemplate` kayıtları aynı normalizer ve 32 karakterlik isim kuralından geçer; böylece Mongo, bellek ve Telegram yüzeyleri aynı davranışı paylaşır.


### Kalıcı batch quote kaydı

Quote kaydı ilk mesajla sınırlı kalmaz: en fazla 10 kaynak mesajı, render seçenekleri, mesaj sayısı ve çıktı türü bounded kayıt olarak saklanır. Metin ve ad alanları sırasıyla 4000 ve 128 karakterle sınırlıdır; böylece batch quote restart sonrası yeniden incelenebilirken sınırsız payload depolanmaz.


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


## Operasyonel gözlemlenebilirlik

`ObservabilityServer` bot ve worker süreçlerinde isteğe bağlı, loopback'e bağlı
bir HTTP yüzeyi sağlar. `/healthz` çekirdek bağımlılıkları ve feature/medya/AI
provider gruplarını bounded sayımla döndürür; çekirdek arızası `unhealthy`,
opsiyonel provider arızası `degraded` durumudur ve snapshot beş saniye cache'lenir.
Readiness istemcileri için `unhealthy` snapshot HTTP `503`, `healthy` veya
`degraded` snapshot HTTP `200` döner; böylece opsiyonel kaynak kesintisi botu
trafikten çıkarmazken MongoDB/Redis arızası reverse proxy ve orkestratöre
ulaşır.
`/metrics` nested queue, typed feature ve AI provider router p50/p95/p99 latency
snapshot'larını Prometheus metriklerine dönüştürür. İstek parser'ı bounded GET
kabul eder, token public bind için zorunludur ve hiçbir kullanıcı, sohbet, tam
URL veya provider payload'ını label olarak üretmez.
Architecture CI ayrıca exception mesajlarının logger f-string'i veya
`str(exc)` ile redaction filtresinden önce birleştirilmesini reddeder.

`main.py` yalnızca composition root olarak `Orchestrator` örneğini oluşturur;
modül yüklenirken global mutable bot durumu yaratılmaz. Handler kayıt fonksiyonu
örneği açıkça alır, lifecycle callback'leri ise Telegram application scope'undan
aynı örneği çözer.

## Monitoring profile

Prometheus ve Grafana ayrı `monitoring` Compose profiliyle çalışır. Bot ve
worker endpoint'leri aynı secret token ile scrape edilir; Prometheus/Grafana
host portları loopback'e bind edilir. Dashboard kuyruk, gecikme, iş sonuçları
ve AI kullanımını gösterir; kullanıcı, sohbet veya provider payload'ı metric
label'ı olarak üretilmez.

### Media latency budget and hard timeout

The media completion SLO is intentionally separate from the hard provider timeout. MEDIA_COMPLETION_SLO_SECONDS (10 seconds by default) records a breach and triggers a bounded progress message; MEDIA_JOB_TIMEOUT_SECONDS (60 seconds by default) is the cancellation ceiling. A slow external source therefore remains a durable job with a visible job ID instead of being discarded at the SLO boundary.


## Media artifact contract

Media providers share MediaRequest, MediaMetadata, and MediaArtifact contracts. MediaArtifact requires the local path, title, duration, byte size, extension, and platform; used_cookie is optional and is present only when the administrator cookie fallback was actually used. The mapping shape preserves the existing transcoder and Telegram delivery boundary while making provider output explicit for new adapters.


## Hedefli grup yanıtları

Grup konuşmasında Arel yalnızca bir mention veya kendi mesajına reply ile
uyandırıldığında sınırlı bir turn snapshot'ı oluşturur. Snapshot aynı `chat_id`
ve `thread_id` içinde son 300 saniye ve en fazla 8 mesajla sınırlıdır; her
kullanıcı için en yeni uygun metin seçilir. Komut, oyun, medya ve sistem
mesajları aday değildir. `GroupReplyRouter` modelin yalnızca snapshot'ta
verilen Telegram `message_id` değerlerini hedeflemesine izin verir ve planı en
fazla üç `ReplySegment` olarak doğrular.

Model planı kullanıcı adlarıyla hedef seçemez ve `Aysel: ... Kemal: ...` gibi
birleşik çıktılar geçersizdir. Geçersiz plan veya EVREN arızasında yalnızca
mevcut tetikleyici mesaja güvenli fallback planlanır. Her segment ayrı
`send_message` çağrısıdır; `reply_to_message_id` ve forum `message_thread_id`
birlikte gönderilir.

Adaylar `RecentMessageIndex` üzerinden MongoDB'ye kalıcı, Redis'e TTL'li olarak
yazılır. `ReplyDeliveryLedger` hedef, konu, feature ve generation anahtarını
idempotent claim eder. Telegram gönderimi başarılı olduktan sonra assistant
mesajı gerçek hedef ve kullanıcı attribution alanlarıyla yazılır. Gönderim
sonucu belirsizse ledger otomatik yeniden gönderim yapmaz; Telegram hedefinin
silindiği kesinleşirse segment atlanır ve mevcut tetikleyici için tek fallback
şansı değerlendirilir.

Planlama ve gönderim aynı grup/forum konu kilidi altında seri hale getirilir.
Bu kilit mesaj kayıtlarını engellemez; ancak eşzamanlı A/B/C tetiklerinde bir
turn'ün aday snapshot'ı ve delivery ledger claim'leri birbirine karışmaz.

## Game runtime boundary

Interactive games share a typed `GameDefinition`/`GameSession` contract. A
session carries its chat scope, player set, turn owner, bounded expiry, combo,
scores, schema version and processed action IDs. `GameStateStore` uses compare
and set semantics so a delayed Telegram callback cannot overwrite a newer turn.
Callbacks claim a round-scoped action through `GameActionLedger`; production
Mongo persistence is required and the bounded in-memory claim store is reserved
for tests or explicitly degraded development.

The word games retain their existing text protocol while their inline buttons
carry a round ID. Connect Four adds a two-player lobby, six-by-seven board,
turn validation, column buttons, win/draw detection, restart recovery and
owner/admin cancellation. `connect4` is registered in the feature catalog and
command registry; handlers only render or dispatch domain results and never
perform external I/O.

Chess adds an 8x8 two-player PvP session in `src/games/chess.py` and
`src/services/chess_ui.py`. The legal-move engine owns FEN reconstruction from
the durable UCI log, SAN history, castling, en-passant, promotion, checkmate,
stalemate, draw claims, resignation and color/turn authorization. The Telegram
adapter uses round-scoped `chess:<round>:...` callbacks, rotates the board so
the active color is at the bottom, and persists every accepted transition
through the existing game action ledger. The interaction model is attributed
to the MIT-licensed Piterden/chessbot project; its Node/Telegraf runtime and
storage are not copied into Arel.

### Versioned game persistence contract

Every persisted `GameSession` carries `schema_version`, a canonical
`state_key`, and (for board games) `board_size` and `connect_length`. The
catalog is the source of truth: XOX is schema `1`, state key `tictactoe`, a
selectable 6x6/7x7/8x8 boards with connect length 4; legacy 3x3 snapshots
remain readable for recovery. Connect Four is schema `1`, state key
`connect4`, a 6x7 board with connect length 4. A snapshot whose metadata does
not match the catalog is rejected before it can enter the store.

Gomoku uses a transport-neutral immutable `GomokuState` with a configurable
6–15 square board and five-in-a-row rule. Its replay log is canonical JSON,
so a restart can verify every move before hydration. The Telegram adapter
offers 6x6, 8x8, 10x10, 12x12 and 15x15 panels, binds callbacks to the round,
prompt message and forum topic, and persists through the same state store and
action ledger. Invalid coordinates, duplicate actions and cross-chat callbacks
fail closed.

`InMemoryGameStateStore` implements the same contract used by durable adapters:
reads are defensive copies, creation is scoped by `chat_id + state_key`, and
saves use compare-and-set on `version`. Recovery parses every record before
swapping the live map, so malformed or duplicate records cannot produce a
partial restore. `snapshot()` emits records suitable for a Mongo/Redis
repository without embedding large media or transport objects.

## Guardian moderation boundary

Arel is the only Telegram action authority in Arel-managed groups. The Kek
Guardian command vocabulary is embedded through `GuardianCommandService` and
the transport-neutral `GuardianSurface`:
commands are normalized to typed `ModerationAction` values and sent through the
same idempotent executor and ledger as automatic policy actions. Old Turkish
names remain aliases only; they do not create a second Arel moderation API.

Kek Guardian's optional external evaluator remains a separate process and, once its transport bridge is installed, receives
bounded moderation events over a local Unix socket. The adapter validates
protocol version, expiry, replay keys and peer credentials. The result passes
through the local ModerationService and then the idempotent
ModerationActionExecutor. No handler calls Telegram moderation methods directly.
Telegram-only effects (membership, permissions, purge, pin and chat profile
changes) go through `GuardianTelegramService`; `/gecemodu`, captcha answer
consumption and bounded administrator listing use the same boundary.

The current VDS Guardian deployment is a separate Telegram polling bot and does
not expose the socket/RPC bridge yet. Therefore the Arel adapter stays disabled
until a real evaluator-backed bridge is installed and owner-group staging
evidence is collected. The synthetic bridge self-test proves only the transport
contract and is never production evidence.

Deployment modes are disabled, shadow and active. Disabled and shadow never authorize a remote action. A bridge timeout or protocol failure falls back to the deterministic local policy and records a degraded metric; it is never treated as allow.

XOX also follows this boundary: a two-player, selectable 6x6/7x7/8x8,
four-in-a-row, round-scoped state machine with persistent turn timeout,
idempotent callbacks and owner/admin cancellation. Board state never lives
only in a Telegram handler; legacy 3x3 snapshots are read-only migration
compatibility.

Game progression is a separate pure domain boundary. Completed rounds emit
bounded, idempotent `ProgressionEvent` records; difficulty, combo and hint
inputs are capped, duplicate or conflicting event IDs are rejected, and
cooldown/window/daily limits prevent score farming. Leaderboard and seasonal
achievement projections are replayable from the event ledger and do not run
Telegram, MongoDB or Redis calls inside the game rules.


### Arel Tahmin / Akinator

Arel Tahmin, `akipy==1.7.0` istemcisini `AkinatorService` üzerinden kullanır.
Telegram handler yalnızca state ve panel teslimini yönetir; upstream çağrısı
typed servis, semaphore ve timeout ile sınırlandırılır. Callback payload'ı
`akinator:<round_id>:...` biçiminde round ve owner kapsamına bağlıdır.
Türkçe akış karakter temasını kullanır; Akinator-Bot deposu kaynak kodu veya
görselleri kopyalanmaz. Mongo state restart sonrasında kaydı korur, fakat
upstream istemci süreç içi olduğundan yarım tur güvenli şekilde expire edilir.
### Yaş Tahmini oyun akışı

`GuessAgeService` grup kapsamındaki `guess_age` kaydını data port üzerinden
kalıcılaştırır. Telegram handler yalnızca komutu, fotoğraf gönderimini ve job
queue zamanlayıcısını yönetir; puan, tekrar koruması, tur geçişi ve asset path
güvenliği `src/games/guess_age.py` ile servis katmanında kalır. Callback payload
yalnızca opak round kimliği ve yaş aralığı taşır; hedef yaş gönderilmez.

### UNO ve Mini App oyun protokolü

`UnoService` Telegram callback'leri ile gelecekteki Mini App arasında ortak
oyun sözleşmesidir. Deste oluşturma, oynanabilir kart, renk seçimi, +2/+4,
pas, UNO çağrısı, sıra ve puan geçişleri `src/games/uno.py` içinde saf
kurallar olarak çalışır; handler yalnızca yetkili kullanıcıyı doğrular,
state'i kaydeder ve paneli günceller. State `game_states` içinde `uno` anahtarı
ile saklanır ve action ID listesi aynı callback'in iki kez uygulanmasını önler.

`src/services/miniapp_game_registry.py` Mini App'in oyun keşif manifestidir.
Yeni bir oyun eklemek için önce aynı `state_key`, `action_namespace` ve
`protocol_version` sözleşmesine sahip bir `MiniAppGameDefinition` kaydı
eklenir. Mini App endpoint'i HTTPS ve Telegram `initData` doğrulaması olmadan
feature flag ile kapalı kalır; bu yüzden UNO Telegram'da tam işlevli fallback
olarak çalışır. Her kullanıcıya yalnızca kendi eli açık görünüm olarak
üretilir, rakip kartları Mini App ve Telegram sunumunda gizlenir.


### Interactive game recovery boundary

Game commands use targeted `hydrate_game` reads from the data port before
starting a round. Stateful callbacks validate chat, round, prompt message and
forum topic identifiers before mutation; a copied button cannot move a round
in another topic. New chess and Connect Four rounds publish their prompt
panel before storing the prompt message id, so a restart or a silent command
never leaves a lobby without controls.
