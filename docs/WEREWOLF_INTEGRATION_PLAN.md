# GreyWolf Werewolf entegrasyonu

Durum: LOCAL_VALIDATED; sunucu ve gerçek Telegram lobi kontrolü bekleniyor. Kaynak commit: `bac172a29f8b394ebbb4822c6f244d769fac21ac`.

## Amaç ve sınır

GreyWolfDev/Werewolf GPL-3.0 kaynak motorunun oyun kuralları ve Türkçe rol içeriği Arel’in Python/MongoDB oyun akışına uyarlanır. Özgün uygulama Windows/.NET Framework 4.5.2/SQL Server bağımlıdır; bu uygulamanın ayrı bir Telegram polling süreci olarak aynen çalıştığı iddia edilmeyecektir. REA becerisi tam kaynak depolar için normal kaynak incelemesini önerir; inceleme GitHub plugin’i, sabitlenmiş kaynak ve yerel kod araçlarıyla yapılmıştır.

## Uygulama sırası

1. Lisanslı özgün rol/denge/motor/dil kaynaklarını hash ve commit ile sakla. 45 rol kataloğu, Türkçe kartlar ve eksik Chef/Barkeep çevirilerini ekle.
2. Kaynaktaki Normal/Kaos dağıtımı, dinamik güç puanları ve bağımlılık düzeltmelerini typed domain katmanına taşı.
3. Gizli ilk gece seçimleri, gece/gündüz yetenekleri, dönüşümler, oy ağırlıkları, taraf/özel kazanımlar ve zincirleme ölüm kurallarını kalıcı saf durum geçişleri olarak uygula.
4. Yeni lobilere gelişmiş kuralları bağla. Eski kayıtlarda `ruleset` yoksa mevcut dört rollü motoru koru. Aktif maçların rolünü veya kurallarını değiştirme.
5. Türkçe lobi, rol rehberi, mod seçimi, özel hedef/yetenek düğmeleri, süreler ve olay özetlerini mevcut private-delivery/CAS altyapısıyla tamamla.
6. Lisans/kaynak bildirimi, komut ve özellik kataloğunu güncelle. Regresyon, rol etkileşimi, gizlilik, eski callback, eşzamanlılık, yeniden başlatma ve tam maç simülasyonlarını çalıştır.
7. Kaynak sunumu/yedek/geri dönüş ile adayı sunucuya dağıt. Canlı lobi/özel sohbeti doğrula; gönüllü oyunculu tam maç yapılmadıysa NOT_RUN tut.

## Yerel kanıt, 2026-10-23

- Kaynak paketindeki 8 özgün dosyanın SHA-256 değeri sabitlenmiş upstream kayıtlarıyla eşleşti.
- Bağımsız kod incelemesindeki Avcı turu, Mezarcı/Seri Katil etkisi ve rehber paneli kusurları regresyon testleriyle düzeltildi.
- Yalıtılmış yayın kapısı 14/14 kontrolü geçti: 3.381 test başarılı, 19 atlandı; mimari ve 91 özellik kaydı doğrulandı.
- 14 senaryo × 5 tohumla 70 sentetik tam maç geçti. Bu, gerçek çok oyunculu Telegram kabulünün yerini tutmaz.

## Korunacaklar

- CLI, sohbet ve görsel sağlayıcıları, hesap anahtarları, diğer oyunlar.
- Sahiplik/forum konusu kapsamı, tura/aşamaya bağlı callback kontrolleri ve tekilleştirilmiş ödüller.
- Grup panelinde gizli roller, ham gece eylemleri, oy hedefleri ve RNG tohumu bulunmaz.
- Özel teslim tamamlanmadan ilgili aşama saati başlamaz; eksik teslim güvenli toparlanır.
- Otomatik test başarısı gerçek çok oyunculu Telegram kabulü sayılmaz.
