# Oyun kart havuzları

Kimim Ben, Tabu, Beş İpucu, Emoji Bilmece ve Yasak Harf için bu sürümde kullanılan kartlar özgün Türkçe içeriktir; dış API veya üçüncü taraf içerik lisansı gerektirmez.

| Oyun | Önce | Şimdi | Korunan kural |
|---|---:|---:|---|
| Kimim Ben | 16 | 108 | Meslek, hayvan, eşya ve yer; özne kimliğini görmez, kategori ipucu iki sorudan sonra açılır. |
| Tabu | 166 | 200 | Her hedef benzersiz ve beş yasak sözcük birbirinden farklıdır. |
| Beş İpucu | 35 | 60 | Cevap ipucunda düz metin olarak bulunmaz; her destede tüm kartlar dolaşmadan tekrar başlamaz. |
| Emoji Bilmece | 60 | 85 | Kabul edilen cevap ve alias başka kartın cevabı ile çakışmaz. |
| Yasak Harf | 24 | 44 | Yasak harf kartın hedef sözcüğünde bulunur; hedefler sözlükte geçerlidir. |

Kimim Ben’in gizli kimliği gruba açık mesaja eklenmez. Önce sır içermeyen bir deneme ile Telegram’ın alıcı kimliği, sıfır açık mesaj kimliği ve ephemeral mesaj kimliği doğrulanır. Yalnız bu doğrulama geçerse kimlik aynı oyuncuya özel grup kartında gösterilir; deneme kartı silinir. Doğrulama başarısızsa açık deneme mesajı silinir ve kimlik yalnız kişiye özel düğme uyarısında kalır. Kalıcı kart botun özel sohbetinde korunarak gönderilir. Üretim Bot API denemesinde ephemeral alanları ve Telegram’ın “Only visible to you” görünümü doğrulandı. Önceden botla özel sohbeti açık oyuncu lobi düğmesiyle doğrudan hazır olur; ilk kez gelen için `/start` bağlantısı korunur.

Kart havuzu doğrulaması sentetik testtir. Gerçek üç ve üzeri oyunculu Telegram kabulü ayrıca yapılmalıdır.

Test grubunda tek oyunculu Kimim Ben lobisini kapatma işlemi kalıcı durumda başarılıydı, fakat eski panel değişmiyordu. Beş sosyal oyunun ortak kapanış ekranı, maç başlamadan önce yalnızca başlangıçta oluşacak tur alanlarını okumayacak şekilde düzeltildi; kalıcı panel teslimi regresyon testiyle doğrulandı.
