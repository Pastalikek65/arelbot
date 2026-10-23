# Arel — özellikler ve komut rehberi

Bu rehber, 23 Ekim 2026 tarihli güncel kaynakta bulunan kullanıcı ve yönetici yüzeylerini açıklar. Daha önce ayrı sürümlerde bulunan ekler de artık bu depoda yer alır. Komut adları kayıt, handler ve servis tanımlarına dayanır.

> **Doğrulama kapsamı:** Bu rehber kaynakta bulunan komutları ve koşullarını belgeler. Otomatik yönlendirme testleri ile gerçek Telegram kabulü ayrı değerlendirilir; sonuçlar ve açık sınırlar [güvenilirlik kaydında](RELIABILITY-2026-10-18.md) tutulur.

**Durumları okuma:** Bütün satırlar **kaynakta tanımlı** anlamındadır. **AI**, yapılandırılmış uygun model bağlantısı; **dış veri**, kaynak erişimi ve üretimde gerekli kaynak onayı; **worker**, çalışan kuyruk işleyicisi; **kalıcılık**, ilgili MongoDB/Redis/Chroma bağlantıları gerektirir. Bu rehber için tek tek canlı özellik kabulü **NOT_RUN** durumundadır. Test, kaynak inceleme ve canlı kabul ayrı kanıt türleridir.

Komutlar Telegram'da `/komut@bot_kullanıcı_adı` biçiminde de kullanılabilir. Grup yöneticiliği, bot sahibi veya sudo yetkisiyle aynı değildir.

## 1. Başlangıç ve sohbet

| Özellik | Erişim | Davranış ve koşul |
|---|---|---|
| Başlangıç ve menü | `/start`, `/menu`, `/help` | Karşılama, kategori menüleri, yardım ve başlangıç bağlantıları |
| Bağlamlı sohbet | Özel mesaj, grupta mention veya bot mesajına yanıt | AI yanıtı, sohbet bağlamı ve araç yönlendirmesi; AI gerekir |
| Hedefli grup yanıtları | Mention/yanıt akışı | Yakın dönem mesajlarını kullanıcı ve konu kapsamında seçip ayrı yanıtlar oluşturabilen yönlendirme; özellik bayrağı ve AI gerekir |
| Kişisel üslup | `/persona`, `/uslup` | Özel sohbette üslup tercihi; Normal ve açık seçimli Roast |
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
| Görsel üretimi | `/image açıklama` veya “Arel bana bir T-rex görseli oluştur” | Prism/Flare ve mevcut yedek adaptör üzerinden iş kuyruğu; `--model prism` / `--model flare` açık seçimdir ve başka modele düşmez; uygun hesap/anahtar ve worker gerekir |
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

Veriler kaynak ve önbelleğe göre gecikebilir. Kültür kaynakları Open Library, TVmaze, Wikidata ve iTunes Search adaptörleridir. Borsa/kripto yüzeylerinde emir veya yatırım hesabı entegrasyonu bulunmaz.

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

**45 oyun için tam liste:** [Oyun rehberi](GAMES.md) — her oyunun komutu, oyuncu sayısı ve mekaniği. Aşağıdaki tablo ortak giriş ve ekonomi yüzeylerini özetler.

| Oyun/özellik | Komut | Davranış |
|---|---|---|
| Oyun salonu | `/oyun` | Oyun, kategori ve zorluk seçimi |
| Bilgi yarışması | `/quiz` | Kategorili yerel soru havuzları ve yanıt/puan akışı |
| Kelime türetme | `/kelimeoyunu`, `/kelimeoyunu bilim zor` | Sözlük tabanlı doğrulama, kategori/zorluk, harf karıştırma ve ipucu |
| Adam asmaca | `/adamasmaca`, `/adamasmaca kolay` | Harf klavyesi, can, ipucu, zorluk ve tur kontrolü |
| Kelime anlatmaca | `/anlat`, `/anlat uzman` | Zamanlı tahmin, ipucu, pas ve bitirme |
| Batak | `/batak` | Kalıcı lobi/masa, sıra tabanlı kart oynama ve kural kontrolleri |
| Blackjack | `/blackjack [bahis]` | Kart çekme/durma/ikiye katlama, ilk hamlede yarım iadeli pes etme, sanal bakiye ve sonuçlandırma |
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
| Mağaza ve envanter | `/magaza`, `/envanter` | Sanal coin ile kozmetik edinme ve sahip olunan öğeleri görüntüleme |
| Günlük ödül | `/gunluk`, `/daily` | Tekilleştirilmiş günlük coin talebi |

Kelime oyunları kolay, normal, zor ve uzman profilleri kullanır. Puanlama hız, uzunluk, ipucu ve deneme gibi oyuna özgü ölçütlere bağlıdır. Oyunların kalıcılığı eşit varsayılmaz; yeniden başlatma toparlanması ve eşzamanlı callback davranışı oyun başına doğrulanmalıdır. Coin'ler oyun içi sanal kayıtlardır; ödeme/çekim entegrasyonu bu yüzeyde bulunmaz.

Kaynaklar: [`games`](../src/games), [`economy_service.py`](../src/services/economy_service.py), [`leaderboard_service.py`](../src/services/leaderboard_service.py), [`orchestrator.py`](../src/orchestrator.py), [`main.py`](../main.py).

## 11. Grup yönetimi

Bir mesaja yanıtla `/report` grup kayıt kanalına (`/logchannel`), `/report itiraz` ise federasyonun itiraz kanalına (`/setappeallog`) kimlik ve işlem kaydı gönderir; mesaj içeriği aktarılmaz.

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

## Güncel yerel ve sunucu sürümündeki ekler

**Sürüm kapsamı:** Bu bölümdeki oyunlar, moderasyon yüzeyi ve komut kayıt katmanı güncel kaynakta bulunmaktadır. Komutların kayıt ve yönlendirme doğrulaması, canlı Telegram kullanımından ayrı tutulur; canlı kabul kanıtları güvenilirlik kaydındadır.

### Ek oyunlar ve kart altyapısı

| Yetenek | Komut/yüzey | Kaynakta tanımlanan davranış |
|---|---|---|
| Connect Four | `/connect4` | İki oyunculu lobi, 6×7 tahta, dört taşı birleştirme, sıra ve callback kontrolleri, kalıcı durum |
| XOX | `/xox`, `/tictactoe` | İki oyunculu lobi; mevcut komut başlangıcında 6×6 tahta ve dört taşlık kazanım; durum ve eski panel kimliği kontrolleri |
| Gomoku | `/gomoku`, `/besli` | İki oyunculu lobi; varsayılan 10×10 tahta, beş taşlık kazanım; 6/7/8/10/12/15 boyut seçimi ve forum konusu kapsamı |
| Akinatör | `/akinator` | Türkçe karakter soruları, geri alma, tahmin onayı ve soru sürümü kontrolü; dış Akinator servisi erişimi gerekir |
| Yaş tahmini | `/yas`, `/yastahmin 27` | Commons fotoğraflarıyla beş turlu grup oyunu; fotoğraf tarihindeki doğrulanmış yaş, tam sayı seçimi, tur sonunda puan; atıflar özel bilgi/kaynak yüzeyinde |
| UNO | `/uno`, `/kartuno` | 2–8 oyuncu, inline sticker el seçimi, 108 kart, renk/sayı/aksiyon kartları, +2/+4 cezası, renk seçimi, UNO çağrısı; kart yüzleri grup panelinde gizli, oyuncuya özel el uyarısı ve kalıcı state |
| Ek oyun adları | `/trivia`, `/dogruluk`, `/cesaret` | Mevcut quiz ve doğruluk/cesaret handler'larına açık eşleme |
| Toplu oyun bitirme adları | `/oyunbitir`, `/oyundur`, `/oyunlardur` | Kayıtlı oyun sonlandırma yüzeyine eşleme; oyuncu/yönetici koşulları ilgili handler'dadır |
| Ortak kart motoru | Dahili `card_engine.py` | Kart/deck değer nesneleri, seed'li karıştırma, snapshot serileştirme, blackjack el değeri ve action-ID tekilleştirme |

Kart motoru ayrı bir kullanıcı komutu değildir; bütün kart oyunlarının aynı motora geçirildiği iddia edilmez. Önceki dataclass import hatası bağımsız varsayılan ve Python 3.11 regresyonuyla düzeltildi; güncel test kapsamı güvenilirlik kaydında izlenir.

Blackjack'teki **Pes et · yarım iade** seçeneği yalnız ilk hamleden önce kullanılabilir. İade tam jetona aşağı yuvarlanır: 101 jeton bahiste 50 jeton geri döner. Eski elin paneli yeni eli pes ettiremez. İade/yarış koruması ve süreç çökmesinde operatör müdahalesi gerektirebilen kilit sınırı [oyun güvenilirlik notunda](GAME_RELIABILITY-2026-10-18.md) açıklanır.

Kaynak dosyaları: `src/features/catalog.py`, `src/handlers/command_handler.py`, `src/services/word_game_ui.py`, `src/services/tictactoe_ui.py`, `src/services/gomoku_ui.py`, `src/games/gomoku.py`, `src/games/card_engine.py`, `src/handlers/callback_handler.py`.

### Grup moderasyonu ve Guardian yüzeyi

Guardian, Arel'in Telegram taşıma ve repository katmanlarına bağlanan moderasyon katmanıdır. Komut sözlüğü grup ayarlarını, Telegram eylemlerini ve federasyon yönetimini kapsar; etkinlik her akışın izin ve yapılandırma koşuluna bağlıdır.

| Alan | Komut örnekleri | Kaynak davranışı / koşul |
|---|---|---|
| Kurulum ve yardım | `/ghelp`, `/hizlikur`, `/kurulum` | Kategori yardımı ve yönetici kontrollü grup koruma varsayılanları |
| Üye yönetimi | `/mute`, `/unmute`, `/ban`, `/unban`, `/kick`, `/tban`, `/tmute` | Hedef ve süre ayrıştırma; Telegram eylem/yetki katmanı gerekir |
| Yönetici yetkileri | `/promote`, `/demote`, `/admin`, `/deadmin`, `/yoneticiler`, `/yetkipaneli` | Üye/yönetici sorguları ve yetki düzenleme; botun Telegram yetkileri ayrıca aranır |
| Uyarı akışı | `/warn`, `/warns`, `/resetwarn`, `/setwarnlimit`, `/setwarnmode` | Kalıcı uyarı kayıtları ve eşik/ceza tercihi |
| Sessiz/özel eylemler | `/sban`, `/skick`, `/smute`, `/swarn`, `/adminmute`, `/adminunmute` | Sessiz eylem metadata'sı; yönetici hedefli varyantlarda ayrı owner/sudo sınırı |
| Mesaj/grup yönetimi | `/purge`, `/pin`, `/unpin`, `/unpinall`, `/settitle`, `/setdesc`, `/setpic` | Mesaj ve grup bilgisi için taşıma eylemleri |
| İçerik/flood koruması | `/antilink`, `/antibotlink`, `/setflood`, `/setfloodmode`, `/addblock`, `/rmblock`, `/blocklistmode` | Link, reklam, yoğunluk ve engelli kelime politikaları |
| Katılım/doğrulama | `/antiraid`, `/captcha`, `/captchatype`, `/fsub` | Katılım koruması, doğrulama ve zorunlu abonelik akışları; kalıcı challenge ve zaman/sahiplik kontrolleri |
| Medya koruması | `/lock`, `/unlock`, `/locks`, `/mban`, `/munban`, `/fakekoruma`, `/gorselkoruma`, `/editkoruma` | Medya türü, sahte katılım, görsel ve düzenlenen mesaj kontrolleri; uygun işleme/ayar gerekir |
| Paket/GIF listeleri | `/packengelle`, `/packizinver`, `/packlistesi`, `/gifengelle`, `/gifizinver`, `/giflistesi` | Grup kapsamlı engel/izin kayıtları |
| Grup içeriği | `/rules`, `/setrules`, `/save`, `/get`, `/notes`, `/filter`, `/filters`, `/stop` | Grup kuralı, not ve otomatik filtre yanıtları; kişisel `/not` kayıtlarından ayrıdır |
| Karşılama/gece | `/setwelcome`, `/welcome`, `/setgoodbye`, `/goodbye`, `/nightmode`, `/nighttime` | Grup mesajları ve gece saati politikaları; her alt seçenek aynı uygulama düzeyinde değildir |
| Kullanıcı bilgisi | `/info`, `/id`, `/afk`, `/userinfo`, `/firstmsg`, `/memberlog`, `/zombies`, `/report` | Kimlik, AFK, kayıt ve rapor yüzeyleri; kapsam/yetki sınırlamaları |
| Grup ekibi/istisnalar | `/staff`, `/free`, `/unfree`, `/unmod`, `/approve`, `/disapprove`, `/approved` | Grup rollerini ve koruma istisnalarını kaydetme |
| Tekrarlı içerik | `/recurring`, `/stoprecurring` | Mevcut duyuru servisi üzerinden aralıklı grup mesajı planlama/durdurma |
| Federasyon | `/newfed`, `/joinfed`, `/leavefed`, `/fedinfo`, `/fedadmins`, `/fban`, `/funban`, `/fedbanlist` | Federasyon kimliği, üyelik/yasak kayıtları ve ilgili eylemler; bütün gruplara canlı yayılım doğrulanmış değildir |
| Federasyon ayrıntıları | `/fedchats`, `/fednotes`, `/fnote`, `/fedtransfer`, `/feddelete`, `/subfed`, `/unsubfed`, `/fedsubs`, `/importfbans` | Üyelik, not, sahiplik ve yasak aktarım yüzeyleri |
| Risk ve genel kayıtlar | `/gban`, `/ungban`, `/whois`, `/toprisk`, `/shadowstats`, `/analytics` | Genel yasak/risk kayıtları ve özetler; çalışma kapsamı ayrı doğrulanmalıdır |
| Komut/ayar yönetimi | `/alias`, `/aliases`, `/delalias`, `/disable`, `/enable`, `/disabled`, `/export`, `/import`, `/reload`, `/stats` | Grup alias'ları, komut kapıları ve sınırlı ayar aktarımı |
| Bot sahibi merkezi/yayın | `/arelmerkez`, `/panel`, `/broadcastpin` | Owner merkez ekranı; duyuruyu gönderip sabitleme; teslim ve izinler ayrıca doğrulanır |

**Etkin Guardian kontrolleri:** `/banlimit`, `/promotelimit` ve `/grupinfolimit` yönetici başına saatlik sınır uygular; `0` sınırı kapatır. `/privaterules` kuralları özel mesajda gösterir. `/language tr|en` grup menü dilini seçer. `/connect` ve `/disconnect` özel sohbetten grup ayarı yönetimini açar/kapatır; her işlemde güncel yöneticilik tekrar doğrulanır. `/logchannel`, `/setfedlog` ve `/setappeallog` grup, federasyon ve itiraz kayıt kanallarını ayarlar. `/cleanservice`, `/cleanwelcome` ve `/welcomemute` servis mesajı, önceki karşılama ve yeni üye susturma akışlarında uygulanır. `/fedapprove` ve `/fedunapprove` federasyon sahibi/yöneticisinin güvenilir kullanıcı listesini değiştirir. Bu kontrollerin olay tüketicileri ve yetki sınırları yerel regresyonlarla sınanmıştır; gerçek gruptaki bütün Telegram izin durumları ayrıca canlı kabul gerektirir.

**Silme yönlendirmesi:** `/del` ve `/mesajisil` artık yetki denetimine ulaşır; sessiz yönlendirme hatası regresyon testiyle kapatıldı. Gerçek grupta mesaj silme teslimi ayrıca doğrulanmalıdır.

Kaynak dosyaları: `src/moderation/guardian_surface.py`, `src/moderation/guardian_commands.py`, `src/moderation/guardian_enforcement.py`, `src/moderation/telegram_guardian_service.py`, `src/handlers/command_handler.py`, `src/handlers/message_handler.py`, `src/orchestrator.py`.

### Komut kayıt sistemi ve dinamik komutlar

`src/telegram/command_registry.py`, canonical komutu, uyumluluk alias'larını, açıklamayı, handler adını ve public/group-admin/owner menü kapsamını tek sözleşmede tutar. Çakışma kontrolü vardır. `main.py` önce bu kayıtları bağlar, ardından eski statik kayıtların aynı adla ikinci kez eklenmesini engeller. Eksik handler için açık “bu kurulumda henüz etkin değil” yanıtı üreten fallback bulunur.

Menü kapsamı yetkilendirme yerine geçmez. Guardian komutları tasarım gereği slash picker yerine düğmeli yardım üzerinden sunulur. Telegram komut menüsü ayrıca filtre ve sayı sınırı uygular; kayıtlı bir adın menüde görünmesi veya gerçekten çalışması garanti edilmez. `/alias` ile oluşturulan gruba özel adlar `filters.COMMAND` catch-all yolu ve repository'deki alias çözümlemesiyle ele alınır. `#not_adı` grup notu kısayolu da ayrı mesaj yüzeyidir. Kullanıcı tarafından tanımlanabilecek adlar sonlu bir sabit listeyle sayılmaz.

Aşağıdaki indeks yalnızca bu commit'in **kayıtlı ad ve alias sözlüğüdür**. Açıklamalar kaynak kaydının niyetini aktarır; etkinlik/izin/teslim sonucu değildir. Her kaydın çalışma koşulu, yetki sınırı ve canlı kabulü ayrıca değerlendirilir.

### Güncel kaynak komut indeksi

| Canonical kayıt | Uyumluluk adları | Kayıt açıklaması | Menü kapsamı |
|---|---|---|---|
| `/broadcastpin` | — | Duyuruyu tüm sohbetlere gönder ve sabitle | Bot sahibi |
| `/arelmerkez` | `/panel` | Arel yönetim merkezi | Bot sahibi |
| `/hizlikur` | `/kurulum` | Grubu adım adım kur | Grup yöneticisi |
| `/kayitkanali` | `/logkanal` | Moderasyon kayıt kanalını ayarla | Grup yöneticisi |
| `/grupayari` | `/ayarlar` | Bu grubun Arel ayarları | Grup yöneticisi |
| `/grupkurallari` | `/rules`, `/setrules`, `/kurallar`, `/chatkurallari` | Grup kurallarını göster | Genel |
| `/sustur` | `/sus`, `/mute` | Bir üyeyi süreli sustur | Grup yöneticisi |
| `/tmute` | — | Bir üyeyi süreli sustur | Grup yöneticisi |
| `/susturac` | `/sesiac`, `/unmute` | Bir üyenin susturmasını kaldır | Grup yöneticisi |
| `/yasakla` | `/ban` | Bir üyeyi gruptan uzaklaştır | Grup yöneticisi |
| `/yasagiac` | `/unban` | Üyenin grup yasağını kaldır | Grup yöneticisi |
| `/yetkiver` | `/admin`, `/promote` | Üyeye yönetici yetkisi ver | Grup yöneticisi |
| `/yetkial` | `/deadmin`, `/demote` | Yönetici yetkisini kaldır | Grup yöneticisi |
| `/yoneticiler` | `/adminler` | Gruptaki yöneticileri listele | Genel |
| `/yetkipaneli` | `/yetkiler` | Yönetici yetkilerini düzenle | Grup yöneticisi |
| `/uyar` | `/warn` | Bir üyeye uyarı ver | Grup yöneticisi |
| `/uyarlar` | `/warns` | Üyenin uyarılarını göster | Grup yöneticisi |
| `/uyarisifirla` | `/resetwarn` | Üyenin uyarılarını sıfırla | Grup yöneticisi |
| `/tekme` | `/kick` | Üyeyi gruptan çıkar | Grup yöneticisi |
| `/geciciyasa` | `/tban` | Süreli grup yasağı uygula | Grup yöneticisi |
| `/hayalet` | `/hayaletban`, `/sban` | Gruba girmeden önce yasakla | Grup yöneticisi |
| `/mesajisil` | `/del` | Yanıtlanan mesajı sil | Grup yöneticisi |
| `/mesajtemizle` | `/purge`, `/temizle` | Son mesajları topluca sil | Grup yöneticisi |
| `/sabitle` | `/pin` | Yanıtlanan mesajı sabitle | Grup yöneticisi |
| `/sabitlekaldir` | `/unpin` | Mesaj sabitlemesini kaldır | Grup yöneticisi |
| `/rapor` | `/report` | Mesajı yöneticilere raporla | Genel |
| `/yasaklikelimeler` | `/yasakkelime`, `/blocklist` | Yasaklı kelimeleri yönet | Grup yöneticisi |
| `/iznilinkler` | `/guvenlilink`, `/whitelist` | İzinli alan adlarını yönet | Grup yöneticisi |
| `/linkkoruma` | — | Link korumasını aç veya kapat | Grup yöneticisi |
| `/reklamkoruma` | — | Reklam korumasını aç veya kapat | Grup yöneticisi |
| `/floodlimiti` | `/floodlimit`, `/setflood` | Flood eşiğini ayarla | Grup yöneticisi |
| `/floodcezasi` | `/floodmod`, `/setfloodmode` | Flood cezasını seç | Grup yöneticisi |
| `/raidkoruma` | `/joinraidac`, `/raidbitti` | Katılım saldırısı korumasını yönet | Grup yöneticisi |
| `/dogrulama` | — | Yeni üyeler için doğrulama ayarla | Grup yöneticisi |
| `/hosgeldin` | `/welcome` | Hoş geldin mesajını yönet | Grup yöneticisi |
| `/veda` | `/goodbye` | Veda mesajını yönet | Grup yöneticisi |
| `/gecemodu` | — | Gece modunu yönet | Grup yöneticisi |
| `/grupdili` | `/dil`, `/language` | Grup dilini değiştir | Grup yöneticisi |
| `/grupistatistik` | `/istatistik` | Grup istatistiklerini göster | Grup yöneticisi |
| `/sohbetmodu` | `/chatmod` | Arel sohbet modunu yönet | Grup yöneticisi |
| `/areluslubu` | `/stil` | Arel üslubunu seç | Grup yöneticisi |
| `/notekle` | `/not_ekle` | Gruba not ekle | Grup yöneticisi |
| `/notsil` | `/not_sil` | Grup notunu sil | Grup yöneticisi |
| `/filtre` | `/filter` | Kelime filtresi oluştur | Grup yöneticisi |
| `/filtreler` | `/filters` | Aktif filtreleri listele | Genel |
| `/filtresil` | `/stop` | Kelime filtresini sil | Grup yöneticisi |
| `/lock` | — | Bir medya türünü kilitle | Grup yöneticisi |
| `/unlock` | — | Bir medya türünün kilidini aç | Grup yöneticisi |
| `/locks` | — | Aktif medya kilitlerini göster | Genel |
| `/oyunbitir` | `/oyundur`, `/oyunlardur` | Aktif oyunları bitir | Grup yöneticisi |
| `/trivia` | — | Bilgi yarışması başlat | Genel |
| `/dogruluk` | — | Doğruluk turu başlat | Genel |
| `/cesaret` | — | Cesaret turu başlat | Genel |
| `/agkur` | `/newfed`, `/fed_olustur` | Federasyon ağı oluştur | Grup yöneticisi |
| `/agaekle` | `/joinfed`, `/fed_katil` | Grubu federasyona bağla | Grup yöneticisi |
| `/agdanayril` | `/leavefed` | Federasyondan ayrıl | Grup yöneticisi |
| `/agdurum` | `/fedstat` | Federasyon durumunu göster | Genel |
| `/agban` | `/fban` | Federasyon yasağı uygula | Grup yöneticisi |
| `/agbanac` | `/unfban`, `/funban` | Federasyon yasağını kaldır | Grup yöneticisi |
| `/agbanlar` | `/fedbanlist` | Federasyon yasaklarını listele | Genel |
| `/golgeban` | `/shadowban` | Gölge yasağı uygula | Grup yöneticisi |
| `/golgeac` | `/unshadowban`, `/unsban` | Gölge yasağı kaldır | Grup yöneticisi |
| `/risk` | `/whois` | Kullanıcı risk kartını göster | Grup yöneticisi |
| `/riskraporu` | `/analytics` | Federasyon risk raporu | Grup yöneticisi |
| `/guvenliliste` | `/fedapprove`, `/fedunapprove` | Güvenilir kullanıcıları yönet | Grup yöneticisi |
| `/info` | — | Sohbet ve kullanıcı kimliğini gösterir | Genel |
| `/id` | — | Sohbet ve kullanıcı kimliğini gösterir | Genel |
| `/afk` | — | Geçici olarak uzakta olduğunu bildirir | Genel |
| `/ghelp` | — | Guardian ghelp komutu | Grup yöneticisi |
| `/muter` | — | Guardian muter komutu | Grup yöneticisi |
| `/unmuter` | — | Guardian unmuter komutu | Grup yöneticisi |
| `/settitle` | — | Guardian settitle komutu | Grup yöneticisi |
| `/setdesc` | — | Guardian setdesc komutu | Grup yöneticisi |
| `/setpic` | — | Guardian setpic komutu | Grup yöneticisi |
| `/ytban` | — | Guardian ytban komutu | Grup yöneticisi |
| `/zombies` | — | Uzun süredir pasif üyeleri listeler | Grup yöneticisi |
| `/reload` | — | Guardian ayarlarını yeniden yükler | Grup yöneticisi |
| `/echo` | — | Metni geri gönderir | Genel |
| `/skick` | — | Guardian skick komutu | Grup yöneticisi |
| `/smute` | — | Guardian smute komutu | Grup yöneticisi |
| `/swarn` | — | Guardian swarn komutu | Grup yöneticisi |
| `/unmod` | — | Guardian moderatör rolünü kaldırır | Grup yöneticisi |
| `/free` | — | Kullanıcıyı koruma istisnasına alır | Grup yöneticisi |
| `/unfree` | — | Koruma istisnasını kaldırır | Grup yöneticisi |
| `/staff` | — | Grup ekibini listeler | Grup yöneticisi |
| `/recurring` | — | Tekrarlı mesaj planlar | Grup yöneticisi |
| `/stoprecurring` | — | Tekrarlı mesajı durdurur | Grup yöneticisi |
| `/unpinall` | — | Guardian unpinall komutu | Grup yöneticisi |
| `/banlimit` | — | Guardian banlimit komutu | Grup yöneticisi |
| `/promotelimit` | — | Guardian promotelimit komutu | Grup yöneticisi |
| `/grupinfolimit` | — | Guardian grupinfolimit komutu | Grup yöneticisi |
| `/reaksiyon` | — | Guardian reaksiyon komutu | Grup yöneticisi |
| `/fakekoruma` | — | Guardian fakekoruma komutu | Grup yöneticisi |
| `/gorselkoruma` | — | Guardian gorselkoruma komutu | Grup yöneticisi |
| `/packengelle` | — | Guardian packengelle komutu | Grup yöneticisi |
| `/packizinver` | — | Guardian packizinver komutu | Grup yöneticisi |
| `/packlistesi` | — | Guardian packlistesi komutu | Grup yöneticisi |
| `/gifengelle` | — | Guardian gifengelle komutu | Grup yöneticisi |
| `/gifizinver` | — | Guardian gifizinver komutu | Grup yöneticisi |
| `/giflistesi` | — | Guardian giflistesi komutu | Grup yöneticisi |
| `/editkoruma` | — | Guardian editkoruma komutu | Grup yöneticisi |
| `/alias` | — | Grup komut takma adı ekler | Grup yöneticisi |
| `/delalias` | — | Grup komut takma adını siler | Grup yöneticisi |
| `/aliases` | — | Grup takma adlarını listeler | Grup yöneticisi |
| `/setwarnlimit` | — | Uyarı sınırını ayarlar | Grup yöneticisi |
| `/setwarnmode` | — | Uyarı cezasını seçer | Grup yöneticisi |
| `/antilink` | — | Bağlantı korumasını açar veya kapatır | Grup yöneticisi |
| `/antibotlink` | — | Bot bağlantısı korumasını yönetir | Grup yöneticisi |
| `/rmwhitelist` | — | Guardian rmwhitelist komutu | Grup yöneticisi |
| `/antiraid` | — | Katılım saldırısı korumasını yönetir | Grup yöneticisi |
| `/approve` | — | Kullanıcıyı onaylı listeye alır | Grup yöneticisi |
| `/disapprove` | — | Kullanıcıyı onaylı listeden çıkarır | Grup yöneticisi |
| `/approved` | — | Onaylı kullanıcıları listeler | Genel |
| `/addblock` | — | Kara listeye kelime ekler | Grup yöneticisi |
| `/rmblock` | — | Kara listeden kelime siler | Grup yöneticisi |
| `/blocklistmode` | — | Kara liste cezasını seçer | Grup yöneticisi |
| `/captcha` | — | Yeni üyeler için doğrulamayı yönetir | Grup yöneticisi |
| `/captchatype` | — | Doğrulama tipini seçer | Grup yöneticisi |
| `/cleanservice` | — | Telegram servis mesajlarını temizler | Grup yöneticisi |
| `/connect` | — | Bağlı sohbet ayarını yönetir | Grup yöneticisi |
| `/disconnect` | — | Bağlı sohbet ayarını kaldırır | Grup yöneticisi |
| `/export` | — | Guardian ayarlarını dışa aktarır | Grup yöneticisi |
| `/import` | — | Guardian ayarlarını içe alır | Grup yöneticisi |
| `/disable` | — | Bir komutu bu grupta kapatır | Grup yöneticisi |
| `/enable` | — | Kapatılmış komutu açar | Grup yöneticisi |
| `/disabled` | — | Kapalı komutları listeler | Genel |
| `/fsub` | — | Zorunlu kanal aboneliğini yönetir | Grup yöneticisi |
| `/gban` | — | Kullanıcıyı genel yasak kaydına alır | Grup yöneticisi |
| `/ungban` | — | Genel yasağı kaldırır | Grup yöneticisi |
| `/setwelcome` | — | Karşılama mesajını ayarlar | Grup yöneticisi |
| `/setgoodbye` | — | Veda mesajını ayarlar | Grup yöneticisi |
| `/cleanwelcome` | — | Karşılama temizliğini yönetir | Grup yöneticisi |
| `/welcomemute` | — | Karşılama susturmasını yönetir | Grup yöneticisi |
| `/lockmode` | — | Medya kilidi cezasını seçer | Grup yöneticisi |
| `/logchannel` | — | Kayıt kanalını ayarlar | Grup yöneticisi |
| `/mban` | — | Medya türünü yasaklar | Grup yöneticisi |
| `/munban` | — | Medya yasağını kaldırır | Grup yöneticisi |
| `/userinfo` | — | Kullanıcı bilgi kartını gösterir | Genel |
| `/firstmsg` | — | Kullanıcının ilk mesajını gösterir | Genel |
| `/memberlog` | — | Üye geçmişini listeler | Genel |
| `/nightmode` | — | Gece modunu yönetir | Grup yöneticisi |
| `/nighttime` | — | Gece saatlerini ayarlar | Grup yöneticisi |
| `/save` | — | Guardian grup notu kaydeder | Grup yöneticisi |
| `/get` | — | Guardian grup notunu getirir | Genel |
| `/notes` | — | Guardian grup notlarını listeler | Genel |
| `/privaterules` | — | Özel kural mesajını yönetir | Grup yöneticisi |
| `/delfed` | — | Guardian delfed komutu | Grup yöneticisi |
| `/fedinfo` | — | Guardian fedinfo komutu | Genel |
| `/myfed` | — | Guardian myfed komutu | Genel |
| `/fpromote` | — | Guardian fpromote komutu | Grup yöneticisi |
| `/fdemote` | — | Guardian fdemote komutu | Grup yöneticisi |
| `/fedadmins` | — | Guardian fedadmins komutu | Genel |
| `/fedchats` | — | Guardian fedchats komutu | Grup yöneticisi |
| `/fedapproved` | — | Guardian fedapproved komutu | Grup yöneticisi |
| `/setappeallog` | — | Guardian setappeallog komutu | Grup yöneticisi |
| `/fednotes` | — | Guardian fednotes komutu | Grup yöneticisi |
| `/fedtransfer` | — | Guardian fedtransfer komutu | Grup yöneticisi |
| `/feddelete` | — | Guardian feddelete komutu | Grup yöneticisi |
| `/setfedlog` | — | Guardian setfedlog komutu | Grup yöneticisi |
| `/fnote` | — | Guardian fnote komutu | Grup yöneticisi |
| `/unsubfed` | — | Guardian unsubfed komutu | Grup yöneticisi |
| `/fedsubs` | — | Guardian fedsubs komutu | Grup yöneticisi |
| `/fbanlist` | — | Guardian fbanlist komutu | Grup yöneticisi |
| `/importfbans` | — | Guardian importfbans komutu | Grup yöneticisi |
| `/subfed` | — | Guardian subfed komutu | Grup yöneticisi |
| `/stats` | — | Guardian durumunu gösterir | Genel |
| `/toprisk` | — | Guardian toprisk komutu | Grup yöneticisi |
| `/shadowstats` | — | Guardian shadowstats komutu | Grup yöneticisi |
| `/addsudo` | — | Guardian addsudo komutu | Grup yöneticisi |
| `/delsudo` | — | Guardian delsudo komutu | Grup yöneticisi |
| `/adminmute` | — | Guardian adminmute komutu | Grup yöneticisi |
| `/adminunmute` | — | Guardian adminunmute komutu | Grup yöneticisi |

**Geriye uyumlu ad:** `/gecicisustur`, `/tmute` ile aynı işleyiciye bağlıdır ve Telegram komut yönlendirmesinde ayrıca sınanır.

**Kapsam kontrolü:** Statik `main.py` kayıtları, canonical registry, takma adlar ve 171 ham Guardian adı örtüşür; mevcut Telegram kaydında **387 benzersiz komut adı** bulunur. Bunların her biri ayrı yönlendirme testinden geçer. Bu sayı 387 ayrı ürün özelliği veya tüm yetki/veri koşullarında canlı başarı anlamına gelmez. Gruba özel dinamik takma adlar bu sabit sayıdan ayrıdır.
