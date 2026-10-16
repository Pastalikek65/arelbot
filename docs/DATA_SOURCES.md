# Veri Kaynakları ve Lisans Politikası

| Kaynak | Kullanım | Lisans/şart | Cache | Not |
|---|---|---|---|---|
| `turkiye-data` | 81 il, 973 ilçe, koordinat | MIT | Yerel snapshot | Güncelleme script'i ile yenilenir |
| BTK Ulusal Numaralandırma Planı | Telefon alan kodları | Resmi kamu kaynağı | Yerel snapshot | Kaynak bağlantısı cevapta tutulur |
| AFAD Event Service | Son depremler | Resmi kamu servisi; kaynak bağlantısı gösterilir | 60 sn / 5 dk | JSON API, HTML uyumluluk fallback'i |
| Telegram Bot API | Bot gönderimi ve file_id | Telegram şartları | 24 saat | Secret redaction zorunlu |
| KekikSpatula/TRSehirler | Davranış referansı | GPLv3+ | Kod kopyalanmaz | Ayrı lisans incelemesi gerekir |

Her yeni kaynak için URL, erişim tarihi, lisans, attribution, robots/terms,
rate limit, freshness hedefi, timeout ve fallback alanları doldurulur.

## Native typed provider kayıtları

| Kaynak | Özellikler | Timeout | Cache / freshness | Fallback |
|---|---|---:|---:|---|
| Nöbetçi Eczaneler | `/eczane` | 8 s | 5 dk / 30 dk | stale cache + kontrollü hata |
| EPDK / Güncel Akaryakıt Fiyatları | `/akaryakit` | 8 s | 15 dk / 60 dk | Kaynak EPDK; türetilmiş JSON snapshot, HTML uyumluluk fallback'i |
| Döviz.com | `/doviz`, `/altin` | 8 s | 1 dk / 5 dk | Kaynak bağlantısı + stale cache |
| TRT Haber RSS | `/haber`, `/digest` | 8 s | 2–10 dk / 10–30 dk | Kaynak bağlantısı + stale cache |
| BİM resmi sayfası | `/bim` | 8 s | 1 saat / 24 saat | Kaynak atfı + stale cache + kontrollü hata |
| Tavily Search API | `/ara`, `/search` | 15 s | 1 dk / 5 dk | API anahtarı rotasyonu, kaynak bağlantısı ve stale cache |
| Habertürk | `/ezan` | 8 s | 1 saat / 24 saat | stale cache + kontrollü hata |

Bu kaynaklar `src/features/providers/public_data.py` içindeki typed provider
sözleşmesini uygular. HTML metni Telegram'a gönderilmeden önce escape edilir;
şema boş veya değişmişse provider hatası üretilir ve legacy scraper sessizce
devreye girmez. Provider runtime bunun üzerine retry, circuit breaker, stale
cache ve health metriği uygular.

Nöbetçi eczane ve döviz/altın kaynakları üretimde etkinleştirilmiştir. İstekler
sabit HTTPS host allowlist'i, içerik boyutu sınırı, redirect reddi ve kaynak
cache politikasıyla korunur; provider şeması değişirse kullanıcıya güvenli
hata kodu döner. Eczane verisi sağlık kaynağı yerine geçmez; kullanıcıya
eczane telefonla teyit uyarısı gösterilir.

## Uçuş arama politikası

`/ucak` canlı fiyat veya sefer sonucu üretmez. Harici hesap ve kırılgan arama
sayfası scraping'i yerine, kalkış/varış/tarih doğrulandıktan sonra Google
Flights, Google ve Bing üzerinde arama yapan HTTPS bağlantıları oluşturulur.
Bu özellik ağ sağlayıcısı çağırmadığı için local typed provider olarak 5 saniye
timeout ile çalışır; sonuçlarda fiyatların arama sitesinde doğrulanması açıkça
belirtilir.


## Typed public web reader and search

`web_reader` accepts only an HTTP(S) URL supplied by the user. The shared public-URL policy rejects credentials, localhost, private/link-local addresses and unsafe redirects; each redirect target is validated again. Response bodies are streamed with a one-megabyte default cap, non-text content is rejected, scripts are removed, and the remaining page is marked as untrusted context.

`web_search` uses the Tavily Search API through `TavilySearchProvider`. API keys remain in `KeyManager`, are never returned in a result or log, and quota/temporary failures rotate through the configured key circuit. Query length, result count, timeout and output size are bounded. Search titles, snippets and links are treated as untrusted data and escaped before the Telegram display surface. Runtime cache, rate limit, provider health, circuit breaker and source attribution are applied by `FeatureExecutor`. The source is an approved API provider; the deployment still requires an operator supplied `TAVILY_KEYS` value and must comply with Tavily's current terms and attribution requirements.

`lyrics` uses the same typed search provider and the bounded public-web reader, but only follows HTTPS results from the explicitly permitted lyrics-domain set. It rejects arbitrary domains, credentials and unsafe redirects through the shared URL policy. The response is a short, escaped excerpt with a source link; it never instructs the model to reproduce a complete copyrighted song. The feature is source-governed and remains pending until the relevant terms and attribution review is recorded.

## EVREN / DeepSeek V4.1 Flash

EVREN is an OpenAI-compatible AI provider. Its endpoint and key are configured
through `EVREN_URL` and `EVREN_LLM_API_KEY`; the selected model is
`EVREN_MODEL=deepseek-v4.1-flash`. EVREN remains the compatible failover route;
when `NVIDIA_API_KEY` is configured, NVIDIA NIM is preferred by the default
route. Legacy Clean APIs configuration is not populated from EVREN and is not part
of the default provider allowlist. The provider uses the same typed completion
adapter, key circuit, timeout, fallback, usage telemetry and bounded error
envelope as the other AI routes. Credentials are never logged or persisted in
repository files. The EVREN terms must be accepted for the account before
production use; the API also supports a models and quota health probe. The
maintained endpoint is `https://evren-llmapi.ssyz.org.tr/v1` and the model name
is sent exactly as `deepseek-v4.1-flash`.

EVREN model capability is split by task. `EVREN_VISION_MODEL=mimo-v2.6-pro`
is used only by the typed vision route for image input and text analysis. The
EVREN model catalogue and gateway documentation do not advertise an image
binary generation response for Mimo; therefore Arel does not send image
generation jobs to this model or claim that an analysis model created a photo.
Image creation remains a separate, queue-owned `CloudflareImageProvider` task
and stays disabled until its provider approval is recorded.
Capability references: [EVREN gateway model catalogue](https://llmtr.com/docs/en/gateway/evren/)
and [Mimo model page](https://llmtr.com/en/models/evren/mimo-v2.6-pro-tr).

## NVIDIA NIM / DeepSeek V4.1 Flash

NVIDIA NIM is an optional OpenAI-compatible provider. The endpoint, deployment
secret and model are configured with `NVIDIA_URL`, `NVIDIA_API_KEY` and
`NVIDIA_MODEL=deepseek-ai/deepseek-v4.1-flash`. The key remains in
`KeyManager`; it is never logged, persisted in repository files or included in
provider health payloads. NVIDIA is selected by the default `nvidia -> evren`
route when its key is configured, or can be pinned with an explicit
`AI_PROVIDER_ROUTES` entry. Optional
`NVIDIA_VISION_MODEL` is scoped to the typed vision route. NVIDIA terms, model
license, quota, retention and attribution requirements require operator review
before production enablement; the source is therefore recorded as pending in
`licenses.json`.

`image_generation` uses `CloudflareImageProvider` for Cloudflare Workers AI Flux
generation. Account keys remain in `KeyManager`; the adapter rotates configured
keys on quota/temporary failures, validates the response as a bounded PNG
artifact, and returns only a temporary local path to the image worker. Policy
rejections, upstream errors and output-size violations use stable error codes.
The provider is asynchronous and queue-owned; its Cloudflare terms and account
authorization must be signed off before production enablement.

## Culture recommendation APIs

- Books use Open Library Search API; series use TVmaze Search API; films and music use Apple iTunes Search API.
- Results are normalized into a shared typed response and include source links when the upstream response provides an HTTPS URL.
- Ratings are displayed only when the upstream payload supplies a numeric rating. The bot never estimates or invents scores.
- Query text is length limited, HTML escaped, and sent through the normal provider timeout, cache, circuit and health runtime.
- Open Library requests identify the application with a user agent, are cached, and stay within the documented request guidance; the provider is not used as a bulk backend.
- TVmaze results carry a TVmaze source link for attribution and the implementation keeps one request per user query with a 900-second cache.
- iTunes results are link-only metadata; no preview audio, artwork or promotional media is downloaded or cached.

## News RSS categories

News commands accept a bounded Turkish category (`güncel`, `dünya`, `ekonomi`,
`spor`, `kültür`, `teknoloji`, or `sağlık`). The RSS provider filters only on
categories explicitly present in the feed item; it does not infer a category
from a headline and returns a stable `news_category_invalid` error for unknown
aliases.

## Story generation policy

Story generation uses an internal typed request provider. It has no external data source, does not scrape or call a network service, and does not cache user prompts. Topic and style are length-limited and HTML-escaped before the AI handoff. The final generation uses the configured AI provider through the existing allowlist and treats the brief as untrusted input.

## User document and RAG policy

Uploaded PDF, DOCX and UTF-8 TXT files are user-provided data rather than an
external source. Ingestion is limited by `RAG_MAX_FILE_BYTES`, MIME and magic
signature checks, a configured upload root and ephemeral cleanup. DOCX archives
are inspected without extraction and reject traversal names, symlinks, excess
entries, oversized members and suspicious compression ratios. Chroma metadata
always includes the Telegram user id and SHA-256 document fingerprint; search
queries are filtered by that user id and duplicate documents are not re-added.
Retrieved text is marked as untrusted context before it reaches an AI provider.
The bot does not execute instructions found in an uploaded document and never
returns parser exception details to the user.

Embedding provider kullanımı RAG_USE_PROVIDER_EMBEDDINGS ile açıkça etkinleştirilir.
Provider çıktısı belge koleksiyonunda sabit vektör uzayı olarak kullanılır; provider
erişilemiyorsa kullanıcıya güvenli embedding hata kodu döner ve farklı boyutlu
yerel vektör sessizce karıştırılmaz. Cache anahtarı belge metninin SHA-256 özeti,
cache bellekte bounded tutulur.
## Grup duyuruları

Bu özellik harici veri kaynağı kullanmaz. Duyuru metni grup yöneticisi
tarafından sağlanır, kalıcı kayıt MongoDB'de tutulur ve Telegram gönderimi
yalnızca grup sohbetine yapılır. Kullanıcı metni HTML olarak yorumlanmadan
escape edilir; kaynak/freshness etiketi uygulanmaz.

## RSS haber özeti

`/digest` varsayılan olarak TRT Haber'in herkese açık RSS akışlarını
`RssNewsProvider` üzerinden okur. `NEWS_RSS_FEEDS` ile kaynak listesi
değiştirilebilir. Başlık ve bağlantılar kaynak gösterimiyle sunulur; XML şeması
bozulduğunda provider güvenli hata üretir ve handler içinde ayrı HTTP fallback
çalışmaz. Cache süresi 10 dakika, stale-while-revalidate penceresi 30 dakikadır.

## AFAD Event Service ve akaryakıt JSON snapshot'ı

Deprem sorgusu AFAD'ın olay servisindeki sınırlı zaman aralığı ve kayıt sayısı
parametreleriyle JSON olarak alınır. Servis geçici olarak eski HTML tabloyu
verirse aynı host üzerindeki uyumluluk yolu kullanılır. Her iki biçim de ortak
şemaya dönüştürülür ve dış metin HTML olarak escape edilir.

Akaryakıt sorgusu günlük `/veri/fiyatlar.json` snapshot'ını kullanır. Snapshot EPDK
verilerinin türetilmiş bir sunumudur; cevapta EPDK ve derleyici atıfları tutulur,
şehir ve yakıt türü alanları doğrulanmadan gösterilmez. JSON erişilemezse mevcut
HTML sayfası yalnızca kontrollü uyumluluk fallback'i olarak denenir.

## CoinGecko crypto prices

`/kripto` uses CoinGecko's typed `simple/price` API adapter. The deployment may use the public endpoint or a configured Demo/Pro endpoint through `COINGECKO_API_URL`; an API key is optional and is never written to logs. Requests are limited to ten coin IDs and one of `usd`, `try`, `eur` or `gbp`. Responses include the upstream 24-hour change and last update field when available. The runtime applies a 30-second cache, 120-second freshness target, retry and circuit-breaker policy. CoinGecko is shown as the source in Telegram; the result is market information, not investment advice.

## Yahoo Finance market quotes

`/borsa` is served by `MarketProvider`; the handler does not import a market client. The provider calls Yahoo's fixed-host Chart API with a bounded async HTTP request, normalizes a seven-day hourly response into a typed quote, and exposes Yahoo Finance attribution. Symbol aliases such as `thy` are normalized to safe Yahoo symbols; arbitrary URLs, whitespace and shell-like characters are rejected. Runtime cache is 120 seconds with a 600-second freshness target. Chart rendering remains a queued follow-up concern; the current command returns a fast, source-linked market summary.

## Open-Meteo air quality

`/hava_kalite <şehir>` and the weather result action use the Open-Meteo Air
Quality API with coordinates resolved from the signed local Turkish city
snapshot. The provider requests current European/US AQI, PM2.5, PM10, ozone,
nitrogen dioxide and available pollen values. It has a five-minute cache, a
30-minute freshness target, bounded timeout/retry and source attribution. No
arbitrary coordinates or upstream URL can be supplied through the Telegram
surface; coordinate ranges are validated in the provider. Open-Meteo terms and
attribution apply.


## Production source sign-off matrix

Typed providerların teknik adapter'ı hazır olsa da aşağıdaki kaynaklar canlıya
alınmadan önce terms/robots/rate-limit ve attribution incelemesinden geçmelidir.
`licenses.json` aynı kayıtları makine okunabilir olarak taşır; `required` durumu
hukuki onay verilmiş anlamına gelmez.

| Kaynak | Provider yüzeyi | Teknik durum | Production sign-off |
|---|---|---|---|
| Telegram Bot API | bot gönderimi/webhook/file_id | aktif ve mock/staging testli | gerekli |
| AFAD Event Service | deprem | typed JSON + HTML fallback, cache/health | onaylı teknik kaynak |
| wttr.in | hava | typed + cache/SWR, redirect/SSRF korumalı | onaylı teknik kaynak; kaynak atfı ve 5 dakikalık cache |
| Nöbetçi Eczaneler | eczane | typed HTML schema | gerekli |
| EPDK türetilmiş JSON snapshot | akaryakıt | typed JSON + HTML fallback | onaylı teknik kaynak; atıf zorunlu |
| TRT Haber | son dakika/RSS özet | typed RSS schema | onaylı teknik kaynak |
| BİM resmi sayfası | aktüel ürün | typed HTML schema | onaylı teknik kaynak; sınırlı istek ve kaynak atfı |
| Habertürk | namaz vakitleri | typed HTML schema | gerekli |
| Open-Meteo | hava kalitesi | typed JSON API | gerekli |
| Open Library / TVmaze / iTunes | kültür ve `/sarkioneri` | typed public APIs | onaylı teknik kaynak; rate limit uygulanır |

Kaynak sözleşmesi netleşmeyen adapter kill-switch/feature flag arkasında
tutulmalı; provider kesintisi stale veri uyarısı ile gösterilmeli ve yeni bir
scraper handler katmanına eklenmemelidir.


## Monitoring image inventory

Prometheus, Alertmanager and Grafana are optional operational images, not
user-data providers. Compose pins Prometheus `v3.13.4`, Alertmanager `v0.34.1`
and Grafana `v13.0.9`; their upstream licenses and release review records are
in `licenses.json`. They run only in the `monitoring` profile and expose host
ports on loopback.

HTML-backed public providers share a bounded fetch policy: HTTPS source-host
allowlist, redirects disabled, accepted HTML content types, and a 2 MiB decoded
response limit. Oversized or unexpected responses fail with a stable provider
policy error and never enter cache or Telegram rendering.

## Makine tarafından uygulanan kaynak yönetişimi

Her dış provider artık feature manifestinde `source_governance`, `robots_policy`,
`terms_review` ve `production_signoff` alanlarını taşır. Teknik adapter'ın çalışması
üretim onayı anlamına gelmez: mevcut public web/API kaynakları `pending` ve
`review_required` durumunda tutulur. Bu durum provider çıktısında kaynak URL'si
olmadan güvenli şekilde raporlanır.

Raporlamak için:

    python scripts/provider_governance.py

Manifestte kayıtlı source ve terms bağlantılarının erişilebilirliğini ayrıca
ölçmek için üçlü live gate zorunludur. Bu kontrol yalnızca HTTP erişilebilirliği
ölçer; hukuki izin veya production sign-off vermez ve rapora tam URL yazmaz.
HEAD isteği 3xx dönerken aynı URL'ye yapılan sınırlı GET isteği 2xx veriyorsa
bağlantı erişilebilir kabul edilir; GET sonrası çözümlenmeyen redirect, 4xx/5xx
veya erişilemeyen bağlantı production onayı için blocker olarak kalır:

    ARELBOT_GOVERNANCE_LINKS_LIVE=1 python scripts/provider_governance.py --check-links --live --confirm-live --report deploy/evidence/provider-governance-links.json

Gerçek üretim açılışından önce hukuki/robots/attribution incelemesini zorunlu
tutmak için:

    python scripts/provider_governance.py --require-approved
    python scripts/production_preflight.py --require-provider-approval --skip-compose

`--require-approved` başarısızken ilgili feature üretimde etkinleştirilmez;
inceleme tamamlandığında manifestteki dört alan birlikte güncellenir ve release
evidence yeniden üretilir. Kod değişikliği gerektirmeyen onay akışı için
`--approval-manifest` kullanılabilir. Bu dosya, source/provider/terms kimliğine
bağlı SHA-256 fingerprint, reviewer, approval ID ve süreli dört onay alanı
taşır; fingerprint uyuşmazlığı veya süre aşımı runtime'da otomatik olarak
fail-closed davranır. Ayrıntılı şema ve dosya izinleri `docs/OPERATIONS.md`
içinde tutulur.

