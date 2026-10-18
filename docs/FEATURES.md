# Arel — özellikler ve komut rehberi

Bu rehber 18 Ekim 2026 tarihinde incelenen kaynak ağacının kullanıcı ve yönetici yüzeylerini açıklar. Komut adları `main.py` kayıtlarından, davranışlar handler/servis kodu ile registry tanımlarından alınmıştır.

> **Bilinen engel:** İncelenen `242a1ec` commit'inde `src/handlers/command_handler.py` kesilmiş çıktı metni içeriyor ve Python tarafından ayrıştırılamıyor. Ayrıca şehir dizinindeki geçersiz karakter eşleme tablosu import sırasında hata verir. Bu engeller giderilmeden uygulama çalışır kabul edilemez. Aşağıdaki tablo, kaynakta bulunan tanımları ve akışları belgelendirir; her satırın tamamlanmış, erişilebilir veya canlıda başarılı olduğunu iddia etmez. Sunucudaki farklı dağıtım ayrıca incelenmelidir.

**Durumları okuma:** Bütün satırlar **kaynakta tanımlı** anlamındadır. **AI**, yapılandırılmış uygun model bağlantısı; **dış veri**, kaynak erişimi ve üretimde gerekli kaynak onayı; **worker**, çalışan kuyruk işleyicisi; **kalıcılık**, ilgili MongoDB/Redis/Chroma bağlantıları gerektirir. Bu rehber için tek tek canlı özellik kabulü **NOT_RUN** durumundadır. Test, kaynak inceleme ve canlı kabul ayrı kanıt türleridir.

Komutlar Telegram'da `/komut@bot_kullanıcı_adı` biçiminde de kullanılabilir. Grup yöneticiliği, bot sahibi veya sudo yetkisiyle aynı değildir.

## 1. Başlangıç ve sohbet

| Özellik | Erişim | Davranış ve koşul |
|---|---|---|
| Başlangıç ve menü | `/start`, `/menu`, `/help` | Karşılama, kategori menüleri, yardım ve başlangıç bağlantıları |
| Bağlamlı sohbet | Özel mesaj, grupta mention veya bot mesajına yanıt | AI yanıtı, sohbet bağlamı ve araç yönlendirmesi; AI gerekir |
| Hedefli grup yanıtları | Mention/yanıt akışı | Yakın dönem mesajlarını kullanıcı ve konu kapsamında seçip ayrı yanıtlar oluşturabilen yönlendirme; özellik bayrağı ve AI gerekir |
| Kişisel üslup | `/persona`, `/uslup` | Özel sohbette üslup tercihi; ciddi/dengeli/iğneleyici ve onaylı roast seçimi |
| Grup üslubu | `/ayar kişilik ...` | Yönetici kontrollü üslup; roast için açık onay ve hassas konularda ayrı politika |
| Gözlem modu | `/obs on`, `/obs off`, `/sohbet` | Yönetici/sudo kapsamındaki sohbet gözlem ayarı |
| Arayüz dili | `/lang` | Dil seçimi; bütün metinlerin aynı dilde olması ayrıca doğrulanmalıdır |
| Sohbet/veri temizleme | `/clear` | Sohbet bağlamı, kişisel hafıza ve belge temizleme akışları; kapsam serviste belirlenir |

Kaynaklar: [`main.py`](../main.py), [`orchestrator.py`](../src/orchestrator.py), [`message_handler.py`](../src/handlers/message_handler.py), [`personality.py`](../src/ai/personality.py), [`group_replies`](../src/group_replies).

## 2. Arama, üretim ve çoklu ortam

| Özellik | Komut/girdi | Davranış ve koşul |
|---|---|---|
| Web araması | `/ara sorgu`, `/search sorgu` | Kaynak bağlantılı sonuçlar; dış arama adaptörü ve erişim politikası |
| Web sayfası okuma | AI araç akışı | Herkese açık sayfadan sınırlı içerik okuma; URL, özel IP, yönlendirme ve boyut kontrolleri; bağımsız `/oku URL` komutu değildir |
| Çeviri | `/cevir İngilizce Merhaba` veya mesaja yanıt | Hedef dile AI çevirisi; `oku` seçeneğiyle seslendirme kuyruğu |
| Hikâye üretimi | `/hikaye` | İstek ayrıştırma ve AI üretimi |
| Görsel üretimi | `/image açıklama` | Görsel adaptörü üzerinden iş kuyruğu; uygun hesap/anahtar ve worker gerekir |
| Görsel analizi ve metin okuma | Fotoğraf açıklaması/yanıtı ve işlem butonları | Açık kullanıcı niyeti sonrası analiz, OCR veya sticker seçimi; görsel modeli ve worker gerekir |
| Sesli mesaj işleme | Telegram sesli mesajı | Ses çözümleme ve yanıt; transkripsiyon modeli, ek dosya kuyruğu ve worker gerekir |
| Metin seslendirme | `/oku metin`, `/seslendir metin`; mesaja yanıt | Sınırlı metni sese çevirip gönderme; TTS servisi ve worker gerekir |
| Şarkı sözü araması | `/soz`, `/sarki`, `/lyrics` | İzinli kaynaklarda arama, sınırlı alıntı ve kaynak bağlantısı |

Metin, görsel, transkripsiyon ve embedding yetenekleri ayrı yapılandırılır. Bir metin anahtarının bulunması bütün ortam türlerinin kullanılabileceğini göstermez.

Kaynaklar: [`command_handler.py`](../src/handlers/command_handler.py), [`media_handler.py`](../src/handlers/media_handler.py), [`router.py`](../src/ai/router.py), [`web_search.py`](../src/features/providers/web_search.py), [`web_reader.py`](../src/features/providers/web_reader.py), [`lyrics.py`](../src/features/providers/lyrics.py), [`image_generation`](../src/image_generation).

## 3. Belgeler ve kişisel hafıza

| Özellik | Komut/girdi | Davranış ve koşul |
|---|---|---|
| Belge yükleme | Özel sohbette PDF, DOCX veya TXT | MIME/boyut kontrolleri, DOCX arşiv kontrolleri ve kuyruk üzerinden içerik alma |
| Belgeye dayalı arama | Yüklemeden sonra ilgili soru | Kullanıcı kapsamındaki parçaları AI bağlamına ekleme; Chroma, metadata deposu ve worker gerekir |
| Belge yönetimi | `/belgeler`, `/documents` | Kişisel belgeleri listeleme ve sahiplik/onay kontrollü silme |
| Kalıcı hafıza izni | `/hafiza on`, `/memory on` | Kullanıcı gerçeklerinin kalıcı tutulmasına açık opt-in |
| Hafızayı kapatma | `/hafiza off` | İzni kapatma ve kayıtlı gerçekleri silme |
| Hafıza inceleme/silme | `/hafiza show`, `/hafiza delete ID`, `/hafiza clear` | Kendi kayıtlarını görme, tek kayıt veya bütün gerçekleri silme; `clear` izin tercihini korur |

Hafıza kontrolleri özel sohbetle sınırlıdır. Belgelerin AI yönlendirmesi ve depolama politikası dağıtım tarafından belirlenir. Kalıcı hafıza izni ve geçici sohbet bağlamı farklı veri yaşam döngülerine sahiptir.

Kaynaklar: [`documents`](../src/documents), [`rag_engine.py`](../src/rag_engine.py), [`document_search.py`](../src/features/providers/document_search.py), [`orchestrator.py`](../src/orchestrator.py), [`memory_tool.py`](../src/tools/memory_tool.py).

## 4. Notlar, görevler ve plan

| Özellik | Komut/örnek | Davranış |
|---|---|---|
| Not oluşturma | `/not alışveriş listesi: süt, ekmek` | Kullanıcıya ait not kaydı |
| Notları arama ve silme | `/notlar`, `/notlar toplantı` | Listeleme, metin araması ve silme butonları |
| Verileri dışa aktarma | `/notlar export` | Kişisel üretkenlik verilerini JSON dosyası olarak gönderme |
| Toplu not temizleme | `/notlar temizle` | Kullanıcıya ait notları onay butonuyla silme |
| Görevler | `/gorev Raporu tamamla`, `/gorevler` | Ekleme, listeleme, tamamlama ve silme |
| Günlük/haftalık plan | `/plan`, `/plan hafta`, `/gunlukplan`, `/haftalikplan` | Açık görevleri, yaklaşan/geciken hatırlatıcıları saat dilimine göre birleştirme |
| Hatırlatıcı | `/remind 10dk Su iç`, `/hatirlat yarın 10:00 Toplantı` | Süre veya Türkçe tarih/saat ifadesiyle kurulum |
| Tekrarlı hatırlatıcı | `/remind her gün 09:00 Su iç` | Günlük, haftalık ve hafta günü kuralları; kalıcı kayıt üzerinden toparlanma |
| Hatırlatıcı yönetimi | `/hatirlatmalar` | Bekleyen kayıtlar ve iptal butonları |
| Kişisel günlük özet | `/gunluk_ozet`, `/daily_brief` ve menü | Hava, deprem, haber ve kişisel plandan bileşik özet; zamanlanmış akış seçimi |

Hatırlatıcı parser'ı geçmiş tarihleri, geçersiz saatleri ve ilk vadesi 30 günü aşan talepleri reddeder. Yeniden başlatma sonrası toparlanma ve teslim için depolar ile zamanlayıcı/worker akışı gerekir. Plan ekranı mevcut kayıtların görünümüdür; harici takvim senkronizasyonu olarak sunulmaz.

Kaynaklar: [`command_handler.py`](../src/handlers/command_handler.py), [`productivity_service.py`](../src/services/productivity_service.py), [`reminder_service.py`](../src/services/reminder_service.py), [`daily_brief_service.py`](../src/services/daily_brief_service.py).

## 5. Türkiye, hava ve ulaşım

| Özellik | Komut | Kaynak / davranış |
|---|---|---|
| Şehir dizini | `/sehir`, `/sehirler` | Repository içindeki il/ilçe snapshot'ı |
| Plaka ve telefon kodu | `/plaka`, `/telefon` | Yerel dizinde kod/şehir araması |
| İlçe sorgusu | `/ilce` | Şehir ve ilçe bilgileri |
| Hava durumu | `/hava İstanbul` | Dış hava kaynağı; sonuç ekranında 1/3/7 günlük tahmin |
| Hava kalitesi | `/hava_kalite İstanbul`, `/hava kalite İstanbul` | AQI, PM2.5/PM10 ve kaynağın sunduğu diğer ölçümler; bölgesel kapsama bağlıdır |
| Son depremler | `/deprem` | AFAD verisi; kaynak ve alınma zamanı |
| Nöbetçi eczane | `/eczane` | Şehir/ilçe girdili dış veri sorgusu |
| Akaryakıt | `/akaryakit` | Dış kaynaktan yakıt fiyatları |
| Namaz vakitleri | `/ezan` | Şehir bazlı dış kaynak sorgusu |
| Aktüel ürünler | `/bim` | BİM aktüel sayfasından veri |
| Uçuş arama bağlantıları | `/ucak` | Doğrulanmış arama sayfası bağlantıları; canlı fiyat veya rezervasyon yoktur |

Yerel şehir dizini dış ağ gerektirmez. Diğer araçlar kaynak erişimi, şema değişiklikleri, önbellek ve üretim kaynak onayına bağlıdır. Deprem/hava takipleri garantili erken uyarı sistemi olarak sunulmaz.

Kaynaklar: [`city_directory.py`](../src/features/city_directory.py), [`providers`](../src/features/providers), [`catalog.py`](../src/features/catalog.py), [`DATA_SOURCES.md`](DATA_SOURCES.md).

## 6. Finans, haber ve kültür

| Özellik | Komut | Davranış ve koşul |
|---|---|---|
| Döviz ve altın | `/doviz`, `/altin` | Dış kaynak kurları; ortak currency adaptörü |
| Kripto | `/kripto btc` | Desteklenen semboller için CoinGecko verisi |
| Borsa | `/borsa THYAO` | Yahoo Finance adaptöründe doğrulanmış sembol/alias sorgusu |
| Piyasa grafiği | `/borsa` sonuç akışı | Grafik üretimi ve fotoğraf teslimi; renderer/worker gerekir |
| Haberler | `/haber` | RSS başlıkları ve kaynaklar; güncel/dünya/ekonomi/spor/kültür/teknoloji/sağlık kategorileri |
| Haber özeti | `/digest` | Ortak haber runtime'ından özet; önbellek ve güncellik bilgisi |
| Kültür önerileri | `/kultur` | Film, dizi, kitap ve müzik kategorileri |
| Kategori komutları | `/filmoneri`, `/dizioneri`, `/kitaponeri`, `/sarkioneri` | İlgili kategoriden kaynak bağlantılı öneriler |

Veriler kaynak ve önbelleğe göre gecikebilir. Kültür kaynakları Open Library, TVmaze ve iTunes Search adaptörleridir. Borsa/kripto yüzeylerinde emir veya yatırım hesabı entegrasyonu bulunmaz.

Kaynaklar: [`market.py`](../src/features/providers/market.py), [`crypto.py`](../src/features/providers/crypto.py), [`rss_news.py`](../src/features/providers/rss_news.py), [`culture.py`](../src/features/providers/culture.py), [`market_chart`](../src/market_chart).

## 7. Bildirim merkezi ve otomasyon

| Özellik | Komut/girdi | Davranış |
|---|---|---|
| Takip merkezi | `/bildirim`, `/takip` | Kategori tabanlı kişisel panel; grupta yönetici paneli |
| Kayıtları listeleme | `/takipler`, `/bildirimler`, `/takip liste` | Kullanıcıya veya yetkili gruba ait akışlar |
| Takibi durdurma | `/takip dur ID` veya panel | Sahiplik kontrollü kapatma |
| Saat dilimi/sessiz saatler | `/bildirim_ayar`, `/bildirimayar` | Özel sohbette kişisel bildirim tercihleri |
| Eşik alarmı | `/alarm kur usd 40 üstü` | Desteklenen kur/yakıt metriği için eşik |
| Alarm yönetimi | `/alarm liste`, `/alarm kapat ID` | Kişisel eşik kayıtlarını listeleme ve kapatma |
| Grup duyuruları | `/duyuru` | Yönetici kontrollü günlük/haftalık duyuru kurma, listeleme ve durdurma |
| Teslim tekilleştirme | Zamanlanmış akışlar | Teslim kaydı, payload parmak izi, lease ve tekrar deneme yolları |

Kişisel panelde hava/hava kalitesi, deprem, kur, yakıt, kripto, haber ve aktüel takipleri bulunur. Grup panelinde hava/hava kalitesi ve haber için şehir, günlük/haftalık zaman ve forum konusu kapsamını koruyan akışlar vardır. Desteklenen aralıklar panel ve parser tarafından sınırlandırılır. Bu mekanizmalar, bütün bildirim türlerinin canlı teslim veya yeniden başlatma testini geçtiğini göstermez.

Kaynaklar: [`notification_ui.py`](../src/services/notification_ui.py), [`subscription_service.py`](../src/services/subscription_service.py), [`announcement_service.py`](../src/services/announcement_service.py), [`command_handler.py`](../src/handlers/command_handler.py).

## 8. Medya indirme

| Özellik | Komut/girdi | Davranış ve koşul |
|---|---|---|
| Bağlantı indirme | `/indir URL`, `/dl URL`, `/download URL` | Desteklenen herkese açık medyayı kuyruğa alma |
| Otomatik bağlantı algılama | Metin veya açıklamadaki desteklenen URL | Sohbetin `media_enabled` ayarı ve mesaj politikası |
| Platform adaptörü | Instagram, TikTok, YouTube, X, Facebook, Reddit | `yt-dlp` platform eşlemesi; her URL, format ve erişim türü için başarı garantisi değildir |
| İş durumu/iptal | `/jobs` | Kullanıcı kapsamındaki medya işleri; yönetim yetkisine göre ek görünürlük |
| Tekrar kullanma | Aynı URL/medya talepleri | Aktif talepleri birleştirme, Telegram `file_id` önbelleği ve geçersiz referansta tekrar indirme |
| Dönüştürme ve teslim | İşleyici akışı | Boyut/süre/zaman aşımı sınırları, dönüştürme, tekrar deneme ve temizlik |

Varsayılan `MEDIA_MAX_FILE_BYTES` sınırı 45 MiB'dir. Özel hesap, erişim kısıtlı içerik ve oturum gerektiren bağlantılar herkese açık indirme kapsamı olarak tanıtılmaz. Yönetici cookie kasası isteğe bağlı bir işletim yüzeyidir; kullanıcıdan hesap şifresi istenmez.

Kaynaklar: [`media`](../src/media), [`media_handler.py`](../src/handlers/media_handler.py), [`orchestrator.py`](../src/orchestrator.py), [`config.py`](../config.py).

## 9. Alıntılar ve sticker

| Özellik | Komut/girdi | Davranış |
|---|---|---|
| Mesaj alıntısı | Mesaja yanıt olarak `/q` | Görsel/sticker alıntı; en fazla 10 mesaj seçimi |
| Alıntı seçenekleri | `/q 3`, `/q p`, `/q i`, `/q r`, `/q theme=dark` | Sayı, PNG, dosya, yanıt gösterimi ve tema |
| Şablon/ayarlar | `/qtemplate`, `/qtheme`, `/qcolor`, `/qemoji`, `/qrate` | Şablon, tema, renk, emoji ve oylama; bazı ayarlar yönetici kapsamındadır |
| Alıntı arşivi | `/qtop`, `/qrand` | En iyi alıntıların inline görünümü ve rastgele kayıt |
| Alıntı silme | `/qd` | Yetki kontrolüyle ilgili kayıt silme |
| Sticker oluşturma | Desteklenen medyaya yanıtla `/sticker` | Statik/video sticker; format, FFmpeg ve süre sınırları |
| Sticker arama | `/st kelime`, inline sorgu | Katalog ve etiketler üzerinden arama |
| Katalog ve paketler | `/catalog`, `/packs` | Katalog işlemleri ve kişisel paket görünümü |
| Katalog kaydı silme | `/stdelete` | Yetki kontrolüyle katalog girdisi silme |
| Paket yönetimi | `/stpack` | Sahiplik kontrollü başlık, emoji, sıra, küçük resim ve silme |
| Otomatik etiketleme | Sticker işleme sonrası | Uygun görsel model/worker yapılandırıldığında AI etiketleri |

Paket komut örnekleri:

```text
/stpack yenile paket_adı Yeni Başlık
/stpack emoji 😀 😂
/stpack sıra 1
/stpack thumbnail
/stpack sticker_sil
/stpack sil paket_adı
```

Emoji, sıra, küçük resim ve tek sticker silme işlemleri paket sticker'ına yanıtla kullanılır. Paket/tek sticker silmede onay akışları vardır. `/ayar` içindeki alıntı ve medya anahtarları ilgili sohbet yüzeylerini kapatabilir.

Kaynaklar: [`quote_handler.py`](../src/handlers/quote_handler.py), [`command_handler.py`](../src/handlers/command_handler.py), [`quotes`](../src/quotes), [`stickers`](../src/stickers).

## 10. Oyunlar, puan ve sanal ekonomi

| Oyun/özellik | Komut | Davranış |
|---|---|---|
| Oyun salonu | `/oyun` | Oyun, kategori ve zorluk seçimi |
| Bilgi yarışması | `/quiz` | Kategorili yerel soru havuzları ve yanıt/puan akışı |
| Kelime türetme | `/kelimeoyunu`, `/kelimeoyunu bilim zor` | Sözlük tabanlı doğrulama, kategori/zorluk, harf karıştırma ve ipucu |
| Adam asmaca | `/adamasmaca`, `/adamasmaca kolay` | Harf klavyesi, can, ipucu, zorluk ve tur kontrolü |
| Kelime anlatmaca | `/anlat`, `/anlat uzman` | Zamanlı tahmin, ipucu, pas ve bitirme |
| Batak | `/batak` | Kalıcı lobi/masa, sıra tabanlı kart oynama ve kural kontrolleri |
| Blackjack | `/blackjack [bahis]` | Kart çekme/pas/ikiye katlama, sanal bakiye ve sonuçlandırma |
| Rus ruleti | `/rusruleti` | Sanal lobi ve tur butonları |
| Şişe çevirme | `/sisecevir` | Grup sosyal oyun akışı |
| Doğruluk/cesaret | `/d`, `/c` | Yerel soru/görev seçimi |
| Düello | `/duello` | Karşılıklı oyun akışı |
| Telegram şans oyunları | `/zar`, `/yazitura`, `/slot` | Zar, yazı-tura ve slot etkileşimleri |
| Tur bitirme | `/iptal`, `/bitir` | Aktif oyun durumunu yetkiye göre sonlandırma |
| Sıralama | `/leaderboard`, `/liderlik`, `/lb` | Sohbet kapsamındaki puan tablosu; kart veya metin görünümü |
| Profil | `/profile`, `/profil` | Oyuncu istatistikleri ve profil kartı |
| Başarımlar/sezon | `/basarimlar`, `/achievements`, `/sezon`, `/season` | Oyun olaylarından başarımlar ve dönemlik sıralama |
| Sanal bakiye | `/ekonomi`, `/bakiye` | Sohbet kapsamındaki coin hesabı |
| Günlük ödül | `/gunluk`, `/daily` | Tekilleştirilmiş günlük coin talebi |

Kelime oyunları kolay, normal, zor ve uzman profilleri kullanır. Puanlama hız, uzunluk, ipucu ve deneme gibi oyuna özgü ölçütlere bağlıdır. Oyunların kalıcılığı eşit varsayılmaz; yeniden başlatma toparlanması ve eşzamanlı callback davranışı oyun başına doğrulanmalıdır. Coin'ler oyun içi sanal kayıtlardır; ödeme/çekim entegrasyonu bu yüzeyde bulunmaz.

Kaynaklar: [`games`](../src/games), [`economy_service.py`](../src/services/economy_service.py), [`leaderboard_service.py`](../src/services/leaderboard_service.py), [`orchestrator.py`](../src/orchestrator.py), [`main.py`](../main.py).

## 11. Grup yönetimi

| Özellik | Komut/yüzey | Davranış ve yetki |
|---|---|---|
| Grup ayarları | `/ayar` | Grup yöneticilerine ayar paneli; özel sohbette kişisel medya/alıntı anahtarları |
| Moderasyon | `/ayar moderation on`, `/ayar link on`, `/ayar repeat on` | Link, tekrar ve moderasyon kuralları |
| Flood/kelime filtresi | `/ayar flood 8`, `/ayar word kelime` | Yoğunluk sınırı ve engelli kelime kaydı |
| Moderasyon raporu | `/moderasyon`, `/mod` | Yetkili yönetici için sınırlı dönem ihlal özeti |
| Grup ban/unban | `/ban`, `/unban` | Mesaja yanıt veya kullanıcı ID'si; grup ve bot Telegram yetkileri gerekir |
| Karşılama/ayrılma | Grup ayarları ve üyelik olayları | Yönetici kontrollü üyelik mesajları |
| İstatistikler | `/istatistik [1\|7\|30\|90]` | Yöneticiye dönem, yoğun saat ve aktif kullanıcı özeti |
| Ortak bildirim/duyuru | `/bildirim`, `/duyuru` | Grup/forum konusu kapsamında yönetici akışları |

Ban komutları özel sohbette sudo için bot kara listesi, grupta Telegram üye yönetimi anlamına gelir. Moderasyon eylemleri botun mesaj silme/üye kısıtlama izinlerine bağlıdır.

Kaynaklar: [`moderation`](../src/moderation), [`message_handler.py`](../src/handlers/message_handler.py), [`command_handler.py`](../src/handlers/command_handler.py), [`orchestrator.py`](../src/orchestrator.py).

## 12. Telegram inline arama

Botun inline modu Telegram bot ayarlarında etkin olmalıdır. `@bot_kullanıcı_adı sorgu` biçimiyle:

| Örnek sorgu | İçerik |
|---|---|
| `sehir Ankara`, `il Ankara` | Şehir dizini |
| `sticker kedi`, `st:kedi` | Sticker kataloğu |
| `haber teknoloji` | Haber araması |
| `ara sorgu`, `search sorgu`, `web sorgu` | Web sonuçları |
| `soz eser`, `sarki eser`, `lyrics eser` | Şarkı sözü araması |
| `kultur kitap` | Kültür önerileri |
| `kripto btc`, `borsa THYAO` | Finans sonuçları |
| `top:SOHBET_ID` | Alıntı sıralaması; genellikle `/qtop` butonuyla açılır |

Sorgu uzunluğu, sayfalama ve sonuç sayısı sınırları vardır. Sonuç seçildiği sohbette paylaşılır; hassas veri eklenmemelidir. Kaynak erişimi ve sohbet bazlı özellik kapıları yine geçerlidir.

Kaynak: [`inline_query_service.py`](../src/services/inline_query_service.py).

## 13. Bot sahibi ve operasyon

Bot sahibi `OWNER_ID` ile, sudo listesi ayrı yetki kaydıyla belirlenir.

| Komut | İşlev / yetki |
|---|---|
| `/health` | Bot sahibi: depolar, kuyruklar, renderer ve sağlayıcı sağlık özeti |
| `/providers`, `/providers governance`, `/providers reset` | Bot sahibi: sağlayıcı durumu, kaynak onayı ve devre sıfırlama |
| `/features`, `/features on\|off ANAHTAR` | Bot sahibi: manifest ve desteklenen özellikler için süreç içi anahtar; restart sonrası config'e döner |
| `/metrics` | Bot sahibi: medya/iş ölçümleri |
| `/cache clear` | Bot sahibi: medya önbelleğini temizleme |
| `/maintenance on\|off` | Bot sahibi: medya bakım durumu |
| `/audit` | Bot sahibi: son denetim kayıtları |
| `/medya_cookie` | Bot sahibi: isteğe bağlı cookie fallback durumu/yönetimi; şifreli kasa ve açık yapılandırma gerekir |
| `/sudo`, `/unsudo` | Bot sahibi: sudo yetkisi ekleme/kaldırma |
| `/sudohelp`, `/sudolist` | Sudo yardım ekranı ve yetkili listesi |
| `/broadcast` | Sudo: aktif sohbetlere toplu bot duyurusu |
| `/groups` | Sudo: kayıtlı gruplar |
| `/ban`, `/unban` | Özel sohbette sudo: bot kara listesi |
| `/ping`, `/test` | Kayıtlı tanılama komutları; ayrıntı ve maliyet kapsamı handler'dan kontrol edilmelidir |

Kaynakta HTTP sağlık/Prometheus uç noktası, token kontrollü izleme, bot/worker ayrımı, MongoDB/Redis kalıcılığı, iş tekrar denemeleri, yedekleme/dağıtım betikleri ve isteğe bağlı MCP entegrasyonu bulunur. Etkinlikleri yapılandırmaya bağlıdır. `/health` çıktısı bütün kullanıcı akışlarının uçtan uca kabulü değildir.

Kaynaklar: [`orchestrator.py`](../src/orchestrator.py), [`sudo_manager.py`](../src/sudo_manager.py), [`observability`](../src/observability), [`worker.py`](../src/worker.py), [`docker-compose.yml`](../docker-compose.yml), [`scripts`](../scripts).

## Yapılandırma ve kabul sınırları

| Gereksinim | İlgili yapılandırma |
|---|---|
| Telegram/rol | `TELEGRAM_TOKEN`, `OWNER_ID`, `TELEGRAM_MODE`, `APP_ROLE`, `APP_ENV` |
| Model yetenekleri | Bağlantı/anahtar alanları, `AI_PROVIDER_ROUTES`, `AI_VISION_MODEL`, `AI_TRANSCRIPTION_MODEL`, `AI_EMBEDDING_MODEL` |
| Kalıcı depolar | `MONGODB_URI`, `REDIS_URL`, gerekli bağlantı bayrakları ve Chroma disk birimi |
| Özellik erişimi | `FEATURE_FLAGS`, `DISABLED_FEATURES`, `AI_TOOL_ALLOWLIST`, `REQUIRE_PROVIDER_APPROVAL` |
| Dosya/iş sınırları | `MEDIA_*`, `RAG_*`, `ATTACHMENT_*`, `QUOTE_*`, `SPEECH_*`, `STICKER_*` |
| İzleme | `OBSERVABILITY_*`, `MONITORING_ENABLED` ve dışarıda saklanan token/parola dosyaları |

Tam alanlar için [`.env.example`](../.env.example) ve [`config.py`](../config.py), teknik registry için [FEATURE_CATALOG.md](FEATURE_CATALOG.md) incelenmelidir. Katalogdaki “aktif” ifadesi canlı test sonucu değildir. Kaynak onayı, AI anahtarı, servis sağlığı ve Telegram teslim kanıtı ayrı koşullardır.

Yeni özellik eklerken komut kaydını, gerçek handler/servis akışını ve yapılandırma gereksinimini doğrulayın. Ölçülmemiş gecikme, kullanıcı kapasitesi veya üretim kararlılığı iddiası eklemeyin.
