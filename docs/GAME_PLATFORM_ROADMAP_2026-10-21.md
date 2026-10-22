# Arel oyun platformu: 40 oyunluk ürün yol haritası

**Tarih:** 2026-10-21

**Durum:** İnceleme ve uygulama planı. Bu listedeki adaylar yayımlanmış oyun sayılmaz.
**Temel:** `remediation/53-fixes` dalı (`534c610`); oyun çalışması ayrı `feature/arel-games-40` dalında.

## Yerel çalışmanın inceleme sonucu

- `main` dalı `c285c96` ile uzak depoyla aynı noktadaydı; takip edilen yeni dosya değişikliği yoktu. `.serena` yalnız yerel araç yapılandırmasıdır.
- Kullanıcının güncel çalışması `remediation/53-fixes` dalında, ana daldan sonra yapılmış 32 commit olarak bulundu. O dalda medya kuyruğu, üretim veritabanı kimliği, ekonomi işlem defteri, test kapısı ve dağıtım güvenliği iyileştirilmiş. Bu değişiklikler korunacak; oyun çalışması onların üstüne kurulacak.
- Oyun açısından kritik düzeltme: `state_store` ve `action_ledger` üretim veritabanını sarmalayıcı arkasında da tanır, bağlantı yoksa sessiz bellek durumuna düşmez. `EconomyService` ödül olayını ve bakiyeyi tek atomik depolama işlemiyle kaydeder. Yeni oyunlar aynı kuralları kullanmalı.
- `GAME_DEFINITIONS` 15 anahtar içeriyor. `wordle_tr` için kalıcı durum tanımı var; oyuncuya açık başlatma akışı bulunmadığından oynanabilir oyun diye sayılmıyor. Diğer 14 anahtarın komut/yüzey izleri var, ancak her biri için gerçek grup kabulü henüz yeniden ölçülmedi. Zar, yazı-tura ve slot gibi tek adımlı araçlar 40 oyun hedefinde sayılmıyor.
- Mevcut `docs/GAME_ROADMAP.md` 17 Ekim tarihli ve 40 oyun, Casus ile Vampir Köylü kapsamını taşımıyor. Bu belge onun yerine geçecek uygulama sırasını belirler; eski belge geçmiş tasarım kaydı olarak kalır.
- Başlangıç kapısı: oyun sözleşmesi, kalıcılık, replay, UNO, satranç ve Akinatör için seçilmiş 94 test geçti. Bu sonuç canlı Telegram kabulü veya bütün komutların geçtiği anlamına gelmez.
- Üretim veritabanının `save_game_state` yolu veritabanı nesnesi yokken sessizce dönebiliyor; `persist_game` bunu başarılı kayıt sanabiliyor. Ayrıca bu genel kayıt yolunda sürüm karşılaştırmalı yazma yok. Yeni grup oyunlarına geçmeden bu iki boşluk kapatılmalı.
- Mimari denetim başlangıçta dört dosyanın LF satır sonu kuralını ihlal ettiğini bildirdi (`chess.py`, `chess_ui.py` ve iki satranç testi). Oyun sürümünden önce düzeltilmeli.

## Sayma ve yayın ilkesi

Bir oyun ancak kendine özgü karar döngüsü, çalışır komut/menü, başlangıç-oynanış-bitiş akışı, hatalı/tekrarlı hamle savunması, yeniden başlatma sonrası devam, sonuç ve otomatik test içeriyorsa **oynanabilir** sayılır. Tema, zorluk, soru kategorisi, aynı oyunun solo/takım görünümü veya yalnızca registry kaydı ayrı oyun sayılmaz. `Hedef` aşağıdaki 40 ayrı oynanış mekaniğinin tamamının bu kabul kapısını geçmesidir; sayı ilerleme olarak şişirilmez. Eski oyunların hiçbiri kaldırılmaz.

| # | Oyun anahtarı | Ayırt edici oynanış | Oyuncu | Başlangıç |
|---:|---|---|---|---|
| 1 | `word_game` | Harf rafından geçerli sözcük türetme | 1–30 | Mevcut, doğrulanacak |
| 2 | `word_guess` | Kademeli ipucuyla gizli kelime tahmini | 1–30 | Mevcut, doğrulanacak |
| 3 | `hangman` | Harf ve tam sözcük tahmini, sınırlı can | 1–30 | Mevcut, doğrulanacak |
| 4 | `connect4` | Yerçekimli dört taş dizme | 2 | Mevcut, doğrulanacak |
| 5 | `gomoku` | Serbest tahtada beş taş dizme | 2 | Mevcut, doğrulanacak |
| 6 | `chess` | Kurallı satranç ve taraf bakışı | 2 | Mevcut, doğrulanacak |
| 7 | `akinator` | Uyarlamalı evet/hayır kişi tahmini | 1 | Mevcut, doğrulanacak |
| 8 | `guess_age` | Fotoğraf çekim tarihindeki yaşı tahmin | 1–100 | Mevcut, doğrulanacak |
| 9 | `uno` | Gizli elli, çok turlu renk/sayı kart oyunu | 2–8 | Mevcut, doğrulanacak |
| 10 | `tictactoe` | Seçilen ızgarada dört taş dizme | 2 | Mevcut, doğrulanacak |
| 11 | `quiz` | Soruları puanlı cevaplama | 1–100 | Mevcut, doğrulanacak |
| 12 | `batak` | Renk takipli el alma | 2–4 | Mevcut, doğrulanacak |
| 13 | `blackjack` | Krupiyeye karşı el kararı | 1–8 | Mevcut, doğrulanacak |
| 14 | `bottle` | Katılımcı seçimi ve doğruluk/cesaret turu | 2–100 | Mevcut, doğrulanacak |
| 15 | `wordle_tr` | Konum geri bildirimli sınırlı kelime denemesi | 1 | Tanım var, yüzey eksik |
| 16 | `tabu` | Gizli hedefi yasak sözcük kullanmadan anlatma | 4–20 | Yerel aday; motor/servis/Telegram adaptörü var, canlı kabul bekliyor |
| 17 | `kelime_zinciri` | Önceki kelimenin son harfiyle yeni kelime | 2–20 | Yerel aday; canlı kabul bekliyor |
| 18 | `karsi_kelime` | Sözcüğe geçerli zıt anlam bulma | 1–30 | Yeni |
| 19 | `anagram` | Karışık harflerden hedefi çözme | 1–30 | Yeni |
| 20 | `harf_maratonu` | Tur harfi ve kategorisiyle hızlı cevap | 2–30 | Yeni |
| 21 | `sozcuk_avi` | Izgarada komşu harflerle sözcük bulma | 1–30 | Yeni |
| 22 | `yasak_harf` | Verilen kavramı belirtilen harfi kullanmadan anlatma | 2–20 | Yeni |
| 23 | `bes_ipucu` | Beş kademeli ipucundan erken tahmin | 1–30 | Yeni |
| 24 | `ortak_hikaye` | Sıralı cümlelerle kısıtlı öykü kurma | 3–20 | Yeni |
| 25 | `emoji_bulmaca` | Emoji dizisinden kavram çözme | 1–30 | Yeni |
| 26 | `kor_siralama` | Sonraki seçenekleri görmeden ilk beşi sıralama | 1–30 | Yeni |
| 27 | `sayisal_tahmin` | Gizli sayıya mesafe ipuçlarıyla yaklaşma | 1–30 | Yeni |
| 28 | `hizli_buton` | Açılan cevap penceresine ilk doğru basış | 2–100 | Yeni |
| 29 | `ters_soru` | Verilen cevaba uygun soruyu kurma | 1–30 | Yeni |
| 30 | `kronoloji` | Olayları zaman sırasına yerleştirme | 1–30 | Yeni |
| 31 | `dogru_yanlis` | Gerekçeli önermeye doğru/yanlış kararı | 1–100 | Yeni |
| 32 | `iki_dogru_bir_yalan` | Oyuncunun üç iddiasından yalanı bulma | 3–30 | Yeni |
| 33 | `kimim_ben` | Alna yapıştırılan gizli kimliği sorularla bulma | 3–20 | Yeni |
| 34 | `hafiza_eslestirme` | Kısa süre gösterilen kart çiftlerini hatırlama | 1–8 | Yeni |
| 35 | `mayin_tarlasi` | Komşu sayılarından güvenli hücre çıkarma | 1–8 | Yeni |
| 36 | `amiral_batti` | Gizli yerleşimli hedef arama ve isabet | 2 | Yeni |
| 37 | `casus` | Gizli mekânı bilmeyen casusu soru ve oyla bulma | 4–16 | Yeni |
| 38 | `vampir_koylu` | Gece eylemi, gündüz tartışma ve gizli rol oylaması | 7–20 | Yeni |
| 39 | `dedektif` | Paylaşılan ipuçlarından suçlu ve yöntemi çıkarma | 3–20 | Yeni |
| 40 | `takim_bilgi_ligi` | Takım cevabı, pas ve tur bazlı risk kararı | 4–40 | Yeni |

## Önce ortak oyun zemini

1. Her oyun için registry + komut + menü + FeatureDefinition eşlemesi tek kaynaktan denetlensin. Yalnız tanımı olan oyun menüde yayımlanmasın. Oyunun görünürlüğü geçerli içerik ve hazır yüzey koşuluna bağlı olsun.
2. `GameSession` şemasına `thread_id`, açık faz, rollerin gizli durumu, sürüm ve zamanlayıcı sözleşmesi geriye uyumlu eklensin. Oyun başına tek aktif oturum, sohbet/konu izolasyonu, CAS ve event kimliği doğrulansın.
3. Ortak eylem sonucu `accepted / rejected / stale / finished` olsun. Callback kimliği ve mesaj kimliği denetlensin; aynı eylem yeniden gelirse ikinci hamle veya ödül oluşmasın.
4. `lobby → ready → active → result/cancel/expire` ve rol oyunlarında alt fazlar typed domain geçişleriyle yönetilsin. Yönetici/kurucu iptali, oyuncu ayrılması, süre aşımı ve bot yeniden başlaması her fazda tanımlı olsun.
5. Gizli bilgi yalnız ilgili kullanıcının özel sohbetine gönderilsin; grup paneli hiçbir rol, el, hedef kelime veya mekânı sızdırmasın. Botla özel sohbet açmamış oyuncu hazır sayılmasın; başlamadan önce anlaşılır bağlantı verilsin.
6. Türkçe `I/ı`, `İ/i`, Unicode ve noktalama için tek kelime normalizasyonu, izinli kelime sözlüğü, uygunsuz içerik filtresi, cevap eşanlamlıları ve anlaşmazlık çözümü kurulsun. Serbest LLM yargısı puan kararının tek kaynağı olmasın.
7. Tüm oyunlar için metin tabanlı erişilebilir görünüm, kısa yönerge, hamle sırası, kalan süre, gerekçe ve yeniden oynama butonu sağlansın. Görsel gerekli olduğunda kaynak lisansı doğrulansın; yapay görsel üretilmesin.
8. Oyun eylemleri, puan ve ekonomi ayrı idempotent event kimlikleriyle işlensin. Oyuncu/sohbet günlük sınırı, test oyunu dışlama ve başarısız ödülün yeniden denenmesi açık olsun.

## İstenen oyunların ayrıntılı kural sözleşmesi

### Tabu

- İki takım, takım başına en az iki kişi. Lobi takım dağılımını dengeler; anlatıcı sırası üyeler arasında döner. İtirazı, çıkar çatışmasını önlemek için maçta oynamayan bir grup yöneticisi değerlendirir; karar penceresi sonunda karar yoksa ilk sonuç korunur. Her oyuncunun özel sohbeti hazır olmadan tur başlamaz.
- Hedef ve 4–5 yasak kelime yalnız anlatıcıya özel mesajla gider. Grup ekranı kategori, anlatıcı, takım, süre ve skor gösterir. Anlatıcının kendi tahmini sayılmaz; yalnız etkin takımın diğer üyeleri cevap verebilir.
- Metin anlatımında hedef, yasak kelime, çekim ekiyle yalın biçim, bitişik yazım ve harf harf söyleme denetlenir. Otomatik kararın belirsiz olduğu eşanlamlı/çok kelimeli durumlar hakem itirazına açık olur; sesli anlatım otomatik denetlendi iddiasıyla sunulmaz.
- Doğru cevap +1, yasak kelime veya hedefi söyleme −1, pas sınırlı ve puansız; süre biterse kart yanar. Tek kart için bir kez sonuç; aynı mesaj tekrar oynatıldığında puan değişmez. Tüm takımlar eşit sayıda kart görür, sonra maç biter.
- Kabul: özel bilgi sızıntısı, yanlış takım cevabı, çift puan, yarışan mesajlar, anlatıcı ayrılması, yeniden başlatma, süre sonu, hakem itirazı ve küçük/kalabalık gruplar.

### Kelime zinciri ve Karşı kelime

- Zincirde ilk kelimeyi sırası gelen oyuncu seçer. Sonraki sözcük öncekinin **son harfiyle** başlar; Türkçe harfler ayrı kabul edilir, aynı sözcük tekrar edilmez, özel ad/yabancı kısaltma varsayılan olarak kabul edilmez. Sırası gelen oyuncuya 30 saniye; geçersiz cevap süreyi sıfırlamaz. Her oyuncu beş hamle ve iki pas hakkı alır; en çok harf puanı toplayan kazanır. Çıkış yolu olmayan sözcükte zincir sıfırlanır, eşit hamle hakkı korunur. Hiç doğru sözcük bulunmayan maç galibiyet veya ödül üretmez.
- Karşı kelime botun verdiği kavrama onaylı zıt anlamı arar. Birden fazla doğru yanıt verisi saklanır; dereceli anlamlar için kaynaklı açıklama gösterilir. Uydurma antonim veya yalnız ek olumsuzlaması kabul edilmez. Çok oyuncuda ilk doğru cevap turu kapatır, diğer oyuncuların eşzamanlı mesajı ledger ile sıralanır.
- Her iki oyunda içerik havuzu ayrı bir doğrulanmış Türkçe veri paketidir. Düşük güvenli otomatik sözlük adayları oyuncuya açılmadan önce gözden geçirilir.

### Casus

- 4–16 oyuncu; 1 casus (büyük masada ayarla 2), diğerleri aynı mekânı özel mesajla görür. Mekân havuzu Türkçeleştirilmiş, ipucu çeşitliliği ve ayırt edilebilirlik açısından gözden geçirilmiş olur. Herkes rol bildirimini aldığını onaylamadan oyun başlamaz.
- Rastgele ilk soran belirlenir; soru ve yanıt sırası grup panelinde görünür. Casus her an **tek** mekân tahmini yapabilir: doğruysa casus, yanlışsa diğerleri kazanır. Oyuncular belirlenen tur/süre sonunda şüpheli adayını oylar; kendine oy yok, ölü/ayrılmış oyuncu oy yok. Çoğunluk casusu bulursa grup, masumu elerse casus kazanır. Beraberlikte bir kısa yeniden oylama, yine eşitse casus kazanır; kural lobiye yazılır.
- Sonuç dışında mekân ve roller grup mesajına/telemetriye çıkmaz. Kullanıcı ayrılırsa oyuncu sayısı alt sınırına göre tur iptal edilir veya kalanlarla sürer; casus ayrılırsa grup galibiyeti, tek casuslu oyunda bitiştir.
- Kabul: dağıtım adaleti, rol sızıntısı, DM engeli, eski buton, konu izolasyonu, yeniden başlatma, çifte oy, eşitlik, son saniye yarışı, casusun mekân tahmini ve kurucu iptali.

### Vampir Köylü

- 7–20 oyuncu; temel roller köylü, vampir, kâhin ve şifacı. Ek roller ancak temel akış doğrulandıktan sonra: koruyucu, avcı, cadı. Rol dağılımı oyuncu sayısına göre dengeli şablon ve güvenli rastgelelikle yapılır; aynı takımdaki vampirler birbirini özel mesajda tanır. Başlangıçta gece vardır.
- Gece sırası: koruma → vampir hedefi → şifacı kurtarma → kâhin öğrenme → ölüm çözümü. Her rolün tek eylemi vardır; süresi dolunca `pas` uygulanır. Aynı kişiyi art arda koruma ve kendini iyileştirme hakları lobi ayarıdır. İşlenen eylemler CAS ile kapanır; sonradan gelen eski gece butonu sonuç değiştirmez.
- Sabah yalnız ölenler ve genel olay açıklanır; rol ancak lobi ayarı izin veriyorsa açıklanır. Gündüz tartışma ve tek oy penceresi, yaşayan oyuncuların çoğunluk oyu, eşitlikte kısa yeniden oylama, tekrar eşitlikte kimse elenmez. Elenen/ölen kişi sonraki gizli eylem ve oylamalara katılamaz; hayalet sohbeti oyun komutu sayılmaz.
- Köylüler tüm vampirleri eleyince, vampir sayısı diğer yaşayanlara eşit veya fazla olunca vampirler kazanır. Her faz geçişinden sonra galibiyet koşulu tekrar hesaplanır. Çıkış, DM engeli, bot yeniden başlatma ve süre sonu için açık senaryolar vardır; iptal edilen maç ödül üretmez.
- Kabul: tüm rol kombinasyonlarının taraf sonuçları, gece eylemi önceliği, eşzamanlı oy, ölü oyuncu yetkisi, korunmuş hedef, geç kalan eylem, beraberlik, rol gizliliği ve puan tekliği.

## Uygulama sırası ve kabul kapıları

| Dalga | İçerik | Bağımlılık | Çıkış koşulu |
|---|---|---|---|
| 0 | Mevcut 14 oyun ve Wordle için komut/menü/kural/envanteri; kırık akışların hata kaydı | Yerel dal | Her oyun için gerçek yüzey ve test durumu raporu |
| 1 | Ortak oturum, konu, gizli bilgi, zamanlayıcı, ledger, Türkçe sözlük ve menü sözleşmesi | 0 | Yarış, tekrar, restart, DM ve izin testleri |
| 2 | Wordle yüzeyi; Tabu, Zincir, Karşı kelime, Anagram, Harf Maratonu | 1 | Ayrı oynanışlı 20 oyun ve grup senaryosu |
| 3 | Sözcük Avı, Yasak Harf, Beş İpucu, Ortak Hikâye, Emoji Bulmaca; mevcut yaş/UNO/satranç son kullanıcı düzeltmeleri | 2 | 25 oyun, içerik kalite incelemesi |
| 4 | Kör Sıralama, Sayısal Tahmin, Hızlı Buton, Ters Soru, Kronoloji, Doğru Yanlış, İki Doğru Bir Yalan, Kimim Ben | 1, 3 | 33 oyun, adil skor ve aynı anda cevap testleri |
| 5 | Hafıza Eşleştirme, Mayın Tarlası, Amiral Battı | 1 | 36 oyun, gizli/ortak tahta güvenliği |
| 6 | Casus → Vampir Köylü → Dedektif → Takım Bilgi Ligi | 1–5 | 40 oyun, rol ve takım kabul matrisi |
| 7 | Tüm 40 oyun için menü, yardım, telemetry, fixture/replay, yük ve gerçek test grubunda baştan sona oynanış | 0–6 | Üretim adayı; kanıtsız oyun sayılmaz |

Her dalgada `python -m pytest`, mimari kontrolü, feature katalog denetimi, faz denetimi ve yayın öncesi aynı profil testleri çalışır. Gerçek grupta özel sohbet gerektiren oyunlar ancak gönüllü test oyuncuları ile doğrulanabilir; otomatik test bu kanıtın yerine geçmez. Sunucuya geçişte mevcut sürüm yedeği, geri dönüş noktası, veritabanı uyumu ve canlı komut kontrolü gerekir. GitHub eşitlemesi kullanıcının istediği gibi yerel/sunucu kabulünden sonra yapılır.

## Dış proje incelemesi

- [Polygraph](https://github.com/PiterPentester/polygraph): MIT lisanslı Telegram casus oyunu; lobi ve rol dağıtımı akışları karşılaştırma için uygun. Kod alınırsa telif bildirimi ve atıf kaydı zorunlu.
- [Mafia_bot](https://github.com/the-mahdi/Mafia_bot): MIT lisanslı grup rol/oy altyapısı örneği; Arel'in kuralları ve durum motoru bağımsız geliştirilecek.
- [turkcewordle](https://github.com/caglarorhan/turkcewordle): MIT lisanslı Türkçe kelime oyunu ve sözlük yaklaşımı için örnek. Kelime listesinin kapsamı ayrıca gözden geçirilmeli.
- [Taboo-Data](https://github.com/Kovah/Taboo-Data): GPL-3.0, İngilizce/Almanca veri. Türkçe içerik sağlamıyor; lisans uyumu ve içerik kalitesi incelenmeden kopyalanmayacak. Arel için özgün Türkçe kart havuzu tercih edilir.

Bu projeler şu an **entegre edilmedi**. Ticari olmamak lisans ve kaynak atfı yükümlülüklerini ortadan kaldırmaz.

## Sekiz bulmaca dalgası — 21 Ekim güncellemesi

Wordle yüzeyi, Karşı Kelime, Anagram, Hafıza Eşleştirme, Mayın Tarlası, Sayı Tahmini ve Refleks yukarıdaki ayrı mekaniklere karşılık gelir. Kullanıcının son listesindeki Sudoku da ek kapsam olarak uygulanır; minimum40 hedefi korunur. Bu adaylarla gerçek oyun katalog tanımı26 olur; bütün26oyun için insanlı canlı kabul iddiası yoktur. Sekiz adayın odaklı86testi geçti; tam kapı ve sunucu adayı ayrı kaydedilir. Ayrıntı: docs/PUZZLE_WAVE_PRODUCT_REVIEW_2026-10-21.md. Kullanıcı canlı Telegram testlerini şimdilik erteledi; GitHub yayını yetkilendirilmedi.

## 40 oyun dalgası — 22 Ekim güncellemesi

Kalan14 özgün mekanik games11 sürümünde yayımlandı; katalog40ayrı oyun içeriyor. Temalar, aliaslar veya zar/slot bu sayıya eklenmedi. Tam otomatik kapı25/25 ve gerçek sunucu adayında2726PASS/6SKIP doğrulandı. İnsanlı son oynanış kabulü kullanıcı isteğiyle ertelendi; katalog sayısı bu kabulün yerine geçmez. Ayrıntı: docs/GAMES40_PRODUCT_REVIEW_2026-10-22.md ve deploy/evidence/games40-deployment-20261022.json. GitHub yayını hâlâ ayrıca yetki bekler.
