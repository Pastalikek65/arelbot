# Sekiz kalıcı oyun — uygulama planı

Kapsam: Onaylı 40 oyun yol haritasının sıradaki bağımsız mekanikleri. Kullanıcı 21 Ekim’de devam et, yerelde geliştir ve sunucuya at dedi; GitHub yasak, canlı Telegram testleri şimdilik atlanacak. Mevcut feature dalı kullanılır; security/ eşzamanlı çalışma korunur.

## Tasarım ve kabul

- Wordle: beş harf, altı deneme, sözlük doğrulaması, tekrarlı harf için adet sınırlı geri bildirim, tek oyuncu.
- Karşı Kelime: en az60 özgün çift, kabul edilen alternatifler, beş tur, ilk geçerli doğru yanıt, sonuç aralığı.
- Anagram: gerçek yerel Türkçe kelime, bozuk dizilim hedefle aynı olmaz, ipucu puan düşürür, beş tur.
- Hafıza: 1–4 oyuncu, süreli önizleme, sıra, eşleşmede ek hamle, yanlış çiftte görünür bekleme, zaman aşımı.
- Mayın: 6×6, ilk hücre ve çevresi güvenli, flood fill, bayrak modu, bütün güvenli hücrelerde zafer.
- Sudoku: 4×4 ve9×9, tek çözüm kanıtı, sabit hücre korunumu, geri al, sınırlı ipucu ve süre.
- Sayısal Tahmin: 1–1000, daralan aralık, sıralı eşit fırsat, beş tur, dönen ilk oyuncu, sınırlı hamle.
- Refleks: 2–30 oyuncu, bekleme→GO, erken basış cezası, hedef/çeldirici, beş tur; ağ gecikmesi açık sınırlama.

Ortak: komut/menü/yardım erişimi; topic/actor/canonical panel/maç/faz bağlama; CAS; yeniden başlatma; teslim sonrası saat; teslimde300sn üst sınır; lobbyTTL; idempotent sonuç puanı; iptal ödülsüz; tek panel; AI veya harici görüntü bağımlılığı yok.

## Görevler

- [x] 1 Kelime motoru, servis, Telegram adaptörü ve adversarial testler (casus_product).
- [x] 2 Hafıza/Mayın motoru, servis, adaptör ve testler (vampir_product).
- [x] 3 Sudoku/Sayı/Refleks motoru, servis, adaptör ve testler (tabu_product).
- [x] 4 Ortak komut/callback/metin/runtime/iptal/menü/manifest entegrasyonu ve bağımsız sınır testleri (root).
- [x] 5 Bağımsız ürün incelemesi; önemli bulguları başarısız regresyonla düzelt; tam yayın kapısı.
- [x] 6 Temiz kaynak paketi; sunucuda yedek, aday testi, şema, aktivasyon ve sağlık. Canlı Telegram UI NOT_RUN — kullanıcı erteledi.

## Review Focus

Yanlış oyuncu, başka konu, eski panel, tekrar hamle, eşzamanlı iki servis, bekleyen ödül varken yeni maç, başarısız panel teslimi, çözüm/tahta sızıntısı, sayı/kelime oyunları arasında belirsiz normal mesaj ve bot yeniden başlatması. Tekli oyunlarda başkasının panelini sahiplenme yok. Yerel hafıza ayarı /hafiza korunur, oyun komutu /eslestirme olur. Sayım yalnız gerçek komut+başlangıç+hamle+bitiş+kalıcılık+testten geçen ayrı mekanikleri kapsar.

## Ledger

Başlangıç: d0c88ae, önceki games8 yayında. Sekiz oyun bu dalganın kapsamıdır; 40 oyun tamamlandı iddiası yok. Root ortak entegrasyonu yürütür; üç modülün sahipleri ayrıdır. Yeni içerik özgün; sözlük mevcut lisans bildirimini korur.

Odaklı86 test PASS. Bağımsız kök/yönlendirme/grid/logic incelemelerinin önemli bulguları başarısız regresyonlardan sonra düzeltildi. Windows clock collision sleep ile gizlenmedi; faz/tur/deadline fingerprint ve CAS ile giderildi. Ürün kaydı: docs/PUZZLE_WAVE_PRODUCT_REVIEW_2026-10-21.md. İlk kök geniş koşumu2547PASS/19atlanan, geç Grid ACK testi kaynak düzeltmesinden önce okunduğu için FAIL; diğer FAIL ilgisiz takip edilmeyen security/ LF dosyası. Nihai kapı temiz yerel kaynak kopyasında koşulur.

İlk temiz tam kapı25/25 PASS: host2571/19 atlanan, Docker2584/6 atlanan. Gerçek yalıtılmış Mongo69durum/37due PASS. Son kapsam incelemesiyle üç ilave tam maç testi eklendi: iki oyunculu Hafıza8çift, Mayın güvenli30hücre ve Refleks5tur; ödül kesintisi/yeniden başlatma tekilliği PASS. Üretim kodu değişmedi; nihai test kaydı bu ilave kapsama göre yenilenecek.

Son kök zamanlayıcı incelemesi5RED regresyonla biten Sudoku deadline ve iptal hazırlık zamanının temizlenmediğini gösterdi; terminal alanları temizlendi ve Mongo taraması terminal kayıtların geçmiş süre alanlarını dışlıyor. games9 adayı bu nedenle etkinleştirilmedi; games10 adayı aynı bağımlılıklar ve yeniden tam kapıyla hazırlanır. Sunucuda Docker Hub DNS hatası nedeniyle doğrulanmış games8 imajları üzerine çevrimdışı kaynak overlay yolu kullanılır; Dockerfile/requirements/constraints/entrypoint/.dockerignore eşitliği zorunludur ve aday gerçek sunucu imajında tam pytest görür.

Sonuç: games10 kaynak4a56272 yayında; şema20, bot/worker sağlıklı. Nihai kapı25/25, host2580/19atlanan, Docker ve gerçek sunucu2593/6atlanan. Gerçek Mongo85durum/37due. Yedek arşiv bütünlüğü geçti; bu yedeğin bağımsız restore denemesi yapılmadı. İnsanlı Telegram UI NOT_RUN — kullanıcının ertelemesi. GitHub güncellenmedi.
