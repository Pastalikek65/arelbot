# Oyun incelemesi — 22 Ekim 2026

İki bağımsız ajan salt okunur kaynak incelemesi yaptı. Aşağıdaki bulgular canlı
maçta yeniden üretilmiş hata kabulü değildir; doğrulama senaryoları ayrıca
yürütülmelidir. Roast içeriği incelenmedi. GitHub değiştirilmedi.

## Öncelikli hata yolları

| Öncelik | Kaynak | Bulgu | Doğrulama / önerilen düzeltme |
|---|---|---|---|
| P1 | `src/handlers/callback_handler.py:7162`, `:7879` | Skor yazıldıktan sonra para ödemesi hata verirse tekrar deneme skor kaydını yinelenmiş buluyor ve ödülü yeniden denemiyor. | Skor yazımını başarılı, ilk para ödemesini hatalı yapıp aynı sonucu yeniden işle. Skor ve ödül için birbirinden bağımsız idempotency ve kalıcı ödeme kuyruğu kullan. |
| P1 | `src/handlers/callback_handler.py:476`, `:7245`, `:7295` | Bazı eski callback yollarında kalıcı işlem makbuzu durum yazımından önce alınıyor; yazım hatasında makbuz serbest bırakılmıyor. | Adam Asmaca'nın ilk durum yazımında Mongo hatası üret, aynı callback'i yeniden dene. İşlem kiralaması ve başarısızlıkta yeniden deneme yolu uygula. Bu bulgu ayrı CAS yolu kullanan tahta oyunlarına genellenmez. |
| P2 | `src/handlers/callback_handler.py:7676` | Kelime pas geçişi, eski durumu sildikten sonra Telegram mesajını değiştirip bellekte üç saniyelik iş planlıyor. Mesaj hatası veya yeniden başlama geçişi yarım bırakabilir. | Telegram düzenlemesini hatalı yap; ayrıca üç saniye dolmadan süreci yeniden başlat. Sonraki turu kalıcı geçiş/deadline ile kurtar. |

## Oynanış ve kullanım geliştirmeleri

| Öncelik | Kaynak | Mevcut sınırlama | Öneri |
|---|---|---|---|
| P1 | `src/handlers/callback_handler.py:7901` | Ortak bilgi yarışması sorusu ilk yanıtla kapanıyor; yanlış yanıt da turu bitiriyor. | Süreli grup turu, kişi başına tek yanıt, özel callback geri bildirimi ve süre sonunda toplu puanlama. |
| P1 | `src/games/word_chain.py:32`, `src/services/word_chain_service.py:122`, `src/handlers/game_runtime.py:140` | Oynanmaya başlanmamış kelime zinciri lobisinin otomatik sonlanması yok. | Kalıcı lobi sonlanma zamanı, yeniden başlatma sonrası temizlik ve gerektiğinde kurucu devri. |
| P2 | `src/games/word_puzzles.py:299`, `src/handlers/word_puzzles_handler.py:195` | Yanlış veya geçersiz bazı kelime yanıtları sessizce tüketiliyor. | Grubu spamlamayan, oran sınırlı geri bildirim ve açık yanıt kuralları. |
| P2 | `src/handlers/word_puzzles_handler.py:181`, `src/services/word_puzzles_ui.py:101` | Aynı konuda birden fazla bulmaca açıksa normal mesajın hangi oyuna ait olduğu belirsiz; yönerge bunu açıklamıyor. | İkinci oyunu engelle veya bu durumda panele yanıt zorunluluğunu açıkça belirt. |
| P2 | `src/handlers/callback_handler.py:7751`, `src/games/quiz.py:213` | Kategori seçiminde yeni QuizGame oluşturulması, nesne içindeki soru tekrar listesini sıfırlıyor. | Sohbet başına yakın geçmişte sorulan soru kimliklerini kalıcı tut. |
| P3 | `src/handlers/command_handler.py:2628`, `src/games/word_chain.py:143` | Süresi bitmiş oyuncuya pas komutu öneriliyor; domain süresi bitmiş pası reddediyor. | Otomatik sıra devrini bekleme/panel yenileme yönergesi göster. |
| P3 | `src/games/word_chain.py:13`, `:89` | En yüksek oyuncu/tur/süre ayarları yaklaşık 50 dakikalık maç oluşturabiliyor. | Kısa ve standart maç önayarları, lobide tahmini süre. |

## Bu yayındaki kart düzeltmesi

52 iskambil ve 54 UNO pasif yüzünün tamamında orijinal renk ve görünür
kart pikselleri korunur. Kilit şeffaf sağ boşluktadır; sayı, sembol veya
aksiyon çizimi örtülmez. Oynanamaz kartlar domain tarafından yine reddedilir;
sticker görünümü hamle yetkisi sağlamaz. Eski `grey:` kimlikleri protokol
uyumluluğu için korunur. Yukarıdaki diğer bulgular bu kart değişikliğinde
düzeltilmiş sayılmaz.

## d2api

Yerel ve sunucu hesap anahtarlarının eşitliği hash ile doğrulandı. Sunucudan
DeepSeek `/api/v0/users/current` isteği HTTP 200 içinde `code=40003` döndürdü.
Hesap dosyasında kullanılabilir cookie veya e-posta/parola yoktu. Bu nedenle
anahtarın kendiliğinden yenilenmesi mümkün değildi; kullanıcı için anahtarı
sohbete yazdırmayan yerel yeniden bağlantı formu hazırlandı. Başarılı gerçek
tamamlama doğrulanana kadar d2api birincil model kabulü verilmez.


## Sonraki yayın: AI rotasyonu

Kullanıcı d2api yerine Cloudflare / EVREN / NVIDIA istedi. 22. sürüm sunucuda etkinleştirildi ve d2api durduruldu. Üç sağlayıcı, altı istekli sıra dönüşümü, Cloudflare JSON ve araç çağrısı ile yapay 503 sonrası EVREN geçişi canlı API testinde doğrulandı. Ayrıntı: `deploy/evidence/cloudflare22-deployment-20261022.json`. Yukarıdaki oyun/ekonomi kaynak bulguları bu AI değişikliğinde düzeltilmiş sayılmaz.
