# Ekonomi ve oyun güvenilirliği · 22 Ekim 2026

Bu dalga mevcut 45 oyunu ve 91 özellik kaydını korur. Yeni ekonomi komutları
aynı ekonomi özelliğinin yüzeyleridir. Telegram oyuncu ve arayüz testleri
kullanıcının talebiyle ertelenmiştir. Otomatik testler, gerçek oyunculu kabul
olarak sunulmaz. GitHub güncellemesi yapılmaz.

## Oyuncu deneyimi

- `/ekonomi` ve `/bakiye`: sohbete özel bakiye, son hareketler, günlük oyun
  kazancı ve seçili profil süsleri.
- `/gunluk`: doğrudan günlük hakkı alır. İstanbul takviminde ardışık günler
  25, 30, 35, 40, 45 ve 50 jeton; altıncı günden sonra 50. Gün atlanırsa
  seri yeniden başlar. Eski UTC tarihleri geçişte çift hak doğurmaz.
- Oyun ödülleri kişi ve sohbet başına günlük toplam 150 jetonla sınırlıdır.
  Günlük hak, bahis ödemesi ve iadesi bu bütçeden düşmez. Sınırdaki sıfır
  ödemeler de işlenmiş sayılır; bekleyen ödül sonsuz tekrar döngüsüne girmez.
- `/magaza`: altı unvan ve dört rozet. Fiyatlar 75–1500 jetondur; profil
  süsleri oyunda güç, ek hamle veya puan avantajı sağlamaz.
- Alışveriş önce fiyat ve etki gösterir; onay beş dakika geçerli, imzalı ve
  kullanıcı/sohbet/ürün/fiyata bağlıdır. Çift tıklama ikinci kez ücret kesmez.
- `/envanter`: sahip olunan ürünleri ve seçili unvan/rozeti yönetir. Sahip
  olunmayan eşya kuşanılamaz. Profilde seçilen süsler görünür.
- Paneller mevcut mesajı düzenler. Düğmeler oyuncuya, sohbete, konuya,
  oturuma ve mesaj kimliğine bağlıdır; eski paneller güvenli biçimde reddedilir.

## Muhasebe ve veri koruması

Tek MongoDB hesap güncellemesi bakiyeyi, sahipliği ve işlem makbuzunu atomik
olarak kaydeder; replica set zorunlu değildir. Görünen son 500 hareketin
budanması tekrar ödeme engelini kaldırmaz. Makbuzlar tam işlem anahtarı ve
payload özetiyle doğrulanır. Gündelik bütçe gibi değişebilir politika alanları
kimliği bozmaz; gece yarısından sonraki tekrar çift ödeme üretmez.

Makbuzların hesap başına 12 MiB güvenlik bütçesi vardır. Bütçe dolarsa yeni
finans işlemi açık hatayla durur; eski makbuzlar sessizce silinmez. Bu sınırsız
muhasebe arşivi değildir. Önceki sürümlerde zaten silinmiş eski makbuzlar
migrasyonla yeniden üretilemez. Şema 21 mevcut bakiyeleri ve envanteri korur;
gerilemede finans kanıtlarını silmez.

## Oyun düzeltmeleri

Sıfır puanlı bitişten yapay kazanan ödülü üretilmez; Casus'ta erken ayrılma
ödül doğurmaz. Kart ve tahmin oyunları gerçek bitiş saatini ve bekleyen panel
ya da ödeme durumunu açık gösterir.

Blackjack bahis, ikiye katlama, sonuç ve iade işlemleri önce kalıcı bekleyen
kayıt oluşturur. Çökme sonrası aynı makbuzla tamamlanır. İkiye katlamada aynı
kart yeniden çekilmez. Geçerli en büyük 10.000 bahis için 40.000 sonuç ve
20.000 iade desteklenir; normal ödül sınırları büyütülmez. Ekonomisiz açılmış
el sonradan para üretemez; eski el için tahsilat kanıtı gerekir. Bekleyen
finans yükümlülükleri 24 saatlik oyun TTL'siyle silinmez.

Rulet'te sonuç, liderlik ve jeton işlemleri kalıcı olarak yeniden denenir.
Düğmeler maç ve güncel panel kimliğiyle eşleştirilir; önceki masanın düğmesi
sonraki maçı oynatamaz. Sonuç mesajı teslimi onaylanana kadar saklanır.

## Doğrulama durumu

Bağımsız inceleme düzeltmelerden sonra tekrarlandı. Sürüm denetimi 25/25;
yerel paket 2.917 PASS / 19 SKIP; Docker paketi 2.930 PASS / 6 SKIP.
Gerçek izole MongoDB kabulünde çift satın alma, günlük hak, ortak ödül bütçesi,
505 hareket sonrası tekrar engeli, migrasyonda bakiye/envanter koruması,
kira devralma ve finansal TTL koruması doğrulandı. Zamanlayıcı sorgusunda
443 senaryodan beklenen 255 kayıt seçildi; gizli alan projeksiyonları korundu.
Ayrıntı: `deploy/evidence/economy-v2-review-20261022.json`.

`2026.10.22-games-15` sunucuda etkinleştirildi; şema 21. Aday imajın sunucu
testi de 2.930 PASS / 6 SKIP. Dört ana servis sağlıklı, yeniden başlama sayıları
sıfır ve son kontrolde bot/worker hata izi sıfır. Telegram salt okunur kontrolde
45 oyun, 38 animasyonlu emoji ve ekonomi/mağaza/envanter menü komutları doğrulandı.
Mongo, Redis ve yapılandırma yedekleri alındı; Mongo arşivinin gzip bütünlüğü
doğrulandı. Geri yükleme tatbikatı yapılmadı. Canlı oyuncu/UI kabulü ertelenmiş
durumdadır; bu sağlık kontrolü maçların insanlarla doğrulandığı anlamına gelmez.
Ayrıntı: `deploy/evidence/economy-v2-deployment-20261022.json`.
