# HRMN canlı kurulum kaydı

Etkin sürüm `2026.10.22-hrmn-3`, başlangıç sürümü `2026.10.22-codex-cli3`.
Çalışan rota OpenAI → HRMN → Cloudflare → EvrenAI → NVIDIA olarak okundu.
Diğer sağlayıcıların kendi sırası, modelleri, anahtarları ve ayarları korundu.
OpenAI modu `cli`, Codex sürümü `0.147.0`; Compose dosyası başlangıç sürümüyle
birebir aynı. Bot ve worker sağlık denetimleri başarılı, yeniden başlama sıfır.

Sunucu DeepSeek denemesi 2,28 saniyede boş olmayan yanıt verdi. İki HRMN görsel
modeli gerçek üretim, indirme ve görsel doğrulaması denemelerinden geçti.
112 ilgili test ve sunucuda 3176 test geçti; altı test atlandı. Gerçek Telegram
kullanıcı arayüzü denemesi yapılmış sayılmaz. GitHub'a yayın yapılmadı.

## Sonraki kurulum için önemli ayrıntı

Bu canlı profil, ortak `.env` dosyasından ayrı bir **sürüme ait `.env`** kullanır.
Compose'un `--env-file` seçeneği tek başına uygulama konteynerine yeni bir
değişkenin geçmesini sağlamaz; servisin `env_file` girdisi ayrıca kontrol
edilmelidir. HRMN anahtarı etkili dosyaya eklendi ve diğer satırların başlangıç
sürümüyle eşleştiği, değerler rapora yazılmadan doğrulandı. Sır içeren dosyalar
kaynak sunumuna ve Git'e dahil edilmedi.

Başlangıç sunucu test kopyasında dört eski yönlendirme beklentisi vardı. Aynı
dört hata değişmemiş başlangıç sürümünde de tekrarlandı. Yerelde zaten bulunan,
mevcut CLI davranışını tanımlayan testler sunucu adayına eşitlendi; sağlayıcı
davranışları bu testleri geçirmek amacıyla değiştirilmedi.

Canlı ölçüm kaydı: `deploy/evidence/hrmn-provider-live-20261022.json`.
