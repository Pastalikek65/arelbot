# Bağımlılık ve veri lisansları

Bu dosya üçüncü taraf kod ve veri için insan okunabilir envanterdir. Makine
okunabilir karşılığı `licenses.json` dosyasındadır.

- `turkiye-data` administrative snapshot: MIT; kaynak ve upstream bilgisi
  `src/data/turkish_cities.json` metadata alanında tutulur.
- BTK telefon alan kodları: resmi kamu kaynağı; URL `docs/DATA_SOURCES.md`
  içinde tutulur.
- KekikSpatula: GPLv3+; davranış referansıdır, kaynak kodu kopyalanmamıştır.
- TRSehirler: GPLv3+; davranış referansıdır, kaynak kodu kopyalanmamıştır.
- Cloudflare Workers AI: sağlayıcı hizmet şartları ve hesap yetkilendirmesi;
  görsel üretim provider'ı üretim sign-off bekler.
- Tavily Search API: API hizmet şartları ve attribution gereksinimleri;
  kaynak governance kaydı `licenses.json` içinde tutulur.

The machine-readable package inventory in `licenses.json` covers every direct
constraint entry. It is checked by `scripts/architecture_check.py --strict`;
new dependencies must add their SPDX or project license before merge.
