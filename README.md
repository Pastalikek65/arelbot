# Arelbot

Arelbot, Telegram Ã¼zerinde Ã§alÄ±ÅŸan, yapay zekÃ¢, medya, kiÅŸisel Ã¼retkenlik,
grup yÃ¶netimi, quote/sticker ve oyun Ã¶zelliklerini tek bir botta birleÅŸtiren
modÃ¼ler bir sÃ¼per bottur.

## Ã–ne Ã§Ä±kan Ã¶zellikler

- EVREN tabanlÄ± OpenAI uyumlu yapay zekÃ¢ yÃ¶nlendirmesi
- Instagram, TikTok, YouTube Shorts ve desteklenen public medya baÄŸlantÄ±larÄ±
- Kuyruklu indirme, yeniden deneme, cache ve Telegram `file_id` kullanÄ±mÄ±
- Grup bazlÄ± quote ve video indirici ayarlarÄ±
- TÃ¼rkÃ§e ÅŸehir/plaka/telefon/ilÃ§e dizini
- Hava, deprem, haber, dÃ¶viz, altÄ±n, eczane ve akaryakÄ±t provider'larÄ±
- Not, gÃ¶rev, hatÄ±rlatÄ±cÄ± ve gÃ¼nlÃ¼k Ã¶zet akÄ±ÅŸlarÄ±
- KalÄ±cÄ± oyun state'i, ekonomi, baÅŸarÄ±mlar ve leaderboard
- Quote temalarÄ±, sticker paketleri ve inline arama
- MongoDB kalÄ±cÄ± veri deposu, Redis queue/cache ve Docker Compose Ã¼retim profili

## Mimari

`main.py` yalnÄ±zca uygulamayÄ± baÅŸlatÄ±r. Telegram handler'larÄ± domain servislerine
delege eder; dÄ±ÅŸ veri provider'larÄ± typed sÃ¶zleÅŸmeleri, aÄŸÄ±r iÅŸler worker
kuyruÄŸunu, kalÄ±cÄ± kayÄ±tlar repository katmanÄ±nÄ± kullanÄ±r.

Mimari kurallar iÃ§in [AGENTS.md](AGENTS.md), hedef mimari iÃ§in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), Ã¶zellik matrisi iÃ§in
[docs/FEATURE_CATALOG.md](docs/FEATURE_CATALOG.md) dosyalarÄ±na bakÄ±n.

## Yerel Ã§alÄ±ÅŸtÄ±rma

1. Python 3.11 veya 3.12 ve Docker kurun.
2. `.env.example` dosyasÄ±nÄ± `.env` olarak kopyalayÄ±n ve gerekli deÄŸerleri
   yalnÄ±zca yerel secret store'da doldurun.
3. GeliÅŸtirme profili:

   ```powershell
   docker compose --profile dev up --build
   ```

   Windows'ta polling, Linux Ã¼retimde webhook iÃ§in `TELEGRAM_MODE` ve ilgili
   webhook deÄŸiÅŸkenlerini yapÄ±landÄ±rÄ±n.

## Test ve kalite kapÄ±larÄ±

```text
python -m pytest -q
python scripts/architecture_check.py --strict
python scripts/feature_catalog_audit.py
```

CanlÄ± token, cookie, `.env`, medya dosyasÄ± ve kullanÄ±cÄ± verisi bu repository'de
tutulmaz. Provider lisanslarÄ± ve veri kaynaklarÄ± [LICENSES.md](LICENSES.md) ile
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) iÃ§inde izlenir.

## Durum

Proje aktif geliÅŸtirme aÅŸamasÄ±ndadÄ±r. Ãœretim daÄŸÄ±tÄ±mÄ± iÃ§in
[docs/OPERATIONS.md](docs/OPERATIONS.md) ve [DEPLOYMENT.md](DEPLOYMENT.md)
okunmalÄ±dÄ±r.
