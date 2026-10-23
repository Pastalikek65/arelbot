# Veri Kaynakları ve Lisans Politikası

| Kaynak | Kullanım | Lisans/şart | Cache | Not |
|---|---|---|---|---|
| [Zemberek-NLP master dictionary](https://github.com/ahmetaa/zemberek-nlp/blob/037202c7e18b977c95181a76db05cb1e95846554/morphology/src/main/resources/tr/master-dictionary.dict) | Kelime Zinciri için yerel Türkçe lemma sözlüğü | Apache-2.0; [lisans](https://github.com/ahmetaa/zemberek-nlp/blob/037202c7e18b977c95181a76db05cb1e95846554/LICENSE) ve `assets/word_chain/LICENSE` | İmaj içine gömülü sabit snapshot; oyun sırasında HTTP yok | Commit, ham dosya hashleri, filtre ve türetilmiş dosya hash'i `assets/word_chain/source.json` içinde |
| `turkiye-data` | 81 il, 973 ilçe, koordinat | MIT | Yerel snapshot | Güncelleme script'i ile yenilenir |
| BTK Ulusal Numaralandırma Planı | Telefon alan kodları | Resmi kamu kaynağı | Yerel snapshot | Kaynak bağlantısı cevapta tutulur |
| AFAD Event Service | Son depremler | Resmi kamu servisi; kaynak bağlantısı gösterilir | 60 sn / 5 dk | JSON API, HTML uyumluluk fallback'i |
| Telegram Bot API | Bot gönderimi ve file_id | Telegram şartları | 24 saat | Secret redaction zorunlu |
| KekikSpatula/TRSehirler | Davranış referansı | GPLv3+ | Kod kopyalanmaz | Ayrı lisans incelemesi gerekir |
| `Piterden/chessbot` | PvP satranç etkileşim referansı; Arel kodu native yeniden uygulanır | MIT | Hamle kuralları ve state Arel domain motorunda; upstream attribution korunur | `https://github.com/Piterden/chessbot` |
| `advnpzn/Akinator-Bot` | Arel Tahmin etkileşim referansı; kaynak/assets kopyalanmaz | Lisans metadata yayınlanmamış | `akipy` ile bağımsız typed adapter; üretim sign-off gerekir | `https://github.com/advnpzn/Akinator-Bot` |
| `advnpzn/akipy` | Türkçe Akinator session client | MIT (PyPI metadata), 1.7.0 | 45 s timeout, 4 concurrent session, response cache yok | `https://github.com/advnpzn/akipy` |
| `FlareSolverr` | Akinator Cloudflare challenge için yalnızca iç ağ solver'ı | GPL-3.0; ayrı runtime hizmeti | Challenge başına; HTML debug kapalı | Host portu yayınlanmaz, erişilemezse güvenli hata | `https://github.com/FlareSolverr/FlareSolverr` |

Akinator'ın [resmî kullanım şartları](https://hi.akinator.com/content/4/seva-ki-sartem)
oyunu eğlence amacıyla kullanmayı tanımlar; veri tabanından kapsamlı çıkarımı ve
içeriğin izinsiz yeniden yayımlanmasını sınırlar. `akipy` paketinin MIT lisansı,
Elokence içeriğinin veya servis erişiminin ayrı izni yerine geçmez. Arel'in
üçüncü taraf Akinator oturumları için kaynak incelemesi `pending` kalır;
üretim onayı bu kayıttan otomatik çıkarılamaz.

Her yeni kaynak için URL, erişim tarihi, lisans, attribution, robots/terms,
rate limit, freshness hedefi, timeout ve fallback alanları doldurulur.

Kelime Zinciri sözlüğü Zemberek'in master sözlüğündeki tek sözcüklü lemmalardan
türetilmiştir. Etiketsiz `-mek/-mak` girdileri fiil, diğer etiketsiz girdiler
isimdir; açık sözcük türü etiketleri de değerlendirilir. Özel ad, kişi, yer ve
korpus sözlükleri yeni sözcük eklemek için kullanılmaz. Bu listelerde ayrıca
geçen `deniz`, `ada`, `bilge`, `özgür` gibi ortak sözcükler master sözlükteki
lemma kanıtı nedeniyle korunur. Özgün 792 Arel sözcüğü ayrıca korunur.
Kaynak ve lisans 2026-10-21 tarihinde sabit commit üzerinden kontrol edildi.
Teknik kaynak yenilemesi sabit commit ve hash doğrulamasına tabidir; üretim
oyunu ağ erişimine veya dış servise başvurmaz. Apache-2.0 attribution ve
lisans kopyası `assets/word_chain/` altında tutulur.

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

Bu kaynaklar `src/features/providers/` altındaki typed provider sözleşmesini
uygular: HTML tabanlı kaynaklar `public_data.py`, TRT Haber RSS `rss_news.py`,
Tavily `web_search.py`, Open-Meteo `weather.py` ve `air_quality.py`, AFAD
`earthquakes.py` içinde yaşar. HTML metni Telegram'a gönderilmeden önce escape
edilir; şema boş veya değişmişse provider hatası üretilir ve legacy scraper
sessizce devreye girmez. Provider runtime bunun üzerine retry, circuit breaker,
stale cache ve health metriği uygular.

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

Bu yüzey hiçbir sayfayı getirmez; yalnızca kullanıcıya açık bir arama
bağlantısı üretir. Bu nedenle kayıt, veri lisansından çok bir kaynak
görünürlüğü kaydıdır: `licenses.json` içinde `google.com` ve `bing.com`
host'larıyla `Google Flights and Google search links` ve `Bing search links`
kayıtları `pending` durumdadır. Rezervasyon, ödeme ve uçuş fiyatı bilgisi Arel
tarafında hiç üretilmez.

## Medya platformları (yt-dlp)

Medya indirme yüzeyi tek bir first-party adaptör kullanır:
`src/media/providers/ytdlp_provider.py` (`yt-dlp`). Instagram, TikTok, YouTube,
X, Facebook ve Reddit adresleri için `Referer` başlığı platform kök alan
adından gelir; istekler cookie kasası kapalıyken kimlik bilgisi taşımaz ve
ortak public-host ve redirect politikasından geçer. Operatör cookie kasasını
açtığında yalnızca kasadaki platform oturumu kullanılır, geçici dosya iş
sonunda job dizininden silinir. `aria2c` yoksa `yt-dlp` indiricisine düşer.
Boyut ve süre sınırları provider tarafından uygulanır, sonuç kaynak
bağlantısıyla birlikte saklanır.

Her platform için `licenses.json` içinde ayrı bir kayıt tutulur:
`youtube.com`, `tiktok.com`, `instagram.com`, `x.com`, `facebook.com` ve
`reddit.com`. Kayıtların tamamı `pending` durumdadır; platform koşulları ve
hak sahibi lisansı gerektiği için `terms_review` tamamlanmadan bu yüzey
üretimde etkinleştirilmez.

## Web arama fallback'i (DuckDuckGo)

`web_search` birincil olarak Tavily Search API'yi kullanır. Anahtar devresi
açıkken ya da çağrı başarısız olduğunda aynı provider `api.duckduckgo.com`
Instant Answer uç noktasına düşer; cevap `Abstract` ve `RelatedTopics`
alanlarına sınırlanır ve aynı escape, timeout, boyut sınırı ile
untrusted-context politikası uygulanır.

Bu fallback de bir kaynak kaydı gerektirir: `licenses.json` içinde
`duckduckgo.com` host'uyla `DuckDuckGo Instant Answers` kaydı `pending`
durumdadır. Tavily ve DuckDuckGo aynı özellik içinde birbirinin yerine geçtiği
için kaynak adı cevapta ayrıca gösterilir.

## Lisans kaydı host alanı

`licenses.json` içindeki her `sources[]` kaydı, kaydın yönettiği host'u `host`
alanıyla taşır ve bu alan zorunludur. `scripts/architecture_check.py` hem eksik
`host` alanını reddeder hem de provider modüllerinde **literal URL** veya
**host allowlist'i** olarak görünen host'ların kayıt altında olduğunu
doğrular. Bir kayıt kendi alt alan adlarını kapsar: `coingecko.com` kaydı
`api.coingecko.com` adresini de kapsar.

Kaydın varlığı bir onay değildir. `source_governance: pending` olan kayıt
envanterde görünür ama hukuki ve robots incelemesi tamamlanmadan production
sign-off sayılmaz; onay ayrı bir adımdır.

Kod, kütüphane ve konteyner imajı kayıtlarında `host` alanı gerçekten
kullanılan host'u gösterir: `github.com` bir upstream depo için, `docker.io`
Docker Hub imajı için, `ghcr.io` GitHub Container Registry imajı için
kullanılır.

## Typed public web reader and search

`web_reader` accepts only an HTTP(S) URL supplied by the user. The shared public-URL policy rejects credentials, localhost, private/link-local addresses and unsafe redirects; each redirect target is validated again. Response bodies are streamed with a one-megabyte default cap, non-text content is rejected, scripts are removed, and the remaining page is marked as untrusted context.

`web_search` uses the Tavily Search API through `TavilySearchProvider`. API keys remain in `KeyManager`, are never returned in a result or log, and quota/temporary failures rotate through the configured key circuit. Query length, result count, timeout and output size are bounded. Search titles, snippets and links are treated as untrusted data and escaped before the Telegram display surface. Runtime cache, rate limit, provider health, circuit breaker and source attribution are applied by `FeatureExecutor`. The source is an approved API provider; the deployment still requires an operator supplied `TAVILY_KEYS` value and must comply with Tavily's current terms and attribution requirements.

`lyrics` uses the same typed search provider and the bounded public-web reader, but only follows HTTPS results from the explicitly permitted lyrics-domain set. It rejects arbitrary domains, credentials and unsafe redirects through the shared URL policy. The response is a short, escaped excerpt with a source link; it never instructs the model to reproduce a complete copyrighted song. The feature is source-governed and remains pending until the relevant terms and attribution review is recorded.

The permitted set is `genius.com`, `musixmatch.com`, `lyrics.com`, `azlyrics.com` and `songlyrics.com`. Each site carries its own license record in `licenses.json`, because each one publishes its own terms; every record is `pending` and needs its own terms and attribution review before production enablement.

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

- Books use Open Library Search API; series use TVmaze Search API; films use Wikidata Action API; music uses Apple iTunes Search API.
- Film searches verify Wikidata's film type before rendering. Genre and no-argument suggestions rotate through a bounded editorial seed list of Wikidata film IDs. The bot displays source titles, release years and descriptions without inventing ratings. Source data is CC0; requests identify Arelbot, use a short timeout and the feature's 900-second cache. Wikimedia's API policy requires respecting rate limits and keeping the provider identifiable.
- Results are normalized into a shared typed response and include source links when the upstream response provides an HTTPS URL.
- Ratings are displayed only when the upstream payload supplies a numeric rating. The bot never estimates or invents scores.
- Query text is length limited, HTML escaped, and sent through the normal provider timeout, cache, circuit and health runtime.
- Open Library requests identify the application with a user agent, are cached, and stay within the documented request guidance; the provider is not used as a bulk backend.
- TVmaze results carry a TVmaze source link for attribution and the implementation keeps one request per user query with a 900-second cache.
- iTunes results are link-only metadata; no preview audio, artwork or promotional media is downloaded or cached.

## News RSS categories

`/haber` komutu yalnızca TRT Haber RSS akışlarını okur; feature manifesti
`breaking_news` kaynağını da TRT Haber RSS olarak bildirir ve başka bir haber
sayfası kazınmaz.

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

## Open-Meteo weather forecast

`/hava` uses the signed local city snapshot to request a bounded 1/3/7-day
forecast from Open-Meteo for Turkish cities. Current temperature, humidity and
WMO condition codes, plus daily min/max temperature, precipitation probability,
wind speed and UV are normalized and attributed in the Telegram result. Cities
outside the snapshot retain the existing wttr.in path. The feature runtime
applies a five-minute cache, 30-minute freshness target and a 30-per-minute
request budget. The Open-Meteo free endpoint is limited to non-commercial use
under CC BY 4.0 with attribution and published request limits; commercial use
requires an Open-Meteo paid plan. The bot owner confirmed non-commercial use on
2026-10-19; a future change to paid membership, advertising or other commercial
use requires a paid Open-Meteo plan before this route remains active.

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
| Open-Meteo / wttr.in | hava | Türkiye şehirleri için Open-Meteo typed forecast, hata halinde ve diğer şehirlerde wttr.in; cache/SWR, redirect/SSRF korumalı | Bot sahibinin doğruladığı ticari olmayan kullanım; Open-Meteo CC BY 4.0 atfı ve 5 dakikalık cache |
| Nöbetçi Eczaneler | eczane | typed HTML schema | gerekli |
| EPDK türetilmiş JSON snapshot | akaryakıt | typed JSON + HTML fallback | onaylı teknik kaynak; atıf zorunlu |
| TRT Haber | son dakika/RSS özet | typed RSS schema | onaylı teknik kaynak |
| BİM resmi sayfası | aktüel ürün | typed HTML schema | onaylı teknik kaynak; sınırlı istek ve kaynak atfı |
| Habertürk | namaz vakitleri | typed HTML schema | gerekli |
| Open-Meteo | hava kalitesi | typed JSON API | gerekli |
| Open Library / TVmaze / iTunes | kültür ve `/sarkioneri` | typed public APIs | onaylı teknik kaynak; rate limit uygulanır |
| DuckDuckGo Instant Answers | `/ara` fallback | typed public API fallback, bounded answer/topic payload | gerekli |
| Google Flights / Bing | `/ucak` | bağlantı üretici; ağ çağrısı ve rezervasyon yok | gerekli |
| Medya platformları (yt-dlp) | medya indirme | cookie kasası kapalıyken kimlik bilgisisiz istek politikası, boyut/süre sınırı | gerekli |

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

## Kek Guardian

- Kaynak: dağıtıma ait ayrı moderasyon servisi; hedef taşıma noktası
  `/run/kek-guardian/guardian.sock`.
- Veri: sınırlı Telegram moderasyon olayı metadata'sı ve sınırlı metin/caption
  alanı.
- Lisans/terms: servis dağıtım sahibine aittir; Guardian kaynak kodu bu
  depoya kopyalanmaz.
- Saklama: bridge frame'leri geçicidir; Arel aksiyon ledger'ı MongoDB retention
  index'lerini izler.
- Güvenlik: Unix socket peer credential allowlist'i, bounded NDJSON, süre sonu,
  nonce/replay denetimi ve fail-closed adapter.
- Mevcut VDS durumu: 2026-10-17 salt-okunur kontrolde Guardian Telegram
  polling servisi aktifti, ancak hedef socket veya RPC endpoint'i bulunmadı.
  Gerçek bridge kurulana kadar provider `disabled` kalır.
- Attribution: kullanıcıya gösterilmez; yalnızca yönetici provider durumu.


## Yaş tahmini

- **Davranış referansı:** https://github.com/mikhailsdv/guess-the-age-bot
- **Beyan edilen lisans:** upstream `package.json` MIT; fotoğraf veri kümesinin ayrı lisansı kayıtlı değil.
- **Arel kullanımı:** Oyun bağımsızdır; indirilen veritabanı 300 Wikimedia Commons portresi içerir. Doğum günü Wikidata P569'dan; fotoğraf Commons P18 bağlantısından alınır.
- **Tarih doğrulaması:** Gün hassasiyetinde doğum tarihi, Commons çekim günü ve kamera EXIF `DateTimeOriginal` günü uyuşmalıdır. Dosya adında farklı bir yıl bulunması, belirsiz tarihler ve uygun lisans bulunmaması kaydı eler. Kırpılmış kopyanın oluşturulma tarihi çekim tarihi sayılmaz.
- **Güncellik:** `python scripts/import_age_catalog.py --target 300` veritabanını indirir/genişletir; `--revalidate` mevcut kayıtların kaynak metadatasını yeniden denetler. Bot güncellemeler sırasında dış siteye bağlanmaz; yerel dosyalar ve SHA-256 manifestiyle çalışır.
- **Attribution:** Her fotoğrafın kaynak sayfası, fotoğrafçısı, tam lisans bağlantısı, doğum ve çekim tarihi [`assets/guess_age/catalog.json`](../assets/guess_age/catalog.json) içinde saklanır. Fotoğrafçı ve lisans adı grup oyun panelinde düz metin gösterilir. Tam kredi, kaynak ve lisans bağlantıları yalnız botun özel sohbetindeki `/yas kaynaklar` bölümünde sayfalı ve bağlantı önizlemesi kapalı sunulur; grup tur sonuçları bağlantı içermez. Lisans dosya bazındadır; genel kaynak açıklaması lisans onayı yerine geçmez.

## UNO

- Kaynak: [maildian/uno-telegram-bot](https://github.com/maildian/uno-telegram-bot), sabit commit `01640d1`.
- Kod AGPL-3.0-or-later başlıkları ve lisansıyla alındı. Gerçek Game, Player, Deck sınıfları güncel Python/Telegram ortamına taşındı; sonuç galerisi de kaynak results.py'den uyarlandı. Tam dosya hashleri src/vendor/uno_maildian/SOURCE.json içinde.
- 60 renkli ve 54 soluk PNG aynen kopyalandı. Upstream atfı, lisansı ve SHA256 kayıtları assets/uno_maildian içindedir. Özgün SVG kaynağı Wikimedia Commons UNO_cards_deck.svg (Dmitry Fomin, CC0 1.0).
- Telegram'da 114 sticker Arel'in kendi kimlikleriyle kaydedildi. Eski botun file_id değerleri kullanılmaz. Galeri yalnız isteyen oyuncunun elini gösterir; seçilen hamle grup/tur/oyuncu/sürüm imzasıyla doğrulanır.
- Yeni masalar kaynak motorunda; açık eski masalar bağımsız eski motorda tamamlanır. Kart ve skor durumu MongoDB'de JSON olarak kalır; eski global GameManager ve Pony ORM alınmadı. +2 biriktirme, +4 blöf ve son kart joker kısıtı kaynak kurallarını izler; Arel 500 puanlık maç/ödül kaydını korur.
- Dağıtım Git kaynak arşivini SOURCE_OFFER.json ile parçalı olarak içerir. Kaynak kodu düğmesi ve özel sohbette /uno kaynak bu sürümün kaynaklarını sunar. Hesap, dotenv, çalışma veritabanı ve takip edilmeyen işletmeci dosyaları Git arşivinde yoktur.
- BotFather inline mod açık, chosen-inline geri bildirimi Enabled olmalıdır. Telegram seçilen sonucun gönderildiği sohbet kimliğini iletmez: imzalı galeri hangi maç için üretildiyse oyuncunun geçerli hamlesi o maçta işlenir; başka sohbete kopyalama bu kapsamı değiştirmez.

## Özgün grup oyunları: Tabu, Casus ve Vampir Köylü

Tabu166Türkçe kart, Casus36mekân×4rol ve Vampir Köylü temel rol/kurallarının kodu ile metinleri bu depoda özgün yazıldı; proje MIT lisansı uygulanır. Harici GitHub oyun kodu veya kart verisi alınmadı. Daha önce incelenen oyun rehberleri yalnız davranış karşılaştırmasıdır. Oyun sırasında içerik üretimi veya kural kararı için dış AI çağrısı yapılmaz.

## Sekiz bulmaca oyunu (21 Ekim 2026)

Wordle, Karşı Kelime, Anagram, Hafıza Eşleştirme, Mayın Tarlası, Sudoku, Sayı Tahmini ve Refleks motorları bu projede uygulandı; üçüncü taraf oyun kodu veya görseli alınmadı. Türkçe sözlük mevcut kaynak ve lisans bildirimini korur. Zıt anlam kartları elle seçilmiş yaygın sözcük çiftleridir. Sudoku sayısal başlangıçları tek çözüm denetiminden geçer; rakam/satır/sütun simetrileriyle çeşitlendirilir. Diğer tahta ve sayılar yerel güvenli rastgelelikle hazırlanır. Yeni harici servis veya kullanıcı hesabı bağımlılığı yoktur. Proje kodu MIT lisansındadır.

## On dört grup ve mantık oyunu (22 Ekim 2026)

Harf Maratonu, Sözcük Avı, Yasak Harf, Ortak Hikâye, Bomba, Kör Sıralama, Kronoloji, Doğru–Yanlış, Dedektif, Amiral Battı, İki Doğru Bir Yalan, Kimim Ben, Takım Bilgi Ligi ve Zehirli Kadeh bu depoda uygulanan yerel motorlardır. Harici oyun kodu, kart paketi veya görsel alınmadı; proje kodu ve özgün metinleri MIT lisansındadır. Türkçe kelimeler mevcut sözlüğün bildirimini korur. Matematik önermeleri hesapla, mantık dosyaları bağımsız çözüm taramasıyla denetlenir. Kronoloji ve Dedektif olayları kurgudur. İki Doğru Bir Yalan iddialarında doğruluk işaretini anlatıcı belirler; bot doğruluğu araştırdığını iddia etmez. Zehirli Kadeh sanal bir oyundur ve gerçek içecek gerektirmez. Bu oyunlar sırasında dış API veya AI hakem çağrısı yapılmaz.

## Telegram buton simgeleri (22 Ekim 2026)

`assets/telegram/button_icons.json`, Telegram Bot API `getForumTopicIconStickers` yanıtından seçilmiş 38 özel emoji kimliğini ve alternatif Unicode simgesini içerir. Kaynak Telegram'ın `Topics` setidir. Sticker/görsel dosyaları indirilmez veya yeniden dağıtılmaz; buton Telegram istemcisinin kendi özel emoji görüntülemesini kullanır. Platformun mevcut Bot API koşulları ve kullanım kısıtları geçerlidir; `licenses.json` içindeki Telegram kaydının onay durumu değiştirilmez. Kimliklerin alınması salt-okunur API işlemidir; oyun sırasında harici emoji araması yapılmaz. Özel simge kullanım uygunluğu ve güvenli Unicode dönüşü [arayüz kaydında](TELEGRAM_BUTTONS_UPDATE_2026-10-22.md) açıklanır.

## Satranç panelinin yerel Telegram emojileri

- Kaynak: https://t.me/addemoji/chessno; Bot API `getStickerSet` ve 26 statik
  `custom_emoji_id` 22 Ekim 2026 tarihinde doğrulandı, tüm kare varyantları gözle incelendi.
- Katalog: `assets/telegram/chess_icons.json`. Her taşın açık/koyu kare varyantı
  ve iki boş kare; a1 koyu karedir. Katalog yalnız native Telegram kimlikleri
  içerir; artwork dosyaları bu depoda dağıtılmaz. Ayrı açık kaynak artwork
  lisansı doğrulanmadığından görseller MIT olarak yeniden lisanslanmaz.
- Sunum: Telegram platformunun kendi custom emoji mekanizması kullanılır.
  Premium düğmeler kapalıysa veya API açıkça reddederse Unicode tahta kullanılır.
  Ağ/429/belirsiz teslim hatasında tekrar mesaj gönderilmez.
- Görsel kaynak bağlantısı grup oyun panelinde paylaşılmaz.

## d2api / DeepSeek Web (22 Ekim 2026)

İşletmecinin sağladığı yerel d2api kaynak anlık görüntüsü `vendor/d2api/SOURCE.json` ile hash bazında kayıtlıdır. Paket MIT beyan eder; ayrı LICENSE dosyası yoktur. DeepSeek Web servisi ve PoW WASM için izin/lisans durumu UNKNOWN olarak korunur. Kullanıcı kendi hesabıyla işletimi ve riskleri açıkça onayladı; bu kayıt servis sağlayıcının hukuki izni değildir. Hesap dosyası ve WASM depoya alınmaz; yalnız özel sunucu servisinde tutulur. Model adı proxy takma adıdır. Ayrıntılı sınırlar ve geri dönüş adımları `docs/D2API_OPERATIONS.md` içindedir.


## Cloudflare Workers AI text rotation (2026-10-22)

Official account API: https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/ . Text model updated after Turkish samples: `@cf/openai/gpt-oss-120b` (previous: `@cf/qwen/qwen3-30b-a3b-fp8`); model license Apache-2.0, hosted-service Cloudflare terms still apply. The operator explicitly supplied their own account authorization for text rotation with EVREN and NVIDIA. This authorization does not assert legal permission beyond applicable terms and does not approve unrelated image generation. Credentials remain outside Git and corresponding-source archives. Model, timeout, JSON/tool checks and deployment evidence: `docs/AI_ROTATION_2026-10-22.md`.


## HRMN Harmon API

Operator-requested chat and image gateway at https://api.hrmn.tr/v1.
DeepSeek chat and image5/image4_ultra/image4/image3_fast/image3 images use `/chat/completions`; Prism/Flare remain explicit diagnostic options. `/models` was
verified. Provider/model terms remain independently unreviewed. Secrets and
generated URLs are not logged. See `HRMN_PROVIDER_2026-10-22.md`.

Image model availability was tested per exact model without cross-provider fallback; see `HRMN_IMAGE_MODELS_2026-10-23.md`. Upstream model names do not independently establish licensing or permanent availability.
