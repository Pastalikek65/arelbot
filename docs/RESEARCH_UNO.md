# UNO upstream incelemesi

Bu not, `nmime/uno` deposunun resmi README'si, package metadata'sı ve
kaynak kodu üzerinden hazırlandı. Amaç upstream kodunu kopyalamak değil,
Arelbot'un oyun motoru ve gelecekteki Mini App sözleşmesi için davranış
gereksinimlerini doğrulamaktır.

## Kaynak bulguları

- Upstream proje Telegram Mini App olarak tanımlanıyor ve çok oyunculu UNO,
  sayaçlar, istatistik/liderlik ve yönetim yüzeyi sunuyor: [README](https://raw.githubusercontent.com/nmime/uno/master/README.md).
- UNO akışı; üst kartla renk, sayı veya sembol eşleşmesi, özel aksiyon kartları
  ve son iki kartta UNO çağrısı etrafında kuruluyor: [README](https://raw.githubusercontent.com/nmime/uno/master/README.md).
- Upstream teknik yığını TypeScript/React/Next.js frontend, Colyseus oyun
  odası, uWebSockets ağ katmanı, grammY bot ve MongoDB olarak listeleniyor:
  [repository README](https://github.com/nmime/uno#technology-stack).
- `package.json` MIT lisansı ve Yarn 3 çalışma alanlarını beyan ediyor:
  [package.json](https://raw.githubusercontent.com/nmime/uno/master/package.json).
- Kaynakta deste dört renk, 0–9, pas, ters, +2, joker ve +4 kartlarını
  içeriyor; oyun başlangıcında oyunculara yedi kart dağıtılıyor. Arelbot bu
  kuralları bağımsız Python domain motorunda yeniden uyguluyor.

## Arelbot uyarlaması

`src/games/uno.py` saf kurallar katmanıdır. `UnoService` MongoDB game state
portunu, chat kilidini ve idempotent action kimliklerini yönetir.

- 2–8 oyunculu grup lobisi
- 108 kartlık deste
- renk/sayı/aksiyon eşleşmesi
- pas, yön değiştir, +2, joker ve +4
- +4 için elde aktif renkten kart varsa kural reddi
- renk seçimi, kart çekme ve çekilen karttan sonra pas
- UNO çağrısı ve tur puanı
- yanlış sıra, başkasının kartı, eski callback ve duplicate action reddi
- restart sonrası Mongo state toparlanması

`src/services/miniapp_game_registry.py` aynı oyunu ileride Mini App'e taşımak
üzere `state_key`, `action_namespace`, oyuncu sınırları ve protocol version
alanlarını kaydeder. HTTPS endpoint ve Telegram `initData` doğrulaması
kurulmadan Mini App flag'i kapalı tutulur; Telegram inline paneli tam çalışan
fallback'tir.

## Lisans ve kapsam kararı

Upstream package MIT beyan ediyor. Buna rağmen upstream kaynak kodu,
Colyseus/React frontend'i, wallet ödeme akışı veya görsel asset'ler Arelbot'a
alınmadı. Arelbot'un Python motoru ve Türkçe metinleri bağımsızdır; davranış
referansı `licenses.json` ve `docs/DATA_SOURCES.md` içinde izlenir.
