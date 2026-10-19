# Akinator-Bot araştırması ve Arel entegrasyon notları

**Araştırma tarihi:** 2026-10-18  
**İncelenen kaynak:** [`advnpzn/Akinator-Bot`](https://github.com/advnpzn/Akinator-Bot)  
**İncelenen dal/commit:** `main` / [`3683c49ccc43932338ca0c0cb2681bf4e54ead35`](https://github.com/advnpzn/Akinator-Bot/tree/3683c49ccc43932338ca0c0cb2681bf4e54ead35)  
**Amaç:** Oyunun Telegram akışını, state/persistence modelini, veri kaynaklarını, Türkçeleştirme sınırlarını ve Arel’e güvenli biçimde uyarlanabilecek parçaları belirlemek.

Bu belge upstream kodun birebir kopyalanması için lisans onayı değildir. Arel’e uygulanacak çözüm, aşağıdaki davranış ve teknik sözleşmeleri kendi mimarimize uyarlamalı; kaynak kod, görseller ve bağımlılık lisansları ayrıca doğrulanmalıdır.

## Arama kapsamı ve yöntem

| Alan | Kapsam |
|---|---|
| Birincil kaynaklar | GitHub deposu, sabit commit kaynak kodu, README, `docs/architecture.md`, testler, `pyproject.toml`, Docker/Compose ve deploy dosyaları |
| GitHub metadata | [`repos/advnpzn/Akinator-Bot`](https://api.github.com/repos/advnpzn/Akinator-Bot) API yanıtı: `main`, Python, archived=false, `license=null` |
| İncelenen ana modüller | `app.py`, `config.py`, `game.py`, `sessions.py`, `controller.py`, `db.py`, `presentation.py`, `keyboards.py`, `strings.py`, `themes.py`, Telegram handler'ları |
| Test kapsamı | `tests/test_application.py`, `test_games.py`, `test_database.py`, `test_presentation.py`, `test_runtime.py` |
| Arama sınırları | `akipy` paketinin ayrı kaynak deposu, Akinator servisinin kapalı sunucu davranışı ve görsel varlıkların özgün üreticisi bu incelemede doğrulanmadı |
| Dışlama | İkincil bloglar, fork'lar ve README'de kaynak gösterilmeyen kopya anlatımlar kanıt olarak kullanılmadı |

## Kaynak değerlendirmesi

| Kaynak | Tür | Kanıt ağırlığı | Bu rapordaki kullanım |
|---|---|---:|---|
| [`README.md`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/README.md) | Proje sahibi dokümanı | Orta | Amaç, komutlar, çalışma ve deploy iddiaları |
| [`src/akinator_bot/game.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/game.py) | Birincil uygulama kodu | Yüksek | `akipy` çağrıları, cevap seçenekleri, timeout ve hata davranışı |
| [`src/akinator_bot/sessions.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/sessions.py) | Birincil uygulama kodu | Yüksek | Oyun sahipliği, TTL, kapasite, kilit ve restart sınırları |
| [`src/akinator_bot/controller.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/controller.py) | Birincil uygulama kodu | Yüksek | Upstream → DB → Telegram sıralaması ve revision idempotency |
| [`src/akinator_bot/db.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/db.py) | Birincil uygulama kodu | Yüksek | SQLite şeması, migration, WAL ve istatistikler |
| [`tests/`](https://github.com/advnpzn/Akinator-Bot/tree/3683c49ccc43932338ca0c0cb2681bf4e54ead35/tests) | Birincil test kanıtı | Orta-yüksek | Kabul edilen state, callback, hata ve medya davranışı |
| GitHub license metadata | Platform metadata | Yüksek | `license=null`; repo kökünde LICENSE/COPYING dosyası yok |

### Lisans ve yeniden kullanım riski

GitHub API deposu için `license` alanı `null` döndürüyor ve sabit commit ağacında `LICENSE`, `LICENSE.md`, `COPYING` veya eşdeğer bir dosya bulunmuyor. Bu durum kodun otomatik olarak MIT, GPL veya başka bir açık kaynak lisansıyla yeniden dağıtılabileceği anlamına gelmez. Depo kamuya açık olsa da telif hakları saklı olabilir.

Sonuç: Arel içine upstream dosyalarını veya `assets/aki_pics` görsellerini kopyalamak için repository sahibinden açık izin veya lisans bildirimi alınmalı. İzin alınana kadar yalnızca davranışsal gözlem ve bağımsız yeniden uygulama yapılmalı; upstream kaynak kodu Arel’e taşınmamalı. `akipy==1.7.0` lisansı, Akinator web servisinin kullanım şartları, `TRAWL/FlareSolverr` kullanımı ve görsel varlıkların provenance bilgisi ayrıca incelenmelidir. `pyproject.toml` yalnızca bağımlılık sürümlerini belirtir; lisans grant etmez.

## Upstream’in gerçek davranışı

### Teknoloji ve çalışma modeli

- Python `>=3.12`.
- `python-telegram-bot[rate-limiter]>=22.8,<23` ile polling tabanlı tek bot süreci.
- `akipy==1.7.0` Akinator oyun istemcisi.
- `aiosqlite>=0.22.1,<0.23` ile SQLite; `WAL`, `NORMAL`, foreign key ve 5 saniyelik busy timeout.
- `httpx` Akinator ve görsel istekleri için kullanılıyor.
- Docker imajı AMD64 ve ARM64 olarak hedeflenmiş; Compose tek `bot` servisi ve kalıcı `akinator-data` volume kullanıyor.
- Redis, MongoDB veya harici kalıcı state servisi yok.
- Opsiyonel solver URL'si (TRAWL/FlareSolverr benzeri) `SOLVER_URL`/`AKIPY_SOLVER_URL` ile verilebiliyor.

Kaynak: [`pyproject.toml`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/pyproject.toml), [`README.md`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/README.md), [`docker-compose.yml`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/docker-compose.yml).

### Oyun akışı

1. Kullanıcı `/play` komutunu gönderir.
2. Kullanıcı profili `ensure_user` ile yazılır; süresi dolan local session'lar temizlenir.
3. `SessionManager` kullanıcı başına tek aktif oyun ve global maksimum session sınırı uygular.
4. Telegram'a yükleniyor görseli gönderilir; `message_id` session'a bağlanır.
5. `GameController.start` session'ı `STARTING` yapar; `GameService.start_akinator` semafor slotu alır; `Akinator.start_game(language, child_mode, game_mode)` çağrılır.
6. Başlatma başarılıysa SQLite `games` kaydı oluşturulur ve `total_guess`/`unfinished_guess` güncellenir.
7. İlk soru Telegram mesajının medyası/caption'ı olarak gösterilir.
8. Kullanıcı inline butonlardan `Yes`, `No`, `Probably`, `I don't know`, `Probably not`, `Back` veya `Cancel` seçer.
9. Callback session ID + expected revision içerir. Sahiplik, mesaj/inline message eşleşmesi ve revision doğrulanmadan upstream çağrılmaz.
10. Upstream kabul edilirse SQLite revision/istatistik transaction içinde yazılır; revision artırılır; ekran Telegram'da güncellenir.
11. Akinator teklif aşamasına geçince `Yes`/`No` onayı gösterilir. `Yes` doğru sonuç, `No` yeni sorular veya tükenme ile ses benzeri sonuç doğurabilir.
12. Hata, iptal veya TTL sonrası oyun kapanır; belirsiz transport timeout'larında cevap otomatik tekrarlanmaz.

Kaynak: [`handlers/play.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/handlers/play.py), [`controller.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/controller.py), [`game.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/game.py).

### Cevap sözleşmesi

`GameService.answer` yalnızca şu seçenekleri kabul ediyor:

- `0`: yes
- `1`: no
- `2`: don't know
- `3`: probably
- `4`: probably not
- `b`: back

Teklif onayı için `y` veya `n` kullanılıyor. Domain state `PENDING → STARTING → PLAYING → PROPOSITION → DONE` olarak ilerliyor. Her accepted action için `revision` ve `questions` güncelleniyor.

### Telegram yüzeyi

Komutlar:

| Upstream komutu | Davranış |
|---|---|
| `/start` | Hoş geldin, menü; `/start play` deep-link'i oyunu başlatır |
| `/help` | Komut ve inline kullanım yardımını gösterir |
| `/play` | Özel sohbet veya grup mesajında kullanıcıya bağlı oyun açar |
| `/cancel` | Oyuncunun aktif oyununu kapatır |
| `/me` | Kullanıcı profili ve istatistikleri |
| `/leaderboard`, `/lead`, `/top` | Kategori seçimi ve sayfalı sıralama |
| `/language`, `/lang` | Akinator soru dili seçimi |
| `/theme` | Characters / Animals / Objects teması |
| `/childmode` | NSFW karakter filtresi |
| `/stats` | Genel sayaç; admin için canlı session sayısı da gösterilir |
| `/admin` | Yalnızca private chat'te `ADMIN_IDS` içindeki kullanıcılar |

Callback protokolü:

- Menü: `menu:*`
- Cevap: `a:<session_id>:<revision>:<0|1|2|3|4|b>`
- Teklif: `w:<session_id>:<revision>:<y|n>`
- İptal: `x:<session_id>`
- Ayarlar: `lang:*`, `theme:*`, `child:*`
- Leaderboard: `lead:<category>:<page>`
- Inline başlatma: `is:pending:<owner_id>`

Callback verilerinde kullanıcı adı yerine kısa session ID ve Telegram kimliği kullanılıyor; kopyalanmış veya eski butonlar owner/message/inline message kontrolünden sonra reddediliyor.

### Inline oyun

BotFather'da inline mode açıldıktan sonra herhangi bir sohbette `@bot play` sonucu paylaşılabiliyor. Sonuç ilk etapta stateless davet mesajıdır; yalnızca daveti paylaşan owner `Start game` butonuna basınca session ayrılır. `inline_message_id` SQLite'da bir kez claim edilir. Inline oyunlar görsel yüklemez; metin tabanlıdır.

Kaynak: [`handlers/inline.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/handlers/inline.py), [`keyboards.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/keyboards.py), README inline bölümü.

### Dil ve tema

- UI metinleri tek bir `strings.py` dosyasında İngilizce sabitler olarak bulunuyor.
- Akinator soru dili `AKI_LANG_CODE` içinde `tr` dahil çoklu dil olarak tanımlanmış.
- `DEFAULT_LANGUAGE` ve `GAME_THEME` ayarları `akipy.dicts.THEMES` ile doğrulanıyor.
- Temalar `c` (Characters), `a` (Animals), `o` (Objects). Dil temayı desteklemiyorsa `normalize_theme` ilk uygun temaya düşüyor.
- `child_mode` varsayılanı true; bu mod NSFW karakterleri filtrelemek için upstream çağrıya aktarılıyor.
- Kullanıcı UI dili ile Akinator soru dili ayrıdır. `language` komutu yalnızca soruların dilini değiştirir; UI İngilizce kalır.

Kaynak: [`strings.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/strings.py), [`themes.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/themes.py), [`config.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/config.py).

### Görseller ve medya

Özel sohbet oyunlarında Akinator'ın `akinator_image_url` veya teklif fotoğrafı kullanılır. Renderer:

- yalnızca HTTPS ve `akinator.com`/`clarinea.com` alt alanlarını kabul eder;
- portu varsayılan HTTPS ile sınırlar;
- kullanıcı adı/parola içeren URL'leri reddeder;
- redirect takip etmez;
- `Content-Type: image/*` ve byte limiti uygular;
- byte bounded cache ile medya belleğini sınırlar;
- Telegram `file_id` değerlerini yeniden kullanır;
- inline oyunlarda URL indirme/yükleme yapmaz.

Kaynak: [`presentation.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/presentation.py), `tests/test_presentation.py`.

### Persistence ve restart

SQLite tabloları:

- `users`: profil, dil/tema, child mode, toplam oyun, doğru/yanlış, unfinished ve soru sayaçları.
- `events`: olay türü, zaman, kullanıcı ve detay.
- `games`: session ID, user ID, status, revision, soru sayısı ve başlangıç/bitiş zamanları.
- `inline_invitations`: inline message ID için tek kullanımlık claim.

Migration additive: eski `users` tablosunda `aki_theme` yoksa kolon ekleniyor. Process açılışında bitmemiş oyunlar `expired` yapılıyor. Active in-memory `Akinator` client'ları restart sonrası restore edilmiyor. Ended games/events retention ile temizleniyor; kullanıcı toplamları korunuyor.

Kaynak: [`db.py`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/src/akinator_bot/db.py), [`docs/architecture.md`](https://github.com/advnpzn/Akinator-Bot/blob/3683c49ccc43932338ca0c0cb2681bf4e54ead35/docs/architecture.md).

## Güçlü noktalar ve sınırlar

### İyi uygulanmış invariants

- Bir kullanıcıya tek aktif oyun.
- Session kapasitesi dolunca canlı oyunlar evict edilmiyor.
- Callback'te owner + message/inline message + expected revision kontrolü.
- Upstream kabulünden sonra DB commit; Telegram render hatası accepted action'ı tekrar oynatmıyor.
- Transport timeout belirsiz olduğu için otomatik replay yapılmıyor.
- SQLite transaction ve unique session/outcome kayıtları duplicate istatistikleri engelliyor.
- Media download allowlist ve byte bound ile SSRF/sınırsız indirme riski azaltılıyor.
- Update processor kullanıcı bazında sıralama, beş dakikalık duplicate update cache ve global concurrency limit uyguluyor.

### Arel’e geçerken korunması gerekenler

- **Revision tabanlı callback:** Arel callback verisine oyun ID + turn/revision + sohbet/thread kapsamı koymalı.
- **Upstream uncertain action replay yok:** Zaman aşımı sonrası aynı hamle otomatik tekrarlanmamalı; kullanıcıya yeni tur/yenileme sunulmalı.
- **Commit before render:** State kabulünü repository transaction'ında tamamlayıp Telegram düzenlemesini sonra yapmak.
- **Owner/message binding:** Kopyalanan buton veya başka sohbetten gelen callback oyuna yönlendirilmemeli.
- **Bounded admission:** Akinator upstream gecikmesini global worker/concurrency sınırına bağlamak.
- **Media allowlist:** Akinator görselleri için ortak SSRF/redirect/MIME/size katmanı kullanmak.

### Arel’e doğrudan taşınamayacak noktalar

- `SessionManager` process-local; çoklu worker/restart için yeterli değil.
- SQLite repository Arel’in Mongo/Redis ve queue sözleşmesine uymuyor.
- Handler'lar upstream bot için yazılmış; Arel handler sınırlarında HTTP veya DB çağrısı yapmamalı.
- UI İngilizce; görünür Türkçe metinler Arel’in merkezî çeviri/UTF-8 denetimine taşınmalı.
- `/play` grup mesajında kullanıcıya özel state açıyor; Arel’de grup oyunlarının scope'u açıkça `chat_id`, `thread_id` ve `owner_id` ile belirlenmeli.
- Akinator görsellerinin lisans/provenance bilgisi yok; assets birebir alınmamalı.
- `ADMIN_SECRET` alanı config'te olsa da admin unlock akışı kaldırılmış; Arel’in mevcut owner/admin policy'siyle karıştırılmamalı.

## Arel’e Türkçeleştirme planı

### Güvenli şekilde çevrilebilecek UI metinleri

`strings.py` içindeki şu metinler Arel’in Türkçe sözlük katmanına taşınabilir:

- start/help metinleri ve menü başlıkları;
- `Play`, `Cancel`, `Back`, `Yes`, `No`, `Probably`, `I don't know`, `Probably not` cevap etiketleri;
- yükleniyor, kapasite dolu, oyun süresi doldu ve hata mesajları;
- profil, istatistik, leaderboard, tema ve child mode ekranları;
- inline davet başlığı/açıklaması;
- admin paneli ve pagination etiketleri.

Callback protocol anahtarları (`a`, `w`, `x`, `menu`, `lead`, `lang`, `theme`, `child`, `is`) ASCII kalabilir; bunlar kullanıcıya gösterilen metin değildir. Komutlar Arel ana isimleriyle (`/akinator`, `/tahmin`, `/karakter`) yayınlanıp `/play`, `/cancel` gibi eski adlar gizli uyumluluk alias'ı olarak bırakılabilir.

### Upstream soruları ve önerileri

Akinator sorularının dili, game start sırasında `language="tr"` verilerek upstream'e bırakılmalı. Arel metin katmanı upstream soru/karakter adını tekrar çevirmeye çalışmamalı; bu isimler HTML escape/sanitize edilmeli. Upstream Türkçe cevap üretmiyorsa otomatik makine çevirisi oyunun anlamını bozabileceği için özellik bayrağı arkasında olmalı.

### Türkçe oyun yüzeyi önerisi

- Varsayılan soru dili: `tr` (Arel Settings ile kullanıcı bazlı).
- UI dili: Arel’in mevcut Türkçe sözlüğü; İngilizce destek korunur.
- Kısa, Telegram'a uygun butonlar: `Evet`, `Hayır`, `Sanırım evet`, `Bilmiyorum`, `Sanırım hayır`, `Geri`, `İptal`.
- Çocuk modu: `Çocuk modu açık`; varsayılan açık.
- Hata metinleri: sağlayıcı/HTTP sınıfı açmadan, güvenli ortak hata kodları.
- Group oyun scope'u: owner-only varsayılan; istenirse davetle ikinci izleyici/oyuncu modu.

## Arel uygulama eşlemesi

| Upstream parçası | Arel’de hedef katman | Uyarlama notu |
|---|---|---|
| `GameService` + `akipy` | `src/games/akinator.py` veya typed feature provider | `DataProvider`/health/circuit breaker; HTTP handler'a çıkmaz |
| `GameSession` | Redis geçici state + Mongo `game_sessions` | TTL, revision, owner/chat/thread scope, restart recovery |
| `GameController` | `src/games/orchestrator.py` | queue job → state commit → Telegram render |
| `keyboards.py` | `src/services/akinator_ui.py` | callback schema, Turkish labels, pagination |
| `presentation.py` | `src/services/media_renderer.py` | shared SSRF/MIME/size policy, file_id cache |
| `db.py` | repository/store interfaces | Mongo/Redis; SQLite kodu kopyalanmaz |
| `strings.py` | i18n/catalog | Türkçe birincil, İngilizce fallback; mojibake testleri |
| `handlers/*` | thin Telegram adapters | commands/callbacks yalnızca typed service çağırır |
| `assets/aki_pics` | Arel-owned artwork veya izinli assets | provenance/license doğrulanmadan kopyalanmaz |

Önerilen `FeatureDefinition`:

```text
key: akinator
category: oyun
commands: akinator, tahmin, play
aliases: aki
input_model: AkinatorAction
output_type: telegram_inline_keyboard
permission: group_user/private_user
provider_key: akipy_akinator
cache_ttl: 0
freshness_sla: realtime
timeout_ms: 120000
async_mode: queue
cost_class: upstream_network
feature_flag: akinator_game
audit_policy: game_action
```

## Test ve kabul planı

### Birim testleri

- `AkinatorAction` için cevap/teklif seçenekleri ve geçersiz callback reddi.
- `session_id + revision + chat_id + thread_id + owner_id` bağlamı.
- Aynı revision callback'i ikinci kez geldiğinde upstream'e tek çağrı.
- Back aksiyonunda ilk sorudan geri gidilememe.
- Timeout/Cloudflare/solver/invalid choice ortak hata kodları.
- Türkçe metinlerde HTML escape ve mojibake kontrolü.
- Child mode ve tema normalizasyonu.
- Akinator image URL SSRF, redirect, authenticated URL, MIME ve byte limitleri.
- Game result/leaderboard istatistiklerinin transaction sonrası tutarlılığı.

### Entegrasyon ve replay

- `akipy` network'ü gerçek çağrı yapmadan kaydedilmiş response fixture'ları ile replay.
- `start_game`, `answer`, `back`, `choose`, `exclude` response'larında state transition testleri.
- Telegram API mock ile fotoğraf/caption edit, inline text edit ve callback answer.
- Redis/Mongo restart sonrası TTL, expired game ve duplicate delivery.
- Forum topic veya group chat'te yanlış callback scope reddi.
- Worker çökmesi sonrası in-flight action'ın replay edilmemesi.

### Canlı kabul

Canlı Akinator endpoint'i kabul testlerinde tek zorunlu kaynak olmamalı; Akinator/Cloudflare değişkenliği nedeniyle fixture/replay gate önce geçmeli. Owner private canary'de `/akinator` başlatma, 2–3 soru, geri, teklif onayı, iptal, restart ve leaderboard kanıtı alınmalı. Canlı test kaydına prompt, kullanıcı mesajı, cookie veya tam upstream URL yazılmamalı.

## Uygulama sırası ve kararlar

1. Lisans/provenance kararı: upstream kod/assets kopyalanmayacak; izin alınırsa attribution dosyası eklenir.
2. `AkinatorService` typed contract ve feature registry kaydı.
3. `akipy` adapter'ı: timeout, circuit breaker, bounded semaphore, error mapping ve health check.
4. Redis/Mongo session repository: TTL, revision, owner/chat/thread scope, recovery.
5. Türkçe i18n ve Arel command/callback yüzeyi; eski komutlar yalnızca gizli alias.
6. Ortak media policy ve Arel-owned visuals.
7. Replay fixture'ları ve Telegram mock E2E.
8. Owner private canary; canlı kanıt olmadan faz `verified` yapılmaz.

## Bulguların özeti

**İyi desteklenen bulgu:** Repo gerçek bir Telegram Akinator oyununu async `python-telegram-bot` + `akipy` + SQLite ile kuruyor; callback revision/owner binding, bounded concurrency, transaction sonrası render ve restart'ta güvenli expiry kod seviyesinde açıkça uygulanmış.

**Sınır:** Repo lisans metadata'sı `null`; kökte lisans metni bulunmuyor. Bu yüzden "birebir ekleme" hukuken doğrulanmış bir izin değildir.

**Arel için güvenli sonuç:** Oyunun kullanıcı akışı ve state invariants bağımsız olarak yeniden uygulanabilir; upstream kaynak dosyaları ve görselleri lisans netleşmeden kopyalamak üretime alınmamalı. Türkçe UI, `tr` soru dili, Arel’in Redis/Mongo/queue ve feature registry sözleşmeleriyle kurulmalı.

**Güven düzeyi:** Mimari ve oyun akışı için yüksek (kaynak kod/test ile doğrudan doğrulandı); `akipy`/Akinator servisinin lisans ve uzun vadeli erişilebilirliği için düşük-orta (bu incelemenin dışında kalan dış kaynaklar).
