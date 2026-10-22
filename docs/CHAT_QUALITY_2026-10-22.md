# Arel Türkçe, kişilik ve gecikme düzeltmesi — 22 Ekim 2026

Durum: aday sürüm. Gerçek Luna kabul testi ve üretim aktivasyonu bekliyor.

## Değişiklikler

- Normal sohbet kartı artık ciddi/dengeli/iğneleyici modlara eklenmiyor. Her mod tek
  seçili talimatla hazırlanıyor. Rastgele persona değişimi yapılmıyor.
- Roast kaynak dosyası değiştirilmedi; mevcut kart aynı şekilde korunuyor.
- Telegram'a gönderimden ve geçmişe kayıttan önce beklenmeyen yabancı yazı kontrolü
  var. Kod, URL, kaynakta geçen özel adlar ve açık çeviri talepleri korunuyor.
- Kontrol, Latin alfabesi dışındaki beklenmeyen metni yakalar; İngilizce gibi Latin
  alfabesinde yazılmış bütün yabancı kelimeleri tanıyan bir dil sınıflandırıcısı değildir.
- Bozuk metin için aynı model/sağlayıcıyla yalnızca bir düzeltme çağrısı yapılır.
  Temiz yanıtta ek ağ çağrısı yoktur. Düzeltme de başarısızsa bozuk yanıt yayınlanmaz.
- Güncel veri isteklerinde geçmiş ve web kanıtı eşzamanlı yüklenir.
- İzin verilen OpenAI modelleri 5.6 Luna ve 6 Luna'dır. Sadece modelin bulunmaması
  veya desteklenmemesi durumunda 6 Luna yedeği denenir; 403 erişim reddi aşılmaz.

## Ölçüm sınırı

Önceki 10 gerçek CLI isteğinin ortalaması 5,614 saniyeydi. Bu aday için yeni bir
Luna ağ gecikmesi ölçülmedi: Arel uygulamasına ait yetkili erişim dosyası yok.
Önceki ölçüm yeni sürümün kabul kanıtı olarak kullanılmıyor.

Aşağıdaki boyutlar uygulamanın ürettiği sistem talimatlarıdır; model token miktarı
veya uçtan uca yanıt süresi değildir.

| Üslup | Önce karakter | Sonra karakter | Azalma |
|---|---:|---:|---:|
| normal | 5144 | 3626 | %29.5 |
| serious | 4734 | 1324 | %72.0 |
| balanced | 5144 | 1701 | %66.9 |
| needle | 5163 | 1826 | %64.6 |
| roast | 4153 | 4411 | %-6.2 |

Roast kartı küçültülmedi; ortak dil kontrolü talimatı eklendi. Önceki gerçek
cevapların ikisindeki yabancı yazı karışması tekrar testinde yakalandı.

## Sunucu

Mevcut `/opt/arel/current` games-24 sürümü ve beş Arel servisi sağlıklı.
Uygulama yetkisi olmadığı için canlı Luna testi/üretim geçişi yapılmayacak.
Aday sürümün sunucu test sonuçları ayrı kanıt kaydında tutulacak.
GitHub yayını yapılmadı.

## Son doğrulama

- İzole yerel kaynakta: 3178 geçti, 22 atlandı.
- Sunucuda ağsız aday konteynerinde: 3194 geçti, 6 atlandı.
- Adayın test sonrası dosya özeti doğrulandı; çalışan Arel servisleri sağlıklı, yeniden başlatma sayıları 0.
- Telegram getMe başarılı; sohbet/persona mesajı gönderilmedi.
- Üretim aktivasyonu yapılmadı. Arel uygulama izni yok; canlı Luna çıkarımı ve yeni uçtan uca hız ölçümü yapılmadı.
- Aday yolu: `/opt/arel/candidates/chat-quality-20261022-4d89e366eb33`.

Kanıt: `deploy/evidence/chat-quality-candidate-20261022.json`.
