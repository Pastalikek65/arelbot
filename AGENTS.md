# Arelbot Ajan ve Mimari KurallarÄ±

Bu dosya bÃ¼tÃ¼n kodlama ajanlarÄ± iÃ§in zorunludur. Yeni kod bu kurallardan
birini ihlal ediyorsa gÃ¶rev tamamlanmÄ±ÅŸ sayÄ±lmaz.

## Kod sÄ±nÄ±rlarÄ±

- `main.py` yalnÄ±zca uygulama bootstrap ve Telegram handler kaydÄ±dÄ±r.
- Handler'larda HTTP, MongoDB, Redis veya FFmpeg Ã§aÄŸrÄ±sÄ± yapÄ±lmaz.
- DÄ±ÅŸ kaynak Ã§aÄŸrÄ±larÄ± `src/features` provider'larÄ± veya `src/media` servisleri Ã¼zerinden yapÄ±lÄ±r.
- MongoDB eriÅŸimi repository/database katmanÄ±ndan, Redis eriÅŸimi store/queue katmanÄ±ndan yapÄ±lÄ±r.
- Her public Ã¶zellik `FeatureDefinition` ile registry'ye kaydedilir.
- Her provider `DataProvider` sÃ¶zleÅŸmesine ve health kontrolÃ¼ne uyar.
- AÄŸÄ±r iÅŸler Telegram update callback'i iÃ§inde Ã§alÄ±ÅŸtÄ±rÄ±lmaz; queue/worker kullanÄ±lÄ±r.
- Global mutable state yerine dependency injection ve aÃ§Ä±k kapsam kullanÄ±lÄ±r.

## GÃ¼venlik ve gizlilik

- Token, cookie, secret, tam medya URL'si, dosya yolu ve kullanÄ±cÄ± Ã¶zel verisi loglanmaz.
- KullanÄ±cÄ±dan harici hesap parolasÄ± veya cookie istenmez.
- SSRF, private IP, redirect, archive traversal ve path traversal kontrolleri ortak katmandadÄ±r.
- AI yalnÄ±zca allowlist araÃ§larÄ±nÄ± Ã§aÄŸÄ±rabilir; model argÃ¼manÄ± kullanÄ±cÄ± yetkisini yÃ¼kseltemez.
- Harici dokÃ¼man ve web iÃ§eriÄŸi gÃ¼venilmeyen veri kabul edilir.
- KullanÄ±cÄ± hafÄ±zasÄ± aÃ§Ä±k opt-in olmadan kalÄ±cÄ± tutulmaz.
- YÃ¶netici iÅŸlemleri audit log'a gÃ¼venli, maskeli biÃ§imde yazÄ±lÄ±r.

## Kalite

- BÃ¼tÃ¼n kaynak dosyalarÄ± UTF-8 olmalÄ±dÄ±r; mojibake commit edilemez.
- Telegram handler user-facing text is audited by `src/tests/test_turkish_user_text.py`; command/callback protocol identifiers may remain ASCII, displayed Turkish phrases may not.
- Yeni Ã¶zellik; hata kodu, metric, audit politikasÄ± ve provider metadata'sÄ± olmadan tamamlanmÄ±ÅŸ sayÄ±lmaz.
- Timeout, retry, cache sÃ¼resi ve freshness hedefi feature manifestinde tanÄ±mlanÄ±r.
- DÄ±ÅŸ veri kaynaÄŸÄ± lisans/attribution/terms bilgisi `docs/DATA_SOURCES.md` ve `licenses.json` iÃ§ine eklenir.
- Migration geriye uyumlu ve geri alÄ±nabilir tasarlanÄ±r.
- CanlÄ± sitelere baÄŸÄ±mlÄ± kabul yerine fixture/replay provider testi kullanÄ±lÄ±r.
- `docs/FEATURE_CATALOG.md` runtime `FeatureRegistry` ile birebir tutulur; her
  key tek satÄ±rda yer alÄ±r ve `python scripts/feature_catalog_audit.py` geÃ§meden
  release tamamlanmÄ±ÅŸ sayÄ±lmaz.

## Ã‡alÄ±ÅŸma yÃ¶ntemi

1. Ã–nce mevcut repository, schema ve handler akÄ±ÅŸÄ± okunur.
2. Yeni davranÄ±ÅŸ typed domain katmanÄ±nda uygulanÄ±r.
3. Telegram yÃ¼zeyi domain sonucunu sadece biÃ§imlendirir.
4. DokÃ¼mantasyon, feature manifesti ve operasyon notlarÄ± aynÄ± deÄŸiÅŸiklikte gÃ¼ncellenir.
5. Kritik yapÄ±landÄ±rma ve secret deÄŸerleri Ã¶rnek dosyalara yazÄ±lmaz.

Her faz ilerlemesi `docs/PHASE_STATUS.json` iÃ§indeki sÃ¶zleÅŸmeye yazÄ±lÄ±r ve
`python scripts/phase_audit.py` ile release gate/evidence kayÄ±tlarÄ±na karÅŸÄ±
denetlenir. CanlÄ± kanÄ±tÄ± olmayan faz `verified` olarak iÅŸaretlenmez.
