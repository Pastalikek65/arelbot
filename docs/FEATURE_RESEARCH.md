# Arelbot Ã–zellik AraÅŸtÄ±rmasÄ± ve Ã–nceliklendirme

Bu belge, Arelbot'un kullanÄ±cÄ±ya gÃ¶rÃ¼nen Ã¶zelliklerini mevcut kod yapÄ±sÄ±yla
eÅŸleÅŸtirir ve uygulanabilir sonraki adÄ±mlarÄ± Ã¶nceliklendirir. Belge, Ã¶zellikle
quote ve medya Ã¶zelliklerinin ayar menÃ¼sÃ¼nden kapatÄ±lÄ±p aÃ§Ä±lmasÄ± isteÄŸi iÃ§in
hazÄ±rlanmÄ±ÅŸtÄ±r. Yeni bir Ã¶zelliÄŸin Ã¼retime alÄ±nmasÄ± iÃ§in bu belge tek baÅŸÄ±na
yeterli deÄŸildir; Ã¶zellik manifesti, testleri, audit kaydÄ± ve kaynak/terms
onayÄ± da aynÄ± deÄŸiÅŸiklikte bulunmalÄ±dÄ±r.

**AraÅŸtÄ±rma tarihi:** 16 Ekim 2026  
**Birincil hedef:** Telegram grup ve Ã¶zel sohbetlerinde hÄ±zlÄ±, anlaÅŸÄ±lÄ±r ve
yetkisi doÄŸru ayrÄ±lmÄ±ÅŸ kullanÄ±cÄ± deneyimi  
**Kapsam:** Telegram Bot API, mevcut Arelbot modÃ¼lleri, kullanÄ±cÄ± akÄ±ÅŸlarÄ±,
cache/queue davranÄ±ÅŸÄ± ve Ã¼retim riskleri

## AraÅŸtÄ±rma sÄ±nÄ±rlarÄ± ve yÃ¶ntem

AraÅŸtÄ±rma sorusu ÅŸudur: "Arelbot'ta kullanÄ±cÄ±larÄ±n en sÄ±k karÅŸÄ±laÅŸtÄ±ÄŸÄ± sÃ¼rtÃ¼nme
noktalarÄ± hangileridir ve Telegram'Ä±n mevcut resmi yÃ¼zeyleri kullanÄ±larak
hangi Ã¶zellikler gÃ¼venli biÃ§imde Ã¶nceliklendirilebilir?"

Arama yalnÄ±zca Telegram'Ä±n resmi Bot API ve Telegram API dokÃ¼mantasyonunda
yapÄ±ldÄ±. Resmi Bot API referansÄ±, API changelog'u, Bot commands, inline bot,
forum, reactions, todo/checklist ve Mini App sayfalarÄ± tarandÄ±. Topluluk
bloglarÄ±, doÄŸrulanmamÄ±ÅŸ limit listeleri, scraper Ã¶rnekleri ve Ã¼Ã§Ã¼ncÃ¼ taraf
"en iyi bot" karÅŸÄ±laÅŸtÄ±rmalarÄ± kanÄ±t olarak kullanÄ±lmadÄ±. DÄ±ÅŸ provider'larÄ±n
terms ve lisanslarÄ± bu Ã§alÄ±ÅŸmanÄ±n kapsamÄ±na alÄ±nmadÄ±; her provider iÃ§in
`docs/DATA_SOURCES.md` ve provider governance onayÄ± gerekir.

## Mevcut repository bulgularÄ±

Ä°nceleme sÄ±rasÄ±nda aÅŸaÄŸÄ±daki davranÄ±ÅŸlar doÄŸrudan koddan doÄŸrulandÄ±:

| Alan | Mevcut durum | KullanÄ±cÄ± etkisi | Ã–ncelik |
|---|---|---|---|
| Quote `/q` | `QuoteHandler.handle_q_command` `q_enabled` ayarÄ±nÄ± kontrol ediyor | Ana quote komutu kapatÄ±labiliyor | P0 tabanÄ± |
| Quote yardÄ±mcÄ± komutlarÄ± | `/qrand`, `/qtop`, `/qd`, `/qtemplate`, `/qcolor`, `/qemoji`, `/qrate`, `/qtheme` aynÄ± ortak guard'Ä± kullanmÄ±yor | Quote kapalÄ±yken bazÄ± yÃ¼zeyler hÃ¢lÃ¢ Ã§alÄ±ÅŸabiliyor | P0 |
| Grup ayarlarÄ± | `CallbackHandler._build_ayar_keyboard` quote toggle iÃ§eriyor; medya toggle iÃ§ermiyor | Video indirme davranÄ±ÅŸÄ± admin panelinden kontrol edilemiyor | P0 |
| Sosyal link akÄ±ÅŸÄ± | `MessageHandler` URL'yi media queue'ya yÃ¶nlendiriyor; explicit command ve otomatik link akÄ±ÅŸÄ± ayrÄ± bir politika nesnesine baÄŸlÄ± deÄŸil | Admin kapatmak istese bile otomatik indirme baÅŸlayabiliyor | P0 |
| Feature registry | Public Ã¶zellikler registry ve manifest Ã¼zerinden tanÄ±mlÄ± | Yeni policy registry metadata'sÄ±na eklenirse komut, AI ve callback aynÄ± kuralÄ± kullanabilir | P0 tabanÄ± |
| Callback yÃ¼zeyi | Inline dÃ¼ÄŸmeler yoÄŸun biÃ§imde kullanÄ±lÄ±yor; callback'ler aÄŸÄ±r iÅŸleri queue'lara aktarÄ±yor | Ayar ekranÄ±nÄ± bÃ¼yÃ¼tmek mÃ¼mkÃ¼n, ancak callback payload sÄ±nÄ±rÄ± korunmalÄ± | P1 |
| Provider/cache | Typed provider, saÄŸlÄ±k ve SWR altyapÄ±sÄ± mevcut | AynÄ± kullanÄ±cÄ± isteÄŸi iÃ§in coalescing ve freshness etiketleri korunmalÄ± | P1 |
| Oyun ve Ã¼retkenlik | KalÄ±cÄ± state, leaderboard, hatÄ±rlatÄ±cÄ± ve plan yÃ¼zeyleri var | Ayarlar ve bildirimler aynÄ± policy/permission katmanÄ±na baÄŸlanmalÄ± | P1 |

Bu tablo `docs/FEATURE_CATALOG.md` ile birlikte okunmalÄ±dÄ±r. Bu belgede yeni
bir feature key tanÄ±mlanmaz; yalnÄ±zca uygulama sÄ±rasÄ± ve araÅŸtÄ±rma sonucu
belgelenir.

## P0 â€” Ã–zellik politikasÄ± ve gÃ¶rÃ¼nÃ¼r ayar kontrolÃ¼

### 1. Tek bir `ChatFeaturePolicy` sÃ¶zleÅŸmesi

Quote ve medya iÃ§in komutlarÄ±n her birine ayrÄ± ayrÄ± `if` eklemek kÄ±sa vadede
Ã§alÄ±ÅŸsa da zamanla yeni callback veya inline yÃ¼zeylerinin bu kuralÄ± atlamasÄ±na
yol aÃ§ar. Bunun yerine `src/features` altÄ±nda dependency injection ile verilen
bir policy nesnesi kullanÄ±lmalÄ±dÄ±r.

Ã–nerilen kavramsal sÃ¶zleÅŸme:

```python
class ChatFeaturePolicy(Protocol):
    async def is_enabled(self, chat_id: int, feature: str) -> bool: ...
    async def require_enabled(self, chat_id: int, feature: str) -> bool: ...
    async def set_enabled(self, chat_id: int, feature: str, enabled: bool, actor_id: int) -> None: ...
```

GerÃ§ek sÄ±nÄ±fÄ±n MongoDB eriÅŸimi repository Ã¼zerinden yapÄ±lmalÄ±dÄ±r. Handler
yalnÄ±zca `policy.is_enabled(...)` sonucunu kullanÄ±r. `feature` deÄŸerleri
registry'deki `quotes` ve `media` gibi key'lerle aynÄ± olmalÄ±dÄ±r; yeni bir
Ã¶zellik iÃ§in key, permission, timeout, cache ve audit alanlarÄ± manifestte
tanÄ±mlanÄ±r.

### 2. Quote kapalÄ±yken bÃ¼tÃ¼n yÃ¼zeyleri kapat

`quotes` kapalÄ± olduÄŸunda aÅŸaÄŸÄ±dakilerin tamamÄ± no-op olmalÄ±dÄ±r:

- `/q`, `/qrand`, `/qtop`, `/qd`, `/qtemplate`
- `/qcolor`, `/qemoji`, `/qrate`, `/qtheme`
- Quote callback'leri ve inline quote aramasÄ±
- Quote iÃ§in otomatik rating veya katalog iÅŸlemleri

Komutun sessizce yok sayÄ±lmasÄ± kullanÄ±cÄ± isteÄŸiyle uyumludur. Bir inline
callback'in Telegram istemcisinde yÃ¼kleniyor gÃ¶stergesini sonsuza kadar
bÄ±rakmamasÄ± iÃ§in callback sorgusu kÄ±sa bir toast ile cevaplanabilir; bu toast
yeni mesaj gÃ¶ndermemeli ve quote iÅŸini baÅŸlatmamalÄ±dÄ±r. YÃ¶netici ayar dÃ¼ÄŸmesi
quote Ã¶zelliÄŸini aÃ§tÄ±ÄŸÄ±nda mevcut theme/template deÄŸerleri korunur.

### 3. Video/medya indirme iÃ§in admin kontrollÃ¼ toggle

`media` kapalÄ± olduÄŸunda grup iÃ§inde:

- Sosyal link otomatik yakalama queue'ya iÅŸ eklememeli.
- `/indir`, `/dl` ve `/download` no-op olmalÄ±.
- Medya menÃ¼sÃ¼ndeki indirme callback'i iÅŸ baÅŸlatmamalÄ±.
- AynÄ± URL'nin yeniden gÃ¶nderilmesi eski cache'ten dosya gÃ¶ndermemeli.

Bu ayar grup sohbetlerinde yalnÄ±zca Telegram grup yÃ¶neticileri ve owner/sudo
iÃ§in deÄŸiÅŸtirilebilir. Ã–zel sohbette kullanÄ±cÄ±ya ait ayrÄ± bir tercih
gerekiyorsa aynÄ± policy store'un user scope'u kullanÄ±labilir; grup ayarÄ± Ã¶zel
sohbet ayarÄ±nÄ± ezmemelidir. VarsayÄ±lan davranÄ±ÅŸ geriye uyumluluk iÃ§in aÃ§Ä±k
tutulabilir, fakat aÃ§Ä±k/kapalÄ± durumu ayar panelinde her zaman gÃ¶rÃ¼nmelidir.

Her toggle deÄŸiÅŸimi `audit_logs` iÃ§ine actor, chat, feature, Ã¶nceki ve sonraki
durum ile yazÄ±lmalÄ±; secret, tam URL ve mesaj iÃ§eriÄŸi yazÄ±lmamalÄ±dÄ±r. Ä°ki metric
yeterlidir: `feature_toggle_changes_total` ve `feature_policy_denied_total`.

### 4. Ayar paneli tasarÄ±mÄ±

Ä°lk sayfa sÄ±k kullanÄ±lan ve maliyeti yÃ¼ksek Ã¶zellikleri gÃ¶sterir:

```text
âš™ï¸ Grup ayarlarÄ±
  Quote                 âœ… aÃ§Ä±k
  Video indirme         âœ… aÃ§Ä±k
  AI sohbet             âœ… aÃ§Ä±k
  Link filtresi         âŒ kapalÄ±
  HoÅŸ geldin            âœ… aÃ§Ä±k
  Sonraki sayfa         â€º
```

Her buton callback'te `chat_id`, `actor_id` ve yetkiyi yeniden doÄŸrular;
mesajdaki Ã¶nceki yazÄ±ya gÃ¼venmez. Uzun callback payload'Ä± yerine kÄ±sa bir
`ayar:toggle:q` gibi protokol kimliÄŸi ve server-side state kullanÄ±lmalÄ±dÄ±r.
Telegram'Ä±n `callback_data` alanÄ± 1â€“64 byte ile sÄ±nÄ±rlÄ±dÄ±r.

## P1 â€” KullanÄ±cÄ± deneyimini Ã¼retim seviyesine Ã§Ä±karma

### AyarlarÄ±n registry'den Ã¼retilmesi

MenÃ¼ metni, command listesi, callback policy ve `/features` Ã§Ä±ktÄ±sÄ± aynÄ±
registry metadata'sÄ±ndan Ã¼retilirse bir feature'Ä±n yalnÄ±zca bir yÃ¼zeyde aÃ§Ä±k
kalmasÄ± Ã¶nlenir. Her satÄ±rda ÅŸu bilgiler bulunabilir:

- durum: aÃ§Ä±k/kapalÄ±/degraded
- kapsam: Ã¶zel sohbet, grup, admin
- provider saÄŸlÄ±k durumu
- son veri zamanÄ± ve stale uyarÄ±sÄ±
- iÅŸlem tÃ¼rÃ¼: anlÄ±k veya kuyruÄŸa alÄ±nan
- ilgili komut ve yardÄ±m dÃ¼ÄŸmesi

Bu ekran provider secret veya tam kaynak URL'si gÃ¶stermemelidir.

### Asenkron iÅŸ merkezi

Medya, quote, sticker, gÃ¶rsel Ã¼retimi ve belge analizi iÃ§in tek bir kullanÄ±cÄ±
paneli Ã¶nerilir:

- aktif iÅŸler
- ilerleme veya aÅŸama (`probe`, `download`, `render`, `send`)
- iptal
- geÃ§ici hata sonrasÄ± yeniden dene
- tamamlanan son iÅŸler

Callback yalnÄ±zca job ID taÅŸÄ±r; job sahibi, chat ve feature server-side ledger
Ã¼zerinden doÄŸrulanÄ±r. AÄŸÄ±r iÅŸlem callback iÃ§inde yapÄ±lmaz. `answerCallbackQuery`
hemen Ã§aÄŸrÄ±lÄ±r, iÅŸ queue'ya bÄ±rakÄ±lÄ±r.

### Cache ve kaynak tazeliÄŸi

Public veri cevaplarÄ±nda `cache_hit`, `fetched_at` ve `stale` zarfÄ± korunmalÄ±.
Stale-while-revalidate akÄ±ÅŸÄ± kullanÄ±cÄ±ya aÃ§Ä±k etiket gÃ¶stermeli; eski deprem,
hava veya fiyat verisi gÃ¼ncelmiÅŸ gibi sunulmamalÄ±dÄ±r. AynÄ± sorgu iÃ§in Redis
request coalescing kullanÄ±lmalÄ±, cache anahtarlarÄ±nda kullanÄ±cÄ± Ã¶zel verisi
veya secret bulunmamalÄ±dÄ±r. Inline sonuÃ§larda chat/user kapsamÄ± cache sÄ±zÄ±ntÄ±sÄ±nÄ±
Ã¶nlemek iÃ§in sonuÃ§ kimlikleri deterministik ama iÃ§erik anahtarÄ± kapsamlÄ±
olmalÄ±dÄ±r.

### GÃ¼nlÃ¼k kullanÄ±m kolaylÄ±klarÄ±

Mevcut altyapÄ±ya az ek yÃ¼kle eklenebilecek Ã¶zellikler:

1. `/menu` ve `/ayar` iÃ§in sayfalÄ± kategori gezintisi ve "son kullanÄ±lanlar".
2. TÃ¼rkÃ§e/Ä°ngilizce command scope'larÄ± ve kullanÄ±cÄ± saat dilimine gÃ¶re tarih.
3. Bildirim merkezinde sessiz saat, bir sonraki teslim zamanÄ± ve tek tÄ±k kapatma.
4. Belge/medya iÅŸlerinde kullanÄ±cÄ±ya hata metni yerine sabit hata kodu ve kÄ±sa
   Ã§Ã¶zÃ¼m dÃ¼ÄŸmesi (`yeniden dene`, `ayarlarÄ± aÃ§`, `iÅŸi iptal et`).
5. Grup iÃ§in forum topic ID'sini koruyan ayar ve bildirim teslimatÄ±.
6. KullanÄ±cÄ± hafÄ±zasÄ± iÃ§in dÄ±ÅŸa aktarma, tek kayÄ±t silme ve tam temizleme.

## P2 â€” Telegram yÃ¼zeyleriyle farklÄ±laÅŸma

### YÃ¶netici komutlarÄ±nÄ± gerÃ§ekten gizlemek

Runtime yetki kontrolÃ¼ zorunludur; ancak komutlarÄ± kullanÄ±cÄ±ya hiÃ§ gÃ¶stermemek
ayrÄ± bir UX kazanÄ±mÄ±dÄ±r. Telegram Bot API `setMyCommands` ile aÅŸaÄŸÄ±daki
kapsamlarÄ± destekler:

- `BotCommandScopeAllGroupChats`
- `BotCommandScopeAllChatAdministrators`
- `BotCommandScopeChatAdministrators`
- `BotCommandScopeAllPrivateChats`
- belirli chat veya chat member kapsamÄ±

Ãœretim bootstrap'Ä±nda normal kullanÄ±cÄ± komutlarÄ± varsayÄ±lan/group kapsamÄ±na,
`/health`, `/metrics`, `/audit`, `/providers`, `/cache`, `/maintenance` gibi
komutlar `AllChatAdministrators` veya owner'a Ã¶zel `ChatMember` kapsamÄ±na
ayrÄ±lmalÄ±dÄ±r. Grup admin olmayan kullanÄ±cÄ± hÃ¢lÃ¢ komutu elle yazarsa runtime
guard isteÄŸi reddeder. Komut listesi deÄŸiÅŸince `getMyCommands` ile replay
doÄŸrulamasÄ± yapÄ±lmalÄ±dÄ±r.

### Reaksiyon tabanlÄ± geri bildirim

Telegram Bot API, yÃ¶neticinin aÃ§Ä±kÃ§a `allowed_updates` iÃ§inde istemesi halinde
`message_reaction` ve `message_reaction_count` gÃ¼ncellemelerini saÄŸlayabilir.
Arelbot bunu quote rating'e ek bir sinyal veya "bu yanÄ±t iÅŸime yaradÄ±" metriÄŸi
olarak kullanabilir. Reaksiyonlar gecikmeli ve toplu gelebileceÄŸi iÃ§in
analytics debounce edilmelidir. Mesaj metni ve kullanÄ±cÄ± adÄ± metric label
olmamalÄ±dÄ±r. Arel'in kendi bot mesajlarÄ±na verdiÄŸi reaksiyonlar update olarak
gelmez; geri bildirim akÄ±ÅŸÄ± buna gÃ¶re tasarlanmalÄ±dÄ±r.

### Native checklist uyarlamasÄ±

Telegram'Ä±n gÃ¼ncel API'si checklist/to-do mesajlarÄ±nÄ± destekliyor. Mevcut
`/gorev` ve `/plan` Mongo state'i Ã¶nce kaynak sistem olarak korunmalÄ±; native
checklist yalnÄ±zca Telegram istemci/API capability doÄŸrulandÄ±ktan sonra
Ã§Ä±ktÄ± formatÄ± olarak eklenmelidir. Aksi halde kullanÄ±cÄ± gÃ¶revleri ile native
mesajlarÄ±n Ã§ift yÃ¶nlÃ¼ senkronizasyonu gÃ¼venilir olmayabilir. Bu Ã¶zellik iÃ§in
ayrÄ± feature flag, migration ve replay fixture gerekir.

### Inline arama ve menÃ¼ button

Åehir, haber, quote, sticker ve public veri aramasÄ± iÃ§in inline sonuÃ§lar
pagination ve bounded `cache_time` ile sunulabilir. KullanÄ±cÄ± Ã¶zel notlarÄ±,
hafÄ±za kayÄ±tlarÄ± ve grup iÃ§i quote sonuÃ§larÄ± inline dÄ±ÅŸÄ±na Ã§Ä±kmamalÄ±.
Bot menu button kÄ±sa vadede command listesi olarak kalabilir; Ã§ok adÄ±mlÄ±
ayarlarÄ±n Mini App'e taÅŸÄ±nmasÄ± ancak HTTPS, `initData` imza doÄŸrulamasÄ±,
user/chat scope kontrolÃ¼ ve audit tamamlandÄ±ktan sonra feature flag arkasÄ±nda
yapÄ±lmalÄ±dÄ±r.

## P3 â€” Orta/uzun vadeli deneyler

Bu iÅŸler temel policy, queue ve gÃ¶zlemlenebilirlik kanÄ±tÄ± olmadan baÅŸlatÄ±lmamalÄ±:

- Reaction-aware baÅŸarÄ± ve oyun Ã¶dÃ¼lleri.
- Native checklist ile Telegram dÄ±ÅŸa aktarma.
- Admin iÃ§in Mini App tabanlÄ± provider/feature dashboard.
- Telegram Stars/payments veya paid media.
- Botun baÄŸlÄ± Business hesabÄ± adÄ±na mesaj iÅŸleme.
- Chat boost/giveaway tabanlÄ± topluluk ekonomisi.

Ã–deme, Stars, Business ve paid media akÄ±ÅŸlarÄ± ayrÄ± terms, muhasebe, fraud ve
geri Ã¶deme tasarÄ±mÄ± gerektirir; bunlar yalnÄ±zca "birkaÃ§ buton" Ã¶zelliÄŸi
deÄŸildir. Ãœretim feature'Ä± olarak aÃ§Ä±lmadan Ã¶nce hukuki ve saÄŸlayÄ±cÄ± koÅŸullarÄ±
incelenmelidir.

## Uygulama sÄ±rasÄ±

### Dalga 1 â€” Policy ve test (P0)

1. `ChatFeaturePolicy` ve repository/store sÃ¶zleÅŸmesini ekle.
2. `quotes` ve `media` iÃ§in varsayÄ±lanlarÄ± migration-safe biÃ§imde tanÄ±mla.
3. QuoteHandler, CommandHandler, CallbackHandler, MessageHandler ve inline
   servislerinin tamamÄ±nÄ± policy guard'a baÄŸla.
4. Admin-only toggle callback'lerini ekle; actor/chat/permission tekrar
   doÄŸrulamasÄ± ve audit/metric yaz.
5. Disabled/enabled, private/group, admin/non-admin ve duplicate callback
   testlerini ekle.

### Dalga 2 â€” UX ve operasyon (P1)

1. SayfalÄ± ayar panelini registry metadata'sÄ±ndan Ã¼ret.
2. Ä°ÅŸ merkezi iÃ§in ortak job projection ve cancel/retry callback'i ekle.
3. Cache freshness/stale etiketini ortak presentation katmanÄ±nda gÃ¶ster.
4. Provider/queue saÄŸlÄ±ÄŸÄ±nÄ± ayar ekranÄ±na yalnÄ±zca Ã¶zet olarak ekle.
5. Feature toggle deÄŸiÅŸimlerini `/audit` ve `/metrics` Ã¼zerinden doÄŸrula.

### Dalga 3 â€” Telegram komut ve inline yÃ¼zeyi (P2)

1. Bootstrap'ta command scope'larÄ±nÄ± kur; admin komutlarÄ±nÄ±n kullanÄ±cÄ±
   menÃ¼sÃ¼nde gÃ¶rÃ¼nmediÄŸini Telegram staging'de doÄŸrula.
2. Inline aramayÄ± public, bounded ve scope-aware hale getir.
3. Forum topic, callback timeout ve pagination replay testlerini Ã§alÄ±ÅŸtÄ±r.
4. Reaksiyon gÃ¼ncellemeleri iÃ§in opt-in analytics feature flag'i ekle.

### Dalga 4 â€” Deneysel Ã¶zellikler (P3)

1. Native checklist capability probe ve kullanÄ±cÄ± opt-in'i.
2. Mini App prototipi; yalnÄ±zca staging'de imza ve scope testleri geÃ§ince
   production flag'i aÃ§.
3. Payments/Stars/Business iÃ§in ayrÄ± karar kaydÄ±, terms ve abuse planÄ±.

## Risk, terms, cache ve rate limit notlarÄ±

| Risk | Etki | Kontrol |
|---|---|---|
| Callback payload 64 byte sÄ±nÄ±rÄ± | Uzun veya manipÃ¼le edilmiÅŸ callback | KÄ±sa opaque ID; server-side state; owner/chat doÄŸrulamasÄ± |
| Callback timeout | Telegram istemcisinde sonsuz spinner | Ã–nce `answerCallbackQuery`, aÄŸÄ±r iÅŸi queue'ya bÄ±rak |
| Command scope fallback'i | Admin komutunun normal kullanÄ±cÄ±ya gÃ¶rÃ¼nmesi | Dar scope'larÄ± set et, `getMyCommands` ile staging replay yap |
| Bot API/update hacmi | Reaksiyon veya geniÅŸ group update'leri gecikme yaratabilir | `allowed_updates` daralt, debounce, bounded queue |
| Provider rate limit/terms | Scraper kapanmasÄ± veya hesap/host engeli | Resmi/RSS/aÃ§Ä±k veri tercih et, provider health/circuit, attribution |
| Stale cache | KullanÄ±cÄ±ya eski kur/hava/deprem gÃ¶stermek | `fetched_at`, `stale`, SWR ve aÃ§Ä±k uyarÄ± |
| Inline cache sÄ±zÄ±ntÄ±sÄ± | Ã–zel veya grup verisi baÅŸka kullanÄ±cÄ±ya Ã§Ä±kar | Public/private cache ayrÄ±mÄ±, kullanÄ±cÄ±/chat kapsamlÄ± key |
| Media cache bypass | KapalÄ± Ã¶zellik cache'ten dosya gÃ¶nderir | Policy check cache lookup'tan Ã¶nce uygulanÄ±r |
| Toggle race | AynÄ± anda iki admin zÄ±t durum yazar | Atomik version/updated_at veya compare-and-set; audit |
| Mini App sahteciliÄŸi | KullanÄ±cÄ±/chat yetkisi taklit edilir | HTTPS, Telegram `initData` doÄŸrulamasÄ±, nonce ve expiry |
| Terms/lisans belirsizliÄŸi | Provider Ã¼retime alÄ±namaz | `docs/DATA_SOURCES.md`, `licenses.json`, approval ID, kill switch |

## Kabul kriterleri

### P0 iÃ§in

- Quote kapalÄ±yken bÃ¼tÃ¼n quote command, callback ve inline yÃ¼zeyleri iÅŸ
  Ã¼retmiyor.
- Media kapalÄ±yken otomatik link, `/indir` ve media callback'leri queue'ya iÅŸ
  gÃ¶ndermiyor; cache'ten gÃ¶nderim yapÄ±lmÄ±yor.
- Grup toggle'Ä±nÄ± yalnÄ±zca admin/owner deÄŸiÅŸtirebiliyor; private kullanÄ±cÄ±
  grubun ayarÄ±nÄ± deÄŸiÅŸtiremiyor.
- AynÄ± callback tekrarlandÄ±ÄŸÄ±nda tek audit kaydÄ± ve tek durum deÄŸiÅŸimi oluÅŸuyor.
- Disabled komutlar kullanÄ±cÄ±ya hata mesajÄ± spam'i Ã¼retmiyor.

### P1/P2 iÃ§in

- Job panelindeki cancel/retry iÅŸlemleri owner/chat scope ile sÄ±nÄ±rlÄ±.
- `callback_data` uzunluk, callback cevap sÃ¼resi ve forum topic id testleri
  fixture/replay ile geÃ§iyor.
- Admin command scope'larÄ± Telegram staging kanÄ±tÄ±nda doÄŸrulanÄ±yor.
- `/health`, `/metrics` ve `/audit` feature policy durumunu secret veya kiÅŸisel
  veri sÄ±zdÄ±rmadan raporluyor.

## Kaynak deÄŸerlendirme tablosu

| Kaynak | TÃ¼r | KanÄ±t seviyesi | Sentezde aÄŸÄ±rlÄ±k |
|---|---|---|---|
| [Telegram Bot API](https://core.telegram.org/bots/api) | Resmi birincil API sÃ¶zleÅŸmesi | YÃ¼ksek; gÃ¼ncel yÃ¶ntem, alan ve limitler | Ã‡ok yÃ¼ksek |
| [Bot API changelog](https://core.telegram.org/bots/api-changelog) | Resmi birincil sÃ¼rÃ¼m kaydÄ± | YÃ¼ksek; yeni update ve method'larÄ±n Ã§Ä±kÄ±ÅŸ kaydÄ± | Ã‡ok yÃ¼ksek |
| [Bot commands](https://core.telegram.org/api/bots/commands) | Resmi birincil komut kapsamÄ± aÃ§Ä±klamasÄ± | YÃ¼ksek; scope fallback algoritmasÄ± | Ã‡ok yÃ¼ksek |
| [Inline bots](https://core.telegram.org/bots/inline) | Resmi Ã¼rÃ¼n/API dokÃ¼manÄ± | YÃ¼ksek; inline kullanÄ±m modeli | YÃ¼ksek |
| [Bot buttons](https://core.telegram.org/api/bots/buttons) | Resmi birincil dÃ¼ÄŸme/callback aÃ§Ä±klamasÄ± | YÃ¼ksek; callback davranÄ±ÅŸÄ± | YÃ¼ksek |
| [Forum topics](https://core.telegram.org/api/forum) | Resmi birincil topic sÃ¶zleÅŸmesi | YÃ¼ksek; thread ve reply kurallarÄ± | YÃ¼ksek |
| [Reactions](https://core.telegram.org/api/reactions) | Resmi birincil reactions aÃ§Ä±klamasÄ± | YÃ¼ksek; update ve gecikme modeli | Orta-yÃ¼ksek |
| [Todo/checklists](https://core.telegram.org/api/todo) | Resmi birincil yeni API yÃ¼zeyi | Orta-yÃ¼ksek; istemci/capability uyumu ayrÄ±ca doÄŸrulanmalÄ± | Orta |
| [Mini Apps](https://core.telegram.org/bots/webapps) | Resmi Ã¼rÃ¼n ve gÃ¼venlik akÄ±ÅŸÄ± | YÃ¼ksek; imza/scope uygulamasÄ± projeye ait | Orta |

Bu kaynaklarÄ±n hiÃ§biri provider terms veya Arelbot'un dÄ±ÅŸ veri kaynaklarÄ±nÄ±n
lisansÄ±nÄ± garanti etmez. Kaynaklarda birden fazla sayfa aynÄ± Bot API sÃ¶zleÅŸmesini
Ã¶zetliyor; bunlar baÄŸÄ±msÄ±z kanÄ±t sayÄ±lmamalÄ±, davranÄ±ÅŸ iÃ§in tek normatif referans
Bot API sÃ¶zleÅŸmesi ve changelog'dur.

## SonuÃ§ ve karar

Ä°lk uygulanacak iÅŸ yeni bir oyun veya yeni bir provider eklemek deÄŸil, mevcut
quote ve medya davranÄ±ÅŸlarÄ±nÄ± tek policy altÄ±nda birleÅŸtirmektir. KullanÄ±cÄ±
ayar menÃ¼sÃ¼nde gÃ¶rdÃ¼ÄŸÃ¼ kapalÄ± durumun komut, callback, inline ve otomatik link
akÄ±ÅŸlarÄ±nda aynÄ± sonucu verdiÄŸini gÃ¶rmelidir. Bu taban kurulduktan sonra
registry'den Ã¼retilen ayar ekranÄ±, async job merkezi ve admin command scope'larÄ±
Arelbot'un gÃ¼venilirlik ve kullanÄ±m kolaylÄ±ÄŸÄ±nÄ± en hÄ±zlÄ± artÄ±ran adÄ±mlardÄ±r.

