# HRMN sağlayıcı entegrasyonu

İşletmecinin isteğiyle `https://api.hrmn.tr/v1` eklendi. Anahtar yalnızca özel
`.env` dosyasındaki `HRMN_API_KEY` değişkeninden okunur.

Sohbet sırası OpenAI → HRMN (`deepseek-v4.1-flash-1017`) → önceden yapılandırılmış
sağlayıcılardır. Eski sağlayıcıların kendi ayarları ve birbirleri arasındaki
sırası korunur. HRMN anahtarı yoksa eski yönlendirme aynen sürer. HRMN sohbeti
tek deneme yapar; en fazla 10 saniye bekler ve hata sonrası 30 saniye soğumaya
alınır. Genel yönlendiricinin daha kısa bütçesi de geçerlidir. HRMN ses,
embedding, görme veya sticker etiketleme rotalarına eklenmez.

Görseller HRMN'in `/chat/completions` protokolünü kullanır. Varsayılan sıra
`gpt-image-2.5-prism` → `gpt-image-2.5-flare` → mevcut Cloudflare'dır.
`--model prism` ve `--model flare` ilk modeli seçer. Her HRMN modeli için
35 saniyelik sınır vardır; mevcut işin toplam süre sınırı korunur. Görsel
bağlantıları HTTPS ve genel ağ adresi olarak doğrulanır, yönlendirmeler izlenmez,
indirilen içeriğin boyutu ve görsel yapısı sınanır. İndirme isteğine API anahtarı
eklenmez. Sağlayıcı yanıtları, medya bağlantıları ve anahtarlar loglanmaz.

Model listesi ve gerçek yanıtlar gözlendi. İlk soğuk sohbet denemesi zaman aşımına
uğradı; sonraki deneme 2,6 saniye sürdü. Prism/Flare üretim yanıtları 16–18
saniyede geldi. Bunlar kısa süreli gözlemlerdir; sürekli kullanılabilirlik
garantisi değildir. Sağlayıcı ve modellerin koşulları bağımsız incelenmiş
sayılmamıştır. GitHub yayını bu değişikliğin kapsamında değildir.
