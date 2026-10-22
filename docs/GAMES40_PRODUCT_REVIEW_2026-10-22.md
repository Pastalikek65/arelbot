# 40 oyun dalgası: ürün ve doğrulama kaydı

Bu dalga mevcut 26 katalog tanımına 14 ayrı mekanik ekler. Zar, slot, tema ve zorluk seçenekleri ayrı oyun sayılmaz. Geliştirme yerelde yapılır; GitHub yayını yetkilendirilmemiştir. Kullanıcı canlı Telegram denemelerini ertelediğinden insanlı maç kabulü `NOT_RUN` kalır.

## Yeni oyunlar

| Oyun / komut | Oyuncu | Temel karar ve maç yapısı |
|---|---:|---|
| Harf Maratonu `/harfmaraton` | 2–20 | Harf ve kategoriye özelden kapalı yanıt; beş tur, benzersiz cevap puanı. |
| Sözcük Avı `/sozcukavi` | 1–12 | 4×4 tabloda sekiz yönde komşu harfler; hücre tekrar kullanılamaz, kelime ilk bulanındır. |
| Yasak Harf `/yasakharf` | 2–12 | Özel hedefi yasak harfi söylemeden anlat; herkes iki kez anlatıcı olur. |
| Ortak Hikâye `/ortakhikaye` | 3–10 | Sırayla cümle, zorunlu sözcük ve önceki cümleyle bağ; kişi başına iki katkı. Edebi kalite puanlanmaz. |
| Bomba `/bomba` | 2–12 | Harf parçasını içeren kullanılmamış sözcükle bombayı aktar; iki can ve gizli fitil. |
| Kör Sıralama `/korsiralama` | 1 | Gelecek değerleri görmeden beş sayıyı geri dönüşsüz sıralara yerleştir; gerçek sırayla karşılaştır. |
| Kronoloji `/kronoloji` | 1–12 | Kurgu olayların kesin zaman ilişkilerinden beş olayın tek sırasını bul; beş tur. |
| Doğru–Yanlış `/dogruyanlis` | 1–30 | On doğrulanmış önerme; üç can, sınırlı risk/koruma ve kapalı oy. |
| Dedektif `/dedektif` | 1–12 | Üç kişi, yer ve nesnenin tekil eşlemelerini çöz; ipucu maliyeti ve sınırlı suçlama; üç dosya. |
| Amiral Battı `/amiral` | 2 | 6×6 gizli filo, isabette ek hamle; kendi özel panosu, süre aşımı ve hükmen sonuç. |
| İki Doğru Bir Yalan `/ikidogru` | 3–12 | Anlatıcı özelden üç iddia ve yalanı seçer; diğerleri kapalı oylar. Doğruluğu oyuncu belirler. |
| Kimim Ben `/kimimben` | 3–12 | Kimlik özneye gizli, diğerlerine özel; soruya akran oyu ve sekiz soru/tahmin bütçesi. |
| Takım Bilgi Ligi `/takimligi` | 4–20 | Dengeli iki takım, dönen kaptan, eşleşmiş sorular, risk/pas ve eşit tur hakkı. |
| Zehirli Kadeh `/kadeh` | 3–10 | Kurmaca gizli roller, canlar ve sınırlı değiştirme/reddetme; beş tur veya son yaşayan. |

## Ortak ürün sözleşmesi

- Güncel grup paneli düzenlenir; başarılı hamle için gruba ayrı onay mesajı gönderilmez.
- Grup, forum konusu, oyuncu, maç, faz ve güncel mesaj kimliği eyleme bağlanır. Eski buton ve yinelenen eylem yeni puan yaratmaz.
- Saatler oynanabilir grup paneli ve gereken kişisel kontroller teslim edilince başlar. Teslim hatası kalıcı yeniden deneme kaydında tutulur; hazırlık sınırsız beklemez.
- Gizli hedef, gemi, iddia, kimlik ve kapalı yanıt AI geçmişine veya güvenli yönlendirme sorgusuna girmez. Özel mesajlar ilgili kişinin güncel kartına bağlanır.
- Birden fazla oyun cevabı tüketebiliyorsa kullanıcı ilgili paneli yanıtlar. İçerik rastgele ilk oyuna yönlendirilmez.
- Tamamlanan/iptal edilen maçın saatleri temizlenir. Puan ve para ayrı, tekrarlanabilir olay kimlikleriyle kaydedilir. İptal ödül üretmez.
- Grup oyunu düz metin ve düğmelerle oynanır; yeni dış API, hesap, yapay görsel veya AI hakem bağımlılığı yoktur.

## İncelemede bulunanlar

İlk uygulama otomatik testten geçse de bağımsız ürün incelemesi özel kart sürümü, aynı saniyede gelen Telegram mesajı, boş maç ödülü, oynarken ayrılma ve rastgele içerik çeşitliliği açıkları buldu. Bunlar ayrı regresyonlarla yeniden üretilir; başarısız testler kapanmadan yayın kabulü verilmez. Ayrıntılı nihai ölçümler yayın kanıtında tutulur.

## Kabul durumu

14 yeni oyun `2026.10.22-games-11` sürümünde yayında; katalog 40 ayrı mekanik içeriyor. Bu oyun dalgasının yayımlandığı kaynak `a5fa94c`, son uygulama değişikliği `607fe1f`; aradaki commit yalnız başarılı kapı raporudur. Odaklı entegrasyon, bağımsız regresyon ve Türkçe denetimi 121 PASS; yeni ailelerin Ruff denetimi temiz.

Tam yayın kapısı 25/25 geçti: host 2.713 PASS / 19 SKIP; yerel konteyner ve sunucunun gerçek aday imajı 2.726 PASS / 6 SKIP. Gerçek yalıtılmış Mongo denemesi 246 durumdan 115 vadesi geleni doğru seçti; gizli yönlendirme ve toplu açık metadata projeksiyonlarında özel alan bulunmadı. Atlanan testler başarılı sayılmadı.

Bot, işçi, Mongo ve Redis sağlıklı; yeniden başlama sayıları sıfır. Çalışan botun Telegram API kimliği, 40 oyun kaydı ve boş bekleyen güncelleme kuyruğu salt-okunur sorguyla doğrulandı. Gruba veya özel sohbete test mesajı gönderilmedi. Kullanıcı verileri durdurulmuş yazıcılarla yedeklendi, arşiv bütünlüğü ve geri dönüş yolu korundu; bu güncellemenin son yedeğine bağımsız restore yapılmış olduğu iddia edilmez.

İnsanlı Telegram arayüzü ve çok oyunculu canlı maçlar kullanıcı isteğiyle `NOT_RUN`. GitHub güncellenmedi. Ayrıntılı kanıt: [yayın kaydı](../deploy/evidence/games40-deployment-20261022.json).

Sonraki arayüz yayını games13, oyun mekaniklerini koruyup ortak buton simgelerini ekledi. Güncel kaynak ve doğrulama: [Telegram buton kaydı](TELEGRAM_BUTTONS_UPDATE_2026-10-22.md).
