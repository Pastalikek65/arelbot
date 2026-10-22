# Telegram butonları ve güncel API incelemesi

## Uygulanan tasarım

Mevcut oyun düzeni korunur. 40 oyun başlığına anlamlı simge atanır; ana menü, kategoriler, yardım ve hazırlık kontrolleri aynı sunum kuralını paylaşır. Başlat/katıl/yeniden oyna yeşil, oyunu bitir kırmızı, oyun salonu mavi; gezinme nötrdür. Özel simge bulunan butonda yinelenen Unicode simgesi kaldırılır, okunabilir metin kalır.

Taşlar, kart yüzleri, koordinatlar, sayılar, oy seçenekleri ve kelime yanıtları değiştirilmez. Callback, URL, konu, oyuncu, maç/faz kimliği, kopyalanan metin ve ödeme alanları aynen korunur. Emoji içeren normal klavye mesajları komut ayrıştırmasını etkileyebileceğinden reply keyboard değiştirilmez. Kural, bot ve işçinin ortak HTTP taşıma katmanında yalnız inline klavyeye uygulanır; 40 ayrı motor düzenlenmez.

## Resmî değişiklikler ve kararlar

- İnceleme anında yayımlanmış son resmî sürüm Bot API **10.3** (24 Ağustos 2026). Rich Messages düğmeleri ve kişiye özel geçici mesajlarda yenilikler var. Oyun kuralları ve gizli bilgi teslim sözleşmeleri bu sunum güncellemesinde korunuyor. [Telegram değişiklik kaydı](https://core.telegram.org/bots/api-changelog)
- Buton `style` ve `icon_custom_emoji_id` alanları **9.4** ile geldi. Özel simgeler doğrudan özel/grup/süpergrup mesajlarında Premium bot sahibiyle veya uygun Fragment kullanıcı adıyla kullanılabiliyor. [9.4 açıklaması](https://core.telegram.org/bots/api-changelog#february-9-2026), [buton sözleşmesi](https://core.telegram.org/bots/api#inlinekeyboardbutton)
- Projede sabitlenmiş python-telegram-bot **22.6** var. Yerel imza incelemesinde bu iki alan için adlandırılmış parametre yok; güncel belgede yerel alanlar 22.7'de eklenmiş. Mevcut sürümün belgelenmiş istek arabirimiyle alanlar gönderiliyor; bağımlılıklar değiştirilmedi. [Kütüphane belgesi](https://docs.python-telegram-bot.org/en/stable/telegram.inlinekeyboardbutton.html)

| İncelenen yenilik | Arel için olası kullanım | Bu yayındaki karar |
|---|---|---|
| Rich Messages ve düğmeleri, 10.1–10.3 | Mantık tabloları ve uzun oyun açıklamaları | Mevcut kalıcı paneller korunuyor; geçiş ayrıca görsel/teslim kabulü gerektirir. |
| Kişiye özel geçici mesajlar, 10.3 | Grup içindeki kişisel geri bildirim | Gizli rol/iddia teslimi özel sohbette kalır; teslim ve mahremiyet modeli değiştirilmedi. |
| Medyalı anketler, 10.0–10.1 | Görsel veya müzikli bilgi soruları | Kaynak, lisans ve doğru yanıt denetimiyle ayrı oyun geliştirmesi olarak değerlendirilebilir. |
| Guest Mode, 10.0 | Botun üyesi olmadığı sohbetlerde çağrılması | Grup üyeliği ve yetki varsayımları kendiliğinden genişletilmedi. |
| Mini App origin koruması, 10.2 | İleride açılacak UNO web yüzeyi | Açılacak bir web yüzeyi origin ve initData denetiminden ayrıca geçmeli. |

Bu tablo sonraki geliştirmelerin değerlendirmesidir; uygulanmış özellik listesi değildir. Kaynak: [resmî değişiklik kaydı](https://core.telegram.org/bots/api-changelog).

## Emoji kaynağı ve kullanım uygunluğu

22 Ekim salt-okunur Telegram API denemesi 112 resmî `Topics` simgesi döndürdü. Oyun, yardım, ev, zihin, kupa, kutlama ve araçlar için 38 kimlik seçildi; kaynak ve tarih yerel katalogda kayıtlı. Yapılandırılmış bot sahibinin test grubundaki kullanıcı bilgisinde `is_premium=True` görüldü. Bu gözlem kalıcı üyelik garantisi veya istemcide görüntüleme kanıtı değildir.

Her gönderimde ağ üzerinden emoji aranmaz. Varsayılan `TELEGRAM_CUSTOM_BUTTON_EMOJI=auto`; `off` simgeleri Unicode olarak tutar. Inline-mode mesaj kimliğinde owner Premium istisnasına güvenilmez, Unicode kullanılır. Premium kullanım hakkı sonradan kaybolursa API'nin açık özel emoji hatası, bir kez Unicode ile güvenli yeniden denemeyi tetikler ve aynı istemcide özel simge denemelerini kapatır. Ağ zaman aşımı, 429 ve 5xx yeniden gönderim yaratmaz. Dosya gönderiminde tekrar için seek konumu korunur; yeniden okunamayan akışta baştan Unicode seçilir.

## Doğrulama ve sınırlar

İlk odaklı testlerde tüm oyun simge eşlemeleri, masa verilerinin korunması, gerçek PTB HTTP serileştirmesi, özel simge reddi, sonraki Unicode gönderimi, HTTP hata ayrımı, inline-mode sınırı, dosya içeriği ve ayrı polling istemcisi denetlendi. Bağımsız inceleme ve son tam yayın ölçümleri aşağıya gerçek koşum sonrası kaydedilir.

Canlı Telegram arayüzü ve insanlı maçlar önceki kullanıcı talimatı doğrultusunda `NOT_RUN`. Salt-okunur API keşfi, butonların bir istemcide görsel olarak render edildiği anlamına gelmez. GitHub güncellemesi yapılmaz.

Durum: `2026.10.22-games-13` sunucuda etkin. Kaynak `4b9c421`; uygulama kodu `f4d83ba`, aradaki değişiklik yalnız kabul raporu ve API inceleme belgesidir. Önceki games12 adayı etkinleştirilmedi.

Son bileşen denetimi 34 PASS; bağımsız inceleme kabul verdi. Emoji kataloğu modül başlangıcında yüklenir; istek işlenirken dosya açılamadığını test doğrular. Tam yayın kapısı 25/25 PASS: host 2.747 PASS / 19 SKIP; yerel konteyner ve sunucunun gerçek aday imajı 2.760 PASS / 6 SKIP.

Etkin bot ve işçi ile Mongo/Redis sağlıklı, yeniden başlama sayıları sıfır. Salt-okunur Telegram sorguları doğru bot kimliği, 40 oyun kaydı, özel emoji kullanım ayarının açık olması ve 38 seçilmiş simgenin tamamının geçerli/animasyonlu olduğunu doğruladı. Bu yayın için gruba veya özel sohbete test mesajı gönderilmedi. Son veri yedeğinin arşiv bütünlüğü doğrulandı ve eski sürüme dönüş korundu; son yedeğe bağımsız restore yapılmadı.

Kanıt: [yayın kaydı](../deploy/evidence/telegram-buttons-deployment-20261022.json). Canlı istemcide görsel kabul ve insanlı maçlar hâlâ `NOT_RUN`; bu sonuçlar API metadata denetimine dönüştürülmedi. GitHub güncellenmedi.
