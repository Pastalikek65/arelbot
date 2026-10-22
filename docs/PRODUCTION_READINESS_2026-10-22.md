# Arel · üretim hazırlığı değerlendirmesi

22 Ekim 2026, oyun/ekonomi/komut dalgası 17. Bu belge kod, sentetik tekrar
ve sunucu denetimini gerçek oyunculu kabulden ayırır. GitHub güncellenmez.

## Mevcut seviye

Bot üretimde çalışıyor ve otomatik denetlenen bir çekirdeğe sahip. Bütün 45
oyun için koşulsuz production kabulü henüz yok. Aşağıdaki 16/20 (yaklaşık 8/10)
bir mühendislik değerlendirmesidir; kullanıcı memnuniyeti veya ölçülmüş SLA değildir.

| Boyut | Değerlendirme | Dayanak ve sınır |
|---|---|---|
| Komut ve keşif | 5/5 | 45/45 oyun girişi menüde ve yedi sayfalık rehberde; kopya alias yok; help ve iptal görünür; scope izin kontrolünün yerine geçmiyor |
| Ekonomi ve finans | 4/5 | Atomik bakiye/sahiplik, makbuz, limit, imzalı alışveriş, bahis kurtarma; gerçek Mongo kabulü var; uzun süreli kesinti gözlemi yok |
| Oyun sürekliliği | 4/5 | Satranç, XOX, Gomoku ve Connect Four dahil CAS, kalıcı panel/ödül kuyruğu; insanlarla tam maç kabulü eksik |
| Operasyon ve saha kanıtı | 3/5 | Yedekli sürüm geçişi ve restore denemesi; iki servisli izole Mongo yük/kesinti enjeksiyonu; gerçek Telegram yoğunluğu ve oyunculu kabul ölçülmedi |

## Bu dalgada yapılanlar

- İlk 100 legacy kaydı kesen slash listesi yerine bilinçli sıralama: temel
  komutlar, ekonomi, bütün oyunlar ve sık araçlar. Gizli uyumluluk alias'ları
  çalışır. Oyun menüsü tüm 45 oyunu açar; yardım rehberi her oyunun komut ve
  oyuncu sayısını gösterir. Pişti için 2 veya 4 kişi açık yazılır.
- Eksik beş yeni oyun merkezi rehbere dahil edildi. Sonuç üretmeyen geliştirme
  fikirleri bağlantısı ana yardımdan çıkarıldı; ekonomi ve medya yardımı eklendi.
- Aynı menüden tekrar Blackjack açarken sabit mesaj kimliği ikinci bahsi yanlış
  tekrar sayıyordu. Başlatma kimliği artık callback kimliğine bağlıdır; proxy
  bu kimliği taşır. Aynı callback tekrarında kimlik aynı kalır.
- Sekiz motor ailesinde açılışta gerekli puan ve jeton servisleri kaydedilir.
  Yeniden başlamada bir servis eksikse ödül borcu kapatılmaz. Sonradan servis
  bağlanması, ekonomisiz açılmış oyuna kendiliğinden ödül üretmez.
- Ruletin 180 saniyelik sırası gerçek zamanlayıcıyla sona erer. Süresi dolan
  sonuç paneli kalıcı olarak teslim edilir; kapanış mesajı beklerken yeni masa
  açılmaz ve zaman aşımı ödül üretmez.
- Ekonomi paneli kalan günlük oyun kazanımını, ürüne eksik jetonu gösterir.
  Seçili unvan/rozet kaldırılabilir; eşya sahipliği ve bakiye korunur.

## Açık sınırlar

Bu dalganın ayrıntılı motor incelemesi 25 oyunu kapsar; matrisin diğer 20
satırı bu dalgada incelenmedi olarak işaretlidir. Katalog ve yönlendirme
kontrolü 45/45 olması, 45 farklı tam maçın insanlarla oynandığı anlamına gelmez.

Satranç, XOX, Gomoku ve Connect Four paneli kalıcı teslim kuyruğundan kurtarılır.
Telegram ilk mesajı alıp yanıtı kaybolursa kör tekrar gönderilmez; aynı oyun
komutuyla panel kurtarılır veya mevcut panel düğmesiyle mesaj kimliği bağlanır.
Blackjack aktif elde otomatik iade süresi tanımlamaz; kullanıcı eli sürdürür
veya /iptal ile kapatır. Bahis kaydı veri TTL'siyle silinmez. Bu Blackjack politikası ayrı ürün değerlendirmesi gerektirir.

Telegram metin tarihleri saniye hassasiyetlidir. Panel ve yanıt aynı saniyedeyse
mesaj kimliği sırası esas alınır; kesirli saatle gerçek mesajı yanlış reddeden
öneri bağımsız incelemede elendi ve yayına dahil edilmedi.

Canlı oyunculu UI/maç testleri kullanıcının önceki kararıyla ertelenmiştir.
Gerçek yoğunluk, Telegram gecikmesinde uzun süreli davranış, çoklu bot instance
ve tam Mongo/Redis/uygulama felaket kurtarması için tamamlanmış kanıt yoktur. Bu sınırlar
kapatılmadan 10/10 veya bütün oyunlar production PASS denmez.

## Kabul kayıtları

Motor incelemesi: `deploy/evidence/game-engine-review-20261022-wave16.json`.
`2026.10.22-games-16` etkinleştirildi; dört ana servis sağlıklı ve yeniden
başlama sayıları sıfır. Sürüm denetimi 25/25, yerel 2.941 PASS / 19 SKIP,
Docker ve sunucu adayı 2.954 PASS / 6 SKIP. Canlı Telegram salt okunur
sorgusunda public/private 83, yönetici 85, işletmeci 95 benzersiz komut;
dört kapsamda 45/45 oyun, yardım ve iptal doğrulandı.

Yeni Mongo yedeği aynı sunucuda izole geçici veritabanına geri yüklendi:
46 koleksiyon ve 42.131 belge okunabildi. Üretim veritabanına yazılmadı;
geçici veritabanı ve arşiv temizlendi. Yedek hash'i ve geçiş sonrası servis
sağlığı doğrulandı. Bu, tüm uygulama/Redis kurtarması veya RTO ölçümü değildir.

Kanıt: `deploy/evidence/games16-deployment-20261022.json`.
Telegram komut scope ve sınır sözleşmesi:
[Telegram Bot API](https://core.telegram.org/bots/api#setmycommands).
İzole namespace geri yükleme sözleşmesi:
[MongoDB mongorestore](https://www.mongodb.com/docs/database-tools/mongorestore/).

## Dalga 17: dört tahta oyununun sürekliliği

- Tek yetkili kayıt MongoDB'dir; bot/worker paylaşımlı lease ve sürüm CAS ile
  eski önbellekten hamle yazımı reddedilir. Forum konusu ve panel kimliği doğrulanır.
- Panel düzenlemesi başarısız olduğunda kalıcı yeniden deneme; silinmiş panelde
  aynı konuya yeni panel; 429 beklemesine uyum. Yeni sıra süresi panel teslimiyle
  başlar, teslim beklerken yeni eylem tüketilmez.
- Kazanan puan/jetonları tur ve oyuncu kimliğiyle ayrı makbuzlara bağlıdır.
  Servis yeniden başlasa da borç tamamlanır; iptal ve süre aşımı ödül üretmez.
  Genel menü iptali de bekleyen kayıtları silmez.
- Şema 22 eski dört oyun kaydını idempotent olarak işaretler; bitmiş eski
  maçların ödülü yeniden verilmez. Başarısız kayıtlar ertelenerek sonraki
  masaların 100 satırlık tarama limitinde aç kalması önlenir.
- İzole gerçek Mongo ve sentetik Telegram: 100 masa / 1.000 yasal geçiş /
  1.000 tekrar reddi; eski snapshot CAS reddi; 100 hatalı satırdan sonraki
  40 sağlıklı satıra erişim. Ayrıntı ve sınırlar
  `src/tests/loadfixtures/board_load_evidence_draft.md` içinde.

Bu değerlendirme 45 oyunun tüm ayrıntılarının kusursuzluğu, gerçek Telegram
kotası, uzun süreli kesinti SLA'sı veya insanlarla maç kabulü iddiası değildir.

## Dalga 17 yayın kanıtı

`2026.10.22-games-17`, şema 22 ve kaynak `139ad03` sunucuda etkin.
25/25 yayın kontrolü geçti. Temiz yerel ortamda 2.974 PASS / 19 SKIP;
Docker ve sunucu adayında 2.987 PASS / 6 SKIP. Dört ana servis sağlıklı,
yeniden başlama ve kontrol penceresinde traceback yok. Telegram salt okunur
kontrolünde 45 oyun, 38 animasyonlu ikon, sıfır bekleyen güncelleme; komut
kapsamları 83/83/85/95 ve her kapsamda 45 oyun girişi doğrulandı.

İzole yük tekrarı: 100 masa, 1.000 geçerli geçiş, 1.000 tekrar reddi,
sıfır hata; 49,75 geçiş/sn, p50/p95/p99 424/706/1.039 ms. Gerçek Telegram
kapasitesi değildir. İzole test koleksiyonu kalıntısı sıfır.

Yeni yedek geçici veritabanına geri yüklendi: 46 koleksiyon, 42.131 belge.
Geçici veritabanı/arşiv temizlendi, üretim verisi değiştirilmedi. Canlı şema
ve üç kurtarma indeksi doğrulandı; bu kontrolde mevcut tahta kaydı sıfırdı.
Eski kayıt backfill ve idempotency kanıtı otomatik testlerdedir.

Kanıt: `deploy/evidence/games17-deployment-20261022.json` ve
`deploy/evidence/games17-review-20261022.json`. Canlı oyunculu kabul ve
uzun süreli gerçek Telegram kesinti/yük ölçümü hâlâ NOT_RUN; GitHub değişmedi.
