# Arelbot Operasyon ve DaÄŸÄ±tÄ±m Rehberi

Bu belge Windows geliÅŸtirme, Linux VDS production, yedekleme, rollback,
saÄŸlÄ±k, gÃ¶zlemlenebilirlik ve replay kapÄ±larÄ±nÄ± tanÄ±mlar. Secret
deÄŸerleri bu dosyaya yazÄ±lmaz.

## Yerel Windows

TELEGRAM_MODE=polling ile MongoDB ve Redis Docker Compose dev profiliyle
Ã§alÄ±ÅŸtÄ±rÄ±lÄ±r. FFmpeg, ffprobe ve Node.js PATH Ã¼zerinde bulunur.

Transport sÃ¶zleÅŸmesi polling ve webhook modlarÄ±nÄ± ayrÄ± doÄŸrular; webhook
modunda HTTPS, credential/query iÃ§ermeyen URL, gÃ¼venli path segmentleri,
URL-safe secret ve 1-65535 port aralÄ±ÄŸÄ± uygulama baÅŸlamadan kontrol edilir.

    docker compose --profile dev up -d mongo redis
    python -m pytest -q
    python scripts/architecture_check.py --strict

Windows'ta varsayÄ±lan olarak iÅŸletim sisteminin doÄŸrulanmÄ±ÅŸ kÃ¶k sertifika deposu
kullanÄ±lÄ±r. Kurumsal HTTPS inspection kullanÄ±lÄ±yorsa doÄŸrulanmÄ±ÅŸ kÃ¶k CA dosyasÄ±
ARELBOT_EXTRA_CA_CERT ile verilir (Ã§alÄ±ÅŸan sÃ¼reÃ§te SSL_CERT_FILE olarak
birleÅŸtirilir). TLS verify=False kullanÄ±lmaz. Docker build iÃ§in
ARELBOT_BUILD_CA_FILE BuildKit secret olarak kullanÄ±lÄ±r.

Bootstrap dispatch'i de aynÄ± sÃ¶zleÅŸmeyi kullanÄ±r: Windows polling profili
yalnÄ±zca `run_polling`, Linux webhook profili yalnÄ±zca `run_webhook`
Ã§aÄŸrÄ±sÄ± alÄ±r. Bu seÃ§im `src/tests/test_main_transport.py` iÃ§inde PTB
uygulamasÄ± baÅŸlatÄ±lmadan iki yÃ¶nlÃ¼ olarak doÄŸrulanÄ±r.

## Linux VDS production

TELEGRAM_MODE=webhook, HTTPS reverse proxy, ayrÄ± bot ve worker container'larÄ±,
MONGODB_REQUIRED=true ve MEDIA_REDIS_REQUIRED=true kullanÄ±lÄ±r. .env dosyasÄ±
0600 izinli olmalÄ±dÄ±r.

DaÄŸÄ±tÄ±m Ã¶ncesi hedef hostun mevcut uygulamalarÄ±nÄ± durdurmadan salt-okunur
hazÄ±rlÄ±ÄŸÄ± Ã¶lÃ§Ã¼lÃ¼r. Probe, known-hosts iÃ§indeki SSH anahtarÄ±nÄ± zorunlu
tutar; Docker/Compose, `/opt/arel` yapÄ±sÄ±, yazma izni, boÅŸ disk kotasÄ±,
paylaÅŸÄ±mlÄ± env varlÄ±ÄŸÄ±, mevcut release sayÄ±sÄ±, Arel Compose projesine ait
container sayÄ±sÄ± ve dinleyen portlarÄ± okur. Uzak
dosya, container, Nginx veya Telegram durumu deÄŸiÅŸtirilmez:

    $env:AREL_SSH_PASSWORD='operator-secret'
    $env:ARELBOT_VDS_READINESS_LIVE='1'
    python scripts/vds_readiness.py --host <vds> --user root --live --confirm-live --json --report deploy/evidence/vds-readiness.json

Rapor `ok=false` ise `blockers` alanÄ±ndaki her madde giderilmeden immutable
release deploy edilmez. Probe host adÄ±, kimlik bilgisi, container adÄ± veya
uzak hata metni saklamaz. `/opt/arel/shared/.env` yoksa deployer'a yalnÄ±zca
operator tarafÄ±ndan hazÄ±rlanmÄ±ÅŸ, UTF-8 ve 0600 izinli production env dosyasÄ±
`--bootstrap-env-file` ile verilebilir; yerel geliÅŸtirme `.env` dosyasÄ±
otomatik olarak production secret store sayÄ±lmaz.

    python scripts/production_preflight.py --nginx-config /etc/nginx/sites-enabled/arelbot
    docker compose --profile prod up -d --build

The optional `--nginx-config` check compares the deployed Nginx file with the
`WEBHOOK_URL` prefix and `WEBHOOK_PATH`. It fails on an exact public-location,
internal upstream-path, TLS-listener, or Telegram secret-header mismatch. The
checked-in example uses `/telegram/webhook`; if a public prefix is configured,
update the exact Nginx location to the public URL path and keep `proxy_pass`
pointing at the Python webhook's internal `WEBHOOK_PATH`.

Reverse proxy yalnÄ±zca webhook yolunu botun loopback portuna iletir. DNS,
sertifika, firewall ve Telegram setWebhook gerÃ§ek VDS kabul listesinde ayrÄ±ca
doÄŸrulanÄ±r. Healthcheck baÅŸarÄ±sÄ±zsa Compose restart politikasÄ± devreye
girer.

Production preflight, MONGODB_REQUIRED=true, REDIS_REQUIRED=true ve
MEDIA_REDIS_REQUIRED=true ayarlarinin acik olmasini kontrol eder; bellek
fallback'iyle production deploy devam etmez. Public observability bind
kullaniliyorsa OBSERVABILITY_ENABLED=true, OBSERVABILITY_LISTEN=0.0.0.0 ve
host tarafinda 0600 izinli ARELBOT_OBSERVABILITY_TOKEN_FILE tanimlanir.
Preflight token icerigini loglamadan dosya varligini, izinlerini ve URL-safe
formatini dogrular.

Yeni veya boÅŸ bir VDS'de `/opt/arel/shared/.env` yoksa deployer, secret iÃ§eriÄŸini terminale yazmadan aÃ§Ä±kÃ§a verilen yerel dosyayÄ± SFTP ile `/tmp` altÄ±nda taÅŸÄ±r ve yalnÄ±zca uzak shared env yoksa `install -m 600` ile kurar:

    python scripts/deploy_release.py --host <vds> --user root --bootstrap-env-file C:/secure/arelbot.production.env

Mevcut uzak `.env` Ã¼zerine yazÄ±lmaz. Yerel dosya normal UTF-8 dosya olmalÄ±, 128 KiB sÄ±nÄ±rÄ±nÄ± aÅŸmamalÄ± ve symlink olmamalÄ±dÄ±r. Uzak preflight veya saÄŸlÄ±k kontrolÃ¼ baÅŸarÄ±sÄ±z olsa bile archive geÃ§ici dosyalarÄ± temizlenir; operator env dosyasÄ± rollback sÄ±rasÄ±nda korunur.

EVREN kullanilacaksa anahtar yalnizca deployment secret store icinden verilir;
repository veya Docker image icine yazilmaz. OpenAI-compatible route asagidaki
gibi acikca secilir:

    EVREN_URL=https://evren-llmapi.ssyz.org.tr/v1
    EVREN_MODEL=deepseek-v4.1-flash
    AI_PROVIDER_ROUTES={"chat":["evren"],"fast":["evren"],"analysis":["evren"],"summary":["evren"],"sticker_tagging":["evren"]}

`EVREN_LLM_API_KEY` degeri preflight/health/log raporlarinda gorunmez. EVREN anahtari ayarlandiginda `chat`, `fast`, `analysis`, `summary` ve `sticker_tagging` gorevleri varsayilan olarak `deepseek-v4.1-flash` modeline yonlendirilir; baska bir provider route'u acikca yazilirsa o allowlist kullanilir. EVREN kullanim sartlari hesapta kabul edilmeden production istegi baslatilmaz.

HÄ±zlÄ± sohbet yolu `AI_FAST_TIMEOUT_SECONDS` ile sÄ±nÄ±rlÄ±dÄ±r (varsayÄ±lan 8
saniye). Tek saÄŸlayÄ±cÄ±lÄ± bir route geÃ§ici 408/429/5xx cevabÄ± verirse
`AI_PROVIDER_RETRY_ATTEMPTS` (varsayÄ±lan 1) kadar kÄ±sa aralÄ±kla yeniden denenir;
okuma zaman aÅŸÄ±mÄ± ikinci kez beklenmez. `AI_PROVIDER_RETRY_BACKOFF_MS`
varsayÄ±lan olarak 150 milisaniyedir. BÃ¶ylece anlÄ±k upstream titremesi kullanÄ±cÄ±ya
hemen servis kesintisi olarak yansÄ±maz, kalÄ±cÄ± yavaÅŸlÄ±kta ise yanÄ±t sÃ¼resi
kontrollÃ¼ kalÄ±r.

GeÃ§ici saÄŸlayÄ±cÄ± hatasÄ±nda anahtar soÄŸuma sÃ¼resi 10 saniyedir; bu sÃ¼re kota
veya geÃ§ersiz anahtar askÄ±ya alma davranÄ±ÅŸÄ±ndan ayrÄ±dÄ±r.

Model endpointini deployment acmadan dogrulamak icin acik gate ile bounded probe calistirilabilir. Probe model ciktisini, anahtari veya upstream hata metnini kaydetmez:

    $env:ARELBOT_CLEANAPIS_LIVE="1"
    python scripts/cleanapis_staging.py --env-file .env --live --confirm-live --report deploy/evidence/cleanapis-staging.json

Evidence yalnizca `cleanapis`, secili model, latency, yanit boyutu ve kullanim metadatasini tasir; bu probe provider terms/robots/production sign-off onayi yerine gecmez.

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

The chat, forum thread and Telegram file/callback identifiers are supplied by
the operator and are never written to the report. Missing identifiers fail
closed; the report records only operation names, bounded success counts and
exception types. The runner sends bounded staging messages and therefore must
target a disposable staging chat, never a production group.

## Live medya staging matrisi

CanlÄ± platform denetimi offline testlerin yerine geÃ§mez. Matris varsayÄ±lan olarak yalnÄ±zca yapÄ±yÄ± doÄŸrular; dÄ±ÅŸ aÄŸa eriÅŸim iÃ§in Ã¼Ã§ baÄŸÄ±msÄ±z onay gerekir:

    ARELBOT_MEDIA_STAGING_LIVE=1 python scripts/media_staging_matrix.py --config docs/media-live-matrix.example.json --live --confirm-live --report deploy/evidence/media-staging-live.json

GerÃ§ek dosya indirme ayrÄ±ca `--download` ister. CanlÄ± indirme raporu `scope=live-media-matrix` taÅŸÄ±r; tam URL, baÅŸlÄ±k, cookie ve upstream hata metni saklamaz; yalnÄ±zca platform, host, sÃ¼re, boyut ve sabit hata kodu taÅŸÄ±r. Her iÅŸ sonrasÄ± geÃ§ici medya klasÃ¶rÃ¼ temizlenir.
Tam deployment kanÄ±tÄ± iÃ§in matriste dÃ¶rt canlÄ± vaka (Instagram, TikTok, YouTube Shorts ve X) etkinleÅŸtirildikten sonra `--require-platform-matrix --require-slo --report deploy/evidence/media-staging-full-matrix.json` kullanÄ±lÄ±r; eksik platform dÄ±ÅŸ aÄŸa Ã§Ä±kmadan reddedilir.

## Immutable release

scripts/deploy_release.py temiz ve commit edilmiÅŸ Git checkout ister. Release
arÅŸivi RELEASE.json ve RELEASE_MANIFEST.sha256 ile hash'lenir. Preflight,
bot/worker health ve rollback tamamlanmadan current release symlink'i
deÄŸiÅŸtirilmez. SSH host key daha Ã¶nceden known_hosts iÃ§inde bulunmalÄ±dÄ±r.

ArÅŸivi SSH baÄŸlantÄ±sÄ±ndan baÄŸÄ±msÄ±z Ã¼retmek iÃ§in temiz checkout'ta
`python scripts/deploy_release.py --host example.invalid --build-only`
Ã§alÄ±ÅŸtÄ±rÄ±labilir. Bu kip aÄŸa baÄŸlanmaz; yalnÄ±zca arÅŸiv yolunu ve
SHA-256 deÄŸerini verir. Normal kip aynÄ± checksum doÄŸrulamasÄ±ndan sonra
SSH/SFTP ile yÃ¼kleme, preflight, health bekleme ve baÅŸarÄ±sÄ±z aktivasyonda
rollback uygular.


SSH kurulum betiÄŸi fail-closed Ã§alÄ±ÅŸÄ±r: preflight veya health baÅŸarÄ±sÄ±zlÄ±ÄŸÄ±nda
commit edilmemiÅŸ release klasÃ¶rÃ¼nÃ¼ ve yÃ¼klenen arÅŸivi EXIT trap ile temizler.
Bilinen Ã¶nceki release varsa rollback sÄ±rasÄ±nda korunur; baÅŸarÄ±lÄ± aktivasyonda
yalnÄ±zca geÃ§ici yÃ¼kleme dosyasÄ± silinir. Bu davranÄ±ÅŸ `test_deploy_release.py`
ile Linux/Bash replay ve Docker release gate iÃ§inde doÄŸrulanÄ±r.

## Health ve yÃ¶netici komutlarÄ±

Telegram yÃ¼zeyleri:

- /health: MongoDB, Redis, FFmpeg, provider ve worker durumlarÄ±.
- /metrics: kuyruk derinliÄŸi, servis ve typed feature p50/p95/p99 gecikmeleri,
  baÅŸarÄ±/hata sayaÃ§larÄ±, AI token/maliyet ve feature telemetry.
- /providers: provider health, circuit ve AI saÄŸlayÄ±cÄ± durumu.
- /providers governance: kaynak yÃ¶netiÅŸimi, robots/terms incelemesi ve Ã¼retim sign-off durumunu URL veya secret gÃ¶stermeden listeler.
- `scripts/evidence_audit.py`: deterministik staging kanÄ±tlarÄ±nÄ±n ÅŸema/invariant/gizli veri kontrolÃ¼; `--require-live` ayrÄ±ca gerÃ§ek Telegram mutation, tam medya matrisi, VDS backup/restore, monitoring delivery ve provider approval kanÄ±t dosyalarÄ±nÄ± zorunlu tutar.
- `--require-live` dosyanÄ±n yalnÄ±zca mevcut olmasÄ±na veya `ok=true` yazmasÄ±na gÃ¼venmez: `telegram-mutation-live`, `live-media-matrix`, `vds-backup-restore`, `live-monitoring-alert-delivery` ve `provider-governance-approval` scope'larÄ±nÄ±; iÅŸlem sayÄ±larÄ±nÄ±, 10 saniye medya p95'ini, en az 100 sohbet slotunu, checksum'Ä± ve approval ID'lerini ayrÄ±ca denetler.
- `scripts/release_gate.py --report <path>` gate Ã§Ä±ktÄ±sÄ±nÄ± atomik ve redacted JSON kanÄ±tÄ± olarak saklar; rapor hedefi symlink ise yazmayÄ± reddeder.
- /jobs <id>: herhangi bir worker iÅŸinin durumu.
- /audit: maskeli yÃ¶netici iÅŸlem kayÄ±tlarÄ±.
- /maintenance on|off: medya bakÄ±m kill-switch'i.

## Loopback observability endpoint

OBSERVABILITY_ENABLED=true ile opsiyonel /healthz ve Prometheus uyumlu
/metrics aÃ§Ä±lÄ±r. Bot OBSERVABILITY_PORT, worker
OBSERVABILITY_WORKER_PORT kullanÄ±r. VarsayÄ±lan bind 127.0.0.1'dir. Public
bind iÃ§in 16-256 karakterlik OBSERVABILITY_TOKEN zorunludur; token
X-Arelbot-Metrics-Token baÅŸlÄ±ÄŸÄ± veya Bearer ÅŸemasÄ±yla gÃ¶nderilir.
Endpoint yalnÄ±zca bounded GET kabul eder, query verisini echo etmez, kullanÄ±cÄ±
etiketi Ã¼retmez ve Cache-Control no-store dÃ¶ner. Reverse proxy/firewall
arkasÄ±nda tutulmalÄ±dÄ±r. `/healthz` `dependencies`, `providers` ve `status`
alanlarÄ±nÄ± dÃ¶ndÃ¼rÃ¼r; `healthy` Ã§ekirdek ve provider'larÄ±n iyi olduÄŸunu,
`degraded` yalnÄ±zca opsiyonel provider sorunu bulunduÄŸunu, `unhealthy` ise
MongoDB/Redis gibi zorunlu baÄŸÄ±mlÄ±lÄ±ÄŸÄ±n eriÅŸilemediÄŸini belirtir. BeÅŸ saniyelik
snapshot cache'i polling trafiÄŸinin upstream provider'larÄ± tetiklemesini Ã¶nler.
HTTP sÃ¶zleÅŸmesinde `healthy` ve `degraded` iÃ§in 200, `unhealthy` iÃ§in 503
dÃ¶ner; Docker, reverse proxy ve readiness probe'larÄ± zorunlu baÄŸÄ±mlÄ±lÄ±k
arÄ±zasÄ±nda yanlÄ±ÅŸlÄ±kla saÄŸlÄ±klÄ± rapor vermez.

## Backup ve restore

MongoDB dump, Redis RDB ve mevcut Chroma klasÃ¶rÃ¼ tek atomik backup klasÃ¶rÃ¼ne
alÄ±nÄ±r. BACKUP.json SHA-256 ve byte boyutlarÄ±nÄ± taÅŸÄ±r.

    python scripts/backup.py --output-dir backups

Restore varsayÄ±lan olarak yalnÄ±zca checksum doÄŸrular. Destructive iÅŸlem iÃ§in
confirm gerekir.

    python scripts/restore.py backups/arel-YYYYMMDDTHHMMSSZ --mongo-uri MONGODB_URI --redis-rdb-target /var/lib/redis/dump.rdb --chroma-target /opt/arel/shared/chroma_db --confirm

Redis RDB hedef data dizinine servis durdurularak yerleÅŸtirilir ve Redis
yeniden baÅŸlatÄ±lÄ±r. Staging restore sonrasÄ± manifest, koleksiyon sayÄ±larÄ±
ve Chroma arama smoke testi yapÄ±lÄ±r. Mongo namespace remap kullanÄ±lÄ±yorsa
mongo-source-database ve mongo-target-database birlikte verilir; restore
kodÄ± URI veritabanÄ± yolunu kaldÄ±rÄ±p nsFrom/nsTo uygulayarak kaynak
arÅŸivini hedefe taÅŸÄ±r. Symlink hedefleri ve archive traversal reddedilir.

Compose staging kanÄ±tÄ± iÃ§in yalnÄ±zca isimlendirilmiÅŸ sentinel verisi kullanan
Mongo dump/namespace-remap ve Redis RDB snapshot/load smoke Ã§alÄ±ÅŸtÄ±rÄ±labilir:

    python scripts/persistence_staging_smoke.py --report deploy/evidence/persistence-staging.json

Komut sonunda sentinel verisi, geÃ§ici container ve arÅŸiv dosyasÄ± temizlenir;
rapor credential veya provider Ã§Ä±ktÄ±sÄ± iÃ§ermez. Bu kontrol VDS backup/restore
tatbikatÄ±nÄ±n yerine geÃ§mez; release Ã¶ncesi gerÃ§ek staging hedefinde tekrar
uygulanmalÄ±dÄ±r.

GerÃ§ek VDS kanÄ±tÄ± iÃ§in backup/restore koÅŸucusu dÃ¶rt baÄŸÄ±msÄ±z kapÄ± ister:

    ARELBOT_VDS_BACKUP_LIVE=1 python scripts/vds_backup_restore_staging.py --live --confirm-live --confirm-destructive --mongo-uri "$MONGODB_URI" --redis-url "$REDIS_URL" --restore-mongo-uri "$STAGING_MONGODB_URI" --restore-redis-target /var/lib/redis-staging/dump.rdb --restore-chroma-target /opt/arel/staging/chroma_db --mongo-source-database arel_bot --mongo-target-database arel_bot_restore --report deploy/evidence/vds-backup-restore.json --json

Ãœretim Compose topolojisinde host iÅŸletim sistemine Mongo/Redis CLI kurmak
gerekmez. `scripts/vds_backup_restore_remote.py` aynÄ± tatbikatÄ± SSH Ã¼zerinden
Ã§alÄ±ÅŸan Mongo, Redis ve worker container'larÄ±nda yÃ¼rÃ¼tÃ¼r; Mongo hedef namespace'i
benzersiz ve geÃ§icidir, Redis ayrÄ± bir disposable container'da aÃ§Ä±lÄ±r, Chroma
geÃ§ici dizine Ã§Ä±karÄ±lÄ±r. Ãœretim servisi durdurulmaz. Komut parola deÄŸerini
komut satÄ±rÄ±na yazmaz; parola yalnÄ±zca `AREL_SSH_PASSWORD` ortam deÄŸiÅŸkeninden
okunur ve rapora alÄ±nmaz:

    AREL_SSH_PASSWORD='***' ARELBOT_VDS_BACKUP_LIVE=1 python scripts/vds_backup_restore_remote.py --host <vds-host> --user root --live --confirm-live --confirm-destructive --report deploy/evidence/vds-backup-restore.json --json

Bu komut yalnÄ±zca operatÃ¶rÃ¼n aÃ§Ä±kÃ§a belirttiÄŸi disposable staging hedefinde
Ã§alÄ±ÅŸtÄ±rÄ±lÄ±r. Restore hedefi aynÄ± Mongo namespace olamaz; Redis servisi hedef
RDB yerleÅŸtirilmeden Ã¶nce durdurulmalÄ± ve sonrasÄ±nda yeniden baÅŸlatÄ±lmalÄ±dÄ±r.
Rapor URI, parola, host yolu veya komut Ã§Ä±ktÄ±sÄ± iÃ§ermez; artifact iÃ§eriÄŸinin
SHA-256 Ã¶zetiyle birlikte backup manifesti, checksum ve restore adÄ±mlarÄ±nÄ±
kanÄ±tlar. Rollback tatbikatÄ± iÃ§in Ã¶nce aynÄ± hedefe ayrÄ± bir geri dÃ¶nÃ¼ÅŸ backup'Ä±
alÄ±nÄ±r; bu koÅŸucu bunu kendiliÄŸinden Ã¼retim verisine uygulamaz.

## Migration ve rollback

Migration runner ileri ve geri yÃ¼rÃ¼tÃ¼lebilir; mevcut schema v14'tÃ¼r.

    python scripts/migrate.py
    python scripts/mongo_migration_smoke.py

Production rollback Ã¶ncesi backup alÄ±nÄ±r. Migration rollback sonrasÄ± index,
TTL ve unique constraint'ler tekrar smoke ile doÄŸrulanÄ±r.

## Worker kuyruklarÄ±

Media, document, attachment, quote, speech, sticker tagging, market chart ve
image iÅŸleri Telegram handler iÃ§inde Ã§alÄ±ÅŸmaz. Redis taÅŸÄ±ma katmanÄ±,
Mongo job kaydÄ± gerÃ§ek durum kaynaÄŸÄ±dÄ±r. Worker yeniden baÅŸladÄ±ÄŸÄ±nda
queued/downloading/processing iÅŸler recovery politikasÄ±yla yeniden
deÄŸerlendirilir. Delivery ledger sending sonrasÄ± belirsiz sonucu duplicate
gÃ¶nderimden korur.

ProviderRuntime stale-while-revalidate gÃ¶revleri shutdown sÄ±rasÄ±nda ayrÄ±ca
iptal edilir. Feature executor Ã¶nce health yazma iÅŸlerini boÅŸaltÄ±r, sonra
refresh task'lerini durdurup cache/inflight state'ini temizler; provider HTTP
istemcileri ancak bundan sonra kapanÄ±r. BÃ¶ylece restart sonrasÄ±nda eski bir
task'in kapanmÄ±ÅŸ istemci Ã¼zerinden yeni network Ã§aÄŸrÄ±sÄ± baÅŸlatmasÄ± engellenir.

## Replay ve kalite kapÄ±larÄ±

    python scripts/release_gate.py --docker --requests 20 --concurrency 10
    python scripts/security_replay.py
    python scripts/provider_matrix.py
    python scripts/provider_load_test.py --requests 100 --concurrency 10
    python -m pytest -q src/tests/test_media_delivery_replay.py
    python scripts/media_queue_load_test.py --jobs 100 --workers 10 --chats 100 --report deploy/evidence/media-queue-load.json
    $env:ARELBOT_MEDIA_STAGING_LIVE="1"; python scripts/media_staging_matrix.py --config docs/media-live-matrix.example.json --live --confirm-live --download --concurrency 10 --jobs 10 --chat-count 100 --require-slo --report deploy/evidence/media-staging-concurrency.json

Release gate; architecture, provider metadata, compileall, pytest, Docker
build/test, health, migration, provider matrix, Mongo ledger, security replay
ve media delivery replay kontrollerini birlikte Ã§alÄ±ÅŸtÄ±rÄ±r. CanlÄ±
Telegram, VDS webhook ve platform medya matrisi bu deterministic kapÄ±nÄ±n
yerine geÃ§mez; staging kimlik bilgileriyle ayrÄ± yÃ¼rÃ¼tÃ¼lÃ¼r.

## Monitoring profile

Prometheus ve Grafana yalnÄ±zca `monitoring` profiliyle aÃ§Ä±lÄ±r; ikisi de
host'ta loopback'e bind edilir. Production'da bot ve worker gÃ¶zlem endpoint'i
iÃ§in `OBSERVABILITY_ENABLED=true`, `OBSERVABILITY_LISTEN=0.0.0.0` ve
`ARELBOT_OBSERVABILITY_TOKEN_FILE` tanÄ±mlanÄ±r. AynÄ± token dosyasÄ± Prometheus'a
secret olarak mount edilir; token `.env`, dashboard veya loglara yazÄ±lmaz.
Monitoring profili aÃ§Ä±lacaksa MONITORING_ENABLED=true ayarlanÄ±r; preflight aynÄ± anda observability'nin etkin olduÄŸunu ve GRAFANA_ADMIN_PASSWORD_FILE dosyasÄ±nÄ±n 0600 izinli, gerÃ§ek bir parola iÃ§erdiÄŸini kontrol eder.

    docker compose --profile prod --profile monitoring up -d

Grafana admin parolasÄ± `GRAFANA_ADMIN_PASSWORD_FILE` ile dÄ±ÅŸarÄ±dan secret
olarak verilir. VarsayÄ±lan placeholder ile production monitoring baÅŸlatÄ±lmaz;
gerÃ§ek secret dosyalarÄ± deployment host'unda 0600 izinli tutulur. Dashboard
Prometheus kurallarÄ± `deploy/monitoring/alerts.yml` iÃ§inde bot/worker scrape
kesintisi, medya kuyruk backlog'u, workersÄ±z kuyruk ve dÃ¼ÅŸen provider health
telemetrisi iÃ§in alarm Ã¼retir.
`deploy/monitoring/grafana/dashboards/arelbot-overview.json` kuyruk derinliÄŸi,
medya p95 gecikmesi, iÅŸ baÅŸarÄ±/baÅŸarÄ±sÄ±zlÄ±k oranÄ± ve AI kullanÄ±mÄ±nÄ± gÃ¶sterir.

Alertmanager aynÄ± `monitoring` profiline dahildir ve Prometheus'un beÅŸ kuralÄ±nÄ±
`deploy/monitoring/alertmanager.yml` Ã¼zerinden tek bir HTTPS webhook alÄ±cÄ±sÄ±na
iletir. AlÄ±cÄ± URL'si `ARELBOT_ALERT_WEBHOOK_URL_FILE` ile 0600 izinli secret
dosyasÄ±ndan okunur; production preflight HTTP, private/loopback host, kimlik
bilgisi ve query-string iÃ§eren hedefleri reddeder.

Disposable staging teslimatÄ± iÃ§in yalnÄ±zca loopback Alertmanager API'sine
canary gÃ¶nderilir:

    ARELBOT_MONITORING_LIVE=1 python scripts/monitoring_alert_staging.py --live --confirm-live --confirm-notification --alertmanager-url http://127.0.0.1:9093 --report deploy/evidence/monitoring-alert-delivery.json --json

KoÅŸucu `/-/ready`, `/metrics` ve `/api/v2/alerts` akÄ±ÅŸÄ±nÄ± Ã¶lÃ§er; webhook
entegrasyon sayacÄ± artmadan baÅŸarÄ±lÄ± rapor Ã¼retmez. VarsayÄ±lan Ã§alÄ±ÅŸtÄ±rma
offline'dÄ±r ve hiÃ§bir alÄ±cÄ±ya istek gÃ¶ndermez.

Alert rule replay is pinned in `deploy/monitoring/alerts.test.yml`; the Docker
release gate runs `promtool test rules` for all five rules, including exact
labels and annotations. Live Alertmanager/webhook delivery remains a VDS-stage
check.

## Source ve secret politikasÄ±

Her typed provider docs/DATA_SOURCES.md ve licenses.json iÃ§inde lisans,
attribution, terms, rate limit, freshness ve fallback kaydÄ± taÅŸÄ±r. Token,
cookie, tam URL, credential, kullanÄ±cÄ± metni ve medya path'i loglanmaz.
Provider terms onayÄ± olmayan adapter feature flag/kill-switch arkasÄ±nda
tutulur.

Public HTML provider transportu HTTPS kaynak hostu, redirect kapatÄ±lmasÄ±,
content-type ve 2 MiB response sÄ±nÄ±rÄ±nÄ± ortak uygular. `PROVIDER_RESPONSE_TOO_LARGE`,
`PROVIDER_CONTENT_TYPE_INVALID` ve `PROVIDER_HOST_NOT_ALLOWED` hatalarÄ± sabit
kullanÄ±cÄ± hata kodlarÄ±na dÃ¶nÃ¼ÅŸÃ¼r; upstream yanÄ±tÄ± cache'e veya loga aktarÄ±lmaz.

Ãœretim operasyon kanalÄ±nda `LOG_CHANNEL_ERRORS_ONLY=true` kullanÄ±lÄ±r. Bu ayar
normal kullanÄ±cÄ± mesajÄ± ve bot yanÄ±tÄ± kayÄ±tlarÄ±nÄ± Telegram log konusuna
gÃ¶ndermez; yalnÄ±zca gÃ¼venli hata sÄ±nÄ±rÄ±ndan Ã¼retilen hata olaylarÄ± gÃ¶nderilir.

`APP_ENV=production` iÃ§in `REQUIRE_PROVIDER_APPROVAL` varsayÄ±lan olarak aÃ§Ä±ktÄ±r.
Bu runtime guard, preflight atlanÄ±p Compose elle baÅŸlatÄ±lsa bile `pending` veya
`disabled` dÄ±ÅŸ provider'Ä±n aÄŸ Ã§aÄŸrÄ±sÄ±ndan Ã¶nce `FEATURE_DISABLED` dÃ¶nmesini
saÄŸlar. Development/replay iÃ§in Ã¶rnek `.env` dosyasÄ± bunu aÃ§Ä±kÃ§a `false` yapar;
Ã¼retimde kapatÄ±lmasÄ± Ã¶nerilmez ve release kanÄ±tÄ± olmadan yapÄ±lmamalÄ±dÄ±r.

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

Provider adapter'larÄ±nÄ±n teknik olarak cevap vermesi, kaynaÄŸÄ±n kullanÄ±m
ÅŸartlarÄ±nÄ±n onaylandÄ±ÄŸÄ± anlamÄ±na gelmez. Release gate pending kaynaklarÄ± gÃ¶rÃ¼nÃ¼r
tutar; VDS aÃ§Ä±lÄ±ÅŸÄ±nda sign-off zorunlu hale getirmek iÃ§in aÅŸaÄŸÄ±daki komut
kullanÄ±lÄ±r:

    python scripts/provider_governance.py --require-approved
    python scripts/production_preflight.py --env-file .env --require-provider-approval --nginx-config /etc/nginx/sites-enabled/arelbot.conf

Bu kontrol canlÄ± kaynaÄŸa Ã§aÄŸrÄ± yapmaz ve Ã§Ä±ktÄ±da source URL, credential veya kullanÄ±cÄ± verisi yazmaz. OnaylanmamÄ±ÅŸ kaynaklar feature kill-switch ile kapalÄ±
tutulmalÄ±; manifestte `source_governance`, `robots_policy`, `terms_review` ve
`production_signoff` alanlarÄ± birlikte gÃ¼ncellenmeden aÃ§Ä±lmamalÄ±dÄ±r.

Ãœretim baÅŸlangÄ±cÄ±nda henÃ¼z incelenmemiÅŸ kaynaklarla birlikte Ã§ekirdek botu
Ã§alÄ±ÅŸtÄ±rmak gerekiyorsa, bekleyen provider'lar `.env` iÃ§indeki
`DISABLED_FEATURES` listesine feature key olarak aÃ§Ä±kÃ§a yazÄ±lÄ±r. Preflight bu
listeyi governance kontrolÃ¼ne taÅŸÄ±r; listede olmayan aktif provider hÃ¢lÃ¢
fingerprint-bound approval ister. Bilinmeyen bir feature key fail-closed
sonuÃ§lanÄ±r ve runtime aynÄ± kaynaÄŸÄ± aÄŸ Ã§aÄŸrÄ±sÄ±ndan Ã¶nce `FEATURE_DISABLED` ile
durdurur. Devre dÄ±ÅŸÄ± bÄ±rakÄ±lan feature'lar approval raporunda `disabled` olarak
gÃ¶rÃ¼nÃ¼r; bu mekanizma hukuki/terms onayÄ±nÄ±n yerine geÃ§mez.

Ã–rnek (yalnÄ±zca kaynak incelemesi tamamlanana kadar geÃ§ici kapatma):

    DISABLED_FEATURES=pharmacy,fuel,currency,crypto,market,earthquakes,bim_deals,breaking_news,weather,air_quality,prayer_times,culture_recommendation,lyrics,image_generation
    python scripts/production_preflight.py --env-file .env --require-provider-approval --nginx-config /etc/nginx/sites-enabled/arelbot.conf

Ä°lk VDS kurulumunda public domain henÃ¼z hazÄ±r deÄŸilse `TELEGRAM_MODE=polling`
aÃ§Ä±kÃ§a seÃ§ilebilir. Bu modda `WEBHOOK_URL`, `WEBHOOK_SECRET` ve Nginx
gerektirilmez; deploy scripti proxy kontrolÃ¼nÃ¼ otomatik olarak atlar. KalÄ±cÄ±
Ã¼retim modeli iÃ§in HTTPS webhook Ã¶nerilir ve polling yalnÄ±zca eriÅŸim modeli
olarak bilinÃ§li biÃ§imde seÃ§ildiÄŸinde Ã§alÄ±ÅŸÄ±r.

### Staging core profili

Kaynak incelemesi tamamlanmadan botun Telegram, AI, yerel veri ve yÃ¶netim
yÃ¼zeylerini test etmek iÃ§in bekleyen provider'larÄ±n tamamÄ±nÄ± aÃ§Ä±kÃ§a kapatan
secret'sÄ±z bir profil Ã¼retilebilir:

    python scripts/staging_profile.py \
      --template deploy/evidence/provider-approval-review-template.json \
      --report deploy/evidence/staging-core-profile.json

Komut dÄ±ÅŸ aÄŸa Ã§Ä±kmaz, onay vermez ve approval evidence Ã¼retmez. Raporun
`disabled_features_csv` alanÄ± staging `.env` dosyasÄ±nda
`DISABLED_FEATURES` olarak kullanÄ±labilir. `REQUIRE_PROVIDER_APPROVAL=true`
korunmalÄ±dÄ±r; bilinmeyen bir feature anahtarÄ± preflight ve governance
kontrolÃ¼nde fail-closed olur. Bu profil Ã¼retim onayÄ±nÄ±n yerine geÃ§mez ve
canlÄ± deployment evidence sayÄ±lmaz.

Runtime profilini doÄŸrudan baÄŸlamak iÃ§in staging ortamÄ±nda
`STAGING_PROFILE_PATH=deploy/evidence/staging-core-profile.json` verilebilir.
Uygulama profili baÅŸlangÄ±Ã§ta doÄŸrular ve bekleyen provider'larÄ± registry'ye
ekler; aynÄ± dosya `APP_ENV=production` ile kullanÄ±lÄ±rsa fail-closed olarak
baÅŸlamaz.

### Kaynak inceleme Ã§alÄ±ÅŸma kÃ¢ÄŸÄ±dÄ±

Onay dosyasÄ± doldurulmadan Ã¶nce, mevcut feature/provider kimliÄŸine baÄŸlÄ±
fingerprint'leri ve inceleme durumunu iÃ§eren gÃ¼venli bir Ã§alÄ±ÅŸma kÃ¢ÄŸÄ±dÄ±
Ã¼retilebilir:

    python scripts/provider_governance.py --review-template --report deploy/evidence/provider-approval-review-template.json

Bu Ã§Ä±ktÄ± bir approval manifesti deÄŸildir; approval ID, reviewer ve geÃ§erlilik
penceresi iÃ§ermediÄŸi iÃ§in production tarafÄ±ndan yÃ¼klenemez. URL'ler rapora
kopyalanmaz. Ä°nceleme tamamlandÄ±ÄŸÄ±nda operatÃ¶r, `docs/DATA_SOURCES.md` ve
provider'Ä±n gÃ¼ncel terms/robots kayÄ±tlarÄ±nÄ± kontrol ederek ayrÄ± approval
manifestini oluÅŸturur. Fingerprint deÄŸiÅŸmiÅŸse eski onay kullanÄ±lmaz.

### Fingerprint-bound approval manifest

Kod manifestindeki dÃ¶rt alanÄ± elle deÄŸiÅŸtirerek kaynak aÃ§mak yerine, gerÃ§ek
inceleme tamamlandÄ±ÄŸÄ±nda `PROVIDER_APPROVAL_MANIFEST` ile makine tarafÄ±ndan
doÄŸrulanan bir operator kanÄ±tÄ± yÃ¼klenebilir. Dosya yalnÄ±zca public kaynak
kimliÄŸinin SHA-256 fingerprint'ini, inceleyen kiÅŸiyi, approval ID ve en fazla
366 gÃ¼nlÃ¼k geÃ§erlilik penceresini taÅŸÄ±r; URL veya secret release raporuna
kopyalanmaz. Fingerprint; feature, provider, kaynak adÄ±, kaynak URL'si, lisans
ve terms URL'sinden Ã¼retilir. Bu alanlardan biri deÄŸiÅŸirse onay otomatik olarak
geÃ§ersiz kalÄ±r.

Åema Ã¶rneÄŸi (gerÃ§ek onay bilgisi iÃ§ermez):

    {
      "schema_version": 1,
      "approvals": [
        {
          "feature_key": "weather",
          "approval_id": "review-2026-001",
          "source_fingerprint": "<64 kÃ¼Ã§Ã¼k harf hex karakteri>",
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

Dosya path'i production `.env` iÃ§inde belirtilir ve preflight aynÄ± dosyayÄ±
feature manifestiyle karÅŸÄ±laÅŸtÄ±rÄ±r:

    PROVIDER_APPROVAL_MANIFEST=/etc/arelbot/provider-approvals.json
    python scripts/provider_governance.py --require-approved --approval-manifest /etc/arelbot/provider-approvals.json
    python scripts/production_preflight.py --env-file .env --require-provider-approval --provider-approval-manifest /etc/arelbot/provider-approvals.json
    python scripts/provider_governance.py --require-approved --approval-manifest /etc/arelbot/provider-approvals.json --approval-evidence --report deploy/evidence/provider-governance-approval.json

Dosya yoksa, sÃ¼resi dolmuÅŸsa, fingerprint uyuÅŸmuyorsa veya dÃ¶rt onay alanÄ±
tamamlanmamÄ±ÅŸsa production provider yine fail-closed kalÄ±r. Approval dosyasÄ±
kaynak terms incelemesinin yerine geÃ§mez; yalnÄ±zca tamamlanmÄ±ÅŸ incelemenin
deploy sÄ±rasÄ±nda tekrar Ã¼retilebilir biÃ§imde uygulanmasÄ±nÄ± saÄŸlar.


### Grup yanÄ±tÄ± rollout ve gÃ¶zlem

`GROUP_TARGETED_REPLIES_ENABLED=true` varsayÄ±lan olarak aÃ§Ä±k olsa da rollout
Ã¶ncesinde `GROUP_TARGETED_REPLIES_SHADOW=true` ile yalnÄ±zca plan ve validation
metrikleri gÃ¶zlemlenir; Telegram'a gÃ¶nderim yapÄ±lmaz. Owner test grubunda
`group_targeted_replies` Ã¶zelliÄŸi doÄŸrulanmadan canary aÃ§Ä±lÄ±mÄ± yapÄ±lmaz.

Ä°zlenecek metrikler kullanÄ±cÄ± adÄ± veya mesaj metni iÃ§ermez:

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

Ã‡apraz kullanÄ±cÄ± reply'i, duplicate gÃ¶nderim veya yanlÄ±ÅŸ forum konusu
gÃ¶rÃ¼lÃ¼rse `GROUP_TARGETED_REPLIES_ENABLED=false` ile kill switch uygulanÄ±r.
Mongo v15 migration'Ä± geri alÄ±nabilir indekslerden oluÅŸur; migration rollback
mesaj kayÄ±tlarÄ±nÄ± silmez. Redis yeniden baÅŸlatÄ±ldÄ±ÄŸÄ±nda aday index'i Mongo'dan
yeniden okunur, delivery ledger ise Mongo'daki claim durumuyla duplicate
gÃ¶nderimi engeller.
