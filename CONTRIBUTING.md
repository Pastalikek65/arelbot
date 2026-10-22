# Katkı rehberi

1. Python 3.12 kullanın; bağımlılıkları `requirements.txt` ve `constraints.txt` ile kurun.
2. `.env.example` dosyasından yerel `.env` oluşturun. Gerçek anahtar, oturum, kullanıcı mesajı veya indirilmiş içeriği commit etmeyin.
3. Değişikliği ilgili domain/provider/service katmanında tutun. Handler içine doğrudan HTTP veya veri tabanı erişimi eklemeyin; [AGENTS.md](AGENTS.md) sınırlarını izleyin.
4. Hata için önce başarısız olan davranışı gösteren test ekleyin. HTTP testlerinde fixture kullanın; canlı API çağrılarını test takımına koymayın.
5. Yeni kullanıcı özelliğinde registry, komut kaydı, yardım, [özellik rehberi](docs/FEATURES.md), test ve kaynak/lisans kaydını birlikte güncelleyin.

## Kontroller

```text
python -m pytest -q src/tests -m "not integration"
python scripts/feature_catalog_audit.py
python scripts/architecture_check.py --strict
python scripts/phase_audit.py
git diff --check
```

Test başarısı ile canlı Telegram kabulünü ayrı raporlayın. Çalıştırılmayan kontrolleri `NOT_RUN` olarak kaydedin. Yayın adımları, yedek ve geri dönüş için [operasyon rehberini](docs/OPERATIONS.md) kullanın.

## Lisans ve veri

UNO kaynak bildirimi ve AGPL kaynak sunumu korunmalıdır. Yeni veri veya kod kaynağını `licenses.json`, `LICENSES.md` ve `docs/DATA_SOURCES.md` içinde kaydedin. Yeni özellik için lisans/servis kullanım iznini varsaymayın.
