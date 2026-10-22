# Arelbot Özellik Araştırması ve Önceliklendirme

Bu belge, Arelbot'un kullanıcıya görünen özelliklerini mevcut kod yapısıyla
eşleştirir ve uygulanabilir sonraki adımları önceliklendirir. Belge, özellikle
quote ve medya özelliklerinin ayar menüsünden kapatılıp açılması isteği için
hazırlanmıştır. Yeni bir özelliğin üretime alınması için bu belge tek başına
yeterli değildir; özellik manifesti, testleri, audit kaydı ve kaynak/terms
onayı da aynı değişiklikte bulunmalıdır.

**Araştırma tarihi:** 16 Ekim 2026  
**Birincil hedef:** Telegram grup ve özel sohbetlerinde hızlı, anlaşılır ve
yetkisi doğru ayrılmış kullanıcı deneyimi  
**Kapsam:** Telegram Bot API, mevcut Arelbot modülleri, kullanıcı akışları,
cache/queue davranışı ve üretim riskleri

## Araştırma sınırları ve yöntem

Araştırma sorusu şudur: "Arelbot'ta kullanıcıların en sık karşılaştığı sürtünme
noktaları hangileridir ve Telegram'ın mevcut resmi yüzeyleri kullanılarak
hangi özellikler güvenli biçimde önceliklendirilebilir?"

Arama yalnızca Telegram'ın resmi Bot API ve Telegram API dokümantasyonunda
yapıldı. Resmi Bot API referansı, API changelog'u, Bot commands, inline bot,
forum, reactions, todo/checklist ve Mini App sayfaları tarandı. Topluluk
blogları, doğrulanmamış limit listeleri, scraper örnekleri ve üçüncü taraf
"en iyi bot" karşılaştırmaları kanıt olarak kullanılmadı. Dış provider'ların
terms ve lisansları bu çalışmanın kapsamına alınmadı; her provider için
`docs/DATA_SOURCES.md` ve provider governance onayı gerekir.

## Mevcut repository bulguları

İnceleme sırasında aşağıdaki davranışlar doğrudan koddan doğrulandı:

| Alan | Mevcut durum | Kullanıcı etkisi | Öncelik |
|---|---|---|---|
| Quote `/q` | `QuoteHandler.handle_q_command` `q_enabled` ayarını kontrol ediyor | Ana quote komutu kapatılabiliyor | P0 tabanı |
| Quote yardımcı komutları | `/qrand`, `/qtop`, `/qd`, `/qtemplate`, `/qcolor`, `/qemoji`, `/qrate`, `/qtheme` aynı ortak guard'ı kullanmıyor | Quote kapalıyken bazı yüzeyler hâlâ çalışabiliyor | P0 |
| Grup ayarları | `CallbackHandler._build_ayar_keyboard` quote toggle içeriyor; medya toggle içermiyor | Video indirme davranışı admin panelinden kontrol edilemiyor | P0 |
| Sosyal link akışı | `MessageHandler` URL'yi media queue'ya yönlendiriyor; explicit command ve otomatik link akışı ayrı bir politika nesnesine bağlı değil | Admin kapatmak istese bile otomatik indirme başlayabiliyor | P0 |
| Feature registry | Public özellikler registry ve manifest üzerinden tanımlı | Yeni policy registry metadata'sına eklenirse komut, AI ve callback aynı kuralı kullanabilir | P0 tabanı |
| Callback yüzeyi | Inline düğmeler yoğun biçimde kullanılıyor; callback'ler ağır işleri queue'lara aktarıyor | Ayar ekranını büyütmek mümkün, ancak callback payload sınırı korunmalı | P1 |
| Provider/cache | Typed provider, sağlık ve SWR altyapısı mevcut | Aynı kullanıcı isteği için coalescing ve freshness etiketleri korunmalı | P1 |
| Oyun ve üretkenlik | Kalıcı state, leaderboard, hatırlatıcı ve plan yüzeyleri var | Ayarlar ve bildirimler aynı policy/permission katmanına bağlanmalı | P1 |

Bu tablo `docs/FEATURE_CATALOG.md` ile birlikte okunmalıdır. Bu belgede yeni
bir feature key tanımlanmaz; yalnızca uygulama sırası ve araştırma sonucu
belgelenir.

## P0 — Özellik politikası ve görünür ayar kontrolü

### 1. Tek bir `ChatFeaturePolicy` sözleşmesi

Quote ve medya için komutların her birine ayrı ayrı `if` eklemek kısa vadede
çalışsa da zamanla yeni callback veya inline yüzeylerinin bu kuralı atlamasına
yol açar. Bunun yerine `src/features` altında dependency injection ile verilen
bir policy nesnesi kullanılmalıdır.

Önerilen kavramsal sözleşme:

```python
class ChatFeaturePolicy(Protocol):
    async def is_enabled(self, chat_id: int, feature: str) -> bool: ...
    async def require_enabled(self, chat_id: int, feature: str) -> bool: ...
    async def set_enabled(self, chat_id: int, feature: str, enabled: bool, actor_id: int) -> None: ...
```

Gerçek sınıfın MongoDB erişimi repository üzerinden yapılmalıdır. Handler
yalnızca `policy.is_enabled(...)` sonucunu kullanır. `feature` değerleri
registry'deki `quotes` ve `media` gibi key'lerle aynı olmalıdır; yeni bir
özellik için key, permission, timeout, cache ve audit alanları manifestte
tanımlanır.

### 2. Quote kapalıyken bütün yüzeyleri kapat

`quotes` kapalı olduğunda aşağıdakilerin tamamı no-op olmalıdır:

- `/q`, `/qrand`, `/qtop`, `/qd`, `/qtemplate`
- `/qcolor`, `/qemoji`, `/qrate`, `/qtheme`
- Quote callback'leri ve inline quote araması
- Quote için otomatik rating veya katalog işlemleri

Komutun sessizce yok sayılması kullanıcı isteğiyle uyumludur. Bir inline
callback'in Telegram istemcisinde yükleniyor göstergesini sonsuza kadar
bırakmaması için callback sorgusu kısa bir toast ile cevaplanabilir; bu toast
yeni mesaj göndermemeli ve quote işini başlatmamalıdır. Yönetici ayar düğmesi
quote özelliğini açtığında mevcut theme/template değerleri korunur.

### 3. Video/medya indirme için admin kontrollü toggle

`media` kapalı olduğunda grup içinde:

- Sosyal link otomatik yakalama queue'ya iş eklememeli.
- `/indir`, `/dl` ve `/download` no-op olmalı.
- Medya menüsündeki indirme callback'i iş başlatmamalı.
- Aynı URL'nin yeniden gönderilmesi eski cache'ten dosya göndermemeli.

Bu ayar grup sohbetlerinde yalnızca Telegram grup yöneticileri ve owner/sudo
için değiştirilebilir. Özel sohbette kullanıcıya ait ayrı bir tercih
gerekiyorsa aynı policy store'un user scope'u kullanılabilir; grup ayarı özel
sohbet ayarını ezmemelidir. Varsayılan davranış geriye uyumluluk için açık
tutulabilir, fakat açık/kapalı durumu ayar panelinde her zaman görünmelidir.

Her toggle değişimi `audit_logs` içine actor, chat, feature, önceki ve sonraki
durum ile yazılmalı; secret, tam URL ve mesaj içeriği yazılmamalıdır. İki metric
yeterlidir: `feature_toggle_changes_total` ve `feature_policy_denied_total`.

### 4. Ayar paneli tasarımı

İlk sayfa sık kullanılan ve maliyeti yüksek özellikleri gösterir:

```text
⚙️ Grup ayarları
  Quote                 ✅ açık
  Video indirme         ✅ açık
  AI sohbet             ✅ açık
  Link filtresi         ❌ kapalı
  Hoş geldin            ✅ açık
  Sonraki sayfa         ›
```

Her buton callback'te `chat_id`, `actor_id` ve yetkiyi yeniden doğrular;
mesajdaki önceki yazıya güvenmez. Uzun callback payload'ı yerine kısa bir
`ayar:toggle:q` gibi protokol kimliği ve server-side state kullanılmalıdır.
Telegram'ın `callback_data` alanı 1–64 byte ile sınırlıdır.

## P1 — Kullanıcı deneyimini üretim seviyesine çıkarma

### Ayarların registry'den üretilmesi

Menü metni, command listesi, callback policy ve `/features` çıktısı aynı
registry metadata'sından üretilirse bir feature'ın yalnızca bir yüzeyde açık
kalması önlenir. Her satırda şu bilgiler bulunabilir:

- durum: açık/kapalı/degraded
- kapsam: özel sohbet, grup, admin
- provider sağlık durumu
- son veri zamanı ve stale uyarısı
- işlem türü: anlık veya kuyruğa alınan
- ilgili komut ve yardım düğmesi

Bu ekran provider secret veya tam kaynak URL'si göstermemelidir.

### Asenkron iş merkezi

Medya, quote, sticker, görsel üretimi ve belge analizi için tek bir kullanıcı
paneli önerilir:

- aktif işler
- ilerleme veya aşama (`probe`, `download`, `render`, `send`)
- iptal
- geçici hata sonrası yeniden dene
- tamamlanan son işler

Callback yalnızca job ID taşır; job sahibi, chat ve feature server-side ledger
üzerinden doğrulanır. Ağır işlem callback içinde yapılmaz. `answerCallbackQuery`
hemen çağrılır, iş queue'ya bırakılır.

### Cache ve kaynak tazeliği

Public veri cevaplarında `cache_hit`, `fetched_at` ve `stale` zarfı korunmalı.
Stale-while-revalidate akışı kullanıcıya açık etiket göstermeli; eski deprem,
hava veya fiyat verisi güncelmiş gibi sunulmamalıdır. Aynı sorgu için Redis
request coalescing kullanılmalı, cache anahtarlarında kullanıcı özel verisi
veya secret bulunmamalıdır. Inline sonuçlarda chat/user kapsamı cache sızıntısını
önlemek için sonuç kimlikleri deterministik ama içerik anahtarı kapsamlı
olmalıdır.

### Günlük kullanım kolaylıkları

Mevcut altyapıya az ek yükle eklenebilecek özellikler:

1. `/menu` ve `/ayar` için sayfalı kategori gezintisi ve "son kullanılanlar".
2. Türkçe/İngilizce command scope'ları ve kullanıcı saat dilimine göre tarih.
3. Bildirim merkezinde sessiz saat, bir sonraki teslim zamanı ve tek tık kapatma.
4. Belge/medya işlerinde kullanıcıya hata metni yerine sabit hata kodu ve kısa
   çözüm düğmesi (`yeniden dene`, `ayarları aç`, `işi iptal et`).
5. Grup için forum topic ID'sini koruyan ayar ve bildirim teslimatı.
6. Kullanıcı hafızası için dışa aktarma, tek kayıt silme ve tam temizleme.

## P2 — Telegram yüzeyleriyle farklılaşma

### Yönetici komutlarını gerçekten gizlemek

Runtime yetki kontrolü zorunludur; ancak komutları kullanıcıya hiç göstermemek
ayrı bir UX kazanımıdır. Telegram Bot API `setMyCommands` ile aşağıdaki
kapsamları destekler:

- `BotCommandScopeAllGroupChats`
- `BotCommandScopeAllChatAdministrators`
- `BotCommandScopeChatAdministrators`
- `BotCommandScopeAllPrivateChats`
- belirli chat veya chat member kapsamı

Üretim bootstrap'ında normal kullanıcı komutları varsayılan/group kapsamına,
`/health`, `/metrics`, `/audit`, `/providers`, `/cache`, `/maintenance` gibi
komutlar `AllChatAdministrators` veya owner'a özel `ChatMember` kapsamına
ayrılmalıdır. Grup admin olmayan kullanıcı hâlâ komutu elle yazarsa runtime
guard isteği reddeder. Komut listesi değişince `getMyCommands` ile replay
doğrulaması yapılmalıdır.

### Reaksiyon tabanlı geri bildirim

Telegram Bot API, yöneticinin açıkça `allowed_updates` içinde istemesi halinde
`message_reaction` ve `message_reaction_count` güncellemelerini sağlayabilir.
Arelbot bunu quote rating'e ek bir sinyal veya "bu yanıt işime yaradı" metriği
olarak kullanabilir. Reaksiyonlar gecikmeli ve toplu gelebileceği için
analytics debounce edilmelidir. Mesaj metni ve kullanıcı adı metric label
olmamalıdır. Arel'in kendi bot mesajlarına verdiği reaksiyonlar update olarak
gelmez; geri bildirim akışı buna göre tasarlanmalıdır.

### Native checklist uyarlaması

Telegram'ın güncel API'si checklist/to-do mesajlarını destekliyor. Mevcut
`/gorev` ve `/plan` Mongo state'i önce kaynak sistem olarak korunmalı; native
checklist yalnızca Telegram istemci/API capability doğrulandıktan sonra
çıktı formatı olarak eklenmelidir. Aksi halde kullanıcı görevleri ile native
mesajların çift yönlü senkronizasyonu güvenilir olmayabilir. Bu özellik için
ayrı feature flag, migration ve replay fixture gerekir.

### Inline arama ve menü button

Şehir, haber, quote, sticker ve public veri araması için inline sonuçlar
pagination ve bounded `cache_time` ile sunulabilir. Kullanıcı özel notları,
hafıza kayıtları ve grup içi quote sonuçları inline dışına çıkmamalı.
Bot menu button kısa vadede command listesi olarak kalabilir; çok adımlı
ayarların Mini App'e taşınması ancak HTTPS, `initData` imza doğrulaması,
user/chat scope kontrolü ve audit tamamlandıktan sonra feature flag arkasında
yapılmalıdır.

## P3 — Orta/uzun vadeli deneyler

Bu işler temel policy, queue ve gözlemlenebilirlik kanıtı olmadan başlatılmamalı:

- Reaction-aware başarı ve oyun ödülleri.
- Native checklist ile Telegram dışa aktarma.
- Admin için Mini App tabanlı provider/feature dashboard.
- Telegram Stars/payments veya paid media.
- Botun bağlı Business hesabı adına mesaj işleme.
- Chat boost/giveaway tabanlı topluluk ekonomisi.

Ödeme, Stars, Business ve paid media akışları ayrı terms, muhasebe, fraud ve
geri ödeme tasarımı gerektirir; bunlar yalnızca "birkaç buton" özelliği
değildir. Üretim feature'ı olarak açılmadan önce hukuki ve sağlayıcı koşulları
incelenmelidir.

## Uygulama sırası

### Dalga 1 — Policy ve test (P0)

1. `ChatFeaturePolicy` ve repository/store sözleşmesini ekle.
2. `quotes` ve `media` için varsayılanları migration-safe biçimde tanımla.
3. QuoteHandler, CommandHandler, CallbackHandler, MessageHandler ve inline
   servislerinin tamamını policy guard'a bağla.
4. Admin-only toggle callback'lerini ekle; actor/chat/permission tekrar
   doğrulaması ve audit/metric yaz.
5. Disabled/enabled, private/group, admin/non-admin ve duplicate callback
   testlerini ekle.

### Dalga 2 — UX ve operasyon (P1)

1. Sayfalı ayar panelini registry metadata'sından üret.
2. İş merkezi için ortak job projection ve cancel/retry callback'i ekle.
3. Cache freshness/stale etiketini ortak presentation katmanında göster.
4. Provider/queue sağlığını ayar ekranına yalnızca özet olarak ekle.
5. Feature toggle değişimlerini `/audit` ve `/metrics` üzerinden doğrula.

### Dalga 3 — Telegram komut ve inline yüzeyi (P2)

1. Bootstrap'ta command scope'larını kur; admin komutlarının kullanıcı
   menüsünde görünmediğini Telegram staging'de doğrula.
2. Inline aramayı public, bounded ve scope-aware hale getir.
3. Forum topic, callback timeout ve pagination replay testlerini çalıştır.
4. Reaksiyon güncellemeleri için opt-in analytics feature flag'i ekle.

### Dalga 4 — Deneysel özellikler (P3)

1. Native checklist capability probe ve kullanıcı opt-in'i.
2. Mini App prototipi; yalnızca staging'de imza ve scope testleri geçince
   production flag'i aç.
3. Payments/Stars/Business için ayrı karar kaydı, terms ve abuse planı.

## Risk, terms, cache ve rate limit notları

| Risk | Etki | Kontrol |
|---|---|---|
| Callback payload 64 byte sınırı | Uzun veya manipüle edilmiş callback | Kısa opaque ID; server-side state; owner/chat doğrulaması |
| Callback timeout | Telegram istemcisinde sonsuz spinner | Önce `answerCallbackQuery`, ağır işi queue'ya bırak |
| Command scope fallback'i | Admin komutunun normal kullanıcıya görünmesi | Dar scope'ları set et, `getMyCommands` ile staging replay yap |
| Bot API/update hacmi | Reaksiyon veya geniş group update'leri gecikme yaratabilir | `allowed_updates` daralt, debounce, bounded queue |
| Provider rate limit/terms | Scraper kapanması veya hesap/host engeli | Resmi/RSS/açık veri tercih et, provider health/circuit, attribution |
| Stale cache | Kullanıcıya eski kur/hava/deprem göstermek | `fetched_at`, `stale`, SWR ve açık uyarı |
| Inline cache sızıntısı | Özel veya grup verisi başka kullanıcıya çıkar | Public/private cache ayrımı, kullanıcı/chat kapsamlı key |
| Media cache bypass | Kapalı özellik cache'ten dosya gönderir | Policy check cache lookup'tan önce uygulanır |
| Toggle race | Aynı anda iki admin zıt durum yazar | Atomik version/updated_at veya compare-and-set; audit |
| Mini App sahteciliği | Kullanıcı/chat yetkisi taklit edilir | HTTPS, Telegram `initData` doğrulaması, nonce ve expiry |
| Terms/lisans belirsizliği | Provider üretime alınamaz | `docs/DATA_SOURCES.md`, `licenses.json`, approval ID, kill switch |

## Kabul kriterleri

### P0 için

- Quote kapalıyken bütün quote command, callback ve inline yüzeyleri iş
  üretmiyor.
- Media kapalıyken otomatik link, `/indir` ve media callback'leri queue'ya iş
  göndermiyor; cache'ten gönderim yapılmıyor.
- Grup toggle'ını yalnızca admin/owner değiştirebiliyor; private kullanıcı
  grubun ayarını değiştiremiyor.
- Aynı callback tekrarlandığında tek audit kaydı ve tek durum değişimi oluşuyor.
- Disabled komutlar kullanıcıya hata mesajı spam'i üretmiyor.

### P1/P2 için

- Job panelindeki cancel/retry işlemleri owner/chat scope ile sınırlı.
- `callback_data` uzunluk, callback cevap süresi ve forum topic id testleri
  fixture/replay ile geçiyor.
- Admin command scope'ları Telegram staging kanıtında doğrulanıyor.
- `/health`, `/metrics` ve `/audit` feature policy durumunu secret veya kişisel
  veri sızdırmadan raporluyor.

## Kaynak değerlendirme tablosu

| Kaynak | Tür | Kanıt seviyesi | Sentezde ağırlık |
|---|---|---|---|
| [Telegram Bot API](https://core.telegram.org/bots/api) | Resmi birincil API sözleşmesi | Yüksek; güncel yöntem, alan ve limitler | Çok yüksek |
| [Bot API changelog](https://core.telegram.org/bots/api-changelog) | Resmi birincil sürüm kaydı | Yüksek; yeni update ve method'ların çıkış kaydı | Çok yüksek |
| [Bot commands](https://core.telegram.org/api/bots/commands) | Resmi birincil komut kapsamı açıklaması | Yüksek; scope fallback algoritması | Çok yüksek |
| [Inline bots](https://core.telegram.org/bots/inline) | Resmi ürün/API dokümanı | Yüksek; inline kullanım modeli | Yüksek |
| [Bot buttons](https://core.telegram.org/api/bots/buttons) | Resmi birincil düğme/callback açıklaması | Yüksek; callback davranışı | Yüksek |
| [Forum topics](https://core.telegram.org/api/forum) | Resmi birincil topic sözleşmesi | Yüksek; thread ve reply kuralları | Yüksek |
| [Reactions](https://core.telegram.org/api/reactions) | Resmi birincil reactions açıklaması | Yüksek; update ve gecikme modeli | Orta-yüksek |
| [Todo/checklists](https://core.telegram.org/api/todo) | Resmi birincil yeni API yüzeyi | Orta-yüksek; istemci/capability uyumu ayrıca doğrulanmalı | Orta |
| [Mini Apps](https://core.telegram.org/bots/webapps) | Resmi ürün ve güvenlik akışı | Yüksek; imza/scope uygulaması projeye ait | Orta |

Bu kaynakların hiçbiri provider terms veya Arelbot'un dış veri kaynaklarının
lisansını garanti etmez. Kaynaklarda birden fazla sayfa aynı Bot API sözleşmesini
özetliyor; bunlar bağımsız kanıt sayılmamalı, davranış için tek normatif referans
Bot API sözleşmesi ve changelog'dur.

## Sonuç ve karar

İlk uygulanacak iş yeni bir oyun veya yeni bir provider eklemek değil, mevcut
quote ve medya davranışlarını tek policy altında birleştirmektir. Kullanıcı
ayar menüsünde gördüğü kapalı durumun komut, callback, inline ve otomatik link
akışlarında aynı sonucu verdiğini görmelidir. Bu taban kurulduktan sonra
registry'den üretilen ayar ekranı, async job merkezi ve admin command scope'ları
Arelbot'un güvenilirlik ve kullanım kolaylığını en hızlı artıran adımlardır.

