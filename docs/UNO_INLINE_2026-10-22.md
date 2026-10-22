# UNO kaynak motoru ve inline galeri

Kaynak 01640d1 sürümü AGPL lisansı korunarak port edildi. Kaynak sınıflar kart kuralları ve yön/sıra etkilerini uygular; Arel adaptörü 108 kart kimliğini JSON olarak saklar. Yeni oyunlar maildian motorunda açılır, mevcut eski maçlar kesilmez.

Oyuncu grupta Kartlarımı aç düğmesine basar. Açılan özel inline galeride bütün kartlar kendi rengindedir; oynanamayan kartlarda kilit rozeti vardır. Kart seçimi aynı grup/tur/oyuncu/sürüm için imzalanır; eski seçim ve yinelenen bildirim durum değiştirmez. 50 sonuçluk sayfalama 105 kartlık elde de işler. Renk seçimi, kart çekme, pas ve blöf seçenekleri kartlarla aynı galeridedir. BotFather geri bildirimi Enabled gerektirir.

Deste tükenince çekme girişimi pas hakkı verir; kısmi ceza çekişi kaybolmaz. Yeniden karıştırma gizli, kalıcı tohumla tekrarlanabilir; bu alanlar oyuncu görünümüne/loglara konmaz. Bozuk kayıtlar UNO_INVALID_STATE ile reddedilir. Oyun sonunda mevcut tekil skor kaydı ve 500 puanlık maç korunur.

Telegram chosen-inline güncellemesinde hedef sohbet kimliği yoktur. Bir oyuncu aynı imzalı sorguyu başka sohbette kullansa da yalnız asıl maçındaki kendi geçerli sırasını oynayabilir. Bu, kaynak botun inline modelinin sınırıdır; başka oyuncunun eli veya başka bir maçın yetkisi verilmez.

114 PNG upstream ile birebir hash eşleşir ve Arel'e ait native sticker file_id değerleri doğrulanmıştır. Kod ve sanat atıfları LICENSES.md, COPYING.AGPL-3.0 ve SOURCE.json kayıtlarında korunur. Çalışan sürümün tam Git kaynak arşivi özel sohbette /uno kaynak ile, Telegram dosya sınırına uygun parçalarda sunulur.

Bu belge canlı yayın veya gerçek çok oyunculu kabul iddiası değildir; kesin kabul ve yayın durumu deploy/evidence/games20b-deployment-20261022.json içine yazılır. GitHub güncellenmez.
