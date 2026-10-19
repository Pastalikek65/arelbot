# Bağımlılık ve veri lisansları

Bu dosya üçüncü taraf kod ve veri için insan okunabilir envanterdir. Makine
okunabilir karşılığı `licenses.json` dosyasındadır.

- `turkiye-data` administrative snapshot: MIT; kaynak ve upstream bilgisi
  `src/data/turkish_cities.json` metadata alanında tutulur.
- BTK telefon alan kodları: resmi kamu kaynağı; URL `docs/DATA_SOURCES.md`
  içinde tutulur.
- python-chess: GPL-3.0-or-later; legal chess rules engine, pinned at 1.11.2.
- akipy: MIT metadata on PyPI; Arel uses the published client API. The
  Akinator-Bot reference repository does not declare a license in its checked
  out metadata, so its source and assets are not copied.
- Akinator-Bot: behavior and interaction reference only; upstream license
  metadata is absent and requires separate review before source reuse.
- FlareSolverr: GPL-3.0; optional private Docker service used only to solve
  the Akinator endpoint challenge; its image is never published to the host.
- KekikSpatula: GPLv3+; davranış referansıdır, kaynak kodu kopyalanmamıştır.
- TRSehirler: GPLv3+; davranış referansıdır, kaynak kodu kopyalanmamıştır.
- Cloudflare Workers AI: sağlayıcı hizmet şartları ve hesap yetkilendirmesi;
  görsel üretim provider'ı üretim sign-off bekler.
- Tavily Search API: API hizmet şartları ve attribution gereksinimleri;
  kaynak governance kaydı `licenses.json` içinde tutulur.

The machine-readable package inventory in `licenses.json` covers every direct
constraint entry. It is checked by `scripts/architecture_check.py --strict`;
new dependencies must add their SPDX or project license before merge.


- `guess-the-age-bot` (davranış referansı): MIT beyanı (`https://github.com/mikhailsdv/guess-the-age-bot/blob/master/package.json`). Upstream fotoğraf varlıkları dağıtıma dahil edilmez; Arelbot portre kartları özgün üretilmiştir.
- `nmime/uno` (davranış ve Mini App mimari referansı): MIT beyanı (`https://github.com/nmime/uno/blob/main/package.json`). Upstream kaynak kodu, frontend ve asset'ler dağıtıma alınmaz; Arelbot UNO motoru bağımsızdır.
