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
    python scripts/architecture_check.py --strict

Windows'ta varsayılan olarak işletim sisteminin doğrulanmış kök sertifika deposu
kullanılır. Kurumsal HTTPS inspection kullanılıyorsa doğrulanmış kök CA dosyası
ARELBOT_EXTRA_CA_CERT ile verilir (çalışan süreçte SSL_CERT_FILE olarak
birleştirilir). TLS verify=False kullanılmaz. Docker build için
ARELBOT_BUILD_CA_FILE BuildKit secret olarak kullanılır.

Bootstrap dispatch'i de aynı sözleşmeyi kullanır: Windows polling profili
yalnızca `run_polling`, Linux webhook profili yalnızca `run_webhook`
çağrısı alır. Bu seçim `src/tests/test_main_transport.py` içinde PTB
uygulaması başlatılmadan iki yönlü olarak doğrulanır.

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
fallback'iyle production deploy devam etmez. Public observability bind
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

Migration runner ileri ve geri yürütülebilir; mevcut schema v14'tür.

    python scripts/migrate.py
    python scripts/mongo_migration_smoke.py

Production rollback öncesi backup alınır. Migration rollback sonrası index,
TTL ve unique constraint'ler tekrar smoke ile doğrulanır.

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
ve media delivery replay kontrollerini birlikte çalıştırır. Canlı
Telegram, VDS webhook ve platform medya matrisi bu deterministic kapının
yerine geçmez; staging kimlik bilgileriyle ayrı yürütülür.

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

Compose servisleri varsayılan olarak yalnızca iç Compose ağına bağlanır; bu
tek başına kimlik doğrulama kanıtı değildir. Üretime geçmeden önce MongoDB için
SCRAM kullanıcısı, Redis için ACL kullanıcısı ve ayrı secret dosyalarıyla
kimlik doğrulama etkinleştirilmelidir. Bot ve worker yalnızca en az yetkili
hesaplarla bağlanmalı, bağlantı dizeleri loglara veya `/health` çıktısına
girmemelidir. Bu değişiklik için mevcut verinin korunacağı bir migration,
backup/restore denemesi ve rollback kanıtı gerekir; kanıt oluşana kadar bu
madde faz 9 operasyon önkoşulu olarak açık kalır.
