# Blackjack: pes et ve oyun durumu sınırları

## Oyuncu davranışı

- Yeni Blackjack panelindeki **Pes et · yarım iade** seçeneği yalnızca oyuncunun ilk hamlesinden önce kullanılabilir. Kart çekildikten, durulduktan veya double yapıldıktan sonra kullanılamaz. Doğal 21 bulunan eller normal sonuçlandırılır.
- Pes etmek krupiyeye kart çektirmez. İlk bahsin yarısı tam jetona aşağı yuvarlanarak iade edilir: 100 jetonda 50, 101 jetonda 50, 1 jetonda 0. Kalan tutar kayıptır.
- Yeni butonlar el kimliği taşır. Önceki elin paneli yeni eli pes ettiremez. Eski üç parçalı buton protokolü kart çek/dur/double için uyumludur; pes et için kapsamlı yeni panel gerekir.
- Metin ve butonla adam asmaca aynı harf normalizasyonunu ve ipucu puan cezasını kullanır. Sıfır canla biten tur ipucuyla kazanıma dönmez. Kelime türetmede aynı görünür ipucu ikinci kez puan cezası oluşturmaz.

## İade ve eşzamanlılık

Başlatma, hamle ve iptal işlemleri kullanıcı+sohbet kapsamında ortak `GameActionLedger` anahtarıyla korunur. Üretimde repository'nin benzersiz anahtarı farklı servis örneklerinin aynı eli aynı anda değiştirmesini engeller. Çakışan işlem açıkça reddedilir; kullanıcı yeniden deneyebilir. Bellek fallback'i üretimdeki kalıcılığın yerine geçmez.

Terminal el sonucu para iadesinden önce kaydedilir. Para hareketi aynı elden türeyen sabit settlement event kimliğini kullanır. İade yapıldıktan sonra kayıt silme başarısız olursa sonraki deneme kayıtlı sonucu tamamlar; aynı iade ikinci kez yapılmaz. Böyle bir pes etme kaydına genel iptal uygulamak tam bahis iadesi sağlamaz.

**Kesin süreç çökmesi sınırı:** Süreç ortak el kilidini tutarken SIGKILL veya makine kaybıyla sona ererse `finally` çalışamaz. Mevcut ledger API'sinde lease/fencing desteği bulunmadığından kilit otomatik devralınmaz; güvenli biçimde kapalı kalır. Sadece yeni servis örneği oluşturmak bu kilidi kaldırmaz. Mevcut ledger TTL'si bir kurtarma süresi garantisi değildir.

Operatör, ilgili mutasyonu yürüten hiçbir süreç kalmadığını doğrulamalı; elin terminal kaydını ve ekonomi event'ini inceleyip yalnızca ilgili `blackjack_session` kaydını kontrollü biçimde bırakmalıdır. Bütün oyun ledger'ını temizlemek tekrar ödeme korumasını kaldırır. Bu değişiklik otomatik lease kurtarması veya MongoDB+ekonomi arasında tek transaction garantisi eklemez.

## Doğrulama kapsamı

Regresyonlar gerçek Blackjack domain'i ve servis akışıyla; tam/tek jeton bahis, ilk hamle sınırı, farklı callback yarışları, iptal yarışı, eski panel, sahiplik, kayıt silme hatası ve tekrar ödeme senaryolarını kapsar. Ekonomi entegrasyon testi gerçek `EconomyService`'in bellek backend'ini kullanır; repository yarış testleri kalıcı claim sözleşmesini uygulayan deterministik test deposundadır.

Canlı Telegram teslimi, gerçek MongoDB/Redis üstünde süreç öldürme ve üretim ekonomi toparlanması bu testlerin kapsamı dışındadır: **NOT_RUN**. Canlı kabul ayrıca yapılmalıdır.
