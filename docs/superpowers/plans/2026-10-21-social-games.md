# Tabu, Casus ve Vampir Köylü Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Üç grup oyununu kalıcı durum, gizli teslim, adil kural ve anlaşılır Telegram yüzeyiyle yerelde geliştirip sunucuya yayımlamak.

**Architecture:** Var olan domain/service/handler ayrımı korunur. Casus ve Vampir Köylü birbirinden bağımsız durum motorları kullanır; ortak repository CAS ve idempotent leaderboard sözleşmelerini paylaşır. AI oyun hakemi olmaz.

**Tech Stack:** Python, python-telegram-bot, MongoDB CAS, mevcut Docker Compose ve release gate.

**Spec:** docs/GAME_PLATFORM_ROADMAP_2026-10-21.md — Tabu/Casus/Vampir Köylü sözleşmeleri. Kullanıcı bu dalgayı yerel → sunucu → Telegram sırasıyla uygulamamızı istedi; GitHub yayını yasak. Yeni tasarım onayı için işi tekrar durdurmama yönündeki önceki açık yetkilendirme korunur.

## Global Constraints

- GitHub push, PR veya depo güncellemesi yok; yalnız yerel commit ve yetkilendirilmiş sunucu.
- Gerçek Telegram test grubu -1004323938202; kullanıcı şu an gönüllü oyuncu olmadığını bildirdi. Canlı kabul lobi/katılma/özel sohbet hazırlığı/eksik oyuncu engeli ile sınırlı.
- `security/` takip edilmeyen eşzamanlı çalışma; değiştirilmez ve pakete alınmaz.
- Lobi, aktör, konu, eski maç/faz/mesaj, son saniye ve eşzamanlı eylem sınırları kalıcı denetlenir.
- Sırlar gruba/loglara çıkmaz; DM daveti kullanıcıya bağlı ve sınırlı sürelidir.
- Oyun kuralı/puanlama AI gerektirmez. DeepSeek web2api bu oyun dalgasının bağımlılığı değildir.
- Grup panelleri düzenlenir; rutin başarılı eylem için ayrı mesaj yok.
- Yedek → aday image testi → etkinleştirme → sağlık → gerçek Telegram. Şema uyumu ve geri dönüş korunur.

## Review Focus

- DM engeli veya kısmi teslimde süre ve rol bilgisi kaybolmamalı; lobiye katılan gerçekten onaylamadan başlamamalı.
- Eski faz/maç düğmesi, başka aktörün DM paneli veya başka forum konusu sonucu değiştirmemeli.
- Son saniyede çift oy, CAS yarışı, puan kaydında geçici hata ikinci ödül üretmemeli.
- Oyuncu çıkışı, rol sahibinin ölmesi ve eşitlik maçı kilitlememeli; iptal ödül vermemeli.
- Özel seçim metni genel AI konuşma belleğine veya açık grup paneline sızmamalı.

### Task 1: Tabu ürün denetimi ve onarımı

**Owner:** tabu_product — src/games/tabu*.py, src/services/tabu*.py, src/handlers/tabu_handler.py, src/tests/test_tabu*.py.
- [x] Mevcut davranışı oku; somut hataya başarısız test ekle.
- [x] Takım, kart, yasak ifade, teslim, süre ve itiraz geçişlerini düzelt; kaliteli özgün Türkçe havuzu genişlet.
- [x] Sızıntı, yanlış pozitif, CAS, BSON, restart, pending reward ve mesaj tekrarlarını test et.
- [x] Hedefli test ve bağımsız kök incelemesi.

### Task 2: Casus

**Owner:** casus_product — src/games/spy.py, src/services/spy_service.py, src/services/spy_ui.py, src/handlers/spy_handler.py, src/tests/test_spy*.py.
- [x] En az4/en çok16 oyuncu, tek casus, özgün30+mekân, rol teslim onayı ve soru sırası.
- [x] Süre sonu gizli oy; katı çoğunluk/eşitlik rövanşı; casusun tek gizli mekân tahmini; çıkış ve iptal.
- [x] CAS, kalıcı teslim/ödül ve actor/topic/replay/timeout testleri.

### Task 3: Vampir Köylü

**Owner:** vampir_product — src/games/vampire.py, src/services/vampire_service.py, src/services/vampire_ui.py, src/handlers/vampire_handler.py, src/tests/test_vampire*.py.
- [x] 7–20 oyuncu; dengeli vampir/köylü/kâhin/şifacı; rol teslim onayı; özel gece eylemleri.
- [x] Koruma→hedef→kehanet→ölüm→zafer; gündüz/tartışma/oy/rövanş; ölü yetki sınırı ve süre sonu pas.
- [x] Tam maç, eşitlik, çıkış, DM engeli, restart, CAS ve ödül tekrar testleri.

### Task 4: Ortak Telegram ve kayıt entegrasyonu

**Owner:** root — main.py, src/orchestrator.py, src/handlers/{callback_handler,command_handler,message_handler,game_runtime}.py, src/database.py, src/services/menu_service.py, src/features/catalog.py, docs ve entegrasyon testleri.
- [x] /casus /spy, /vampir /vampirkoylu komutları; sp:/vp: callback ve sp_/vp_ DM başlangıçları.
- [x] Menü/yardım/feature kaydı; restart taraması due-query; genel bitir kalıcı servis iptali.
- [x] Gizli mesajları genel AI işleminden önce route et; sadece ilgili girdi tüketilsin.
- [x] Yeni oyun duequery ve runtime dispatch, menü erişimi, izinli iptal ve deeplink önceliğini test et.
- [x] Tam gate25/25, architecture, feature katalog, faz kayıtları; temiz yerel kopyadan paket.

### Task 5: Sunucu ve gerçek Telegram

**Owner:** root.
- [x] Mevcut games7 sağlık/commit doğrulaması; yedek/rollback ile aday yayımla.
- [x] Aday image aynı profilde offline test; şema korunumu ve kaynak manifesti.
- [ ] Lobi/katıl/kişisel hazırlık/eksik oyuncu/iptal gerçek oturumla: menü görüldü; Katıl ve özel hazırlık düğmesi tarayıcı bağlantısı nedeniyle doğrulanamadı. Gerçek API üzerinden üç tek kişilik lobi teslimi/başlama engeli/iptali doğrulandı ve tüm test lobileri kapatıldı.
- [x] Sonuçları test türüne göre kanıt dosyasında ayır; çok oyunculu insan maçını NOT_RUN tut.

## Execution rulings

- Tabu için dönen oyuncu hakemi yerine mevcut oyun dışı yönetici kararı korunur; iki takım üyesinin çıkar çatışması önlenir. İtiraz süreyle sınırlıdır, dış yönetici yokken maç kilitlenmez. Yol haritası bu açık ürün kararıyla uyumlu güncellendi.
- Casusun mekân tahmini için sohbet/konu kodu yazdıran komut temel kullanım olmayacak; özel buton ve son onay kullanılacak.
- İlk10 ortak sınır testi uygulamadan önce başarısız, entegrasyondan sonra geçti: callback ack önceliği, deep link, runtime, menü ve medya caption/edit yönlendirmesi.

## Final verification

2026.10.21-games-8, kaynak30cde7a, şema20 ve bot/worker sağlıklı. Tam kapı25/25; host2471/19 atlanan, Docker ve sunucu adayı2484/6 atlanan. Gerçek yalıtılmış Mongo13senaryo geçti. İnsanlı tam maç NOT_RUN; gerçek Katıl/özel hazırlık onayı tarayıcı bağlantısı nedeniyle BLOCKED. Ayrıntı: deploy/evidence/social-games-live-20261021.json. GitHub güncellenmedi.
