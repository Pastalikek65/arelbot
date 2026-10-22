# Arelbot Operasyon ve Dağıtım Rehberi

Bu belge Windows geliştirme, Linux VDS production, yedekleme, rollback,
sağlık, gözlemlenebilirlik ve replay kapılarını tanımlar. Secret
değerleri bu dosyaya yazılmaz.

## Yerel Windows

TELEGRAM_MODE=polling ile MongoDB ve Redis Docker Compose dev profiliyle
çalıştırılır. FFmpeg, ffprobe ve Node.js PATH üzerinde bulunur.

Transport sözleşmesi polling ve webhook modlarını ayrı doğrular; webhook
modunda HTTPS, credential/query içermeyen URL, güvenli path segmentleri,
URL-safe secret ve 1-65535 port aralığı uygulama başlamadan kontrol edilir.

    docker compose --profile dev up -d mongo redis
    python -m pytest -q
    python -m pytest -q --cov=src --cov-report=term:skip-covered
    python scripts/architecture_check.py --strict

Compose MongoDB ve Redis servisleri anonim bağlantıyı reddeder. Yerel
çalıştırma için `.env` dosyasında beş kimlik değişkeni tanımlanmalıdır;
Compose bu değerleri zorunlu tutar ve eksik olduğunda `docker compose
config` hata verir:

    MONGO_ROOT_USERNAME=areladmin
    MONGO_ROOT_PASSWORD=<yerel gelistirme parolasi>
    MONGO_APP_PASSWORD=<yerel gelistirme parolasi>
    MONGO_TEST_PASSWORD=<yerel gelistirme parolasi>
    REDIS_PASSWORD=<yerel gelistirme parolasi>

`MONGO_ROOT_*` idari hesabın, `MONGO_APP_*` uygulama hesabının ve
`MONGO_TEST_*` test hesabının parolasıdır. Bu değerler `.env` dosyasına
yazılır, Git'e girmez ve yalnızca yerel gelistirme içindir. Üretim
parolaları deployment secret store'da tutulur.

Pytest çalıştırması `pyproject.toml` içindeki `timeout = 300` ayarı ile her
testi 300 saniyeyle sınırlar; asılı kalan bir test bütün suite'i kilitlemez.
Kapsama ölçümü `pytest-cov` ile alınır; `[tool.coverage.run]` altındaki
`branch = true` ayarı dal kapsamasını da ölçer ve `--cov-config` seçeneği alt
süreçlerin aynı kapsama yapılandırmasını okumasını sağlar. Toplam kapsama
`[tool.coverage.report]` bloğundaki `fail_under` eşiğinin altına düşerse
kapsama komutu başarısız sonuçlanır.

Windows'ta varsayılan olarak işletim sisteminin doğrulanmış kök sertifika deposu
kullanılır. Kurumsal HTTPS inspection kullanılıyorsa doğrulanmış kök CA dosyası
ARELBOT_EXTRA_CA_CERT ile verilir (çalışan süreçte SSL_CERT_FILE olarak
birleştirilir). TLS verify=False kullanılmaz. Docker build için
ARELBOT_BUILD_CA_FILE BuildKit secret olarak kullanılır.

Bootstrap dispatch'i de aynı sözleşmeyi kullanır: Windows polling profili
yalnızca `run_polling`, Linux webhook profili yalnızca `run_webhook`
çağrısı alır. Bu seçim `src/tests/test_main_transport.py` içinde PTB
uygulaması başlatılmadan iki yönlü olarak doğrulanır.

## Compose ortam dosyaları

Compose'ta iki ayrı ortam kaynağı vardır ve karıştırılmamalıdır:

* **Interpolasyon kaynağı** — `--env-file` ile verilen dosya. `${MONGO_TEST_PASSWORD:?...}`
  gibi referanslar buradan çözülür. Bu dosya konteynere **hiçbir şey enjekte etmez**.
* **`env_file`** — servis tanımındaki dosya. **Konteynere enjekte edilir.**

Profil başına kural:

| Profil | Interpolasyon kaynağı | Konteynere enjekte edilen dosya |
|---|---|---|
| `test` (pytest) | `.env.test` (`--env-file .env.test`) | `.env.test` (`env_file:`) |
| `dev`, `prod`, `monitoring` | `.env` (proje kökü) | `.env` (`env_file:`) |

`test` profili **her zaman** sentetik dosyadan interpolasyon yapar; böylece test
konteynerine production parolası giremez. `.env.test` Git'e girmez; kaynak
commit'li `.env.test.example` dosyasıdır ve eksikse gate bu dosyadan kopyalar:

    python scripts/release_gate.py --docker --requests 20 --concurrency 10

Sürüm kapısı testleri `arelbot-gate` adlı ayrı Compose projesinde çalışır.
Bu projenin Mongo/Redis hacimleri sentetik `.env.test` ayarlarıyla oluşturulur;
üretim ve mevcut geliştirme hacimleri kullanılmaz. Profil grafiği doğrulaması
da sentetik dosyayı kullanır. `bot` ve `worker`
servisleri her zaman `env_file: .env` ile gerçek `.env` üzerinde çalışır.

## Linux VDS production

TELEGRAM_MODE=webhook, HTTPS reverse proxy, ayrı bot ve worker container'ları,
MONGODB_REQUIRED=true ve MEDIA_REDIS_REQUIRED=true kullanılır. .env dosyası
0600 izinli olmalıdır.

Dağıtım öncesi hedef hostun mevcut uygulamalarını durdurmadan salt-okunur
hazırlığı ölçülür. Probe, known-hosts içindeki SSH anahtarını zorunlu
tutar; Docker/Compose, `/opt/arel` yapısı, yazma izni, boş disk kotası,
paylaşımlı env varlığı, mevcut release sayısı, Arel Compose projesine ait
container sayısı ve dinleyen portları okur. Uzak
dosya, container, Nginx veya Telegram durumu değiştirilmez:

    $env:AREL_SSH_PASSWORD='operator-secret'
    $env:ARELBOT_VDS_READINESS_LIVE='1'
    python scripts/vds_readiness.py --host <vds> --user root --live --confirm-live --json --report deploy/evidence/vds-readiness.json

Rapor `ok=false` ise `blockers` alanındaki her madde giderilmeden immutable
release deploy edilmez. Probe host adı, kimlik bilgisi, container adı veya
uzak hata metni saklamaz. `/opt/arel/shared/.env` yoksa deployer'a yalnızca
operator tarafından hazırlanmış, UTF-8 ve 0600 izinli production env dosyası
`--bootstrap-env-file` ile verilebilir; yerel geliştirme `.env` dosyası
otomatik olarak production secret store sayılmaz.

    python scripts/production_preflight.py --nginx-config /etc/nginx/sites-enabled/arelbot
    docker compose --profile prod up -d --build

The optional `--nginx-config` check compares the deployed Nginx file with the
`WEBHOOK_URL` prefix and `WEBHOOK_PATH`. It fails on an exact public-location,
internal upstream-path, TLS-listener, or Telegram secret-header mismatch. The
checked-in example uses `/telegram/webhook`; if a public prefix is configured,
update the exact Nginx location to the public URL path and keep `proxy_pass`
pointing at the Python webhook's internal `WEBHOOK_PATH`.

Reverse proxy yalnızca webhook yolunu botun loopback portuna iletir. DNS,
sertifika, firewall ve Telegram setWebhook gerçek VDS kabul listesinde ayrıca
doğrulanır. Healthcheck başarısızsa Compose restart politikası devreye
girer.

Production preflight, MONGODB_REQUIRED=true, REDIS_REQUIRED=true ve
MEDIA_REDIS_REQUIRED=true ayarlarinin acik olmasini kontrol eder; bellek
fallback'iyle production deploy devam etmez. Preflight ayrica production
`.env` icindeki MONGODB_URI ve REDIS_URL degerlerinin bir kimlik
tadigini tasidigini dogrular; anonim bir baglanti string'i
`mongodb_credentials_missing` veya `redis_credentials_missing` hata koduyla
reddedilir. Public observability bind
kullaniliyorsa OBSERVABILITY_ENABLED=true, OBSERVABILITY_LISTEN=0.0.0.0 ve
host tarafinda 0600 izinli ARELBOT_OBSERVABILITY_TOKEN_FILE tanimlanir.
Preflight token icerigini loglamadan dosya varligini, izinlerini ve URL-safe
formatini dogrular.

Yeni veya boş bir VDS'de `/opt/arel/shared/.env` yoksa deployer, secret içeriğini terminale yazmadan açıkça verilen yerel dosyayı SFTP ile `/tmp` altında taşır ve yalnızca uzak shared env yoksa `install -m 600` ile kurar:

    python scripts/deploy_release.py --host <vds> --user root --bootstrap-env-file C:/secure/arelbot.production.env

Mevcut uzak `.env` üzerine yazılmaz. Yerel dosya normal UTF-8 dosya olmalı, 128 KiB sınırını aşmamalı ve symlink olmamalıdır. Uzak preflight veya sağlık kontrolü başarısız olsa bile archive geçici dosyaları temizlenir; operator env dosyası rollback sırasında korunur.

EVREN kullanilacaksa anahtar yalnizca deployment secret store icinden verilir;
repository veya Docker image icine yazilmaz. OpenAI-compatible route asagidaki
gibi acikca secilir:

    EVREN_URL=https://evren-llmapi.ssyz.org.tr/v1
    EVREN_MODEL=deepseek-v4.1-flash
    AI_PROVIDER_ROUTES={"chat":["nvidia","evren"],"fast":["nvidia","evren"],"analysis":["nvidia","evren"],"summary":["nvidia","evren"],"sticker_tagging":["nvidia","evren"]}

`EVREN_LLM_API_KEY` degeri preflight/health/log raporlarinda gorunmez. NVIDIA anahtari ayarlandiginda gorevler varsayilan olarak `nvidia -> evren` sirasini kullanir; NVIDIA anahtari yoksa yalnizca EVREN rotasi etkin kalir. Baska bir provider route'u acikca yazilirsa o allowlist kullanilir. Her provider kullanim sartlari hesapta kabul edilmeden production istegi baslatilmaz.

NVIDIA NIM opsiyonel bir OpenAI-compatible failover'dur. Anahtar yalnızca
deployment secret store'dan `NVIDIA_API_KEY` olarak verilir; repository veya
image içine yazılmaz. EVREN ve NVIDIA birlikte kullanılacaksa route sırası
açıkça tanımlanır:

    NVIDIA_URL=https://integrate.api.nvidia.com/v1
    NVIDIA_MODEL=deepseek-ai/deepseek-v4.1-flash
    AI_PROVIDER_ROUTES={"chat":["nvidia","evren"],"fast":["nvidia","evren"],"analysis":["nvidia","evren"],"summary":["nvidia","evren"],"sticker_tagging":["nvidia","evren"]}

NVIDIA modeline reasoning payload'i gönderilmez; istekler ortak token, timeout
ve medya boyutu sınırlarından geçirilir. NVIDIA terms, quota ve model lisansı
onayı olmadan production route'u açılmaz.

Hızlı sohbet yolu `AI_FAST_TIMEOUT_SECONDS` ile sınırlıdır (varsayılan 8
saniye). Tek sağlayıcılı bir route geçici 408/429/5xx cevabı verirse
`AI_PROVIDER_RETRY_ATTEMPTS` (varsayılan 1) kadar kısa aralıkla yeniden denenir;
okuma zaman aşımı ikinci kez beklenmez. `AI_PROVIDER_RETRY_BACKOFF_MS`
varsayılan olarak 150 milisaniyedir. Böylece anlık upstream titremesi kullanıcıya
hemen servis kesintisi olarak yansımaz, kalıcı yavaşlıkta ise yanıt süresi
kontrollü kalır.

Geçici sağlayıcı hatasında anahtar soğuma süresi 10 saniyedir; bu süre kota
veya geçersiz anahtar askıya alma davranışından ayrıdır.

EVREN model endpointi için bounded canlı probe yalnızca deployment secret ile,
ayrı bir operatör işlemi olarak çalıştırılır. Probe prompt, model çıktısı veya
anahtar yazmaz; secret-free `evren-staging.json` kanıtı üretir. Clean APIs
probe'u ve canlı route'u artık release sürecinin parçası değildir. EVREN route'u
üretime açılmadan önce provider terms ve governance onayı tamamlanmalıdır.

### Telegram staging probe

The probe is read-only and has a deliberately offline default. It validates
only the token shape without echoing the token:

    python scripts/telegram_staging_probe.py --env-file staging.env --json

To contact Telegram, the operator must provide all three independent signals;
the command reads `getMe`, `getWebhookInfo` and `getMyCommands` only. It never
sends a message or changes webhook/command state. The report contains no token,
webhook URL or Telegram error text:

    ARELBOT_TELEGRAM_STAGING_LIVE=1 python scripts/telegram_staging_probe.py --env-file staging.env --live --confirm-live --json --max-pending-updates 100 --report deploy/evidence/telegram-staging.json

The Telegram report is written atomically and rejects symlink targets/parents;
it contains only bounded identity, webhook status, backlog warning state and command metadata. `--max-pending-updates` is an optional fail-closed deployment gate; the default probe reports a warning above 100 pending updates without mutating Telegram. Public
provider staging is a separate, bounded matrix. Its report may be written
atomically for release evidence; provider exception text is not
included:

    ARELBOT_STAGING_LIVE=1 python scripts/staging_matrix.py --config staging-matrix.json --live --confirm-live --report reports/staging-matrix.json

### Telegram mutation staging

Mutation evidence is a separate operator action. The runner never contacts
Telegram unless all three command flags and the environment gate are present:

    ARELBOT_TELEGRAM_MUTATION_LIVE=1 python scripts/telegram_mutation_staging.py --env-file staging.env --live --confirm-live --allow-mutation --chat-id <staging-chat-id> --thread-id <forum-thread-id> --video-file-id <staging-video-file-id> --sticker-file-id <staging-sticker-file-id> --file-id <staging-file-id> --callback-query-id <callback-id> --json --report deploy/evidence/telegram-mutation-live.json

The chat, optional forum thread, and Telegram file/callback identifiers are
supplied by the operator and are never written to the report. Missing media or
callback identifiers fail closed. A normal supergroup has no forum topic; when
`--thread-id` is omitted, the forum operation is skipped and the remaining
operations are still checked. The runner sends bounded staging messages and
therefore must target a disposable staging chat, never a production group.

#### Kimliklerin güvenli şekilde toplanması

`chat_id` tek başına tam mutation kanıtı için yeterli değildir. Telegram Bot
API, bir `file_id` veya callback kimliğini yalnızca ilgili güncellemenin içinde
verir; bunlar URL'den, grup kullanıcı arayüzünden veya `getChat` çağrısından
çıkarılamaz. Aşağıdaki değerler operatör tarafından ayrı ayrı sağlanır:

- `thread_id`: yalnızca forum grubu kullanılıyorsa, test mesajının ait olduğu
  konu kök mesajının `message_thread_id` değeridir. Normal süpergrupta boş
  bırakılır.
- `video_file_id`: test grubuna gönderilmiş kısa bir videonun
  `message.video.file_id` değeri.
- `sticker_file_id`: test grubuna gönderilmiş statik veya video sticker'ın
  `message.sticker.file_id` değeri.
- `file_id`: test grubuna gönderilmiş desteklenen bir belgenin
  `message.document.file_id` değeri. Aynı değer `getFile` ve
  `sendDocument` smoke adımlarında kullanılır.
- `callback_query_id`: test grubunda botun inline düğmesine basıldığı anda
  oluşan `callback_query.id` değeri. Bu değer kısa ömürlüdür; callback
  alındıktan hemen sonra kullanılmalıdır.

Çalışan polling veya webhook sürecinin yanında `getUpdates` çağırarak bu
değerleri toplamaya çalışmayın. Polling güncellemeyi yarışta tüketebilir,
webhook modunda ise Telegram `getUpdates` çağrısını reddeder. Bu yüzden canlı
uygulamanın güncelleme akışını durdurmadan kimlikleri otomatik çekmek güvenli
değildir. Botun güncelleme işleyicisinde bu değerleri raporlamak için kalıcı
bir debug özelliği de üretimde açılmamalıdır; file ID ve callback ID hassas
transport kimlikleridir.

Önerilen disposable staging akışı:

1. `-1003896362491` grubunun gerçekten disposable olduğunu, botun yönetici
   olduğunu ve owner test hesabının bulunduğunu doğrulayın. Üretim grubunda
   mutation çalıştırmayın.
2. Botun güncelleme akışı çalışırken gruba kısa bir video, bir sticker ve
   küçük bir `.txt`/`.pdf` belge gönderin. Forum kullanılıyorsa aynı topic'te
   gönderin; `thread_id` değerini uygulamanın güvenli staging çıktısından veya
   Telegram update inspector'ından alın.
3. Her medya türünün `file_id` değerini deployment secret store'a yazın;
   komut satırına, Git'e, rapora veya sohbete kopyalamayın. Callback için
   düğmeye basıldığı update'in `callback_query.id` değerini hemen kullanın.
4. Önce yalnızca dosya/kimlik şekli doğrulamasını çalıştırın. Sonra üç canlı
   kapıyı (`--live`, `--confirm-live`, `--allow-mutation`) ve
   `ARELBOT_TELEGRAM_MUTATION_LIVE=1` ortam kapısını aynı anda vererek runner'ı
   çalıştırın. Rapor dosyası yalnızca disposable staging kanıtı olarak
   saklanmalıdır.

Bu bilgilerin hiçbiri kullanıcıdan parolası, cookie'si veya Telegram token'ı
istemeyi gerektirmez. Yalnızca test grubunun `chat_id` değeri biliniyorsa,
read-only Telegram probe çalıştırılabilir; tam mutation kanıtı fail-closed
kalır ve `telegram-mutation-live.json` üretim kanıtı sayılmaz.

## Live medya staging matrisi

Canlı platform denetimi offline testlerin yerine geçmez. Matris varsayılan olarak yalnızca yapıyı doğrular; dış ağa erişim için üç bağımsız onay gerekir:

    ARELBOT_MEDIA_STAGING_LIVE=1 python scripts/media_staging_matrix.py --config docs/media-live-matrix.example.json --live --confirm-live --report deploy/evidence/media-staging-live.json

Gerçek dosya indirme ayrıca `--download` ister. Canlı indirme raporu `scope=live-media-matrix` taşır; tam URL, başlık, cookie ve upstream hata metni saklamaz; yalnızca platform, host, süre, boyut ve sabit hata kodu taşır. Her iş sonrası geçici medya klasörü temizlenir.
Tam deployment kanıtı için TikTok, YouTube Shorts ve X vakaları etkinleştirilir; Instagram erişim kısıtı varsa owner waiver ile birlikte `--waive-platform instagram --waiver-approver owner --waiver-reason "source access restricted" --require-platform-matrix --require-slo --report deploy/evidence/media-staging-full-matrix.json` kullanılır. Waiver rapora yazılır ve release auditor tarafından doğrulanır.

## Immutable release

scripts/deploy_release.py temiz ve commit edilmiş Git checkout ister. Release
arşivi RELEASE.json ve RELEASE_MANIFEST.sha256 ile hash'lenir. Preflight,
bot/worker health ve rollback tamamlanmadan current release symlink'i
değiştirilmez. SSH host key daha önceden known_hosts içinde bulunmalıdır.

Arşivi SSH bağlantısından bağımsız üretmek için temiz checkout'ta
`python scripts/deploy_release.py --host example.invalid --build-only`
çalıştırılabilir. Bu kip ağa bağlanmaz; yalnızca arşiv yolunu ve
SHA-256 değerini verir. Normal kip aynı checksum doğrulamasından sonra
SSH/SFTP ile yükleme, preflight, health bekleme ve başarısız aktivasyonda
rollback uygular.


SSH kurulum betiği fail-closed çalışır: preflight veya health başarısızlığında
commit edilmemiş release klasörünü ve yüklenen arşivi EXIT trap ile temizler.
Bilinen önceki release varsa rollback sırasında korunur; başarılı aktivasyonda
yalnızca geçici yükleme dosyası silinir. Bu davranış `test_deploy_release.py`
ile Linux/Bash replay ve Docker release gate içinde doğrulanır.

## Health ve yönetici komutları

Telegram yüzeyleri:

- /health: MongoDB, Redis, FFmpeg, provider ve worker durumları.
- /metrics: kuyruk derinliği, servis ve typed feature p50/p95/p99 gecikmeleri,
  başarı/hata sayaçları, AI token/maliyet ve feature telemetry.
- /providers: provider health, circuit ve AI sağlayıcı durumu.
- /providers governance: kaynak yönetişimi, robots/terms incelemesi ve üretim sign-off durumunu URL veya secret göstermeden listeler.
- `scripts/evidence_audit.py`: deterministik staging kanıtlarının şema/invariant/gizli veri kontrolü; `--require-live` ayrıca gerçek Telegram mutation, tam medya matrisi, VDS backup/restore, monitoring delivery ve provider approval kanıt dosyalarını zorunlu tutar.
- `--require-live` dosyanın yalnızca mevcut olmasına veya `ok=true` yazmasına güvenmez: `telegram-mutation-live`, `live-media-matrix`, `vds-backup-restore`, `live-monitoring-alert-delivery` ve `provider-governance-approval` scope'larını; işlem sayılarını, 10 saniye medya p95'ini, en az 100 sohbet slotunu, checksum'ı ve approval ID'lerini ayrıca denetler.
- `scripts/release_gate.py --report <path>` gate çıktısını atomik ve redacted JSON kanıtı olarak saklar; rapor hedefi symlink ise yazmayı reddeder.
- /jobs <id>: herhangi bir worker işinin durumu.
- /audit: maskeli yönetici işlem kayıtları.
- /maintenance on|off: medya bakım kill-switch'i.

## Loopback observability endpoint

OBSERVABILITY_ENABLED=true ile opsiyonel /healthz ve Prometheus uyumlu
/metrics açılır. Bot OBSERVABILITY_PORT, worker
OBSERVABILITY_WORKER_PORT kullanır. Varsayılan bind 127.0.0.1'dir. Public
bind için 16-256 karakterlik OBSERVABILITY_TOKEN zorunludur; token
X-Arelbot-Metrics-Token başlığı veya Bearer şemasıyla gönderilir.
Endpoint yalnızca bounded GET kabul eder, query verisini echo etmez, kullanıcı
etiketi üretmez ve Cache-Control no-store döner. Reverse proxy/firewall
arkasında tutulmalıdır. `/healthz` `dependencies`, `providers` ve `status`
alanlarını döndürür; `healthy` çekirdek ve provider'ların iyi olduğunu,
`degraded` yalnızca opsiyonel provider sorunu bulunduğunu, `unhealthy` ise
MongoDB/Redis gibi zorunlu bağımlılığın erişilemediğini belirtir. Beş saniyelik
snapshot cache'i polling trafiğinin upstream provider'ları tetiklemesini önler.
HTTP sözleşmesinde `healthy` ve `degraded` için 200, `unhealthy` için 503
döner; Docker, reverse proxy ve readiness probe'ları zorunlu bağımlılık
arızasında yanlışlıkla sağlıklı rapor vermez.

## Backup ve restore

MongoDB dump, Redis RDB ve mevcut Chroma klasörü tek atomik backup klasörüne
alınır. BACKUP.json SHA-256 ve byte boyutlarını taşır.

    python scripts/backup.py --output-dir backups

MongoDB ve Redis parolasi komut satirinda gecmez: `mongodump`/`mongorestore`
baglanti dizisini gecici bir 0600 YAML dosyasindan okur, `redis-cli` parolayi
`REDISCLI_AUTH` ortam degiskeninden alir. Ayrinti icin "MongoDB ve Redis
üretim ağı" bolumune bakin.

Restore varsayılan olarak yalnızca checksum doğrular. Destructive işlem için
confirm gerekir.

    python scripts/restore.py backups/arel-YYYYMMDDTHHMMSSZ --mongo-uri MONGODB_URI --redis-rdb-target /var/lib/redis/dump.rdb --chroma-target /opt/arel/shared/chroma_db --confirm

Redis RDB hedef data dizinine servis durdurularak yerleştirilir ve Redis
yeniden başlatılır. Staging restore sonrası manifest, koleksiyon sayıları
ve Chroma arama smoke testi yapılır. Mongo namespace remap kullanılıyorsa
mongo-source-database ve mongo-target-database birlikte verilir; restore
kodı URI veritabanı yolunu kaldırıp nsFrom/nsTo uygulayarak kaynak
arşivini hedefe taşır. Symlink hedefleri ve archive traversal reddedilir.

Compose staging kanıtı için yalnızca isimlendirilmiş sentinel verisi kullanan
Mongo dump/namespace-remap ve Redis RDB snapshot/load smoke çalıştırılabilir:

    python scripts/persistence_staging_smoke.py --report deploy/evidence/persistence-staging.json

Komut sonunda sentinel verisi, geçici container ve arşiv dosyası temizlenir;
rapor credential veya provider çıktısı içermez. Bu kontrol VDS backup/restore
tatbikatının yerine geçmez; release öncesi gerçek staging hedefinde tekrar
uygulanmalıdır.

Gerçek VDS kanıtı için backup/restore koşucusu dört bağımsız kapı ister:

    ARELBOT_VDS_BACKUP_LIVE=1 python scripts/vds_backup_restore_staging.py --live --confirm-live --confirm-destructive --mongo-uri "$MONGODB_URI" --redis-url "$REDIS_URL" --restore-mongo-uri "$STAGING_MONGODB_URI" --restore-redis-target /var/lib/redis-staging/dump.rdb --restore-chroma-target /opt/arel/staging/chroma_db --mongo-source-database arel_bot --mongo-target-database arel_bot_restore --report deploy/evidence/vds-backup-restore.json --json

Üretim Compose topolojisinde host işletim sistemine Mongo/Redis CLI kurmak
gerekmez. `scripts/vds_backup_restore_remote.py` aynı tatbikatı SSH üzerinden
çalışan Mongo, Redis ve worker container'larında yürütür; Mongo hedef namespace'i
benzersiz ve geçicidir, Redis ayrı bir disposable container'da açılır, Chroma
geçici dizine çıkarılır. Üretim servisi durdurulmaz. Komut parola değerini
komut satırına yazmaz; parola yalnızca `AREL_SSH_PASSWORD` ortam değişkeninden
okunur ve rapora alınmaz:

    AREL_SSH_PASSWORD='***' ARELBOT_VDS_BACKUP_LIVE=1 python scripts/vds_backup_restore_remote.py --host <vds-host> --user root --live --confirm-live --confirm-destructive --report deploy/evidence/vds-backup-restore.json --json

Bu komut yalnızca operatörün açıkça belirttiği disposable staging hedefinde
çalıştırılır. Restore hedefi aynı Mongo namespace olamaz; Redis servisi hedef
RDB yerleştirilmeden önce durdurulmalı ve sonrasında yeniden başlatılmalıdır.
Rapor URI, parola, host yolu veya komut çıktısı içermez; artifact içeriğinin
SHA-256 özetiyle birlikte backup manifesti, checksum ve restore adımlarını
kanıtlar. Rollback tatbikatı için önce aynı hedefe ayrı bir geri dönüş backup'ı
alınır; bu koşucu bunu kendiliğinden üretim verisine uygulamaz.

## Migration ve rollback

Migration runner ileri ve geri yürütülebilir; mevcut schema v20'tür.

    python scripts/migrate.py
    python scripts/mongo_migration_smoke.py

Production rollback öncesi backup alınır. Migration rollback sonrası index,
TTL ve unique constraint'ler tekrar smoke ile doğrulanır.

### v20 ekonomi ledger seed'i

v20, ekonomi hesabını tek belgeye taşır ve `economy_transactions` koleksiyonundaki
geçmiş satırları hesap dokümanındaki `applied_events` alanına seed eder. Seed
olmadan migration öncesi kullanılmış bir `event_id` yeni görünür ve deploy
gününün `daily:` claim'i iki kez ödenebilirdi.

- Seed bir **veri migration**'ıdır: gruplar `allowDiskUse` ile diskte gruplanır
  ve akış halinde işlenir, hesap başına en fazla 500 satır yazılır, ilerleme
  her 500 grupta bir loglanır. Büyük bir veritabanında ilk çalışma **dakikalar
  sürebilir**.
- Migration bot açılışında da çalışır. Büyük veritabanında botu beklemeden
  `python scripts/migrate.py` ile uygulamak, seed'i bot açılışından ayırmak
  için önerilen yoldur.
- Seed idempotent ve resumable'dır: aynı satırlar aynı değerlerle yazılır,
  zaten seed edilmiş event'ler atlanır. Hata ile durursa v20 uygulanmış
  sayılmaz; `python scripts/migrate.py` tekrar çalıştırıldığında kaldığı yerden
  devam eder. **Seed tamamlanmadan bot başlatılmamalıdır** (bot bu durumda
  açılmaz).
- **Deploy penceresi:** seed, deploy gününün çift claim penceresini kapatır.
  Seed satırları okunmadan bir `/gunluk` claim penceresini geçen deploy
  yapılmamalıdır.

### v20 rollback'in kaybı

Rollback `applied_events` alanını silmeden önce içindeki history'yi
`economy_transactions` koleksiyonuna geri yazar, böylece yalnızca hesap
dokümanında duran event'ler kaybolmaz. Ancak rollback hesap kimliği
(`user_id`, `chat_id`) unique index'ini de düşürür; index base şema tarafından
yeniden kurulana kadar veritabanı v1'in tanımladığı index'ten yoksundur. Geri
alınan kod yolunun idempotency'i bu index'e değil, `economy_transactions`
üzerindeki unique `event_key` index'ine dayandığı için geri alınan kod yolu
doğru çalışmaya devam eder. Migration rollback sonrası index, TTL ve unique
constraint'ler tekrar smoke ile doğrulanır.

## Worker kuyrukları

Media, document, attachment, quote, speech, sticker tagging, market chart ve
image işleri Telegram handler içinde çalışmaz. Redis taşıma katmanı,
Mongo job kaydı gerçek durum kaynağıdır. Worker yeniden başladığında
queued/downloading/processing işler recovery politikasıyla yeniden
değerlendirilir. Delivery ledger sending sonrası belirsiz sonucu duplicate
gönderimden korur.

ProviderRuntime stale-while-revalidate görevleri shutdown sırasında ayrıca
iptal edilir. Feature executor önce health yazma işlerini boşaltır, sonra
refresh task'lerini durdurup cache/inflight state'ini temizler; provider HTTP
istemcileri ancak bundan sonra kapanır. Böylece restart sonrasında eski bir
task'in kapanmış istemci üzerinden yeni network çağrısı başlatması engellenir.

## Medya diski ve geçici dosya temizliği

Bot ve worker ayrı konteynerlerde çalışır ve **aynı `arel_downloads` adlı
hacmi** paylaşır (`/app/downloads`). Bu yüzden iki süpürücü de aynı hacmi
temizler: bot süpürücüsü worker'ın ürettiği iş dizinlerine de ulaşır, ama
süpürücüyü yalnızca bot sürecine bırakmak savunma derinliği olmazdı. İki
süreç de kendi süpürücüsünü çalıştırır.

    bot     : src/orchestrator.py -> arel-file-cleanup görevi (3600 sn)
    worker  : src/worker.py       -> arel-file-cleanup görevi (3600 sn)

Süpürücü yalnızca dosya silmez: `downloads/<job id>/` gibi eski iş dizinlerini
de kaldırır. Süpürülen dizinler yapılandırmadan okunur
(`MEDIA_DOWNLOAD_DIR`, `RAG_UPLOAD_DIR`, `TTS_OUTPUT_DIR`, `QUOTE_OUTPUT_DIR`,
`STICKER_TEMP_DIR`, `ATTACHMENT_TEMP_DIR`, `STICKER_TAG_TEMP_DIR`); üretici ile
süpürücü aynı değeri okuduğu için taşınmış bir dizin artık geride kalmaz.

Süpürücü temizlediği dizinin dışına çıkmaz. Sembolik bağlantı, Windows
junction ve mount point takip edilmez ve kökün dışına çözen bir girdi hiçbir
zaman silinmez; böylece spool içindeki bir bağlantı dışarıdaki dosyaları
silemez. Bir dizin ancak süpürme sonrası boşaldıysa kaldırılır, böylece
indirmesi devam eden iş dizini kaybolmaz. Dosya silme işlemi `asyncio.to_thread`
ile event loop dışında yürütülür; bot ve worker'ın olay döngüsü süpürme
sırasında bloklanmaz. Geri alınan girdi sayısı `media_cleanup_entries_removed`
sayacıyla `/metrics` çıktısında görünür.

Zaman aşımına düşen indirme iş dizinini bir arka plan reaper'ına devreder.
Reaper, indirme işçisi gerçekten durmadan dizini silmez; servis periyodik
görevi ve kapanış sırasındaki `wait_for_background_cleanup()` bu reaper'ları
sınırlı süre bekleyerek boşaltır. Reaper takılırsa dizin yaş kuralıyla da bir
saat sonra geri alınır.

`downloads` hacmi dolduğunda **tüm medya teslimatı durur**. Kapasite izlemede
bu hacim için ayrı alarm tanımlanmalıdır. Elle müdahale yalnızca acil durum
içindir:

    docker compose --profile prod exec worker sh -c 'du -sh /app/downloads'
    docker compose --profile prod restart worker

Normal akışta elle silme yapılmaz; süpürücü aynı yaş kuralını kendiliğinden
uygular.

## Replay ve kalite kapıları

    python scripts/release_gate.py --docker --requests 20 --concurrency 10
    python scripts/security_replay.py
    python scripts/provider_matrix.py
    python scripts/provider_load_test.py --requests 100 --concurrency 10
    python -m pytest -q src/tests/test_media_delivery_replay.py
    python scripts/media_queue_load_test.py --jobs 100 --workers 10 --chats 100 --report deploy/evidence/media-queue-load.json
    $env:ARELBOT_MEDIA_STAGING_LIVE="1"; python scripts/media_staging_matrix.py --config docs/media-live-matrix.example.json --live --confirm-live --download --concurrency 10 --jobs 10 --chat-count 100 --require-slo --report deploy/evidence/media-staging-concurrency.json

Release gate; architecture, provider metadata, compileall, pytest, Docker
build/test, health, migration, provider matrix, Mongo ledger, security replay
ve media delivery replay kontrollerini, ayrıca commit'lenmiş kanıt denetimi
(`scripts/evidence_audit.py`) ve faz sözleşme denetimini
(`scripts/phase_audit.py`) birlikte çalıştırır. Canlı
Telegram, VDS webhook ve platform medya matrisi bu deterministic kapının
yerine geçmez; staging kimlik bilgileriyle ayrı yürütülür.

`--requests` ve `--concurrency` değerleri yük kanıtının gücünü belirler; arşiv
olan `deploy/evidence/release-gate.json` bu değerleri kaydeder. Kanıtı
üretirken operasyon runbook'undaki değerler kullanılmalıdır.

## Monitoring profile

Prometheus ve Grafana yalnızca `monitoring` profiliyle açılır; ikisi de
host'ta loopback'e bind edilir. Production'da bot ve worker gözlem endpoint'i
için `OBSERVABILITY_ENABLED=true`, `OBSERVABILITY_LISTEN=0.0.0.0` ve
`ARELBOT_OBSERVABILITY_TOKEN_FILE` tanımlanır. Aynı token dosyası Prometheus'a
secret olarak mount edilir; token `.env`, dashboard veya loglara yazılmaz.
Monitoring profili açılacaksa MONITORING_ENABLED=true ayarlanır; preflight aynı anda observability'nin etkin olduğunu ve GRAFANA_ADMIN_PASSWORD_FILE dosyasının 0600 izinli, gerçek bir parola içerdiğini kontrol eder.

    docker compose --profile prod --profile monitoring up -d

Grafana admin parolası `GRAFANA_ADMIN_PASSWORD_FILE` ile dışarıdan secret
olarak verilir. Varsayılan placeholder ile production monitoring başlatılmaz;
gerçek secret dosyaları deployment host'unda 0600 izinli tutulur. Dashboard
Prometheus kuralları `deploy/monitoring/alerts.yml` içinde bot/worker scrape
kesintisi, medya kuyruk backlog'u, workersız kuyruk ve düşen provider health
telemetrisi için alarm üretir.
`deploy/monitoring/grafana/dashboards/arelbot-overview.json` kuyruk derinliği,
medya p95 gecikmesi, iş başarı/başarısızlık oranı ve AI kullanımını gösterir.

Alertmanager aynı `monitoring` profiline dahildir ve Prometheus'un beş kuralını
`deploy/monitoring/alertmanager.yml` üzerinden tek bir HTTPS webhook alıcısına
iletir. Alıcı URL'si `ARELBOT_ALERT_WEBHOOK_URL_FILE` ile 0600 izinli secret
dosyasından okunur; production preflight HTTP, private/loopback host, kimlik
bilgisi ve query-string içeren hedefleri reddeder.

Disposable staging teslimatı için yalnızca loopback Alertmanager API'sine
canary gönderilir:

    ARELBOT_MONITORING_LIVE=1 python scripts/monitoring_alert_staging.py --live --confirm-live --confirm-notification --alertmanager-url http://127.0.0.1:9093 --report deploy/evidence/monitoring-alert-delivery.json --json

Koşucu `/-/ready`, `/metrics` ve `/api/v2/alerts` akışını ölçer; webhook
entegrasyon sayacı artmadan başarılı rapor üretmez. Varsayılan çalıştırma
offline'dır ve hiçbir alıcıya istek göndermez.

Alert rule replay is pinned in `deploy/monitoring/alerts.test.yml`; the Docker
release gate runs `promtool test rules` for all five rules, including exact
labels and annotations. Live Alertmanager/webhook delivery remains a VDS-stage
check.

## Source ve secret politikası

Her typed provider docs/DATA_SOURCES.md ve licenses.json içinde lisans,
attribution, terms, rate limit, freshness ve fallback kaydı taşır. Token,
cookie, tam URL, credential, kullanıcı metni ve medya path'i loglanmaz.
Provider terms onayı olmayan adapter feature flag/kill-switch arkasında
tutulur.

Public HTML provider transportu HTTPS kaynak hostu, redirect kapatılması,
content-type ve 2 MiB response sınırını ortak uygular. `PROVIDER_RESPONSE_TOO_LARGE`,
`PROVIDER_CONTENT_TYPE_INVALID` ve `PROVIDER_HOST_NOT_ALLOWED` hataları sabit
kullanıcı hata kodlarına dönüşür; upstream yanıtı cache'e veya loga aktarılmaz.

Üretim operasyon kanalında `LOG_CHANNEL_ERRORS_ONLY=true` kullanılır. Bu ayar
normal kullanıcı mesajı ve bot yanıtı kayıtlarını Telegram log konusuna
göndermez; yalnızca güvenli hata sınırından üretilen hata olayları gönderilir.

`APP_ENV=production` için `REQUIRE_PROVIDER_APPROVAL` varsayılan olarak açıktır.
Bu runtime guard, preflight atlanıp Compose elle başlatılsa bile `pending` veya
`disabled` dış provider'ın ağ çağrısından önce `FEATURE_DISABLED` dönmesini
sağlar. Development/replay için örnek `.env` dosyası bunu açıkça `false` yapar;
üretimde kapatılması önerilmez ve release kanıtı olmadan yapılmamalıdır.

### Provider kill-switch

The runtime reads `FEATURE_FLAGS` as a JSON object and `DISABLED_FEATURES` as
a comma-separated list. Feature keys and the optional
`FeatureDefinition.feature_flag` name are both accepted. An explicit false
value prevents the provider call before DNS/network work starts and returns
the stable `FEATURE_DISABLED` error code. `/features` shows each registered
feature as `acik` or `KAPALI`; `/providers reset` only resets circuit state and
does not re-enable a kill-switched source. Keep the change in the deployment
environment and record the operator action in the release/audit log.

The generic runtime toggle applies only to provider-backed features because
`FeatureExecutor` owns that execution path. Local games, renderers, sticker
flows and queue-owned services keep their own maintenance controls; `/features`
will refuse to toggle those keys instead of reporting a misleading state.

### Media latency budget and hard timeout

MEDIA_COMPLETION_SLO_SECONDS controls the latency budget and bounded progress updates. It records an SLO breach without cancelling the durable job. MEDIA_JOB_TIMEOUT_SECONDS is the hard cancellation ceiling for one provider attempt; keep it separate from the 10-second completion target so slow public sources remain recoverable and observable.

## Provider governance preflight

Provider adapter'larının teknik olarak cevap vermesi, kaynağın kullanım
şartlarının onaylandığı anlamına gelmez. Release gate pending kaynakları görünür
tutar; VDS açılışında sign-off zorunlu hale getirmek için aşağıdaki komut
kullanılır:

    python scripts/provider_governance.py --require-approved
    python scripts/production_preflight.py --env-file .env --require-provider-approval --nginx-config /etc/nginx/sites-enabled/arelbot.conf

Bu kontrol canlı kaynağa çağrı yapmaz ve çıktıda source URL, credential veya kullanıcı verisi yazmaz. Onaylanmamış kaynaklar feature kill-switch ile kapalı
tutulmalı; manifestte `source_governance`, `robots_policy`, `terms_review` ve
`production_signoff` alanları birlikte güncellenmeden açılmamalıdır.

Üretim başlangıcında henüz incelenmemiş kaynaklarla birlikte çekirdek botu
çalıştırmak gerekiyorsa, bekleyen provider'lar `.env` içindeki
`DISABLED_FEATURES` listesine feature key olarak açıkça yazılır. Preflight bu
listeyi governance kontrolüne taşır; listede olmayan aktif provider hâlâ
fingerprint-bound approval ister. Bilinmeyen bir feature key fail-closed
sonuçlanır ve runtime aynı kaynağı ağ çağrısından önce `FEATURE_DISABLED` ile
durdurur. Devre dışı bırakılan feature'lar approval raporunda `disabled` olarak
görünür; bu mekanizma hukuki/terms onayının yerine geçmez.

Örnek (yalnızca kaynak incelemesi tamamlanana kadar geçici kapatma):

    DISABLED_FEATURES=pharmacy,fuel,currency,crypto,market,earthquakes,bim_deals,breaking_news,weather,air_quality,prayer_times,culture_recommendation,lyrics,image_generation
    python scripts/production_preflight.py --env-file .env --require-provider-approval --nginx-config /etc/nginx/sites-enabled/arelbot.conf

İlk VDS kurulumunda public domain henüz hazır değilse `TELEGRAM_MODE=polling`
açıkça seçilebilir. Bu modda `WEBHOOK_URL`, `WEBHOOK_SECRET` ve Nginx
gerektirilmez; deploy scripti proxy kontrolünü otomatik olarak atlar. Kalıcı
üretim modeli için HTTPS webhook önerilir ve polling yalnızca erişim modeli
olarak bilinçli biçimde seçildiğinde çalışır.

### Staging core profili

Kaynak incelemesi tamamlanmadan botun Telegram, AI, yerel veri ve yönetim
yüzeylerini test etmek için bekleyen provider'ların tamamını açıkça kapatan
secret'sız bir profil üretilebilir:

    python scripts/staging_profile.py \
      --template deploy/evidence/provider-approval-review-template.json \
      --report deploy/evidence/staging-core-profile.json

Komut dış ağa çıkmaz, onay vermez ve approval evidence üretmez. Raporun
`disabled_features_csv` alanı staging `.env` dosyasında
`DISABLED_FEATURES` olarak kullanılabilir. `REQUIRE_PROVIDER_APPROVAL=true`
korunmalıdır; bilinmeyen bir feature anahtarı preflight ve governance
kontrolünde fail-closed olur. Bu profil üretim onayının yerine geçmez ve
canlı deployment evidence sayılmaz.

Runtime profilini doğrudan bağlamak için staging ortamında
`STAGING_PROFILE_PATH=deploy/evidence/staging-core-profile.json` verilebilir.
Uygulama profili başlangıçta doğrular ve bekleyen provider'ları registry'ye
ekler; aynı dosya `APP_ENV=production` ile kullanılırsa fail-closed olarak
başlamaz.

### Kaynak inceleme çalışma kâğıdı

Onay dosyası doldurulmadan önce, mevcut feature/provider kimliğine bağlı
fingerprint'leri ve inceleme durumunu içeren güvenli bir çalışma kâğıdı
üretilebilir:

    python scripts/provider_governance.py --review-template --report deploy/evidence/provider-approval-review-template.json

Bu çıktı bir approval manifesti değildir; approval ID, reviewer ve geçerlilik
penceresi içermediği için production tarafından yüklenemez. URL'ler rapora
kopyalanmaz. İnceleme tamamlandığında operatör, `docs/DATA_SOURCES.md` ve
provider'ın güncel terms/robots kayıtlarını kontrol ederek ayrı approval
manifestini oluşturur. Fingerprint değişmişse eski onay kullanılmaz.

### Fingerprint-bound approval manifest

Kod manifestindeki dört alanı elle değiştirerek kaynak açmak yerine, gerçek
inceleme tamamlandığında `PROVIDER_APPROVAL_MANIFEST` ile makine tarafından
doğrulanan bir operator kanıtı yüklenebilir. Dosya yalnızca public kaynak
kimliğinin SHA-256 fingerprint'ini, inceleyen kişiyi, approval ID ve en fazla
366 günlük geçerlilik penceresini taşır; URL veya secret release raporuna
kopyalanmaz. Fingerprint; feature, provider, kaynak adı, kaynak URL'si, lisans
ve terms URL'sinden üretilir. Bu alanlardan biri değişirse onay otomatik olarak
geçersiz kalır.

Şema örneği (gerçek onay bilgisi içermez):

    {
      "schema_version": 1,
      "approvals": [
        {
          "feature_key": "weather",
          "approval_id": "review-2026-001",
          "source_fingerprint": "<64 küçük harf hex karakteri>",
          "reviewer": "platform-governance",
          "issued_at": "2026-10-07T09:00:00+00:00",
          "expires_at": "2027-01-07T09:00:00+00:00",
          "source_governance": "approved",
          "robots_policy": "allowed",
          "terms_review": "approved",
          "production_signoff": "approved"
        }
      ]
    }

Dosya path'i production `.env` içinde belirtilir ve preflight aynı dosyayı
feature manifestiyle karşılaştırır:

    PROVIDER_APPROVAL_MANIFEST=/etc/arelbot/provider-approvals.json
    python scripts/provider_governance.py --require-approved --approval-manifest /etc/arelbot/provider-approvals.json
    python scripts/production_preflight.py --env-file .env --require-provider-approval --provider-approval-manifest /etc/arelbot/provider-approvals.json
    python scripts/provider_governance.py --require-approved --approval-manifest /etc/arelbot/provider-approvals.json --approval-evidence --report deploy/evidence/provider-governance-approval.json

Dosya yoksa, süresi dolmuşsa, fingerprint uyuşmuyorsa veya dört onay alanı
tamamlanmamışsa production provider yine fail-closed kalır. Approval dosyası
kaynak terms incelemesinin yerine geçmez; yalnızca tamamlanmış incelemenin
deploy sırasında tekrar üretilebilir biçimde uygulanmasını sağlar.


### Grup yanıtı rollout ve gözlem

`GROUP_TARGETED_REPLIES_ENABLED=true` varsayılan olarak açık olsa da rollout
öncesinde `GROUP_TARGETED_REPLIES_SHADOW=true` ile yalnızca plan ve validation
metrikleri gözlemlenir; Telegram'a gönderim yapılmaz. Owner test grubunda
`group_targeted_replies` özelliği doğrulanmadan canary açılımı yapılmaz.

İzlenecek metrikler kullanıcı adı veya mesaj metni içermez:

```text
group_reply_requests_total
group_reply_split_total
group_reply_segments_total
group_reply_validation_reject_total
group_reply_unknown_target_total
group_reply_send_failures_total
group_reply_reply_fallback_total
group_reply_plan_seconds
group_reply_send_seconds
```

Çapraz kullanıcı reply'i, duplicate gönderim veya yanlış forum konusu
görülürse `GROUP_TARGETED_REPLIES_ENABLED=false` ile kill switch uygulanır.
Mongo v15 migration'ı geri alınabilir indekslerden oluşur; migration rollback
mesaj kayıtlarını silmez. Redis yeniden başlatıldığında aday index'i Mongo'dan
yeniden okunur, delivery ledger ise Mongo'daki claim durumuyla duplicate
gönderimi engeller.


## Kek Guardian entegrasyonu

Guardian Arel koduna kopyalanmaz; ayrı servis olarak değerlendirme yapar.
Telegram gruplarında tek bot Arel olur ve Telegram moderasyon aksiyonlarını
yalnızca Arel'in `ModerationActionExecutor` sınırı uygular. Guardian'ın kendi
botu Arel tarafından yönetilen gruplarda aksiyon otoritesi olarak kullanılmaz.

İlk rollout `GUARDIAN_MODE=disabled`, ardından `shadow`, son olarak owner
grubunda `active` canary sırasını izler. Arel ile Guardian arasındaki hedef
kanal Linux'ta `/run/kek-guardian/guardian.sock` Unix socket'idir. 2026-10-17
salt-okunur VDS keşfinde Guardian Telegram polling servisi aktifti, ancak bu
socket veya başka bir RPC endpoint'i bulunmadı. Gerçek evaluator-backed bridge
kurulana kadar provider `disabled` kalır; yalnızca Arel'in yerel politikası
çalışır. Socket sahipliği ve izinleri servis hesabına özel olmalı (`0660`);
`GUARDIAN_PEER_UIDS` veya `GUARDIAN_PEER_GIDS` açıkça tanımlanmadan active moda
geçilmez. Bridge hata verirse yerel politika çalışır ve hata kullanıcıya grup
mesajı olarak yansıtılmaz. Ayrıntılı keşif kaydı `docs/GUARDIAN_DEPLOYMENT.md`
içindedir.

```text
GUARDIAN_MODE=shadow
GUARDIAN_SOCKET_PATH=/run/kek-guardian/guardian.sock
GUARDIAN_TIMEOUT_SECONDS=0.15
GUARDIAN_REQUIRE_PEER_CREDENTIALS=true
```

Staging canlı kapısı yalnızca bounded çıktı üretir; secret, ham mesaj, cookie
veya tam URL rapora girmez:

```text
python scripts/guardian_staging.py
ARELBOT_GUARDIAN_LIVE=1 python scripts/guardian_staging.py --live --confirm-live --shadow
```

Guardian systemd unit'inde veritabanı parolası veya Telegram token'ı
`ExecStart`/unit metninde tutulmaz. Salt-okunur VDS keşfinde `DATABASE_URL`
secret'ının doğrudan `Environment=` satırında bulunduğu görüldü; değer rapora
alınmadı ve Arel'e taşınmadı. Bu **yüksek önemde bir secret exposure**
bulgusudur. Guardian yeniden başlatılmadan önce veritabanı parolası rotate
edilmeli, eski değer iptal edilmeli ve secret root'un okuyabildiği (`0600`)
`EnvironmentFile` veya systemd `LoadCredential` kaynağına taşınmalıdır.
Eski unit satırı kaldırıldıktan sonra `systemctl cat`, `systemctl show`, process
environment ve journal sızıntı taraması yapılır; ancak bundan sonra kontrollü
restart ve secret-free health kanıtı alınır. Bu kapılar geçilene kadar
`GUARDIAN_MODE=disabled` korunur.

### Kek Guardian bridge server (optional)

`src/moderation/guardian_server.py` is a bounded Unix-socket bridge for
deployments where the separate Kek Guardian decision engine needs a local
transport endpoint. It is an integration scaffold, not a replacement for the
Guardian engine: a deployment must inject the real evaluator and must not use
the synthetic staging evaluator in production.

The bridge validates the protocol version, request expiry, replay nonce,
frame size, provider identity, response expiry, action vocabulary and Linux
peer credentials before evaluation. It handles one request per connection,
limits concurrent clients, and closes malformed or unauthorized connections
without sending request details back to the caller. Provider exceptions are
reduced to bounded error codes; message text, credentials and full URLs are
never included in logs or reports.

The socket parent directory should be created by systemd with a dedicated
service account. Use a `0660` socket and configure the Arel client UID/GID in
`GUARDIAN_PEER_UIDS` or `GUARDIAN_PEER_GIDS`. Do not set active mode until the
real Guardian evaluator is installed and a staging canary is recorded.

Secret-free local checks:

```text
python scripts/guardian_bridge_staging.py
python scripts/guardian_bridge_staging.py --check --socket /run/kek-guardian/guardian.sock
python scripts/guardian_bridge_staging.py --self-test
```

`--self-test` starts a temporary in-process bridge with a synthetic allow
decision and proves only the transport contract. It does not contact Telegram,
Guardian, MongoDB or Redis, and its report explicitly says that the real
engine was not tested. Arel production remains `GUARDIAN_MODE=disabled` until
the independent Guardian service is wired to the bridge and owner-group
shadow/active canary evidence is collected.

### MongoDB ve Redis üretim ağı

Compose MongoDB ve Redis servisleri artık anonim bağlantıyı reddeder.
MongoDB `MONGO_INITDB_ROOT_USERNAME`/`MONGO_INITDB_ROOT_PASSWORD` ile SCRAM
kullanıcısı açar, Redis `--requirepass` ile parola ister. Compose ağındaki
her konteyner (flaresolverr, prometheus, grafana, test) veritabanının
tamamını ve kuyruk Redis'ini kimliksiz okuyup yazamadığı için ağ izolasyonu
tek başına yeterli değildir.

MongoDB'de üç ayrı kimlik vardır ve her biri **kendi veritabanında** yaşar.
Bu yüzden bağlantı dizisindeki `authSource` sorgu parametresi kimliğin hangi
veritabanında tutulduğunu açıkça belirtmelidir:

| Kimlik | Veritabanı | Yetki | Bağlantı dizisi |
|---|---|---|---|
| `arel_app` | `arel_bot` | `readWrite` | `mongodb://arel_app:<parola>@mongo:27017/arel_bot` + `?authSource=arel_bot` |
| `arel_test` | `arel_bot_test` | `readWrite` + `dbOwner` | `mongodb://arel_test:<parola>@mongo:27017/arel_bot_test` + `?authSource=arel_bot_test` |
| `areladmin` (idari) | `admin` | `root` | `mongodb://areladmin:<parola>@mongo:27017` + `?authSource=admin` |

Bot, worker ve geliştirme profili `arel_app` ile bağlanır; test profili
`arel_test` ile. `areladmin` yalnızca kurulum, backup/restore ve parola
rotasyonu gibi idari işlerde kullanılır, uygulama bağlantı dizisinde
**bulunmaz**.

#### 1. Parola kurulumu

Parolalar deployment secret store'da üretilir; `.env` dosyası 0600 izinli
olmalıdır ve Git'e girmez. `.env` içinde şu kimlikler tanımlanır:

    MONGO_ROOT_USERNAME=areladmin
    MONGO_ROOT_PASSWORD=<üretim parolası>
    MONGO_APP_PASSWORD=<üretim parolası>
    MONGO_TEST_PASSWORD=<üretim parolası>
    REDIS_PASSWORD=<üretim parolası>

`MONGO_ROOT_*` idari hesabın, `MONGO_APP_*` uygulama hesabının ve
`MONGO_TEST_*` test hesabının parolasıdır; `arel_app` ve `arel_test`
kullanıcıları init script'i tarafından oluşturulur. Compose bağlantı
dizisini paroladan birebir kurar:

    MONGODB_URI=mongodb://arel_app:<parola>@mongo:27017/arel_bot
    REDIS_URL=redis://:<parola>@redis:6379/0

Uygulama hesabı `arel_bot` veritabanında yaşadığı için bu adres
`?authSource=arel_bot` sorgu parametresiyle, test hesabı ise
`?authSource=arel_bot_test` parametresiyle kullanılmalıdır; `docker-compose.yml`
parametreleri kendisi yazar. İdari bağlantıda ise `?authSource=admin`
gerekir, çünkü root kullanıcısı `admin` veritabanında oluşturulur.
Parametre yoksa kullanıcı reddedilir.

Parola URI'ye doğrudan gömüldüğü için `A-Z a-z 0-9 - _ . ~` karakterleriyle
sınırlanmalıdır; `@ : / # %` gibi ayırıcı karakterler bağlantı
dizisini bozar. Bu sınır bir **kuraldır**, yalnızca bir not değil:
`scripts/production_preflight.py` parolaları doğrular ve küme dışında bir
karakter varsa deployment'ı `mongodb_password_charset_invalid` veya
`redis_password_charset_invalid` hata koduyla durdurur; uygulama da
production'da başlarken aynı kuralı uygular. Operasyonel problar da aynı
kuralı kendi doğrulamasıyla uygular, böylece sessizce bozuk bir
bağlantı dizisi üretilmez. Bu karakterlerden biri gerekiyorsa parolayı
değiştirin. Uygulama tarafında `config.build_mongodb_uri()` parolayı
URL-encode eder, ancak Compose değişkeni kendisi encode edemez; bu yüzden
kural uygulamanın kendi bağlantı dizesinde bile geçerlidir.

`deploy/mongo/init-app-users.js` init script'i `arel_app` kullanıcısını
`arel_bot` veritabanında yalnızca `readWrite` yetkisiyle, `arel_test`
kullanıcısını `arel_bot_test` veritabanında `readWrite` ve geçici migration
veritabanında `dbOwner` yetkisiyle oluşturur; `areladmin` yalnızca `admin`
veritabanındaki `root` rolünü korur. Script tekrar çalıştırıldığında aynı
duruma gelir, yani kullanıcılar zaten varsa yetkileri güncellenir. Bu iş
yalnızca `mongo_data` volume'u ilk kez oluşturulduğunda çalışır.

#### 2. Mevcut üretim verisini koruyarak kimlikli bağlantıya geçiş

Init script'leri yalnızca boş volume üzerinde çalışır. Mevcut üretim
volume'u silinmez. İlk kimlikli deployment şu sırayla yapılır:

1. Mongo archive, Redis snapshot ve mevcut ortam dosyası yalnız root'un
   okuyabildiği bir yedek klasörüne alınır. Mongo yedeği üretim ağı ve
   volume'ları bağlanmamış geçici bir veritabanına geri yüklenerek doğrulanır.
2. Eski kimliksiz Mongo henüz çalışırken localhost bağlantısından `admin`
   veritabanında idari kullanıcı, `arel_bot` içinde `arel_app` ve test
   veritabanında `arel_test` oluşturulur. Var olan kullanıcılar önce
   denetlenir; bilinmeyen hesabın parolası otomatik değiştirilmez. Parolalar
   komut satırı argümanına veya log'a yazılmaz; süreç standart girdisinden
   geçirilir ve yeni ortam dosyası 0600 izinle hazırlanır.
3. Bot ve worker durdurulur; son tutarlı yedek alınır. Yeni ortam ve Compose
   yapılandırmasıyla Mongo auth ve Redis parola zorunluluğu etkinleştirilir.
   Aynı veri volume'ları kullanılır.
4. Yeni uygulama kimliğiyle bağlantı, veri sayıları, migration ve bot/worker
   sağlık kontrolleri doğrulanır. Kimliksiz koleksiyon okumasının reddedildiği
   ayrıca denetlenir. Sağlık doğrulanmadan current release değiştirilmez.
5. Başarısızlıkta önceki sürüm, önceki ortam ve önceki bağlantı yapılandırması
   geri alınır; yedekler korunur. Yeni ekonomi şemasına geçilmişse eski ekonomi
   kodu açılmadan önce aşağıdaki v20 geri dönüş prosedürü uygulanır.

Redis parola değişikliği de mevcut veri volume'unu koruyarak yapılır.
Boş geliştirme veritabanı kurmak için kullanılan hacim silme komutları
üretim geçişinin parçası değildir.

#### 3. Doğrulama

    python scripts/production_preflight.py --env-file .env --nginx-config /etc/nginx/sites-enabled/arelbot.conf
    docker compose --profile prod config --quiet

Preflight `mongodb_credentials_missing` ve `redis_credentials_missing` hata
kodlarıyla anonim bağlantıyı reddeder; parola değerini hiçbir zaman
yazdırmaz. `docker compose config` eksik parolada `required variable ...
is missing a value` hatası verir.

#### 4. Parola rotasyonu

Parola rotasyonu iki ayrı işlem gerektirir:

1. Yeni parolalar üretilir ve secret store'a yazılır.
2. `MONGO_ROOT_PASSWORD`, `MONGO_APP_PASSWORD`, `MONGO_TEST_PASSWORD` ve
   `REDIS_PASSWORD` değerleri güncellenir; `docker compose --profile prod up -d`
   ile servisler yeniden başlatılır.

MongoDB parolaları `admin.system.users` içinde saklandığı için sadece
environment değişikliğiyle değişmez; her hesabın parolası
`changeUserPassword` ile **ayrı ayrı** döndürülmelidir. Yeni parola **ortam
değişkeni** olarak konteynere verilir, parolayı `mongosh` **TTY üzerinden
kendisi** sorar; parola bu yüzden ne komut satırına ne de `argv` listesine
girer.

**Sıra önemlidir.** Üç komut aşağıdaki sırayla çalıştırılır:

| # | Döndürülen parola | Yönetici oturumu bu parolayla açılır |
|---|---|---|
| 1 | `areladmin` | **ESKİ** `areladmin` parolası |
| 2 | `arel_app` | **YENİ** `areladmin` parolası |
| 3 | `arel_test` | **YENİ** `areladmin` parolası |

İlk komut `areladmin` parolasını değiştirdiği için ikinci ve üçüncü komutlar
artık **yeni** `areladmin` parolasıyla oturum açar; eski parola
`Authentication failed.` ile reddedilir. Uygulama ve test hesaplarının
kendi parolaları değiştirilirken oturum yine `areladmin` ile açılır, çünkü
`areladmin` `admin` veritabanında `root` rolüne sahiptir.

Yeni parolaları **kabuk geçmişine yazmadan** ortama alın. `read -rs` değeri
ekrana basmadan okur:

    read -rs -p 'Yeni idari parola: ' NEW_MONGO_ROOT_PASSWORD; echo
    read -rs -p 'Yeni uygulama parolası: ' NEW_MONGO_APP_PASSWORD; echo
    read -rs -p 'Yeni test parolası: ' NEW_MONGO_TEST_PASSWORD; echo
    export NEW_MONGO_ROOT_PASSWORD NEW_MONGO_APP_PASSWORD NEW_MONGO_TEST_PASSWORD

Alternatif olarak değerler doğrudan secret store'dan okunup ortam
değişkenine atanabilir. Her iki yolda da parola yalnızca **ortam
değişkeninde** bulunur, komut satırına ve kabuk geçmişine yazılmaz.

Şimdi üç MongoDB hesabının parolasını döndürün. `areladmin` `admin`
veritabanında, `arel_app` `arel_bot` veritabanında, `arel_test`
`arel_bot_test` veritabanında yaşar:

    docker compose exec -e NEW_MONGO_ROOT_PASSWORD mongo mongosh --quiet --username areladmin --authenticationDatabase admin --eval 'db.getSiblingDB("admin").changeUserPassword("areladmin", process.env.NEW_MONGO_ROOT_PASSWORD)'
    docker compose exec -e NEW_MONGO_APP_PASSWORD mongo mongosh --quiet --username areladmin --authenticationDatabase admin --eval 'db.getSiblingDB("arel_bot").changeUserPassword("arel_app", process.env.NEW_MONGO_APP_PASSWORD)'
    docker compose exec -e NEW_MONGO_TEST_PASSWORD mongo mongosh --quiet --username areladmin --authenticationDatabase admin --eval 'db.getSiblingDB("arel_bot_test").changeUserPassword("arel_test", process.env.NEW_MONGO_TEST_PASSWORD)'

Kanalın çalışma nedeni şudur:

* `docker compose exec -e AD` **değeri host ortamından miras alır**; parola
  `--eval` ifadesinde `process.env` üzerinden okunur, bu yüzden `argv`
  listesinde görünmez.
* `--password` bayrağı **verilmez**. `mongosh` oturum açarken o an geçerli
  olan parolayı kendi interaktif isteminde sorar, bu yüzden komut TTY'ye
  bağlı çalıştırılmalıdır (`-T` bayrağı kullanılmaz).
* `prompt()` mongosh'ta **bulunmaz** (`mongosh` yalnızca `process`,
  `process.env` ve kabuk yardımcılarını sunar); bu yüzden yeni parola
  `process.env` üzerinden verilir.
* `areladmin` `admin` veritabanında `root` rolüne sahip olduğu için diğer
  veritabanlarındaki kullanıcıların parolasını da değiştirebilir.

Redis parolası `redis-server`'a `--requirepass` ile verildiği için dosyada
saklanır: `REDIS_PASSWORD` değerini secret store'da güncelleyip servisleri
yeniden başlatmak yeterlidir.

Döndürme bittikten sonra dışa aktarılan değişkenleri temizleyin:

    unset NEW_MONGO_ROOT_PASSWORD NEW_MONGO_APP_PASSWORD NEW_MONGO_TEST_PASSWORD

Ardından secret store ve `.env` değerleri güncellenip servisler yeniden
başlatılır. Volume sıfırlama rotasyonda gerekli değildir; init script'i
yalnızca ilk kurulumda çalışır.

#### 5. Backup ve restore

`scripts/backup.py` ve `scripts/restore.py` MongoDB ve Redis parolasını
komut satırına yazmaz. `mongodump`/`mongorestore` bağlantı dizisini 0600
izinli, geçici bir YAML yapılandırma dosyasından okur ve dosya komut
bittikten sonra silinir; `redis-cli` parolayı `REDISCLI_AUTH` environment
değişkeninden alır. Böylece `ps` çıktısında parola görünmez. `MONGODB_URI`
environment değişkeni de aynı değeri taşır.

#### 6. Staging ve live-VDS probları

Üç operatör probu kimlikli servislere karşı çalışır ve parolayı hiçbir
zaman komut satırına yazmaz:

* `scripts/persistence_staging_smoke.py` — Compose yığınına karşı. MongoDB
  yarısı idari kimliği çalışan konteynerin yapılandırmasından okur ve
  `mongosh` için kimlik betiğini, veritabanı araçları için YAML yapılandırma
  dosyasını **standard input** üzerinden konteynere yazar. Redis yarısı
  konteynerin `REDISCLI_AUTH` ortam değişkenini kullanır.
* `scripts/vds_backup_restore_remote.py` — VDS üzerinde. Aynı kanalları
  kullanır; kimlik betiği ve YAML dosyası konteynerin **içinde** yazılır, bu
  yüzden parola VDS'yi terk etmez.
* `scripts/vds_migration_rollback_remote.py` — VDS üzerinde. Kimlik betiği
  konteynerin içinde yazılır ve bağlantı dizisi worker'a **ortam
  değişkeni** olarak (`MONGODB_URI`) verilir; `argv` temiz kalır.

Bu probların ürettiği kanıtlar `deploy/evidence/persistence-staging.json`,
`deploy/evidence/vds-backup-restore.json` ve
`deploy/evidence/vds-migration-rollback.json` dosyalarıdır.


## Satranç oyun operasyonu

`/satranc` (ve gizli uyumluluk alias'ı `/chess`) Arel'in yerel satranç
oyununu açar. Oyun MongoDB `game_states` içinde `chess` anahtarıyla tutulur;
Redis veya ayrı bir Node botu gerekmez. Her düğme `round_id`, sohbet ve forum
konusu ile doğrulanır ve `GameActionLedger` üzerinden tek kez işlenir. Worker
restart'ında state store oyunu yeniden hydrate eder; bozuk UCI hamle geçmişi
fail-closed olur ve kullanıcıya yeni tur açma önerilir.

Legal rules engine `chess==1.11.2` olarak constraints dosyasına sabitlenmiştir.
Yeni imaj oluşturulurken `pip install -c constraints.txt -r requirements.txt`
çalıştırılmalı, ardından `python -m pytest src/tests/test_chess.py
src/tests/test_chess_surface.py` ile kural ve Telegram callback replay testi
koşturulmalıdır. Canlı doğrulamada geçici owner grubunda `/satranc`, ikinci
oyuncu katılımı, e2-e4 hamlesi, eski round düğmesi ve `/iptal` akışı denenir;
kalıcı oyun verisi veya kullanıcı mesajı evidence raporuna yazılmaz.


## Arel Tahmin / akipy

`/akinator` ve `/aki` Türkçe Arel Tahmin turunu başlatır. Sorular yalnızca
başlatan kullanıcının round-scoped butonlarından ilerler; `akipy` erişimi
başarısızsa kullanıcıya güvenli servis hatası gösterilir. Akinator istemcisi
süreç içi olduğundan worker yeniden başlarsa aktif tur expire edilir; Mongo
state yeniden yüklenebilir ama upstream oturumu tekrar oynatılamaz.
`AKINATOR_MAX_CONCURRENCY`, `AKINATOR_TIMEOUT_SECONDS`,
`AKINATOR_SOLVER_URL` ve `AKINATOR_SOLVER_TIMEOUT_MS` deployment ayarlarıdır.
Dev/prod Compose profilleri, solver'ı `flaresolverr` adıyla yalnızca iç ağa
ekler ve host portu yayınlamaz. `AKINATOR_SOLVER_URL` boş bırakılırsa Compose
varsayılanı `http://flaresolverr:8191` olur; bağımsız host kurulumlarında bu
değer açıkça verilmeden Arel doğrudan bağlantıda kalır ve Cloudflare 403'ünü
güvenli servis hatasına çevirir. Solver loglarında HTML veya challenge içeriği
debug seviyesinde açılmamalıdır.
### Yaş Tahmini varlıkları

Üretim imajında `assets/guess_age/*.jpg` fotoğraflarının bulunması gerekir.
Fotoğraflar Wikimedia Commons kaynaklıdır; her görselin çekim tarihi, fotoğrafçı,
lisans ve kaynak bağlantısı `assets/guess_age/README.md` içinde kayıtlıdır.
Oyun sırasında harici fotoğraf indirme veya görsel analiz servisi kullanılmaz.
Eski `*.png` kartları yalnızca önceki sürümde açılmış turların okunabilmesi için
korunur. `GUESS_AGE_ROUNDS` ve
`GUESS_AGE_ROUND_SECONDS` ile tur sayısı ve tur süresi ayarlanır. State MongoDB
oyun kaydında tutulur; worker/bot yeniden başlasa bile yeni bir `/yas` çağrısı
aktif turu okuyabilir.

### UNO ve Mini App oyunları

`/uno` yalnızca grup/süpergrup sohbetlerinde çalışır. `UNO_TURN_SECONDS`
15–300 saniye arasında tutulur. Oyun MongoDB `game_states` içinde `uno`
anahtarıyla saklanır; bot yeniden başlasa bile lobi ve el durumu yeniden
okunabilir. `GameActionLedger` callback kimliklerinin tekrar uygulanmasını
engeller. `/iptal` masayı yalnızca kuran kişi veya grup yöneticisi için kapatır.

Mini App için `MINIAPP_GAMES_ENABLED=false` varsayılanı korunur. Açmadan önce
HTTPS endpoint, Telegram `initData` doğrulaması, chat/user scope kontrolü,
nonce-expiry denetimi ve staging replay kanıtı tamamlanmalıdır. `MINIAPP_URL`
yalnızca doğrulanmış endpoint'e işaret ettiğinde ayarlanır; boş bırakılırsa
UNO Telegram butonlarıyla tam çalışır ve Mini App düğmesi gösterilmez.

## Inline buton görünümü

`TELEGRAM_CUSTOM_BUTTON_EMOJI=auto` varsayılan olarak doğrulanmış yerel özel simge kataloğunu kullanır. `off`, Unicode simgeleri ve buton renklerini koruyup özel simgeleri kapatır. Ortam değişikliğinden sonra bot ve işçi aynı yapılandırmayla yeniden oluşturulmalıdır. Açık bir özel emoji HTTP400 reddi tek bir Unicode tekrarına ve ilgili istemcide kullanımın kapanmasına yol açar; yeniden başlatmada `auto` tekrar denenir. Ağ hatası veya 429/5xx nedeniyle bu sunum katmanı mesajı yeniden göndermez. Yalnız ikon metadata kimlikleri yereldedir; emoji dosyası indirilmez. Uygunluk, güncel Bot API koşulları ve canlı istemci kabul sınırları docs/TELEGRAM_BUTTONS_UPDATE_2026-10-22.md içinde tutulur.

2026-10-22 games-14: 45 oyun etkin. Yedek: /opt/arel/backups/games-20261022-games45-product. Şema20, bağımlılıklar aynı. Bot/worker/Mongo/Redis sağlıklı. Mevcut veriler korundu; Mongo yedek dosya bütünlüğü doğrulandı, bu dalgada ayrı restore denemesi yapılmadı. Kanıt: deploy/evidence/games45-deployment-20261022.json.
## Codex CLI hesabı

Üretimde `OPENAI_CHATGPT_ENABLED=true` ve `OPENAI_CHATGPT_MODE=cli` kullanılır.
Host üzerinde `codex login status` ile hesabın durumunu kontrol edin. Giriş
yoksa bot kapanmaz; OpenAI rotası `cli_auth_required` durumuyla açılır ve EVREN
ile diğer provider'lara failover yapar. Bot ve worker aynı `codex` CLI sürümünü
imajdan kullanır; `OPENAI_CHATGPT_CODEX_HOST_DIR` yalnızca Codex home olarak
`/run/arel-openai/codex` yoluna bağlanır ve CLI metadata yazabilsin diye
yazılabilirdir. Auth dosyası, prompt ve tam CLI çıktısı loglanmaz. Hesabı
yenilemek için hostta `codex login` çalıştırıp bot ve worker konteynırlarını
yeniden oluşturun.
