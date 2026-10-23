# Oyun kart havuzları

Kimim Ben, Tabu, Beş İpucu, Emoji Bilmece ve Yasak Harf için bu sürümde kullanılan kartlar özgün Türkçe içeriktir; dış API veya üçüncü taraf içerik lisansı gerektirmez.

| Oyun | Önce | Şimdi | Korunan kural |
|---|---:|---:|---|
| Kimim Ben | 16 | 108 | Meslek, hayvan, eşya ve yer; özne kimliğini görmez, kategori ipucu iki sorudan sonra açılır. |
| Tabu | 166 | 200 | Her hedef benzersiz ve beş yasak sözcük birbirinden farklıdır. |
| Beş İpucu | 35 | 60 | Cevap ipucunda düz metin olarak bulunmaz; her destede tüm kartlar dolaşmadan tekrar başlamaz. |
| Emoji Bilmece | 60 | 85 | Kabul edilen cevap ve alias başka kartın cevabı ile çakışmaz. |
| Yasak Harf | 24 | 44 | Yasak harf kartın hedef sözcüğünde bulunur; hedefler sözlükte geçerlidir. |

Kimim Ben’in gizli kimliği gruba açık mesaja eklenmez. İlgili diğer oyuncu grup düğmesinden yalnız kendisine görünen kısa uyarı alır; kalıcı rol kartı botun özel sohbetinde korunarak gönderilir. Telegram’ın yeni ephemeral grup mesajı sır içermeyen hatırlatma için kullanılır; alıcı kimliği ve ephemeral mesaj kimliği doğrulanmazsa olası açık mesaj silinir. Üretim Bot API denemesinde iki alanın da döndüğü doğrulandı. Önceden botla özel sohbeti açık oyuncu lobi düğmesiyle doğrudan hazır olur; ilk kez gelen için `/start` bağlantısı korunur.

Kart havuzu doğrulaması sentetik testtir. Gerçek üç ve üzeri oyunculu Telegram kabulü ayrıca yapılmalıdır.
