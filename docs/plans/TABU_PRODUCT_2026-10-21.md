# Tabu ürün sözleşmesi ve uygulama sırası

Durum: 40 oyunluk yol haritasının ikinci uygulama adımı. Kullanıcının “adım adım uygula; yerel, sunucu, Telegram; GitHub sonra” talimatı kapsamında. Henüz çalışan/yayımlanmış özellik değildir.

## Oynanış

- Kırmızı ve mavi iki takım, takım başına 2–10 kişi; başlangıçta fark en fazla bir kişi. Lobi sahibi başlatır; herkes botun özel sohbetini açmış ve DM hazırlığını doğrulamış olmalıdır.
- Takım başına `max(takım boyları) * 2` kart, sırayla A/B. Anlatıcı takım üyeleri arasında döner. Takımlar eşit kart fırsatı alır. Her kart için anlatıcı onayından başlayan 60 saniye.
- Hedef ve beş yasaklı ifade yalnız anlatıcının DM'ine gider. Grup paneli hedef veya yasaklı sözcük taşımaz. Aynı kartın yeniden teslimi süreyi yenilemez. Yeni kart öncekinin düğmelerini geçersiz kılar.
- Doğru tahmin +1, anlatıcının hedef/yasaklı ifadeyi açık kullanması −1, süre/pas 0. Takım başına maç boyunca iki pas. Tahmin yalnız aktif takımın anlatıcı dışı üyelerinden kabul edilir. Diğer takımın metni oyunu etkilemez.
- Türkçe harf ayrımı korunur. Tam sözcük/ifade, belgeli çekim ekleri ve tek harflerle açık heceleme deterministik denetlenir; belirsiz eşanlam veya örtülü anlatım otomatik cezalandırılmaz. Kart sonu delili ve gerekçe görünür; gizli kart aktifken grup metnine sızmaz.
- İtiraz, kart kapandıktan sonraki 15 saniyede bir kez kaydedilebilir. Oyun dışı grup yöneticisi delili inceleyip mevcut sonucu onaylayabilir veya tarafsız bir sıfır kararı verebilir. Karar için 45 saniye; yetkili hakem yoksa sistemin nesnel kararı korunur. Tek itiraz nedeniyle maç sonsuza dek beklemez. Bir sonuç iki kez uygulanmaz.
- İki takıma eşit ek kart çiftiyle en çok iki uzatma, ardından beraberlik. İptal/eksik maç puan veya jeton dağıtmaz. Oynanmış tamamlanmış maç sonuçları olay kimliğiyle bir kez işlenir.
- Kart özelden teslim edilemezse saat başlamaz, masa duraklar; anlatıcı tekrar deneyebilir. Lobide ayrılma/kurucu aktarımı vardır. Maç sırasında ayrılma kartı ve takım fırsatlarını koruyarak güvenli biçimde ele alınır; takım iki kişinin altına düşerse maç iptal olur. 20 dakika hareketsiz lobi kapanır.

## Kalıcılık ve güvenlik

Konu başına oturum, CAS sürümü, maç/kart/tur kimliği, atomik işlem makbuzu, süre sonu ve panel teslim kirası. Deep link token'ı rastgele, süreli ve kullanıcıya bağlı; yalnız hash'i saklanır. Grup, özel sohbet, kullanıcı ve kart bağı her mutasyonda denetlenir. Yeniden başlatma aktif süreyi yenilemez. Durumda tutulan özel kart grup renderer'ına verilse bile güvenli alanlar dışında çıktı üretmez.

## Uygulama

1. Özgün Türkçe kart bankası ve Telegram'dan bağımsız motor; hata testleri, dengeli fırsat, bütün fazlar, özel bilgi ve itiraz sonuçlarının tekliği.
2. Kalıcı servis, özel kart teslim/onay köprüsü, puan ve jeton onarımı, zamanlayıcı ve teslim kirası.
3. `/tabu`, oyun menüsü, takım düğmeleri, özel sohbet deep link'i, metin anlatım/tahmin yönlendiricisi; FeatureDefinition ve katalog.
4. Hata, tekrar, yarış, eski düğme, DM engeli, ayrılma, sınır süre, restart, itiraz ve sonuç testleri; bağımsız denetim.
5. Tam sürüm kapısı, yerel commit, sunucu yedeği/geri dönüş ile kurulum, yetkili Telegram grubunda gerçek kullanıcı denemesi. GitHub'a yazılmaz.

## İlk denetim kararları

- Gizli deste 128 bit seed ile tekrarsız karıştırılır; kartın açık kimliği kaynak kart ID bilgisini içermez.
- Kart onayından önce gruba bir başlangıç mesajı gönderilir; bu mesajın kimliği ve onay aynı CAS içinde bağlanır. Daha eski mesajlar, Telegram tarihinin saniye çözünürlüğünden bağımsız reddedilir.
- Tek grup paneli düzenlenir. Davet düğmesinde aynı isteğin yeniden işlenmesi ve 30 saniye içindeki tekrarlar yeni link/mesaj oluşturmaz.
- Teslim veya anlatıcı onayı 120 saniye içinde tamamlanmazsa masa ödülsüz kapanır.
- Puan/jeton onarımı geçici hata verse bile maç sonucu gösterilir; tamamlanınca panel yeniden güncellenir.
