# Sohbet ve görsel üretimi: hata nedenleri ve düzeltmeler

## Ölçülen nedenler

Kullanıcı sohbeti `fast` görevine gönderiliyordu. Toplam sekiz saniye beş
sağlayıcıya paylaştırılıyor, ilk dört sağlayıcı yaklaşık 1,6 saniyede iptal
ediliyordu. Sunucudaki doğrudan denemede OpenAI CLI 17,55 saniyede; HRMN
eski anahtarla 2,95, yeni anahtarla 1,56 saniyede yanıt verdi. Bu yüzden
NVIDIA'ya düşülmesi, önceki sağlayıcıların çalışmadığını kanıtlamıyordu.

CLI modeli boştu. Açıkça `gpt-5.6-luna` ile yapılan deneme başarılı oldu ve
11,24 saniyede samimi Türkçe yanıt verdi. `gpt-6-luna` denemesi başarısızdı;
çalıştığı iddia edilmez. CLI artık varsayılan olarak açıkça `gpt-5.6-luna`
seçer; yalnızca bu model ve `gpt-6-luna` kabul edilir.

## Uygulanan değişiklikler

- Kullanıcı sohbeti ve grup yanıt planı mevcut `chat` zaman bütçesini kullanır.
  Hafif makine görevlerinin `fast` bütçesi ve diğer sağlayıcıların ayarları korunur.
- CLI'nin süreç açılışına ayrılan süre, toplam konuşma bütçesinde bir HTTP
  sağlayıcı denemesi için süre bırakarak hesaplanır. Sonsuz bekleme yoktur.
- Dış iptal veya zaman aşımında yalnızca bu isteğin başlattığı CLI süreç grubu
  sonlandırılır ve süreç toplanır. Hesap veya kimlik dosyası değiştirilmez.
- Standart `codex exec` kullanılır; app-server kullanılmaz.
- Cloudflare'ın mevcut FLUX.2 klein 4B modeli için multipart gövde gönderilir.
  Önceki JSON modellerinin taşıması korunur. HTTP 400/404/415/422 gibi gövde/model
  hataları ortak hesap anahtarını soğutmaz; asıl görsel hatası gizlenmez.
- Yeni HRMN anahtarı yalnızca özel ortam dosyasında tutulur; kaynak paketine,
  örnek ortama ve kanıtlara eklenmez.

Cloudflare JSON denemesi HTTP 400, multipart denemesi HTTP 200 verdi; 1024×1024
JPEG doğrulandı. HRMN Prism/Flare denemeleri upstream `HTTP 500/server_error`
döndürdü. Bu dış servis hatası giderilmiş gibi gösterilmez; mevcut görsel yedeği
uygulama yolu üzerinden ayrıca doğrulanır.

Resmî taşıma sözleşmesi:
[Cloudflare FLUX.2 klein 4B](https://developers.cloudflare.com/changelog/post/2026-01-15-flux-2-klein-4b-workers-ai/).

Normal/Roast kartları bu düzeltmede değiştirilmez. Önceki üslup bozukluğunun
NVIDIA yedeğine düşmeden kaynaklanan bölümü, gerçek sohbet yolunda yeniden
ölçülür. Canlı sonuçlar `deploy/evidence/ai-reliability-live-20261023.json`
dosyasında raporlanır. Otomatik kontrol, manuel Telegram kullanıcı testi yerine
geçmez. GitHub yayını bu kapsamda yapılmaz.
