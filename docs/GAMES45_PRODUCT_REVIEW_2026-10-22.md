# 45 oyun genişletmesi · 22 Ekim 2026

Bu dalga, önceki 40 oyunluk sürüme beş ayrı motor ekler. Katalog sayısı,
gerçek oyunculu kabul anlamına gelmez. Canlı Telegram oyuncu/UI testleri
işletmecinin talebiyle ertelenmiştir. GitHub yayını yetkilendirilmemiştir.

| Oyun | Giriş | Oynanış ve kapsam |
|---|---|---|
| Pişti | `/pisti` | 52 kart; 2 kişi bireysel, 4 kişi karşılıklı takım; dörderli dağıtım; vale, pişti, özel kart ve fazla kart puanları; son alan masayı toplar |
| Papaz Kimde | `/papazkimde` | 3–8 kişi; 49 kart ve tek papaz; çiftleri otomatik çıkarma, sunucuda gizli rastgele çekme, eli boşalanın oyundan çıkması |
| 2048 | `/2048` | 4×4 gerçek kaydırma ve birleştirme; taş başına hamlede tek birleşme; geçerli hamlede 2/4 üretimi; kaybetme, 2048 sonrası devam ve kazanımı alma |
| Beş İpucu | `/besipucu` | Kategorili çağrışım kartları; ortak beş kademe; kademe başına kişi başı tek tahmin veya pas; herkes hakkını tüketince erken açılım; 10/8/6/4/2 puan ve beş tur |
| Emoji Bilmece | `/emojibilmece` | Özgün yerel emoji bilmeceleri; normal mesajla yanıt; tur başına iki tahmin, 6/3 puan ve beş tur |

## Oyun yüzeyleri

Kart oyunları grup lobisi kullanır. Oyuncular botun özel sohbetini hazırlayıp
ellerini orada alır. Her sırada güncel grup paneli ve gerekli özel ellerin
teslimi tamamlanmadan saat başlamaz. Eldeki kart düğmeleri kullanıcıya,
maça, aşamaya ve özel mesaj kimliğine bağlıdır. Ortak panelde rakip elleri
ve deste gösterilmez. Pişti'de takım yerleşimi ve Papaz Kimde'de sıradaki
oyuncu görünür. Bitmemiş maçtan ayrılma ve tekrar eden zaman aşımı ücretsiz
puan üretmemelidir.

2048 ve yeni tahmin oyunları özel sohbette de açılabilir. Grup tahminleri
oyuncunun lobiye veya aktif oyuna Katıl düğmesiyle katılmasından sonra normal
mesajla işlenir. Birden fazla kelime/sayı oyunu açıksa ilgili paneli yanıtlamak
gerekir; rastgele ilk oyuna puan yazılmaz. Düzenlenen mesajlar yeni tahmin
olarak sayılmaz. Başarılı tahminde ayrıca “kaydedildi” mesajı gönderilmez.

## Telegram yenilikleri

Resmî Bot API dokümanı 22 Ekim 2026 tarihinde incelendi; güncel duyuru
10.3 / 24 Ağustos 2026 olarak görünüyor.

- Yeni ve mevcut 27 kalıcı bulmaca panelindeki **Nasıl oynanır?** düğmesi,
  grup kurallarını yalnız isteyen oyuncuya ephemeral mesajla gösterir.
  Özel sohbette tam kurallar doğrudan yanıtlanır. Grup gönderimi reddedilirse
  veya bağlantı belirsizse tekrar gönderim ve herkese açık yedek mesaj yoktur;
  kısa düğme uyarısı kullanılır. Bu mekanizma gizli kart/rol veya süre onayı değildir.
- Hafıza'da eşleşen/açık kartlar ve Mayın'da açılmış hücreler yeni
  `disabled: {}` düğme türünü kullanır; aynı düğmede callback bulunmaz.
  Bayraklı kapalı hücre etkin kalır, böylece bayrak geri alınabilir.
- Ortak simge sistemi yeni beş menü başlığını da kapsar. Önceki 38 doğrulanmış
  animasyonlu simge kataloğu korunur; bulunmayan simge Unicode olarak gösterilir.
- PTB 22.6 bağımlılığı değiştirilmedi. Yeni alanlar belgelenen `api_kwargs`,
  ephemeral gönderimi ham `do_api_request` yoluyla iletilir. Eski Message
  ayrıştırıcısına yeni ephemeral yanıt biçimi zorlanmaz.

Kaynaklar: [Telegram Bot API](https://core.telegram.org/bots/api),
[Ephemeral mesajlar](https://core.telegram.org/bots/api#ephemeral-messages-and-commands),
[DisabledButton](https://core.telegram.org/bots/api#disabledbutton),
[PTB Bot API genişletme yolu](https://docs.python-telegram-bot.org/en/stable/telegram.bot.html).

## Dayanıklılık ve kabul sınırı

Her yeni aile CAS sürüm kontrolü, kalıcı zamanlayıcı, teslim bekleme kaydı,
tekilleştirilmiş puan olayı ve terminal temizliği kullanır. Yeni kart ailesini
incelerken ortak panel teslim beklemesinin özel el/zamanlayıcı/puan işlerini
engellediği bulundu. Veritabanı sorgusu ve oyun çalıştırıcısı, bulmaca ailelerinde
bu işlerin bağımsız yürümesini sağlayacak şekilde düzeltildi. Aynı profilin
gerçek MongoDB sorgu senaryoları buna göre genişletildi.

Telegram mesaj zamanları saniye hassasiyetindedir. Önceki saniyeden kalan
girdiler ve eski mesaj kimlikleri reddedilir. Aynı saniyenin içinde panel
tesliminden önce/sonra gönderilen iki metni zaman alanı tek başına ayıramaz;
aynı saniyedeki meşru yanıtları engellememek için bu belirsizlik korunur.
Butonlar ayrıca aşama kimliğine bağlıdır.

Otomatik maçlar ve API fikstürleri, gerçek Telegram istemcisinde görsel veya
insanlarla tam maç kabulü yerine geçmez. Sunucu aktivasyonu, test sonuçları
ve bu sınırlar `deploy/evidence/games45-deployment-20261022.json` içinde
gerçek ölçümlerle kaydedilmiştir. Oyunlar dış AI hakemi kullanmaz; yeni
kart ve bilmece motorları için harici veri kaynağı veya ücretli API eklenmedi.

## Sonuç

2026.10.22-games-14 sunucuda etkin. 45 oyun, 91 feature kaydı. Son kaynak için yayın denetimi 25/25; yerel pytest 2829 başarılı/19 atlandı; Docker ve sunucu adayı 2842 başarılı/6 atlandı. Bağımsız inceleme PASS. Ayrı gerçek Mongo testinde 433 senaryodan 248 iş doğru seçildi; gizli ve açık yönlendirme projeksiyonları güvenli.

Bot, worker, MongoDB ve Redis sağlıklı; yeniden başlatma 0, son bot/worker hata izleri 0. Salt okunur Telegram kontrolünde @ArelChat_Bot, 45 oyun, 38 animasyonlu simge ve bekleyen güncelleme 0 doğrulandı. MongoDB yedeği alınıp sıkıştırılmış dosyanın bütünlüğü kontrol edildi; bu güncellemede ayrı geri yükleme tatbikatı yapılmadı. Gerçek oyunculu/istemci görsel kabulü NOT_RUN; GitHub güncellenmedi.
