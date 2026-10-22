# Yayın sonucu: 2026.10.23-ai-reliability-1

Yerelde **3232 test geçti, 19 atlandı**. Sunucunun yalıtılmış ortamında
**3236 test geçti, 6 atlandı**. Özellik kataloğu 91/91 uyumlu. Sürüm manifesti,
kaynak paketinin üç parçası ve çalışan dört değişiklik dosyasının hash'leri
karşılaştırıldı. Bot ve işçi sağlıklı, yeniden başlatma sayıları sıfır.

## Gerçek sağlayıcı denemeleri

Normal üslupla yapılan dört gerçek `get_response` çağrısının tamamı
OpenAI `gpt-5.6-luna` tarafından yanıtlandı. Süreler 17,47 / 10,52 / 16,28 /
11,85 saniyeydi. Örnek yanıtlar:

- Gündelik sohbet: “selam, iyiyim ya sen nasılsın?”
- Diziye dalma: “bir bölüm diye açmışsın, dizi de geceyi komple rehin almış djsjsj”
- Moral bozukluğu: “tabii, buradayım. ne oldu, seni bu kadar düşüren şey ne?”

İsteklerin kısa `fast` bütçesine gönderilmesi ve yaklaşık 1,6 saniyede iptal
edilmesi düzeltildi. CLI kaynaklı açılış süresi korunurken bir HTTP yedeği için
süre bırakılır; toplam bütçe sınırlıdır. CLI'nin oturumu, normal `codex exec`
girişi ve kurulu 0.147.0 sürümü korunmuştur. Uygulama app-server kullanmaz.

Yalnızca yalıtılmış deneme örneğinde OpenAI hatası oluşturuldu. Gerçek HRMN
`deepseek-v4.1-flash-1017` yanıtı 2,60 saniyede geldi; üretimde hata enjekte
edilmedi ve kalıcı sağlayıcı ayarı değiştirilmedi.

## Görsel üretimi

Üretimdeki `ImageRouter` yolu, HRMN Prism ve Flare hatalarından sonra mevcut
Cloudflare yedeğiyle **1024×1024 JPEG** üretti. Dosya 263142 bayttı; Pillow
ile açıldı ve bütünlüğü doğrulandı. Süre 18,04 saniyeydi. HRMN modelleri hâlâ
upstream HTTP 500 döndürüyor; bu dış hata giderilmiş olarak kaydedilmedi.

Cloudflare'ın mevcut modeli değiştirilmeden JSON gövde hatası multipart
taşımayla düzeltildi. Aynı hesap ve model kullanılıyor.

## Korunanlar ve sınırlar

Yeni HRMN anahtarı bot ve işçide karşılaştırılarak doğrulandı. Diğer ortam
değerleri, Compose, sağlayıcı yapılandırma blokları ve Normal/Roast kartları
önceki sürümle karşılaştırıldı; korunuyor. GitHub güncellenmedi.

Kontroller gerçek API ve uygulama sohbet/üretim yolu üzerinden, sentetik
mesajlarla ve kalıcı konuşma kaydı kapalı olarak yapıldı. Manuel Telegram
kullanıcı testi **NOT_RUN**. Örneklerin kabulü, gelecekte her yanıtın aynı
mizah kalitesinde olacağının veya dış servislerin kesilmeyeceğinin kanıtı değildir.

Kanıt: `deploy/evidence/ai-reliability-live-20261023.json`.
