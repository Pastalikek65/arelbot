# Feature KataloÄŸu

| Key | Komutlar | Kategori | Kaynak | Durum |
|---|---|---|---|---|
| `city_directory` | `/sehir`, `/plaka`, `/telefon`, `/ilce`, `/sehirler` | TÃ¼rkiye | Yerel snapshot | Aktif |
| `pharmacy` | `/eczane` | TÃ¼rkiye | Provider | Aktif â€” schema/cache/health |
| `fuel` | `/akaryakit` | Finans | Provider | Aktif â€” schema/cache/health |
| `currency` | `/doviz`, `/altin` | Finans | Provider | Aktif â€” schema/cache/health |
| `crypto` | `/kripto` | Finans | CoinGecko typed provider | Active - bounded symbols, cache/health and source attribution |
| `market` | `/borsa` | Finans | Yahoo Finance typed provider | Active - fixed-host symbol validation, cache/health and source attribution |
| `earthquakes` | `/deprem` | TÃ¼rkiye | AFAD typed provider | Active - schema validation, source metadata, cache/health and admin-controlled notifications |
| `breaking_news` | `/haber [gÃ¼ncel\|dÃ¼nya\|ekonomi\|spor\|kÃ¼ltÃ¼r\|teknoloji\|saÄŸlÄ±k]` | Haber | RSS/provider | Aktif â€” typed category filter, schema/cache/health |
| `weather` | `/hava` + 1/3/7 gÃ¼n inline tahmin | Hava | wttr.in typed provider | Aktif - bounded forecast, source/fetched-at, cache ve admin kontrollÃ¼ grup bildirimleri |
| `air_quality` | `/hava_kalite`, `/hava kalite`, hava sonucu inline | Hava | Open-Meteo Air Quality typed provider | Active - AQI, PM2.5/PM10, ozone, NO2, pollen, source/fetched-at and cache |
| `prayer_times` | `/ezan` | TÃ¼rkiye | Provider | Aktif â€” schema/cache/health |
| `flights` | `/ucak` | UlaÅŸÄ±m | Local typed provider | Aktif â€” doÄŸrulanmÄ±ÅŸ arama baÄŸlantÄ±larÄ±; canlÄ± fiyat yok |
| `bim_deals` | `/bim` | TÃ¼rkiye | Provider | Aktif â€” schema/cache/health |
| `culture_recommendation` | `/kultur [film\|dizi\|kitap\|mÃ¼zik]`, `/filmoneri`, `/dizioneri`, `/kitaponeri`, `/sarkioneri` | Kultur | Open Library / TVmaze / iTunes | Active - typed JSON provider and owner-scoped category picker |
| `story` | `/hikaye` | AI | Typed generation provider | Active - validated brief and allowlisted AI handoff |
| `web_reader` | AI tool | AI | Typed public web provider | Active - SSRF/private-host/redirect policy, bounded body and untrusted-context metadata; source sign-off pending |
| `web_search` | `/ara`, `/search`, `@bot ara ...`, AI tool | AI | Typed Tavily provider | Active - key rotation, bounded results, cache/health/circuit, source attribution and automatic live-search routing for current/date/exam questions; terms sign-off pending |
| `lyrics` | `/soz`, `/sarki`, `/lyrics`, `@bot soz ...`, AI tool | AI | Typed search + public page provider | Active - permitted-domain allowlist, bounded excerpt and source attribution; terms sign-off pending |
| `image_generation` | `/image`, AI tool | AI | Typed Cloudflare Workers AI provider + image worker | Active - key rotation, bounded prompt/params/output, policy rejection handling, queue delivery and temp cleanup; provider sign-off pending |
| `notes` | `/not`, `/notlar` | KiÅŸisel | MongoDB/bellek | Aktif |
| `tasks` | `/gorev`, `/gorevler` | KiÅŸisel | MongoDB/bellek | Aktif |
| `reminders` | `/remind`, `/hatirlat`, `/hatirlatmalar` | KiÅŸisel | MongoDB/Redis | Aktif |
| `productivity_plan` | `/plan`, `/gunlukplan`, `/haftalikplan` | KiÅŸisel | MongoDB/bellek productivity store | Active - timezone-aware daily/weekly plan, overdue reminders and idempotent action callbacks |
| `media` | `/indir` | Medya | Worker/provider | Aktif - chat bazlÄ± `media_enabled` ayarÄ±, grup/Ã¶zel sohbet butonu ve otomatik link/caption kapÄ±sÄ± |
| `quotes` | `/q`, `/qtemplate`, `/qtop`, `/qrand` | Quote | Bounded QuoteRenderService | Aktif - chat bazlÄ± `q_enabled` ayarÄ±; tÃ¼m komut, callback ve inline yÃ¼zeyleri ortak kapÄ±dan geÃ§er |
| `sticker_ai_tagging` | Paket oluÅŸturma sonrasÄ± | Sticker | Vision provider + worker | Aktif - migration v10, bounded tags, retry ve cleanup |
| `market_chart_render` | `/borsa` | Finans | Typed market provider + worker renderer | Aktif - migration v11, bounded rows, async photo delivery |
| `notification_preferences` | `/bildirim_ayar` | Personal | MongoDB/memory | Active - timezone and quiet hours |
| `daily_brief` | Inline menu / `/gunluk_ozet` | Personal | MongoDB/Redis + typed providers | Active - optional timezone-aware morning digest for weather, earthquakes, news and personal plan |
| `notification_center` | `/bildirim`, `/takip`, `/takipler`, `/bildirimler`, `/alarm` | Otomasyon | Subscription and delivery services | Active - category panels, personal and admin-only group flows, SWR cache, quiet hours and delivery ledger |
| `document_search` | AI tool / uploaded PDF, DOCX, TXT, `/belgeler` | AI/RAG | MongoDB metadata + persistent Chroma | Active - owner-scoped metadata, bounded ingestion/retrieval, confirmed single-document deletion and `/clear` cleanup |
| `document_ingest` | Telegram PDF/TXT/DOCX upload | AI/RAG | Redis + MongoDB + Chroma | Active - durable owner-scoped ingestion job with MIME, size and archive traversal validation |
| `multimodal_attachment_jobs` | Telegram photo, voice and sticker worker queue | AI / medya | Redis + MongoDB | Active - bounded attachment processing, retry/dead-letter and owner/group policy |
| `memory_controls` | `/hafiza`, `/memory`, `/clear` | AI/RAG | MongoDB user memory store | Active - explicit opt-in, owner-scoped show/delete/clear and private-chat enforcement |
| `group_moderation_settings` | `/ayar`, `/moderasyon [1\|7\|30]`, inline group settings | Grup | MongoDB chat settings + moderation violation ledger | Active - admin-scoped controls, persistent idempotent strikes, bounded admin summary, audit and 30-day retention |
| `group_personality` | `/ayar kiÅŸilik ciddi|dengeli|iÄŸneleyici|roast`, grup ayarlarÄ± inline | Grup / AI | Persistent chat settings + PersonalityPolicy | Active - administrator-only scoped tone, explicit roast consent, bounded sharp banter, sensitive-topic override, and restart-safe persistence |
| `personal_personality` | `/persona`, `/uslup` ve inline seÃ§im | KiÅŸisel / AI | Persistent user settings + PersonalityPolicy | Active - private-chat-only preference, explicit roast consent, global fallback, bounded sharp banter and sensitive-topic override |
| `games_leaderboard` | `/quiz`, `/leaderboard`, `/liderlik`, `/lb`, `/profile`, `/profil` | Oyun | MongoDB game_scores/player_stats + local Pillow cards | Active - bounded, idempotent score events with deterministic profile and leaderboard cards plus text fallback |
| `game_progression` | `/basarimlar`, `/sezon` | Oyun | MongoDB game_progress_events | Active - unique event ledger, achievement aggregation and quarterly chat-scoped seasons |
| `blackjack` | `/blackjack [bahis]`, `/bitir`, oyun menÃ¼sÃ¼ | Oyun | MongoDB game_states + action ledger + economy | Active - owner-scoped persistent hand, Hit/Stand/Double callbacks, idempotent settlement and charged-stake refund on cancel |
| `batak` | `/batak`, `/bitir`, grup inline masasÄ± | Oyun | MongoDB game_states + action ledger + economy | Active - persistent lobby, owner/admin cancellation, turn-based trick play, follow-suit validation and settlement |
| `notification_dedup` | Hava/deprem/haber/fiyat inline bildirim ekranlarÄ± | Otomasyon | MongoDB subscriptions + provider result fingerprint | Active - atomic delivery lease, changed-payload delivery, retry-safe restart recovery |
| `group_targeted_replies` | Grup mention/reply akÄ±ÅŸÄ± | Grup / AI | EVREN fast + typed message-id router | Active - 5-minute/8-message pending window, latest-per-user selection, max 3 separate replies, idempotent delivery ledger and forum-topic scoping |
| `news_digest` | `/digest` | Haber | Typed RSS provider | Active - shared runtime cache, stale-while-revalidate, source/fetched-at metadata |
| `roulette` | `/rusruleti` + inline lobi/turn butonlarÄ± | Oyun | Persistent RouletteService | Active - Mongo game state, idempotent callback, owner/admin cancellation |
| `group_analytics` | `/istatistik [1|7|30|90]` | Grup | MongoDB repository | Active - admin-only bounded period, safe names, peak-hour and active-user summary |


Yeni feature ekleyen kiÅŸi bu tabloyu ve registry manifestini aynÄ± deÄŸiÅŸiklikte
gÃ¼nceller.

Inline surfaces: `InlineQueryService` currently exposes bounded city lookup
(`sehir`, `ÅŸehir`, `il`), culture recommendations (`kultur [film|dizi|kitap|mÃ¼zik]`),
news/web/lyrics search, quote ranking (`top:<chat_id>`) and sticker search
(`sticker <term>`, `st:<term>`). Results use deterministic IDs and offset
pagination; query length and offset limits are part of the feature contract.

AI provider routing includes the typed `evren` route for the OpenAI-compatible
DeepSeek V4.1 Flash endpoint. EVREN is the default text/analysis/sticker route;
`AI_PROVIDER_ROUTES` can explicitly override it for a reviewed failover. No key
or prompt is stored in this catalog.



## Kisisel uretkenlik

Hatirlaticilar mevcut sure ifadelerine ek olarak timezone-aware Turkce tarih/saat girdilerini kabul eder (`yarin 10:00`, `pazartesi 09:00`, `10.10.2026 14:30`). Gecmis tarihler, gecersiz saatler ve 30 gunu asan talepler reddedilir; kullanicinin bildirim saat dilimi tercih edilir.
TekrarlÄ± hatÄ±rlatÄ±cÄ±lar gÃ¼nlÃ¼k, haftalÄ±k ve TÃ¼rkÃ§e hafta gÃ¼nÃ¼ kurallarÄ±yla kalÄ±cÄ± recurrence kaydÄ± ve restart sonrasÄ± recovery kullanÄ±r.
Notlar export edilebilir; toplu silme iÅŸlemleri kullanÄ±cÄ± kapsamÄ± ve onay callback'i olmadan Ã§alÄ±ÅŸmaz.
Grup karÅŸÄ±lama/ayrÄ±lma mesajlarÄ± `/ayar` Ã¼zerinden admin kontrollÃ¼dÃ¼r; geÃ§iÅŸler ChatMemberHandler ile gÃ¼venli biÃ§imde iÅŸlenir.
Flood tekrarlarÄ± admin kontrollÃ¼ kademeli otomatik ban ile sÄ±nÄ±rlanabilir; karar motoru Telegram handler'Ä±ndan ayrÄ±dÄ±r.
| `economy` | `/ekonomi`, `/bakiye`, `/gunluk` | Oyun | MongoDB/bellek ledger | Aktif â€” idempotent grup kapsamlÄ± coin hesabÄ± |
| `price_alerts` | `/alarm kur`, `/alarm liste`, `/alarm kapat` | Finans | Currency/Fuel typed provider | Active - persistent threshold condition, deduplication and quiet worker delivery |
| `group_announcements` | `/duyuru`, inline stop | Grup | MongoDB/Redis subscription lease | Active - admin-only daily/weekly schedules, HTML-safe delivery, forum-topic scoped delivery and deduplication |
| `multimodal_ai` | GÃ¶rsel/ses mesajlarÄ±, `/oku` | AI | AIProviderRouter + PersonalityPolicy | Active - typed vision/transcribe/embedding adapters, untrusted prompt boundary, serious-tone guardrail and ChatEngine voice response routing |
| `tts` | `/oku`, `/seslendir` | AI | Typed SpeechProvider | Active - bounded synthesis concurrency, timeout, atomic output and health |
| `stickers` | `/sticker`, `/st`, `/catalog`, `/packs`, `/stdelete`, `/stpack`, `/stpack thumbnail`, `/stpack sticker_sil` | Sticker | StickerRenderService + StickerPackService + Telegram API | Active - bounded static/video rendering, owner-scoped pack management, idempotent catalog-backed add, persistent reorder/thumbnail/delete operations, FFmpeg health and output limits |

## notification_center

- Komutlar: `/bildirim`, `/takip`, `/takipler`, `/bildirimler`, `/alarm`
- Yuzey: kategori tabanli inline panel, kullanici listesi, grup admin paneli
- Kapsam: hava, hava kalitesi, deprem, kur/yakÄ±t takibi ve eÅŸik alarmÄ±, kripto, haber/aktÃ¼el takipleri, sessiz saatler.
- KiÅŸisel merkezde kur (1/6 saat), akaryakÄ±t (6 saat), Bitcoin/Ethereum/Solana (1 saat/1 gÃ¼n), haber (2 saat) ve BÄ°M aktÃ¼el (12 saat) akÄ±ÅŸlarÄ± inline callback ile kurulabilir; callback sahibi ve Ã¶zel sohbet sÄ±nÄ±rÄ± yeniden doÄŸrulanÄ±r.
- Yetki: ozel sohbet kullanici; grup ortak akisi yalnizca Telegram grup yoneticisi
- Provider/cache: secilen feature provider runtime; SWR, stale uyarisi ve dedupe
- Teslim: `notification_deliveries` ledger, retry/restart recovery, audit policy `notification_policy`
- Grup akÄ±ÅŸlarÄ±: hava ve hava kalitesi iÃ§in 81 ili kapsayan sayfalÄ± ÅŸehir seÃ§imi, gÃ¼nlÃ¼k/haftalÄ±k saat seÃ§imi; haber Ã¶zeti iÃ§in gÃ¼nlÃ¼k/haftalÄ±k saat seÃ§imi. Her ikisi de forum topic kimliÄŸini korur ve admin dÄ±ÅŸÄ± callback'leri reddeder. KiÅŸisel hava/hava kalitesi ÅŸehir seÃ§imleri de sayfalÄ±dÄ±r; kiÅŸisel callback'ler grup sohbetinde Ã§alÄ±ÅŸmaz.

## Finans provider dikeyi

- `crypto` / `/kripto`: CoinGecko simple-price, 30s cache, 120s freshness, typed replay fixture, AI tool `get_crypto_prices`.
- `market` / `/borsa`: Yahoo Finance fixed-host Chart API adapter, 120s cache, 600s freshness, safe symbol aliasing, AI tool `get_market_quote`.
- Her iki feature kaynaÄŸÄ±, correlation ID, provider health, circuit breaker, stale cache ve ortak Telegram attribution zarfÄ±nÄ± kullanÄ±r.
- Inline surfaces: `sehir`, `sticker`, `top:<chat_id>` yanÄ±nda `haber <terim>`, `kripto <sembol>` ve `borsa <sembol>` typed runtime sonuÃ§larÄ±nÄ± kaynak linki ve offset pagination ile gÃ¶sterir. Inline servis network/scraper iÃ§ermez; `FeatureExecutor` dependency injection kullanÄ±r.

### Media provider capability and cache contract

- `yt-dlp` is the first-party adapter for Instagram, TikTok, YouTube, X, Facebook and Reddit URLs. It uses a shared credential-free public-request policy for probe/download and exposes stable access/rate-limit error codes without leaking extractor text.
- Configured HTTP fallbacks are wildcard providers; registry ordering prefers an exact platform capability before wildcard and unknown legacy adapters.
- The former Spatula tool surface (`get_pharmacies`, `get_fuel_prices`, `get_currency_rates`, `get_recent_earthquakes`, `get_bim_deals`, `get_breaking_news`, `get_weather`, and `get_prayer_times`) delegates to the same typed providers; it has no direct HTTP or HTML scraper path.
- Provider health delegates to the real adapter, records circuit/error/latency state, and surfaces missing dependencies.
- A stale Telegram `file_id` is evicted on the specific invalid-reference error class and the job falls back to a fresh download. Active identical URL requests are coalesced through a Redis URL-hash lock; waiters reuse the first Telegram `file_id`.

## Productivity plan surface

`/plan` is a read-only, user-scoped projection over open tasks and pending reminders. `/plan hafta` expands the local calendar window to seven days. The service resolves the user's notification timezone, separates overdue reminders from upcoming items, bounds the result, escapes all user text and exposes completion/cancellation callbacks that retain the existing owner check. It is deliberately a projection rather than a second scheduler, so restart recovery and delivery idempotency remain owned by the reminder worker.

## Explicit AI memory controls

- `/hafiza on` (or `/memory on`) enables persistent user facts; `/hafiza off` disables consent and deletes all stored facts.
- `/hafiza show` lists bounded, owner-scoped records with deterministic IDs; `/hafiza delete ID` removes one record; `/hafiza clear` removes all facts while keeping consent unchanged.
- All memory controls are private-chat only. Group messages cannot inspect or mutate personal memory.

| document_ingest | ai | /belgeler, /documents | Telegram PDF/TXT/DOCX upload, durable queue and owner-scoped RAG metadata | Redis + Mongo + Chroma | private chat, explicit user upload | 24h job TTL; temp file per attempt |

| multimodal_attachment_jobs | AI / medya | Telegram photo, voice and sticker worker queue | `attachment_jobs` + Redis | user/group policy | v7 |


### Media artifact contract

The media feature exposes a typed MediaArtifact provider hand-off. Every adapter returns path, title, duration, size, extension, and platform; used_cookie is optional and remains false/absent for public downloads. Registry and worker paths preserve this contract before transcoding or Telegram delivery.

### Oyun merkezi

- `/oyun` oyun salonunu aÃ§ar; kelime tÃ¼retme artÄ±k oyunu rastgele baÅŸlatmak yerine butonlu kategori seÃ§imi sunar.
- Kelime kategorileri TÃ¼rkÃ§e karakterli ve ASCII yazÄ±mlÄ± sorgularÄ± birlikte kabul eder (`ÅŸehirler` / `sehirler`, `doÄŸa` / `doga`).
- `src/games/vocabulary.py` 620 benzersiz yerel kelimeyi 12 kategoriye ayÄ±rÄ±r; kelime oyunu her raf iÃ§in gerÃ§ek sÃ¶zlÃ¼k havuzu Ã¼retir ve geÃ§ersiz/uydurma cevaplarÄ± reddeder.
- Adam asmaca aynÄ± ortak havuzdan 4+ harfli kelimeler seÃ§er; yeni turda tekrarlarÄ± azaltan mevcut sohbet geÃ§miÅŸi korunur.
- Quiz menÃ¼sÃ¼nde bilim kategorisi dahil tÃ¼m yerel soru havuzlarÄ± gÃ¶rÃ¼nÃ¼r; araÃ§ yÃ¼zeyi doÄŸru cevabÄ± sÄ±zdÄ±rmadan yalnÄ±zca soruyu Ã¼retir.
- `/oyunbitir` servis tabanlÄ± oyunlarÄ±n yanÄ±nda restart sonrasÄ± geri yÃ¼klenen quiz, kelime, adam asmaca ve ÅŸiÅŸe durumlarÄ±nÄ± da kalÄ±cÄ± depodan temizler.
- Restart sonrasÄ± geri yÃ¼klenen kelime anlatmaca oturumunun dÃ¶rt dakikalÄ±k zaman aÅŸÄ±mÄ± yeniden kurulur; aynÄ± sohbet iÃ§in yinelenen zamanlayÄ±cÄ± aÃ§Ä±lmaz.
- Kelime oyunlarÄ±nda dÃ¶rt zorluk profili bulunur: kolay, normal, zor ve uzman. Profil; hedef kelime sayÄ±sÄ±nÄ±, kabul edilen kelime uzunluÄŸunu, tur sÃ¼resini ve puan Ã§arpanÄ±nÄ± belirler.
- Komutlar zorlukla birlikte kullanilabilir: `/kelimeoyunu bilim zor` ve `/anlat uzman`. Oyun salonundaki kategori ve zorluk butonlari ayni typed akisina baglanir.
- Puan; kelime uzunluÄŸu, hÄ±z, combo, deneme sayÄ±sÄ± ve kullanÄ±lan ipuÃ§larÄ±na gÃ¶re hesaplanÄ±r. State iÃ§indeki sÃ¼re ve zorluk bilgisi restart sonrasÄ± geri yÃ¼klemede korunur.
- Adam asmaca da aynÄ± dÃ¶rt zorluk profilini kullanÄ±r: `/adamasmaca kolay`, `/adamasmaca zor` veya oyun salonundaki zorluk butonu. Kelime uzunluÄŸu, can sayÄ±sÄ±, hÄ±z ve hatalÄ± tahminler puana yansÄ±r; tur state'i kalÄ±cÄ± depoda tutulur.
- Adam asmaca turu TÃ¼rkÃ§e alfabetik inline klavye, ipucu, turu bÄ±rakma ve kalan harfleri otomatik kapatma davranÄ±ÅŸÄ±yla ilerler. Her callback action ledger Ã¼zerinden tekilleÅŸtirilir; grup iÃ§inde turu baÅŸlatan kiÅŸi veya yÃ¶netici kapatabilir.
- Kelime tÃ¼retme turu da inline kontrol paneli kullanÄ±r: harfleri karÄ±ÅŸtÄ±rma, ipucu, kategori/zorluk akÄ±ÅŸÄ±na dÃ¶nme ve turu bitirme callback'leri kalÄ±cÄ± state ile Ã§alÄ±ÅŸÄ±r; kullanÄ±lan ipuÃ§larÄ± puan hesabÄ±na yansÄ±r.
- Kelime anlatmaca turu; ipucu, pas geÃ§me ve turu bitirme butonlarÄ±nÄ± kalÄ±cÄ± state ve action ledger ile sunar. Ä°pucu aÃ§Ä±ldÄ±ÄŸÄ±nda aynÄ± mesaj paneli gÃ¼ncellenir; hedef cevap kullanÄ±cÄ±ya gÃ¶sterilmez.
