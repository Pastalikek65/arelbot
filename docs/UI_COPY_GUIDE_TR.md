# Arel Türkçe arayüz dili

Bu düzenleme Impeccable `clarify` ilkeleriyle mevcut Telegram arayüzüne uygulanır. Hedef, grupta oyun oynayan ve hata anında kısa, anlaşılır yönlendirme bekleyen kullanıcıdır. Telegram'ın yazı tipi, renk ve düğme yapısı korunur.

- Kullanıcıya tutarlı biçimde “sen” diye hitap et. Kısa, tam cümleler kullan; övgü, alay ve bağıran başlıklar ekleme.
- Aynı işlemi aynı adla göster: “Oyunu bitir”, “Yeni oyun”, “Yenile”, “Katıl”, “Özel sohbeti aç”. Oyuna özgü “pas”, “el”, “şah” gibi yerleşik terimleri koru.
- Önce mevcut durumu, ardından yapılabilecek işlemi anlat. Düğme, dokunma hareketini değil sonucunu adlandırsın.
- Hatalarda iç hata kodu, sağlayıcı, oturum sürümü ve veri katmanı gibi uygulama ayrıntılarını gösterme. Nedeni bilinmiyorsa neden uydurma.
- İşlem tamamlanmadan “kaydedildi”, “gönderildi” veya “hazır” deme. Yeniden deneme ve puan kaydı bekliyorsa bunu açıkça ayır.
- Oyun panelinde sıra, hedef, süre ve puan önce gelsin. Kurallar lobide açıklansın; her hamlede uzun yönerge tekrarlanmasın.
- Renkleri takım adıyla, simgeleri metinle birlikte kullan. Emoji anlamın tek taşıyıcısı olmasın; başlık ve durumlar için ölçülü kullan.
- Kullanıcı adlarını ve içeriklerini HTML açısından kaçır. Komutlar, callback verileri, çeviri anahtarları ve oyun kuralları dil düzenlemesi sırasında değişmez.
- Kaynak, fotoğraf lisansı, yaşın hesaplandığı tarih ve gerçek oyuncu sınırları korunur. Eksik veya doğrulanmamış bir özellik için yeni iddia ekleme.

Denetim: mevcut testler, Türkçe metin denetimi ve telefondaki Telegram görünümü birlikte değerlendirilir. CSS, font veya SVG değişikliği bu Telegram metin yüzeyinin kapsamı değildir.
