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


- `guess-the-age-bot` (davranış referansı): MIT beyanı (`https://github.com/mikhailsdv/guess-the-age-bot/blob/master/package.json`). Upstream fotoğraf varlıkları dağıtıma dahil edilmez; güncel fotoğraf havuzu Wikimedia Commons dosya bazlı lisans/atıf kayıtlarıyla tutulur.
- `nmime/uno` (davranış ve Mini App mimari referansı): MIT beyanı (`https://github.com/nmime/uno/blob/main/package.json`). Upstream kaynak kodu, frontend ve asset'ler dağıtıma alınmaz; Güncel motor için aşağıdaki maildian AGPL entegrasyonu esas alınır.

## Kaynak UNO entegrasyonu

`src/vendor/uno_maildian` ve bu koddan türetilen UNO entegrasyonu GNU AGPL-3.0-or-later koşullarıyla sunulur; özgün telif başlıkları korunur. Lisans metni `COPYING.AGPL-3.0` içindedir. Orijinal Arel dosyalarının MIT bildirimi korunur. Bu birleşik dağıtımın lisans envanteri tümüyle MIT olarak yorumlanmamalıdır. Çalışan sürümün tam Git kaynak arşivi, UNO panelindeki Kaynak kodu düğmesi veya botun özel sohbetinde `/uno kaynak` ile sağlanır; ortam ve hesap dosyaları Git arşivine girmez.

Kart PNG dosyaları maildian/uno-telegram-bot commit `01640d1df9e3b06f81727aff38ea02990f3c8d83` içinden aynen alınmıştır. Upstream lisansı/atfı ve PNG hashleri `assets/uno_maildian` içinde tutulur; belirtilen özgün SVG kaynağı Dmitry Fomin, Wikimedia Commons CC0 1.0.
# Standard playing-card artwork

`assets/playing_cards/` contains Kenney's Board Game Pack artwork under CC0-1.0. Original PNGs, vector sheet, license, source URL and SHA-256 records are included. Sticker faces are raster crops of the official vector sheet; muted variants and the Blackjack table are local compositions. No AI-generated artwork is used.

## GreyWolf Werewolf entegrasyonu

`src/vendor/greywolf` sabitlenmiş `bac172a29f8b394ebbb4822c6f244d769fac21ac` kaynağını ve GPL-3.0 metnini korur. `src/games/werewolf`, `src/services/werewolf_ui.py` ve türetilmiş Türkçe rol verisi GPL-3.0-only uyarlamadır. Özgün C# uygulaması Windows/SQL Server gerektirir; Arel oyun kurallarını mevcut Python/MongoDB taşımaya uyarlar. Snapshot dosya hashleri `UPSTREAM.json` içindedir. Birleşik dağıtım tümüyle MIT değildir; önceki AGPL UNO bildirimi ve tam çalışan kaynak sunumu korunur.
