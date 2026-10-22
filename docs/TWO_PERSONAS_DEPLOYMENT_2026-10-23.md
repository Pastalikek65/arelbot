# İki kişilik yayını: 2026.10.23-persona-5

Yerel kod ve sunucudaki bot/işçi kaynak hash'leri karşılaştırıldı. İki servis
sağlıklı, yeniden başlatma sayıları sıfır. Kaynak paketi üç parçadan oluşuyor;
manifest ve kaynak parçası hash'leri doğrulandı. Önceki sağlıklı sürüm geri
dönüş için korunuyor; başarısız adaylar ve geçici derleme dosyaları temizlendi.

Yalnızca Normal ve Roast seçilebilir. Eski tercihler Normal'e çözülür. Grup
seçimi yönetici yetkisi ister; Roast onayı korunur. Normal'in arkadaş üslubu
ayrı bir karttır; eski seviyelerin sürekli çözüm öneren tur talimatlarıyla
birleştirilmez. Grup yanıt planlayıcısı ve yedek yanıtlar da seçili modu alır.

Roast'ın içerik taşıyan politika metotları ilk kaynakla birebir aynıdır.
Sağlayıcı yapılandırmaları, etkin `.env`, Compose ve Codex CLI korunmuştur.
GitHub güncellenmemiştir.

## Doğrulama sınırı

- Yerel tam test: **3223 geçti, 19 atlandı**.
- Sunucu tam test: **3223 geçti, 6 atlandı**.
- Özellik kataloğu: **91/91**.
- Manuel Telegram kullanıcı testi: **NOT_RUN**.
- Genel faz denetiminde önceden eksik canlı Telegram değişiklik ve alarm
  teslim kanıtları bu işle kapanmış sayılmaz.

## Canlı konuşma kalitesi: HOLD

Son gerçek sohbet yolu denemelerinin ikisinde NVIDIA yedeği yanıt verdi;
birinde hiçbir sağlayıcıdan yanıt alınamadı ve uygulamanın bağlantı hata
yanıtı döndü. Dönen metinler istenen samimi/esprili tonu tutarlı yansıtmadı.
Kişilik seçimi ve talimat iletimi doğrulandı; bu sonuç, konuşma kalitesinin
kabulü anlamına gelmez. Sağlayıcı ayarları değiştirilmedi.

Ham son sonuçlar: `deploy/evidence/two-persona-live-20261023.json`.
Önceki sürüm denemeleri: `deploy/evidence/two-persona-live-20261023-v4.json`.
