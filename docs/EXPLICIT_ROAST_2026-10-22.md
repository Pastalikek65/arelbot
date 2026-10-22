# Açık seçimli roast üslubu

Kullanıcı isteği: persona mesajdan otomatik seçilmesin; seçilmiş roast modunda
hafif argo ile sınırlı kalmadan gerçek, sansürsüz küfürler, küfürlü deyimler ve
atasözü uyarlamaları kullanılabilsin. Önceki salt-okunur roast incelemesinden
sonra kullanıcı bu davranış değişikliğini açıkça istedi.

## Davranış

- Mevcut `/persona` ve yöneticiye ait `/ayar kişilik` seçimleri ile onay düğmeleri
  korunur. Ayar seçilmediyse normal sohbet varsayılandır. Veri göçü veya mevcut
  kullanıcı/grup ayarlarını otomatik değiştirme yoktur.
- Hakaret sözlüğüyle roast açıp kapatma kaldırıldı. Seçilmiş roast, normal mesajda
  da geçerlidir; diğer modlar hakaret geldiğinde roast'a yükselmez.
- Roast yönergesi açık küfürlere izin verir ve sansür istemez; örnekleri grameri
  doğru kullanmak için verir. Her yanıtta küfür zorunlu değildir. Uydurma sözler
  gerçek atasözü olarak sunulmaz. Üçüncü kişileri hedef gösterme, tehdit, nefret
  ve cinsel aşağılama sınırları korunur.
- Hassas yardım isteği, roast yanıtından önce değerlendirilir. Bu, kayıtlı persona
  ayarını değiştirmez. Persona ana anahtarı kapalıysa roast yönergesi oluşmaz.
- Onay ekranı artık açık küfür kullanılabileceğini doğru biçimde bildirir.

## Doğrulama sınırı

Yerel kurallar ve onay/scope akışları deterministik test edilir. Gerçek sağlayıcı
örnekleri sentetiktir; küfür üretebilmek, her şakanın doğal veya kaliteli olacağı
anlamına gelmez. İnsanlı Telegram sohbeti ayrıca doğrulanmadıkça NOT_RUN kalır.

Geliştirme sırasında önceki davranışı gösteren beş regresyon testi başarısız
oldu; düzeltmeden sonra ilgili 68 test geçti. Son adayın Cloudflare örnekleri
`deploy/evidence/roast24-model-probe.json` içindedir. Roast yanıtlarında açık
küfür gözlendi; dengeli modda gözlenmedi. Espri ve deyim kalitesi hâlâ modele
bağlıdır. Evren ön denemesinde iki roast yanıtı küfür içerdi, dengeli mod çağrısı
zaman bütçesi içinde sonuçlanmadı; bu ön deneme son kaynak kabulü sayılmaz.

## Yayın sonucu

`2026.10.22-games-24` sunucuda aktif; kaynak `d59dc102fc87915e21c7379fbaa4e75d425f0504`.
Yerelde 3140 geçti / 21 atlandı, yalıtılmış sunucuda 3155 geçti / 6 atlandı.
25 gate kontrolü ve uygulama bootstrap başarılı. Son kaynakla gerçek API
denemesinde Cloudflare ve Evren açık küfür üretti; dengeli modda küfür
üretilmedi. Roast seçiliyken şakalaşmayı durdurma isteğinde küfür kesildi ve
policy seviyesi değişmedi. Espri/atasözü Türkçesinde bazı yapay ifadeler
görüldü; içerik kalitesi kusursuz kabul edilmedi. İnsanlı Telegram UI NOT_RUN.
Bot, worker, MongoDB ve Redis sağlıklı; restart sayıları sıfır. Yedekler alındı,
GitHub değiştirilmedi. Ayrıntı: `deploy/evidence/roast24-deployment-20261022.json`.
