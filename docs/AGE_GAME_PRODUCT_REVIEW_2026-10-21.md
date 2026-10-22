# Yaş Tahmini oynanışı ve oyun menüsü

## İnceleme sınırı

Referans: [@turkishgamesbot](https://t.me/turkishgamesbot), 21 Ekim 2026 tarihli gerçek Telegram Web oturumu. Ana ekranı, dört sayfalık oyun rehberi ve Yaş Tahmini, Casus, Vampir Köyü, Tabu rehberleri açıldı. Grup maçları oynanmadı; rehberdeki kuralların uygulamada eksiksiz çalıştığı iddia edilmez. Tanıtımdaki oyun sayısı bağımsız maç doğrulaması değildir. Görsel, marka, kod, kart içeriği ve rehber metinleri alınmadı.

Referansın yararlı yanı, oyunları kısa rehberlerle erişilebilir parçalara ayırması ve tur başına tek tahmin ile sonuç okuma aralığını açıklaması. Arel için aynı sayfalama yerine oyun türüne göre beş kategori seçildi. Rol oyunlarındaki gizli bilgi/özel sohbet hazırlığı gelecekteki Casus ve Vampir Köylü çalışmasına girdi sağlar; bu sürüm o oyunları eklemez.

## Doğrulanan sorunlar

- Metinle tahmin, puanı tur kapanmadan grupta açıklıyordu. Yakınlık puanından doğru yaşın hesaplanması mümkündü.
- Konu, fotoğraf mesajı ve beklenen tur sınırı olmadan gelen sayı, yanlış turun tahmin hakkını tüketebiliyordu.
- İki bağımsız servis aynı kaydı okuyup koşulsuz yazınca tahminlerden biri kaybolabiliyordu.
- Bekleyen puanlar yeni maçın kaydıyla silinebiliyordu. Kapalı tur listesini 20 kişiyle kesmek, 21. ve sonraki oyuncuların kalıcı ödüllerini de düşürüyordu.
- İlk fotoğraf henüz teslim edilmeden süre başlayabiliyordu. Tekrar `/yas`, aynı tur fotoğrafını çoğaltıyordu.
- Tur sonucu ve yeni uzun fotoğraf art arda gönderiliyor; sonucu okumak için ayrı süre bırakılmıyordu.
- Kaynak dosyası bağlantısı öznenin adını açıklıyordu. Bir kartta fotoğrafçı kredisi de özneyle aynıydı.

## Uygulanan tasarım

Mevcut Telegram düğmeleri ve Arel dili korunur. Gruptaki oyuncu fotoğraf için yalnız sayı yazar. Düğmeli aralık/tam yaş seçimi isteğe bağlıdır. Her oyuncu her turda tek tahmin yapar; skor ve hedef yaş süre bitene kadar açıklanmaz. Varsayılan 30 saniyelik süre, fotoğrafın başarılı teslim onayıyla başlar. Beş tur ve 100 − 5 × yaş farkı puan kuralı korunur.

Tur sonucu en az sekiz saniye görünür. Süpürme zamanlayıcısının sıklığı ve Telegram gecikmesi sonraki ekranın gelişini uzatabilir. Bekleme zamanı kayıtta tutulur; `/yas` ve yeniden başlatma bu aralığı atlayamaz. Eski fotoğrafın düğmeleri kapanır; cevap, çekim tarihi ve fotoğrafçı sonuçta açıklanır; kaynak bağlantıları özel sohbetten açılır. Tahmin sayacı yeni fotoğraf göndermeden güncellenir; güncellenemezse Kalan süre düğmesi güncel kaydı okur.

Katalogdaki 300 dosya ve tüm atıflar korunur. Öznenin tam adını fotoğrafçı kredisinde taşıyan kart seçime alınmaz; oynanabilir havuz 299 fotoğraftır. Aktif turda fotoğrafçı/lisans bilgisi korunur, Commons dosya bağlantıları yalnız özel kaynak bölümünde gösterilir. Sonuç fotoğrafın altında da açıklanır; fotoğrafı değiştirmez veya yeni görsel üretmez.

Maça ilk tahminiyle en fazla 100 kişi katılır. Mevcut oyuncu sonraki turda oynamaya devam eder. Tur ekranı ilk 10, maç ekranı ilk 20 sonucu gösterir; kabul edilen bütün oyuncuların tahmini ve puanı saklanır. Eski, 100 kişiden büyük kayıtlar geçiş sırasında silinmez.

## Kayıt ve geri dönüş

Bütün Yaş Tahmini mutasyonları sürümlü koşullu kayıt kullanır. Eski kaydın yalnız eksik `version` alanı atomik doldurulur; fotoğraf, cevap veya ödül verisi yeniden oluşturulmaz. Yeni maç, bekleyen ödüller tamamlanmadan eski kaydın üstüne yazmaz. Her ödül için aynı olay kimliği korunur. Genel bitir komutu da geçmişi veya bekleyen puanları silmez; konu ve kurucu/yönetici izni denetlenir. İlk fotoğraf, iptal ve sonraki tur teslimleri eski tur kimliğiyle yeni turun durumunu değiştiremez.

Şema 20 korunur; yeni alanlar eklemelidir. Mevcut açık maçın süre ayarı değişmez; 30 saniye yeni maçlara uygulanır. Yedek, aday paket testi, etkinleştirme ve geri dönüş mevcut yayın akışını kullanır. GitHub güncellemesi yapılmaz.

## Kabul ve sınırlar

Hedefli testler: gizli puan, yanlış konu/eski fotoğraf/eski tarih, kişiye bağlı düğme, depo hatasından sonra tekrar deneme, eşzamanlı iki servis, bekleyen ödül, 26. oyuncunun ödülü, 100 oyuncu sınırı, ilk teslim, sonuç beklemesi, yeniden başlatma ve menüden bütün eski oyunlara erişim.

Sürüm7 tam kapısı25/25 geçti; sunucu sağlıklı. Gerçek Telegram oturumunda bir hesapla beş tur, normal ve düğmeli tahmin,130 toplam puan, sessiz kabul ve özel kaynak sayfalaması doğrulandı. Odaklı kanıt: `deploy/evidence/age-gameplay-quiet-live-20261021.json`. Bu, bütün oyunlar veya çok oyunculu kapsam için kabul değildir. Telegram gönderimi başarılı olup teslim onayı kaydedilemezse tekrar mesaj olasılığı sürer; Telegram dış gönderimi için tam tek-sefer garantisi iddia edilmez. 40 oyun hedefi ayrıca açıktır.

## Grup mesajlarını azaltma

Kullanıcının sonraki geri bildirimiyle başarılı tahmin yanıtları kaldırıldı. Tek fotoğrafın sayacı güncellenir; puanlar yine tur sonunda görünür. Fotoğrafçı ve lisans adı grup açıklamasında kalır; bağlantılar yalnız botun özel sohbetindeki `/yas kaynaklar` bölümünde, önizlemesiz ve sayfalı gösterilir. Bu değişiklik otomatik özel mesaj göndermez. Önceki maçların grup geçmişi topluca silinmez.
