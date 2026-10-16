# BaÄŸÄ±mlÄ±lÄ±k ve veri lisanslarÄ±

Bu dosya Ã¼Ã§Ã¼ncÃ¼ taraf kod ve veri iÃ§in insan okunabilir envanterdir. Makine
okunabilir karÅŸÄ±lÄ±ÄŸÄ± `licenses.json` dosyasÄ±ndadÄ±r.

- `turkiye-data` administrative snapshot: MIT; kaynak ve upstream bilgisi
  `src/data/turkish_cities.json` metadata alanÄ±nda tutulur.
- BTK telefon alan kodlarÄ±: resmi kamu kaynaÄŸÄ±; URL `docs/DATA_SOURCES.md`
  iÃ§inde tutulur.
- KekikSpatula: GPLv3+; davranÄ±ÅŸ referansÄ±dÄ±r, kaynak kodu kopyalanmamÄ±ÅŸtÄ±r.
- TRSehirler: GPLv3+; davranÄ±ÅŸ referansÄ±dÄ±r, kaynak kodu kopyalanmamÄ±ÅŸtÄ±r.
- Cloudflare Workers AI: saÄŸlayÄ±cÄ± hizmet ÅŸartlarÄ± ve hesap yetkilendirmesi;
  gÃ¶rsel Ã¼retim provider'Ä± Ã¼retim sign-off bekler.
- Tavily Search API: API hizmet ÅŸartlarÄ± ve attribution gereksinimleri;
  kaynak governance kaydÄ± `licenses.json` iÃ§inde tutulur.

The machine-readable package inventory in `licenses.json` covers every direct
constraint entry. It is checked by `scripts/architecture_check.py --strict`;
new dependencies must add their SPDX or project license before merge.
