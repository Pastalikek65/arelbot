# Arelbot Ajan ve Mimari Kuralları

Bu dosya bütün kodlama ajanları için zorunludur. Yeni kod bu kurallardan
birini ihlal ediyorsa görev tamamlanmış sayılmaz.

## Kod sınırları

- `main.py` yalnızca uygulama bootstrap ve Telegram handler kaydıdır.
- Handler'larda HTTP, MongoDB, Redis veya FFmpeg çağrısı yapılmaz.
- Dış kaynak çağrıları `src/features` provider'ları veya `src/media` servisleri üzerinden yapılır.
- MongoDB erişimi repository/database katmanından, Redis erişimi store/queue katmanından yapılır.
- Her public özellik `FeatureDefinition` ile registry'ye kaydedilir.
- Her provider `DataProvider` sözleşmesine ve health kontrolüne uyar.
- Ağır işler Telegram update callback'i içinde çalıştırılmaz; queue/worker kullanılır.
- Global mutable state yerine dependency injection ve açık kapsam kullanılır.

## Güvenlik ve gizlilik

- Token, cookie, secret, tam medya URL'si, dosya yolu ve kullanıcı özel verisi loglanmaz.
- Kullanıcıdan harici hesap parolası veya cookie istenmez.
- SSRF, private IP, redirect, archive traversal ve path traversal kontrolleri ortak katmandadır.
- AI yalnızca allowlist araçlarını çağırabilir; model argümanı kullanıcı yetkisini yükseltemez.
- Harici doküman ve web içeriği güvenilmeyen veri kabul edilir.
- Kullanıcı hafızası açık opt-in olmadan kalıcı tutulmaz.
- Yönetici işlemleri audit log'a güvenli, maskeli biçimde yazılır.

## Kalite

- Bütün kaynak dosyaları UTF-8 olmalıdır; mojibake commit edilemez.
- Telegram handler user-facing text is audited by `src/tests/test_turkish_user_text.py`; command/callback protocol identifiers may remain ASCII, displayed Turkish phrases may not.
- Yeni özellik; hata kodu, metric, audit politikası ve provider metadata'sı olmadan tamamlanmış sayılmaz.
- Timeout, retry, cache süresi ve freshness hedefi feature manifestinde tanımlanır.
- Dış veri kaynağı lisans/attribution/terms bilgisi `docs/DATA_SOURCES.md` ve `licenses.json` içine eklenir.
- Migration geriye uyumlu ve geri alınabilir tasarlanır.
- Canlı sitelere bağımlı kabul yerine fixture/replay provider testi kullanılır.
- `docs/FEATURE_CATALOG.md` runtime `FeatureRegistry` ile birebir tutulur; her
  key tek satırda yer alır ve `python scripts/feature_catalog_audit.py` geçmeden
  release tamamlanmış sayılmaz.

## Çalışma yöntemi

1. Önce mevcut repository, schema ve handler akışı okunur.
2. Yeni davranış typed domain katmanında uygulanır.
3. Telegram yüzeyi domain sonucunu sadece biçimlendirir.
4. Dokümantasyon, feature manifesti ve operasyon notları aynı değişiklikte güncellenir.
5. Kritik yapılandırma ve secret değerleri örnek dosyalara yazılmaz.

Her faz ilerlemesi `docs/PHASE_STATUS.json` içindeki sözleşmeye yazılır ve
`python scripts/phase_audit.py` ile release gate/evidence kayıtlarına karşı
denetlenir. Canlı kanıtı olmayan faz `verified` olarak işaretlenmez.
