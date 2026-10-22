# Oynanabilirlik ve özel AI sağlayıcısı — 19. dalga

Bu dalga oyun sayısını artırmaz; mevcut 45 oyunun 12 tanesinin arayüz/akışını iyileştirir: UNO, Pişti, Papaz Kimde, Batak, Türkçe Wordle, Karşı Kelime, Anagram, Sudoku, Sayı Tahmini, Hızlı Buton, Hafıza Eşleştirme ve Mayın Tarlası.

- UNO: kart çekme sonrasında motor yalnız yeni çekilen kartı kabul eder; aynı sırada yeniden çekme gizlenir. Grup kart konumları 12'lik sayfalarda sunulur; geçerli kart işaretleri yalnız özel Elim yanıtında görünür. Renk seçimi, +4 itirazı, yön, sıra ve bekleyen işlemler açıklanır. Uzun adlar panel sınırına göre kısaltılır.
- Pişti/Papaz: hazır olma, sıra, masa, el sayıları, son hamle ve skor daha okunur sunulur; özel kart değerleri grup panelinde gösterilmez.
- Batak: gizli el grup metninden çıkarılır. Elim yalnız tıklayan oyuncuya sayfalı yanıt verir. Aktif masa komutla açılabilir; tur numarası ve löve sonuçları görünürdür. Geçersiz/eskimiş eylemler servis sınırında reddedilir. Mevcut motor rastgele kozlu bireysel löve oyunudur; gerçek ihale ve eşli Batak tamamlanmış değildir.
- Kelime/bulmaca panelleri: gerçek asgari oyuncu sayısı, sıra, kalan süre/hak, puan, ipucu bütçesi, işaretleme modu ve maç sonu yönlendirmesi gösterilir. Oyun sırasında çözümler gizli kalır. Uzun oyuncu adları Telegram uzunluk sınırını aşmaz.

Impeccable, mevcut Telegram yüzeylerini sadeleştirmek ve durumları açıklaştırmak için kullanıldı. Context7 ile AI yanıt sözleşmesi kontrol edildi. Bağımsız ajan incelemelerinde bulunan gizlilik, eski buton ve mesaj uzunluğu hataları ayrıca regresyon kapsamına alındı.

d2api, yalnız metin görevlerinde birinci sağlayıcıdır; görsel ve medya görevleri mevcut sağlayıcılarda kalır. Hesap bilgileri depoya ve bot imajına alınmaz. Sınırlar ve işletmeci yetkilendirmesi D2API_OPERATIONS.md içindedir. Kaynak lisansı ve servis izni birbirinden ayrıdır.

Bu belge tek başına canlı kabul kanıtı değildir. Kesin test sayıları, kaynak commit ve yayın durumu deploy/evidence/games19-deployment-20261022.json içine yazılır. Canlı oyuncu/Telegram arayüz denemeleri işletmecinin önceki isteğiyle ertelendi; otomatik testler bu kabulün yerini tutmaz. GitHub güncellenmez.
