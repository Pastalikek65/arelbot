# Arel güvenilirlik düzeltmeleri — 18 Ekim 2026

## Doğrulanan düzeltmeler

- Python 3.11 üzerinde kart motorunun açılmasını engelleyen dataclass varsayılanı düzeltildi.
- MongoDB ve Redis sağlık denetimleri açık bağlantı noktasını başarılı protokol yanıtı olarak kabul etmiyor.
- Grup analizi araçları gerçek araç kimliği ve doğrulanmış yönetici bağlamıyla yetkilendiriliyor; model başka sohbet seçemiyor.
- Grup içinde hava ve deprem gibi araç gerektiren mesajlar yalnız sohbet üreten yanıt planlayıcısında kaybolmuyor.
- Altı Guardian komut takma adı ve mesaj silme yönlendirmesi düzeltildi. Henüz uygulanmayan federasyon yetkileri açıkça belirtiliyor.
- Medya dönüştürme süre aşımında veya iptal edildiğinde alt işlem sonlandırılıyor; hatalı dönüşüm dosya gibi sunulmuyor ve başarılı çıktı korunuyor.
- Haberlerde kategori etiketi bulunmayan gerçek RSS akışları işleniyor. Hatalı veya bekleyen birincil kaynak yedek kaynağı engellemiyor; kaynak bilgisi gerçek katkıyı gösteriyor.
- Grup menüsündeki tekrarlı ve özel sohbete ait çıkmaz seçenekler çıkarıldı. Türkçe açıklamalar sadeleştirildi; çok bölümlü özellik sonuçlarının kalan bölümleri gösteriliyor.
- Paket/imaj tutarlılığı için başlangıç betiğinin satır sonları LF olarak sabitlendi; sır içermeyen CA yer tutucusu imaj kapsamına alındı. İmajın dosya özeti dağıtımdan önce ayrıca doğrulanmalıdır.

## Kanıt ve sınırlar

Üretim bağımlılıklarını taşıyan mevcut Python 3.11 imajında, üretim ağına ve sırlarına erişmeyen geçici kopyada: **1431 geçti, 2 atlandı**. Mimari kontrolü: 0 sorun. Özellik kataloğu: 56/56. Bağımsız kaynak incelemesinin RSS süre bütçesi bulgusu regresyon testiyle kapatıldı.

Herkese açık 12 kaynak denemesinde 11 akış veri döndürdü. Film araması Apple kaynağından boş sonuç aldı; film önerilerinin güvenilir çalıştığı doğrulanmadı. Ana sohbet sağlayıcısı canlı, sınırlı isteğe HTTP 200 ile yanıt verdi; alternatif sağlayıcıdaki bir istek 25 saniyede zaman aşımına uğradı. Bunlar kapasite veya süreklilik garantisi değildir.

Telegram'a gerçek kullanıcı mesajı gönderimi, her komutun her yetki durumunda çalışması ve tüm medya siteleri uçtan uca doğrulanmış değildir. Komut kayıtları veya geçen birim testleri bu kanıtların yerine geçmez. Üretim dağıtımının sonucu ayrı operasyon kaydında tutulmalıdır.
