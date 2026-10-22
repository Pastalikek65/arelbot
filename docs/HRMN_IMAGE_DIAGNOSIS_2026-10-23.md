# Prism / Flare görsel üretim tanısı — 23 Ekim 2026

## Sonuç

**BLOCKED_UPSTREAM:** İstenen iki model için yeni anahtarla gerçek üretim
denendi; HRMN `HTTP 500` ve `no provider responded, server side error`
döndürüyor. Başarılı görsel üretildiği iddia edilmiyor. Bu hizmet içindeki
sağlayıcı eşlemesi/kapasitesi buradan değiştirilemiyor; daha ayrıntılı kök
neden ancak HRMN sunucu kayıtlarından belirlenebilir.

## Yapılan ayrıştırma

| Kontrol | Sonuç |
|---|---|
| Yetkili `GET /v1/models` | 200; iki model de listede |
| Mevcut `/chat/completions`, `size=1024x1024`, `stream=false` | İki modelde 500; yaklaşık 2 saniye |
| Yalnız `model` ve `messages` içeren en küçük istek | İki modelde aynı 500 |
| Aynı modelleri sunucudan çağırma | Aynı hata; yerel makineye özgü değil |
| Önceki `/images/generations` denemesi | 404; bu uç noktaya geçmek çözüm değil |
| Aynı hesapla metin modeli | Önceki canlı kontrolde 200; genel anahtar geçersizliği doğrulanmadı |
| Önceki günkü görsel denemeleri | Her iki modelde geçerli görsel alınmıştı; tarihsel başarı bugünkü kabul değildir |

Sunucu istek kimlikleri (sağlayıcı desteği için):

- Prism: `req_edbfc0dfac9c4de0801bb3f8`
- Flare: `req_5c46b94da4a0cdd05816d8d4`

## Arel tarafında düzeltme

- Açık `--model prism` / `--model flare` seçimi yalnız seçilen modeli çağırır.
  Başka sağlayıcının çıktısı seçilen modelin başarısı gibi sunulmaz.
- Model belirtilmeyen isteklerde mevcut Prism → Flare → Cloudflare yedeği
  korunur; sohbet sağlayıcıları ve Codex CLI köprüsü değiştirilmez.
- Hata tanısı yalnız HTTP durumu, doğrulanmış istek kimliği ve izinli neden
  kodunu taşır. Ham hata, anahtar ve imzalı indirme bağlantısı dışarı verilmez.
- Hata gövdesi en fazla 4096 bayt okunur. Sınırsız tekrar deneme eklenmez.

## Yeniden doğrulama

```text
python scripts/check_hrmn_images.py
python scripts/check_hrmn_images.py --models prism
python scripts/check_hrmn_images.py --models flare
```

Bu komut **gerçek, ücret doğurabilecek** üretim çağrısı yapar. Her model için
JSON sonuç verir; yedeğe geçmez. Başarıda dosyanın biçimini/boyutunu doğrular
ve geçici dosyayı siler. İki üretim başarılıysa çıkış 0, herhangi biri
başarısızsa 1, anahtar yoksa 2 olur. Yeni API anahtarı yalnız özel ortam
dosyasında tutulur.

Hizmet düzeldikten sonra iki modelin de `status=generated` sonucu alınmadan
bu kaydın durumu başarılıya çevrilmemelidir. Manuel Telegram kabulü bu
tanının kapsamında değildir.
