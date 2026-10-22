# Sekiz kalıcı oyun: ürün ve doğrulama kaydı

Bu dalga 40 oyun hedefine sekiz ayrı mekanikle devam eder. Önceki18 oynanabilir giriş ve Wordle tanımının yüzeye açılması/yedi yeni tanım ile katalog26 olur; 72 feature oyun sayısı değildir. 40 oyun hedefi tamamlanmadı. GitHub yayımlanmaz; kullanıcı canlı Telegram testlerini şimdilik erteledi.

## Oynanış

- Türkçe Wordle: 329 gözden geçirilmiş yerel hedef, geçerli Türkçe sözlük kelimeleri, beş harf/altı deneme, yinelenen harf adetlerini doğru değerlendirme ve tek oyuncu yetkisi.
- Karşı Kelime: 81 özgün kart; kabul edilen zıt anlam alternatifleri, katılımcılar arasında ilk doğru cevap, beş tur ve teslim sonrası sonuç aralığı.
- Anagram: 538 yerel hedef; gerçek karıştırma, geçerli eş harfli alternatifleri kabul, sınırlı ipucunda puan düşüşü, beş tur.
- Hafıza Eşleştirme: 1–4 kişi; 4×4/sekiz çift, 8sn teslim sonrası önizleme, sırayla iki kart, yanlışta en az3sn görünüm, doğru çiftte puan+ek hamle; 64sıra üst sınırı.
- Mayın Tarlası: tek kişi; 6×6 ve6mayın, ilk hücre ve çevresi güvenli, boş alan açılımı, bayrak modu, yalnız bütün güvenli hücreler açılınca zafer; 600sn.
- Sudoku: 4×4 ve9×9, tek çözüm denetimi, yapıyı koruyan rastgele simetriler; satır/hücre/rakam seçimi, sabit rakam korunumu, geri al/sil ve üç sınırlı ipucu; 1800sn.
- Sayı Tahmini: 1–30 kişi, 1–1000 aralık, her oyuncuya10hak, daralan aralık ve yakınlık, sıralı oyun, timeout hak tüketimi, beş tur ve dönen ilk oyuncu. Solo binary search ile çözülebilir.
- Refleks: 2–30 kişi, rastgele bekleme, hedef sembol+çeldirici, erken/yanlış basış cezası, tur başına tek hak ve beş tur. Telegram teslim/ağ gecikmesi sonucu etkiler; hassas kronometre iddiası yok.

## Kalıcılık ve güvenilirlik

Domain/service/Telegram ayrımı; Mongo CAS, konu/oyuncu/maç/aşama/mesaj bağlama; kalıcı panel kirası ve ACK, geç teslim onarım işareti, teslimden sonra başlayan süreler ve300sn hazırlık üst sınırı. İptal ödül üretmez; biten maçın puanı olay kimliğiyle yeniden denenir, bekleyen ödül yeni maçla ezilmez. Tek panel düzenlenir. Telegram gönderimi ile kalıcı ACK arasında kesintide dış mesajın tam bir kez teslimi garanti edilemez.

Birden fazla metin oyunu aynı kapsamda açıksa normal yanıt kendiliğinden ilk oyuna puan yazmaz; güncel panele reply gerekir. Açık Wordle panel yanıtı Tabu/Casus ipucu tarafından yutulmaz. /hafiza kişisel AI hafıza izni olarak kalır; kart oyunu /eslestirme kullanır. Yeni oyunlar grup içindir.

## Kanıtlanan ve giderilen bulgular

Bağımsız regresyonlar: açılış callback onayının eksikliği; geç Grid ACK sonrasındaki eski panel onarımının eksikliği; Wordle durum/yenile yolunun lease almaması; oynanabilir sayı/refleks panelinden önce başlayan süre; 30uzun emoji adının Telegram sınırını aşması; normal Wordle cevabının Tabu tarafından tüketilmesi. Kök ürün incelemesi: tek haklı sayının anlamsız şans döngüsü, Sudoku'da hücre silme/bağlı sayı eksikliği, sonuç aşamasının süresiz kalması ve Windows saat kimliği çakışması. Hepsi deterministik testle kapsandı. İlave tam maç ve terminal süre regresyonları da geçti. Nihai tam kapı25/25: host2580/19atlanan, Docker ve gerçek sunucu2593/6atlanan. Gerçek yalıtılmış Mongo85durum/37due PASS. games10 kaynak4a56272 yayında; şema20 ve servisler sağlıklı. Kanıt: deploy/evidence/puzzle-wave-deployment-20261021.json. Bu güncellemenin yedeği arşiv bütünlüğüyle doğrulandı, bağımsız restore edilmedi.

İnsanlı Telegram maçları ve düğme testleri NOT_RUN — kullanıcı açıkça erteledi. Otomatik testler insanlı kabul olarak sunulmaz. Yeni oyunlar AI gerektirmez, yeni dış kaynak veya paket eklenmedi.

Son kök incelemesindeki beş RED→GREEN regresyon biten/iptal edilen oyunların zamanlayıcı alanlarını temizledi ve terminal kayıtları geçmiş süreleri yüzünden tekrar taramadan dışladı. games9 adayı etkinleştirilmeden games10 ile değiştirildi. Docker Hub DNS hatasında bağımlılık dosyaları byte eşitliğiyle doğrulanıp çalışan games8 imajları üzerine ağsız kaynak overlay yapıldı; bu imaj sunucuda aynı tam testten geçti.
