# Yaş Tahmini araştırma notu

## Kaynak ve kapsam

`mikhailsdv/guess-the-age-bot` Telegram grup akışını davranış referansı olarak
tanımlar: bot bir kişinin fotoğrafını paylaşır, kullanıcılar yaş tahmini
gönderir ve hedefe yakın cevap daha çok puan alır. Upstream depo
`package.json` içinde MIT olarak beyan edilir; README'de fotoğraf veri
kümesinin lisansını veya kişi görsellerinin provenance bilgisini açıklayan bir
kayıt bulunmaz.

Arelbot upstream JavaScript kodunu ve görsellerini kullanmaz. Oyun çekirdeği
bağımsız Python state/service sözleşmesidir. Fotoğraflar Wikimedia Commons'tan
alınır ve oyunla birlikte dağıtılır. Her fotoğrafın kişi, çekim günü, fotoğrafçı,
lisans ve kaynak kaydı `assets/guess_age/README.md` içinde tutulur.

## Arel uygulaması

- `/yas` yalnızca grup ve süpergruplarda çok turlu oyunu başlatır.
- Her turda yerel bir gerçek kişi fotoğrafı gösterilir; yaş aralığı ardından
  kesin yaş seçimi veya `/yastahmin 27` ile tahmin alınır.
- Kullanıcı aynı turda bir kez cevap verebilir; callback ledger tekrarları
  yutar.
- Puan `100 - (fark × 5)` formülüyle 0–100 aralığında hesaplanır.
- Tur bitimi job queue ile yapılır; fotoğraf tarihindeki hedef yaş ve Commons
  kaynağı açıklanır ve sonraki görsel
  gönderilir. Son turda grup sıralaması kalıcı puan state'inden üretilir.
- State `guess_age` oyun kaydı olarak MongoDB/bellek data portunda tutulur ve
  restart sonrası tekrar hydrate edilebilir.

## Güvenlik ve sınırlamalar

Path yalnızca `assets/guess_age` altında çözülür; symlink/path traversal kabul
edilmez. Hedef yaş callback verisine yazılmaz. Oyunun durdurulması başlatan
kişi veya grup yöneticisiyle sınırlıdır. Görseller AI veya yüz tanıma servisine
gönderilmez.
