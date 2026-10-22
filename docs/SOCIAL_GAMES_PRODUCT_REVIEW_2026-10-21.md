# Tabu, Casus ve Vampir Köylü — ürün kaydı

Bu dalga mevcut oyun yol haritasını uygular; GitHub yayımlanmaz. Kullanıcı gönüllü oyuncu olmadığını söylediği için canlı kabul lobi/katılma/kişisel hazırlık/eksik oyuncu engeliyle sınırlıdır. Tam insanlı maç kabulü NOT_RUN kalır.

## Tabu

166 özgün Türkçe kart, kategori ve beş yasak ifade; alias, sınırlı çekim/yumuşama ve harfleme denetimi. İki takım, dönen anlatıcı, eşit kart hakkı, sınırlı pas, süre, itiraz ve uzatma var. İtirazda hedef/yasak/ham ipucu gruba çıkmaz. Kararı oyunda oynamayan yönetici verir; karar gelmezse sınırlı pencere sonunda ilk sonuç korunur. Sesli veya görsele gömülü ipucunun otomatik denetlendiği iddia edilmez. Metin ve medya açıklaması/editleri aynı denetime gider.

## Casus

4–16 oyuncu; 36 özgün mekânın her birinde dört rol. SystemRandom ile seçilen casus mekânı görmez; diğer oyuncular aynı mekânda farklı bağlam rollerini alır. Özel teslim ve kişisel onay tamamlanmadan soru başlamaz. Grup soru/yanıt sırası, tartışma, gizli callback oyu, katı çoğunluk ve tek rövanş var. Kimin kime oy verdiği grup paneline yazılmaz. Casus özel listeden bir mekân seçer ve son onayla tek tahminini kullanır; doğru/yanlış tahmin maçı bitirir. Kod yazdıran komut zorunluluğu yok.

## Vampir Köylü

7–20 oyuncu; dengeli vampir/köylü/kâhin/şifacı. Roller ve vampir takım arkadaşları yalnız özel sohbetten görülür. Kişisel rol onayı, gece eylemi, koruma, kalıcı kâhin sonucu, tartışma, gizli oy ve rövanş vardır. Ölü/ayrılan oyuncu eylem ve oy kullanamaz; zafer her ölümden sonra hesaplanır. Koruma tekrarı, kendini iyileştirme ve ölümde rol açıklama ayarları lobi kurucusunun düğmelerine bağlıdır. Normal rol gizliliği korunur; iptal gizli rolleri ifşa etmez. Uzun/eylemsiz maçlar ödülsüz kapanır.

## Ortak güvenilirlik ve gizlilik

Konu/maç/faz/aktör ve mesaj kimliği bağlanır. CAS, iki çalışan örneğin aynı eylem ve sonucu iki kez üretmesini engeller. Kalıcı teslim lease ve makbuzları yeniden başlatmadan sonra devam eder. Yeni aşama saati grup paneli tesliminden, özel hamle gereken aşamalarda bütün gerekli özel kontrollerin tesliminden sonra başlar. Aşama hazırlığı kalıcı300 saniye üst sınırını aşarsa ödülsüz iptal olur. Telegram gönderimi ile makbuz kaydı arasında kesinti olursa dış mesajın tam bir kez teslimi garanti edilemez.

Ödüller olay kimliğiyle tekrar denenir; bekleyen ödül varken yeni maç eski kaydı ezmez. Menüden genel bitirme domain iptalini çağırır. Grup üyesinin /iptal komutu başka oyuncuların eski oyunlarını topluca silemez; kendi kalıcı oyununu domain yetkisiyle kapatır. Aktif gizli rol oyununa katılmış kişinin normal özel mesajı AI geçmişine gönderilmez; hamle için özel düğmelere yönlendirilir. Gizli içerik sunucu loguna yazılmaz.

## Bağımsız inceleme

Somut başarısız testlerle bulundu ve düzeltildi: Casus'ta soran/yanıtlayan son saniyede ayrıldığında yeni kişinin süresi; Vampir'de iptal sırasında rol sızıntısı; özel hamle düğmeleri teslim edilmeden faz saati; Tabu itirazı ve medya açıklaması bypass; genel bitirme yetki/bağlantı eksikleri. Son tam kapı25/25 geçti: host2471/19 atlanan, Docker ve sunucu adayı2484/6 atlanan. Yalıtılmış gerçek Mongo13durumu geçti. games8 sunucuda şema20 ile sağlıklı. Gerçek Telegram menüsü görüldü; üç oyunun gerçek API lobi/tek kişi başlatma engeli/iptal ve genel özel mesaj teslimi canary ile geçti. Bu canary insan düğme kabulü değildir: Katıl/özel hazırlık onayı tarayıcı bağlantısı nedeniyle BLOCKED, tam insanlı maç gönüllü olmadığından NOT_RUN. Kanıt: deploy/evidence/social-games-live-20261021.json.

## AI değerlendirmesi

Sunucu ayarlarında ana ve yedek yollar DeepSeek Flash modeli için yapılandırılmıştır; bu, gerçek yanıt kalitesinin karşılaştırmalı ölçümü değildir. Web2api uygulanmadı, web hesabı parolası veya cookie alınmadı. Oyun kuralları ve puanlama AI'dan bağımsızdır. Sağlayıcı değiştirme sonraki karşılaştırmalı çalışma olur. Resmî API OpenAI uyumlu bağlantı sağlar: https://api-docs.deepseek.com/ .
