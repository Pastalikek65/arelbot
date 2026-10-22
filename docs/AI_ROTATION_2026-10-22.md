# Cloudflare / EVREN / NVIDIA metin rotasyonu

Amaç: d2api bağımlılığını kaldırmak; bir sağlayıcının arızasını kullanıcıya
doğrudan yansıtmadan diğer yetkili sağlayıcılara geçmek. Bütün dış servislerin
aynı anda erişilebilir olacağı garanti edilmez.

- Varsayılan metin sırası: `cloudflare`, `evren`, `nvidia`.
- `chat`, `fast`, `analysis`, `summary` için ilk iki aday her istekte bir adım döner.
  `AI_ROTATION_PRIMARY_COUNT=2`: Cloudflare ve EVREN normal rotasyondadır;
  NVIDIA ikisi de başarısızsa denenir. Bu sınır aday listesini silmez.
- Açıkça belirtilmiş sağlayıcı listesi ve tek sağlayıcılı operatör rotaları
  korunur. Sunucu ortamındaki eski rota listesi yayın sırasında yenilenir.
- Arızalı sağlayıcının devresi geçici açılır; bekleme süresinden sonra yeniden
  denenebilir. Geçersiz kimlik, kota ve geçici kapasite hataları ayrılır.
- Normal istek bütçesi 40 saniye, metin sağlayıcı denemesi en fazla 12 saniye.
  Hızlı görevler mevcut `AI_FAST_TIMEOUT_SECONDS` bütçesini kalan adaylar
  arasında paylaşır. Bu, tek bir yavaş adayın bütün hızlı görev süresini
  tüketmesini önler; ağ hızına göre bir sonraki sağlayıcıya erken geçilebilir.
- Boş yanıtlar, protokol hataları ve sağlayıcı hataları geçiş nedenidir.
  Geçersiz kullanıcı girdisi başka sağlayıcılara tekrar gönderilmez.
- Açıkça boş medya/embedding rotası metin rotasına dönmez. Cloudflare metin
  adaptörü görsel, ses ve embedding çağrılarını ağ isteği yapmadan reddeder.
- Cloudflare hesap kimliği yalnızca sabit resmî URL yoluna yerleştirilir;
  bearer başlığına yalnızca token girer. Yönlendirmeler izlenmez.
- d2api ürün rotalarında emekliye ayrılmıştır. Eski adaptörün fixture testleri
  yalnızca geri dönüş/uyumluluk incelemesi için tutulur.

## Model ve maliyet seçimi

Cloudflare metin modeli `@cf/openai/gpt-oss-120b` olarak güncellendi.
22 Ekim 2026 resmî tarifesi: milyon giriş tokenı başına $0.35,
milyon çıkış tokenı başına $0.75. Önceki Qwen3 daha ucuzdu, fakat Türkçe
sohbet ve talimat takibi örneklerinde resmî metni tekrarlama ve yapay
ifadeler görüldü. NVIDIA Lightning örneğinde de bozuk Türkçe görüldüğü için
normal rotasyon yerine kesinti yedeği olarak tutulur.

Seçim üç sentetik Türkçe senaryonun manuel değerlendirmesine dayanır;
standartlaştırılmış bir dil benchmarkı veya her yanıtın kaliteli olacağına
dair garanti değildir. GPT-OSS 120B bağlamı korudu ve daha akıcı yanıtlar
verdi; samimi üslup ve gereksiz öğüt vermeme konusunda hâlâ kusurlar var.
Gemma ve Cloudflare DeepSeek adayları bu hesapla HTTP 403 verdi; NVIDIA
GPT-OSS 20B süre sınırını aştı, katalogda görünen Kimi ise HTTP 404 döndü.
Bunlar seçilmedi. Denemelerin ham sentetik yanıtları ve sınırlamaları
`deploy/evidence/turkish23-model-comparison.json` içindedir.

GPT-OSS kısa yanıtlarında `reasoning_effort=low` kullanılır. Açıkça yüksek
düşünme isteyen çağrılar korunur. Qwen'e özgü `/no_think` yalnızca Qwen3
seçildiğinde eklenir. Model değişimi kullanıcı mesajını değiştirmez.
512 token bütçeli Türkçe JSON ve weather araç denemeleri geçti; ilk
256 token JSON denemesi protokol hatası verdi ve gizlenmedi. Kısa bütçede
geçersiz yanıt mevcut fallback yoluna gider. Roast/persona içerikleri
incelenmedi ve değiştirilmedi.

Kaynaklar:

- https://developers.cloudflare.com/workers-ai/models/gpt-oss-120b/
- https://developers.cloudflare.com/workers-ai/models/qwen3-30b-a3b-fp8/
- https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/
- https://developers.cloudflare.com/workers-ai/features/reject-if-busy/
- https://developers.cloudflare.com/workers-ai/platform/pricing/
- https://huggingface.co/Qwen/Qwen3-30B-A3B-FP8

NVIDIA metin rotası için `NVIDIA_CHAT_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b` seçildi. Eski DeepSeek modeli sunucu denemesinde zaman aşımına uğrarken bu model aynı hesaptan yaklaşık 0.8 saniyede işaret yanıtını döndürdü. Ayrı metin ayarı mevcut görsel/ses/embedding model seçimlerini değiştirmez. Kaynak: https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b/modelcard .

Kullanıcı kendi Cloudflare hesabının API yetkisini açıkça sağladı. Bu kayıt
hesap kullanımı için işletmeci yetkisidir; model veya servis şartları hakkında
ek bir hukuki onay iddiası değildir. Hesap bilgileri Git, kaynak paketi ve
bu belgeye alınmaz. Roast içeriği incelenmez veya değiştirilmez.

## Doğrulama sınırları

Birim testleri: eşzamanlı dağılım, hatadan sonra geçiş ve devre toparlanması,
süre bütçesi, boş yanıt, JSON, araç yanıtı, medya izolasyonu, kimlik başlığı
ayrımı, eski d2api ortamının yeniden etkinleşememesi.

Yayın ve canlı sağlayıcı sonucu ayrıca `deploy/evidence` altında kaydedilir.
Bu tasarım belgesi tek başına canlı kabul kanıtı değildir.

## Türkçe düzenlemesi yayını (games23)

`2026.10.22-games-23`, kaynak `a6c971b078d55b8e0d120c7a726bae934b643646`
ile sunucuda aktif. Yerelde 3136 geçti / 21 atlandı; yalıtılmış sunucu
testinde 3151 geçti / 6 atlandı. Yeni 25 gate kontrolü başarılı.
Canlı API testinde altı seçim Cloudflare/Evren sırasıyla döndü; JSON, araç
ve iki ana sağlayıcı arızasında NVIDIA yedeği doğrulandı. Bot, worker, MongoDB
ve Redis sağlıklı, yeniden başlama sayıları sıfır. Veritabanı ve ayar yedekleri
alındı. İnsanlı Telegram sohbet testi NOT_RUN; dil değerlendirmesi sentetik
örneklerle sınırlı. Ayrıntı: `deploy/evidence/turkish23-deployment-20261022.json`.

## Sonraki roast kapsamı

Önceki yayınlarda roast içeriği kullanıcının isteğiyle okunmadı/değiştirilmedi.
Kullanıcı daha sonra yerel incelemeyi, ardından açık seçimli ve sansürsüz küfür
destekli roast değişikliğini açıkça istedi. Bu yeni kapsam ve doğrulama
`docs/EXPLICIT_ROAST_2026-10-22.md` içinde ayrı kaydedilir.
