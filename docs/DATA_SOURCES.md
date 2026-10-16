# Veri KaynaklarÄ± ve Lisans PolitikasÄ±

| Kaynak | KullanÄ±m | Lisans/ÅŸart | Cache | Not |
|---|---|---|---|---|
| `turkiye-data` | 81 il, 973 ilÃ§e, koordinat | MIT | Yerel snapshot | GÃ¼ncelleme script'i ile yenilenir |
| BTK Ulusal NumaralandÄ±rma PlanÄ± | Telefon alan kodlarÄ± | Resmi kamu kaynaÄŸÄ± | Yerel snapshot | Kaynak baÄŸlantÄ±sÄ± cevapta tutulur |
| AFAD Event Service | Son depremler | Resmi kamu servisi; kaynak baÄŸlantÄ±sÄ± gÃ¶sterilir | 60 sn / 5 dk | JSON API, HTML uyumluluk fallback'i |
| Telegram Bot API | Bot gÃ¶nderimi ve file_id | Telegram ÅŸartlarÄ± | 24 saat | Secret redaction zorunlu |
| KekikSpatula/TRSehirler | DavranÄ±ÅŸ referansÄ± | GPLv3+ | Kod kopyalanmaz | AyrÄ± lisans incelemesi gerekir |

Her yeni kaynak iÃ§in URL, eriÅŸim tarihi, lisans, attribution, robots/terms,
rate limit, freshness hedefi, timeout ve fallback alanlarÄ± doldurulur.

## Native typed provider kayÄ±tlarÄ±

| Kaynak | Ã–zellikler | Timeout | Cache / freshness | Fallback |
|---|---|---:|---:|---|
| NÃ¶betÃ§i Eczaneler | `/eczane` | 8 s | 5 dk / 30 dk | stale cache + kontrollÃ¼ hata |
| EPDK / GÃ¼ncel AkaryakÄ±t FiyatlarÄ± | `/akaryakit` | 8 s | 15 dk / 60 dk | Kaynak EPDK; tÃ¼retilmiÅŸ JSON snapshot, HTML uyumluluk fallback'i |
| DÃ¶viz.com | `/doviz`, `/altin` | 8 s | 1 dk / 5 dk | Kaynak baÄŸlantÄ±sÄ± + stale cache |
| TRT Haber RSS | `/haber`, `/digest` | 8 s | 2â€“10 dk / 10â€“30 dk | Kaynak baÄŸlantÄ±sÄ± + stale cache |
| BÄ°M resmi sayfasÄ± | `/bim` | 8 s | 1 saat / 24 saat | Kaynak atfÄ± + stale cache + kontrollÃ¼ hata |
| Tavily Search API | `/ara`, `/search` | 15 s | 1 dk / 5 dk | API anahtarÄ± rotasyonu, kaynak baÄŸlantÄ±sÄ± ve stale cache |
| HabertÃ¼rk | `/ezan` | 8 s | 1 saat / 24 saat | stale cache + kontrollÃ¼ hata |

Bu kaynaklar `src/features/providers/public_data.py` iÃ§indeki typed provider
sÃ¶zleÅŸmesini uygular. HTML metni Telegram'a gÃ¶nderilmeden Ã¶nce escape edilir;
ÅŸema boÅŸ veya deÄŸiÅŸmiÅŸse provider hatasÄ± Ã¼retilir ve legacy scraper sessizce
devreye girmez. Provider runtime bunun Ã¼zerine retry, circuit breaker, stale
cache ve health metriÄŸi uygular.

NÃ¶betÃ§i eczane ve dÃ¶viz/altÄ±n kaynaklarÄ± Ã¼retimde etkinleÅŸtirilmiÅŸtir. Ä°stekler
sabit HTTPS host allowlist'i, iÃ§erik boyutu sÄ±nÄ±rÄ±, redirect reddi ve kaynak
cache politikasÄ±yla korunur; provider ÅŸemasÄ± deÄŸiÅŸirse kullanÄ±cÄ±ya gÃ¼venli
hata kodu dÃ¶ner. Eczane verisi saÄŸlÄ±k kaynaÄŸÄ± yerine geÃ§mez; kullanÄ±cÄ±ya
eczane telefonla teyit uyarÄ±sÄ± gÃ¶sterilir.

## UÃ§uÅŸ arama politikasÄ±

`/ucak` canlÄ± fiyat veya sefer sonucu Ã¼retmez. Harici hesap ve kÄ±rÄ±lgan arama
sayfasÄ± scraping'i yerine, kalkÄ±ÅŸ/varÄ±ÅŸ/tarih doÄŸrulandÄ±ktan sonra Google
Flights, Google ve Bing Ã¼zerinde arama yapan HTTPS baÄŸlantÄ±larÄ± oluÅŸturulur.
Bu Ã¶zellik aÄŸ saÄŸlayÄ±cÄ±sÄ± Ã§aÄŸÄ±rmadÄ±ÄŸÄ± iÃ§in local typed provider olarak 5 saniye
timeout ile Ã§alÄ±ÅŸÄ±r; sonuÃ§larda fiyatlarÄ±n arama sitesinde doÄŸrulanmasÄ± aÃ§Ä±kÃ§a
belirtilir.


## Typed public web reader and search

`web_reader` accepts only an HTTP(S) URL supplied by the user. The shared public-URL policy rejects credentials, localhost, private/link-local addresses and unsafe redirects; each redirect target is validated again. Response bodies are streamed with a one-megabyte default cap, non-text content is rejected, scripts are removed, and the remaining page is marked as untrusted context.

`web_search` uses the Tavily Search API through `TavilySearchProvider`. API keys remain in `KeyManager`, are never returned in a result or log, and quota/temporary failures rotate through the configured key circuit. Query length, result count, timeout and output size are bounded. Search titles, snippets and links are treated as untrusted data and escaped before the Telegram display surface. Runtime cache, rate limit, provider health, circuit breaker and source attribution are applied by `FeatureExecutor`. The source is an approved API provider; the deployment still requires an operator supplied `TAVILY_KEYS` value and must comply with Tavily's current terms and attribution requirements.

`lyrics` uses the same typed search provider and the bounded public-web reader, but only follows HTTPS results from the explicitly permitted lyrics-domain set. It rejects arbitrary domains, credentials and unsafe redirects through the shared URL policy. The response is a short, escaped excerpt with a source link; it never instructs the model to reproduce a complete copyrighted song. The feature is source-governed and remains pending until the relevant terms and attribution review is recorded.

## EVREN / DeepSeek V4.1 Flash

EVREN is an OpenAI-compatible AI provider. Its endpoint and key are configured
through `EVREN_URL` and `EVREN_LLM_API_KEY`; the selected model is
`EVREN_MODEL=deepseek-v4.1-flash`. EVREN is the sole production AI route;
legacy Clean APIs configuration is not populated from EVREN and is not part
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

News commands accept a bounded Turkish category (`gÃ¼ncel`, `dÃ¼nya`, `ekonomi`,
`spor`, `kÃ¼ltÃ¼r`, `teknoloji`, or `saÄŸlÄ±k`). The RSS provider filters only on
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

Embedding provider kullanÄ±mÄ± RAG_USE_PROVIDER_EMBEDDINGS ile aÃ§Ä±kÃ§a etkinleÅŸtirilir.
Provider Ã§Ä±ktÄ±sÄ± belge koleksiyonunda sabit vektÃ¶r uzayÄ± olarak kullanÄ±lÄ±r; provider
eriÅŸilemiyorsa kullanÄ±cÄ±ya gÃ¼venli embedding hata kodu dÃ¶ner ve farklÄ± boyutlu
yerel vektÃ¶r sessizce karÄ±ÅŸtÄ±rÄ±lmaz. Cache anahtarÄ± belge metninin SHA-256 Ã¶zeti,
cache bellekte bounded tutulur.
## Grup duyurularÄ±

Bu Ã¶zellik harici veri kaynaÄŸÄ± kullanmaz. Duyuru metni grup yÃ¶neticisi
tarafÄ±ndan saÄŸlanÄ±r, kalÄ±cÄ± kayÄ±t MongoDB'de tutulur ve Telegram gÃ¶nderimi
yalnÄ±zca grup sohbetine yapÄ±lÄ±r. KullanÄ±cÄ± metni HTML olarak yorumlanmadan
escape edilir; kaynak/freshness etiketi uygulanmaz.

## RSS haber Ã¶zeti

`/digest` varsayÄ±lan olarak TRT Haber'in herkese aÃ§Ä±k RSS akÄ±ÅŸlarÄ±nÄ±
`RssNewsProvider` Ã¼zerinden okur. `NEWS_RSS_FEEDS` ile kaynak listesi
deÄŸiÅŸtirilebilir. BaÅŸlÄ±k ve baÄŸlantÄ±lar kaynak gÃ¶sterimiyle sunulur; XML ÅŸemasÄ±
bozulduÄŸunda provider gÃ¼venli hata Ã¼retir ve handler iÃ§inde ayrÄ± HTTP fallback
Ã§alÄ±ÅŸmaz. Cache sÃ¼resi 10 dakika, stale-while-revalidate penceresi 30 dakikadÄ±r.

## AFAD Event Service ve akaryakÄ±t JSON snapshot'Ä±

Deprem sorgusu AFAD'Ä±n olay servisindeki sÄ±nÄ±rlÄ± zaman aralÄ±ÄŸÄ± ve kayÄ±t sayÄ±sÄ±
parametreleriyle JSON olarak alÄ±nÄ±r. Servis geÃ§ici olarak eski HTML tabloyu
verirse aynÄ± host Ã¼zerindeki uyumluluk yolu kullanÄ±lÄ±r. Her iki biÃ§im de ortak
ÅŸemaya dÃ¶nÃ¼ÅŸtÃ¼rÃ¼lÃ¼r ve dÄ±ÅŸ metin HTML olarak escape edilir.

AkaryakÄ±t sorgusu gÃ¼nlÃ¼k `/veri/fiyatlar.json` snapshot'Ä±nÄ± kullanÄ±r. Snapshot EPDK
verilerinin tÃ¼retilmiÅŸ bir sunumudur; cevapta EPDK ve derleyici atÄ±flarÄ± tutulur,
ÅŸehir ve yakÄ±t tÃ¼rÃ¼ alanlarÄ± doÄŸrulanmadan gÃ¶sterilmez. JSON eriÅŸilemezse mevcut
HTML sayfasÄ± yalnÄ±zca kontrollÃ¼ uyumluluk fallback'i olarak denenir.

## CoinGecko crypto prices

`/kripto` uses CoinGecko's typed `simple/price` API adapter. The deployment may use the public endpoint or a configured Demo/Pro endpoint through `COINGECKO_API_URL`; an API key is optional and is never written to logs. Requests are limited to ten coin IDs and one of `usd`, `try`, `eur` or `gbp`. Responses include the upstream 24-hour change and last update field when available. The runtime applies a 30-second cache, 120-second freshness target, retry and circuit-breaker policy. CoinGecko is shown as the source in Telegram; the result is market information, not investment advice.

## Yahoo Finance market quotes

`/borsa` is served by `MarketProvider`; the handler does not import a market client. The provider calls Yahoo's fixed-host Chart API with a bounded async HTTP request, normalizes a seven-day hourly response into a typed quote, and exposes Yahoo Finance attribution. Symbol aliases such as `thy` are normalized to safe Yahoo symbols; arbitrary URLs, whitespace and shell-like characters are rejected. Runtime cache is 120 seconds with a 600-second freshness target. Chart rendering remains a queued follow-up concern; the current command returns a fast, source-linked market summary.

## Open-Meteo air quality

`/hava_kalite <ÅŸehir>` and the weather result action use the Open-Meteo Air
Quality API with coordinates resolved from the signed local Turkish city
snapshot. The provider requests current European/US AQI, PM2.5, PM10, ozone,
nitrogen dioxide and available pollen values. It has a five-minute cache, a
30-minute freshness target, bounded timeout/retry and source attribution. No
arbitrary coordinates or upstream URL can be supplied through the Telegram
surface; coordinate ranges are validated in the provider. Open-Meteo terms and
attribution apply.


## Production source sign-off matrix

Typed providerlarÄ±n teknik adapter'Ä± hazÄ±r olsa da aÅŸaÄŸÄ±daki kaynaklar canlÄ±ya
alÄ±nmadan Ã¶nce terms/robots/rate-limit ve attribution incelemesinden geÃ§melidir.
`licenses.json` aynÄ± kayÄ±tlarÄ± makine okunabilir olarak taÅŸÄ±r; `required` durumu
hukuki onay verilmiÅŸ anlamÄ±na gelmez.

| Kaynak | Provider yÃ¼zeyi | Teknik durum | Production sign-off |
|---|---|---|---|
| Telegram Bot API | bot gÃ¶nderimi/webhook/file_id | aktif ve mock/staging testli | gerekli |
| AFAD Event Service | deprem | typed JSON + HTML fallback, cache/health | onaylÄ± teknik kaynak |
| wttr.in | hava | typed + cache/SWR, redirect/SSRF korumalÄ± | onaylÄ± teknik kaynak; kaynak atfÄ± ve 5 dakikalÄ±k cache |
| NÃ¶betÃ§i Eczaneler | eczane | typed HTML schema | gerekli |
| EPDK tÃ¼retilmiÅŸ JSON snapshot | akaryakÄ±t | typed JSON + HTML fallback | onaylÄ± teknik kaynak; atÄ±f zorunlu |
| TRT Haber | son dakika/RSS Ã¶zet | typed RSS schema | onaylÄ± teknik kaynak |
| BÄ°M resmi sayfasÄ± | aktÃ¼el Ã¼rÃ¼n | typed HTML schema | onaylÄ± teknik kaynak; sÄ±nÄ±rlÄ± istek ve kaynak atfÄ± |
| HabertÃ¼rk | namaz vakitleri | typed HTML schema | gerekli |
| Open-Meteo | hava kalitesi | typed JSON API | gerekli |
| Open Library / TVmaze / iTunes | kÃ¼ltÃ¼r ve `/sarkioneri` | typed public APIs | onaylÄ± teknik kaynak; rate limit uygulanÄ±r |

Kaynak sÃ¶zleÅŸmesi netleÅŸmeyen adapter kill-switch/feature flag arkasÄ±nda
tutulmalÄ±; provider kesintisi stale veri uyarÄ±sÄ± ile gÃ¶sterilmeli ve yeni bir
scraper handler katmanÄ±na eklenmemelidir.


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

## Makine tarafÄ±ndan uygulanan kaynak yÃ¶netiÅŸimi

Her dÄ±ÅŸ provider artÄ±k feature manifestinde `source_governance`, `robots_policy`,
`terms_review` ve `production_signoff` alanlarÄ±nÄ± taÅŸÄ±r. Teknik adapter'Ä±n Ã§alÄ±ÅŸmasÄ±
Ã¼retim onayÄ± anlamÄ±na gelmez: mevcut public web/API kaynaklarÄ± `pending` ve
`review_required` durumunda tutulur. Bu durum provider Ã§Ä±ktÄ±sÄ±nda kaynak URL'si
olmadan gÃ¼venli ÅŸekilde raporlanÄ±r.

Raporlamak iÃ§in:

    python scripts/provider_governance.py

Manifestte kayÄ±tlÄ± source ve terms baÄŸlantÄ±larÄ±nÄ±n eriÅŸilebilirliÄŸini ayrÄ±ca
Ã¶lÃ§mek iÃ§in Ã¼Ã§lÃ¼ live gate zorunludur. Bu kontrol yalnÄ±zca HTTP eriÅŸilebilirliÄŸi
Ã¶lÃ§er; hukuki izin veya production sign-off vermez ve rapora tam URL yazmaz.
HEAD isteÄŸi 3xx dÃ¶nerken aynÄ± URL'ye yapÄ±lan sÄ±nÄ±rlÄ± GET isteÄŸi 2xx veriyorsa
baÄŸlantÄ± eriÅŸilebilir kabul edilir; GET sonrasÄ± Ã§Ã¶zÃ¼mlenmeyen redirect, 4xx/5xx
veya eriÅŸilemeyen baÄŸlantÄ± production onayÄ± iÃ§in blocker olarak kalÄ±r:

    ARELBOT_GOVERNANCE_LINKS_LIVE=1 python scripts/provider_governance.py --check-links --live --confirm-live --report deploy/evidence/provider-governance-links.json

GerÃ§ek Ã¼retim aÃ§Ä±lÄ±ÅŸÄ±ndan Ã¶nce hukuki/robots/attribution incelemesini zorunlu
tutmak iÃ§in:

    python scripts/provider_governance.py --require-approved
    python scripts/production_preflight.py --require-provider-approval --skip-compose

`--require-approved` baÅŸarÄ±sÄ±zken ilgili feature Ã¼retimde etkinleÅŸtirilmez;
inceleme tamamlandÄ±ÄŸÄ±nda manifestteki dÃ¶rt alan birlikte gÃ¼ncellenir ve release
evidence yeniden Ã¼retilir. Kod deÄŸiÅŸikliÄŸi gerektirmeyen onay akÄ±ÅŸÄ± iÃ§in
`--approval-manifest` kullanÄ±labilir. Bu dosya, source/provider/terms kimliÄŸine
baÄŸlÄ± SHA-256 fingerprint, reviewer, approval ID ve sÃ¼reli dÃ¶rt onay alanÄ±
taÅŸÄ±r; fingerprint uyuÅŸmazlÄ±ÄŸÄ± veya sÃ¼re aÅŸÄ±mÄ± runtime'da otomatik olarak
fail-closed davranÄ±r. AyrÄ±ntÄ±lÄ± ÅŸema ve dosya izinleri `docs/OPERATIONS.md`
iÃ§inde tutulur.
