# Sohbet aracı sonrası sağlayıcı geçişi

## Bulgu

Araç kullanan sohbette ilk yanıt bir sağlayıcıdan alınsa bile araç çalıştırıldıktan sonraki tamamlayıcı istek yalnız o sağlayıcıya sabitleniyordu. Bu sağlayıcı zaman aşımına uğradığında yapılandırılmış diğer sağlayıcılar denenmeden kullanıcıya genel bağlantı yanıtı dönüyordu. Ayrı olarak, iki yavaş sağlayıcı 40 saniyelik toplam sohbet bütçesini tüketip çalışan üçüncü sağlayıcıya süre bırakabiliyordu.

## Düzeltme

- Araç sonucu tek kez üretildikten sonra tamamlayıcı yanıt, mevcut sıralı sohbet rotasına gönderilir. Her sağlayıcı kendi yapılandırılmış modelini kullanır; ilk sağlayıcının model kimliği başka birine taşınmaz.
- CLI başlatma süresi korunurken sonraki sağlayıcılar için toplam bütçenin en az yarısı ayrılır. Her başarılı yanıt rotayı durdurur.
- Sağlayıcı anahtarları, sıralama, CLI modeli ve mevcut sağlayıcıların kendisi değiştirilmedi.

## Kanıt sınırı

Regresyon testleri araç sonucunun tekrar çalıştırılmadığını ve ilk iki sağlayıcı zaman aşımına uğrasa da üçüncünün yanıt verebildiğini doğrular. Canlı ortamda gerçekten her sağlayıcının her isteğe cevap vereceği iddia edilmez; dış hizmet kesintisi yine mümkündür.
