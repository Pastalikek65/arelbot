# Satranç tahtası görünümü · 22 Ekim 2026

Mevcut inline tahta için okunabilirlik düzeltmesi. Oyun motoru ve kurallar
aynı kalır. Sırası gelen oyuncunun tarafı alttadır; 64 karenin callback kare
kimliği yön değişirken korunur.

- Telegram `chessno` setinden 26 doğrulanmış statik karo: 12 taş × iki kare
  rengi ve iki boş kare. a1 koyudur. Hareketli efekt kullanılmaz.
- Mavi seçili taş; yeşil boş hamle hedefi; kırmızı alınabilir taş. İşaretler
  taş çizimini değiştirmez. Caption seçili kareyi ve hedef koordinatlarını yazar.
- A–H harfleri tahta ile birlikte döner; harfe dokunma yalnız bilgi gösterir,
  hamle makbuzu veya oyun durumunu değiştirmez.
- Premium kapalıysa veya Telegram custom emoji/düğme metnini açık HTTP400 ile
  reddederse Unicode görünümüyle tek yeniden deneme. Timeout/429/5xx yeni
  gönderim üretmez. Kareler klasik metin varyantıyla gösterilir.
- Görsel dosyalar dağıtılmaz; katalog native custom emoji kimlikleri içerir.
  Kaynak ve lisans sınırı `docs/DATA_SOURCES.md` içinde kayıtlıdır.

Kontrol: Yerel tarayıcı önizlemesi gerçek markup'tan üretildi; 340px genişlikte
beyaz, siyah, seçim/alma ve Unicode görünümü karşılaştırıldı; yatay taşma yok.
Bu, gerçek Telegram istemcisinden alınmış bir ekran görüntüsü değildir.
İmpeccable dedektörü Python hedeflerinde bulgu vermedi; bağımsız kod ve görsel
inceleme yeni engel bulmadı. Canlı oyunculu testler işletmeci isteğiyle ertelidir.

Yerel Docker motoru eski IPC soketine erişemediği için başlayamadı. Yerel
kaynak testleri ile ayrı proje/ağ/veritabanı kullanan sunucu Docker kontrolleri
aynı uygulama commit'i üzerinde birleştirilir. Yerel Docker çalışmış sayılmaz.

## Yayın sonucu

`games-18`, kaynak `13947c0`, şema 22 sunucuda etkin. Uygulama dosyaları ve
varlıkların hash'leri, tam testten geçen `76439c1` ile aynı. 25/25 yayın
kontrolü; 2.981 yerel PASS / 19 SKIP, yalıtılmış sunucu Docker profilinde
2.994 PASS / 6 SKIP. Son yayın imajında 83 ilgili test ayrıca geçti.

Canlı salt okunur kontrolde 26 statik custom emoji kimliği, 64 kare, iki
bakış ve Unicode geri dönüşü doğrulandı. Dört ana servis sağlıklı ve ilk
kontrol penceresinde yeniden başlama/traceback yok. Komut kapsamları
83/83/85/95; her kapsamda 45 oyun, yardım ve iptal var. Yeni yedek
geçici veritabanına geri yüklendi: 46 koleksiyon, 42.348 belge; geçici
veriler temizlendi. Gerçek Telegram istemcisindeki oyunculu görsel kabul
henüz yapılmadı. GitHub güncellenmedi.

Kanıt: `deploy/evidence/games18-deployment-20261022.json`.
