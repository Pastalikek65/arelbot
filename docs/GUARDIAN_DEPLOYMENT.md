# Kek Guardian deployment contract

**Son doğrulama:** 2026-10-17, embedded Guardian genişletilmiş yüzey + hedefli test paketi
**Kapsam:** `78.135.83.87` üzerinde Arel’in tek bot olarak çalışması

## Keşif sonucu

VDS keşfi sırasında `kek-guardian.service` etkin bir systemd servisi olarak
çalışıyordu. Servis `/root/guardian` altındaki ayrı bir Telegram polling botuydu.
Arel'in
beklediği `/run/kek-guardian/guardian.sock` Unix socket'i, yerel Guardian RPC
portu ve systemd socket activation birimi keşfedilmedi.

Mevcut süreç Arel'in bounded NDJSON sözleşmesine bağlanamadığı için
`GUARDIAN_MODE=disabled` kalır. Socket veya evaluator varmış gibi active/shadow
kanıtı üretilemez.

## Secret bulgusu

Salt-okunur unit incelemesinde Guardian systemd tanımında `DATABASE_URL` değerinin
doğrudan `Environment=` satırında tutulduğu görüldü. Değer rapora yazılmadı.
Guardian servisi devre dışı bırakıldı; eski unit yeniden etkinleştirilecekse
veritabanı parolası döndürülmeli ve değer root'un okuyabildiği `0600`
`EnvironmentFile` veya systemd `LoadCredential` kaynağına taşınmalıdır. Eski
`Environment=` satırı kaldırıldıktan sonra
`systemctl cat`, `systemctl show` ve journal içinde secret taraması yapılmalıdır.

## Arel entegrasyon modeli

Guardian'ın tam komut kataloğu `src/moderation/guardian_surface.py` içinde
Arel'e taşındı. Yönetim, anti-link/anti-flood, blocklist/whitelist, lock ve
medya yasağı, captcha, karşılama/veda, not/alias, roller, global ban,
federasyon state'i, gece modu, recurring ayarları ve bilgi komutları aynı
Mongo chat/user state'i üzerinden çalışır. Ağır veya değişken işlerde yine
Arel'in worker/queue katmanı kullanılır.

Genişletilmiş uyumluluk yüzeyinde ayrıca admin izin profilleri (`/admin`,
`/deadmin`, `/muter`, `/unmuter`, `/yetkiler`), sabit/purge işlemleri,
hayalet ban, reaksiyon ve hesap/medya düzenleme korumaları, sticker/GIF paket
listeleri, limit ayarları, federasyon notları, federasyon log/itiraz kanalı,
federasyon abonelikleri, fban içe/dışa aktarma, risk/istatistik kartları ve
federasyon sahiplik işlemleri bulunur. Bu komutlar her grup için ayrı Mongo
ayarında tutulur; Telegram çağrısı gereken işlemler
`GuardianTelegramService` üzerinden yapılır.

Kullanıcıya görünen Guardian yardım yüzeyi `/ghelp` ile açılır. `/hizlikur`
grup yöneticisinin grup bazlı temel korumaları tek işlemde kurmasına izin verir;
`/kayitkanali`, `/yoneticiler`, `/notekle` ve `/notsil` Arel adlarıdır. Eski
Guardian adları gizli uyumluluk alias'ı olarak çalışır. Katılım doğrulaması
buton, matematik veya metin kodu olarak seçilebilir (`/captchatype`); doğru
cevap pending kaydını tüketir. `/gecemodu` Telegram izinlerini gerçekten
kilitler/açar, sessiz moderasyon komutları ise komut mesajını silip yalnızca
audit kaydı bırakır.

Telegram Bot API tarihsel üye listesini ve geçmişteki tüm mesajları vermez.
Bu nedenle `/zombies`, `/firstmsg` ve `/memberlog` Arel'in sakladığı 30 günlük
mesaj/aktivite penceresiyle sınırlıdır. `/purge` Telegram mesaj kimliklerinin
ardışık olduğu bounded bir aralıkta en fazla 200 silme dener; silinemeyen
mesajlar kullanıcıya sahte başarı olarak gösterilmez. Federasyon kararları
kalıcı ortak ban kaydı ve her gruptaki yeni mesaj/giriş enforcement'ı olarak
uygulanır; ayrı bir ikinci Telegram polling botu çalıştırılmaz.

Destructive komutlar (`/mute`, `/ban`, `/kick`, `/warn`, `/pin` ve silent
varyantları dahil) `GuardianCommandService` ile typed `ModerationAction`
üretir; Telegram çağrısı yalnızca ortak authorization, idempotency ledger ve
`TelegramModerationActionExecutor` üzerinden yapılır. Join captcha callback'i
chat ve kullanıcı kimliğini birlikte doğrular. Gban ve medya kilidi policy'si
AI, medya ve oyun akışından önce uygulanır.

VDS'teki `/root/guardian` polling servisi Arel'in token'ı için çalıştırılmaz.
Aynı token ile iki polling botu Telegram update'lerini yarıştırır ve iki ayrı
aksiyon otoritesi üretir. `/root/guardian` kaynakları yalnızca port/replay
karşılaştırması için tutulur; SQLAlchemy veritabanı, Telethon oturumu ve
legacy global state Arel imajına alınmaz. İstenirse ileride mevcut
kimlik doğrulamalı Unix-socket evaluator bridge'i ayrı bir provider olarak
bağlanabilir, ancak Arel'in yerel Guardian komutları buna bağlı değildir.

Canlı durum: `kek-guardian.service` `inactive` ve `disabled`; Arel release
`2026.10.17-c5abf2f` içindeki bot ve worker `healthy` durumundadır. Geri alma
gerekirse eski unit tekrar etkinleştirilmeden önce Arel polling durdurulmalı;
aksi halde aynı token ile iki update tüketicisi oluşur.

## Rollout kapıları

Sıra `disabled` → owner test grubunda `shadow` → kontrollü `active` canary'dir.
Her adımda bounded ve secret-free kanıt, timeout/replay/peer-credential reddi
testleri ve geri alma kararı tutulur. `scripts/guardian_bridge_staging.py
--self-test` yalnızca sentetik transport testidir; üretim evaluator'ı değildir.

Gerçek bağlantı testi açık üçlü gate ister:

```text
ARELBOT_GUARDIAN_LIVE=1 python scripts/guardian_staging.py --live --confirm-live --shadow
```

Socket yoksa beklenen sonuç `socket_unavailable` ve `ok=false` olur. Bu sonuç
başarıya çevrilmez ve `docs/PHASE_STATUS.json` faz 9'u `verified` olarak
işaretlenmez.

## Mevcut durum

`deploy/evidence/vds-readiness.json` son salt-okunur VDS çalışmasında bot,
worker, MongoDB ve Redis healthcheck'lerinin başarılı olduğunu gösterir.
Guardian mode `disabled`, socket `present=false` ve `readable=false` olarak
raporlanmıştır. Bu, mevcut bridge eksikliği nedeniyle beklenen güvenli durumdur;
canlı Guardian enforcement kanıtı değildir.
