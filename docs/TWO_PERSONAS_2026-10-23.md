# Arel sohbet üslubu

Kullanıcıya sunulan iki kişilik vardır: **Normal** ve **Roast**.

Normal, samimi ve esprili bir arkadaş gibi konuşur. Kullanıcının mesaj diline
uyar; bağlama göre kinaye ve kısa takılmalar kullanabilir. Her mesajda şaka,
“kanka”, random gülme veya emoji tekrarı istenmez. Noktalama gündelik sohbette
sadedir; teknik içerik, kod ve anlaşılabilirlik korunur. Ciddi ve hassas konular
ciddiyetle ele alınır. Kurumsal kapanış, otomatik övgü ve ahlak dersi eklenmez.
Gündelik paylaşımlar otomatik olarak tavsiye veya görev listesine çevrilmez;
üslup kartındaki kısa örnekler samimi mizahın ritmini gösterir, sabit cevap değildir.
Normal'e eski seviyelerden gelen çözüm/tavsiye ve mizah sıklığı talimatları eklenmez.
Grupların çoklu yanıt planlayıcısı ve yedek yanıtları da kaydedilmiş kişilik
tercihini alır; özel sohbet ile grup ayarları birbirine karışmaz.

Eski Ciddi, Dengeli, İğneleyici ve Suç Ortağı tercihleri okuma sırasında Normal
olarak çözülür. Kalıcı kayıtlar topluca değiştirilmez; eski düğmeler de Normal'e
yönlenir. Varsayılanı kullanmak Normal'e döner. Grup kişiliğini yalnızca grup
yöneticisi seçer. Roast kendiliğinden etkinleşmez; mevcut onay akışı korunur.

Roast içerik politikası ve içerik taşıyan mevcut metotlar değiştirilmemiştir.
Önceki dosyayla yapısal kaynak karşılaştırması ve SHA-256 kontrolü uygulanır.
LLM sağlayıcıları, sıralama, hesap erişimi ve Codex CLI yapılandırması bu
değişikliğin kapsamı dışındadır.

Doğrulama: iki seçenekli menüler, eski tercih uyumluluğu, grup yetkisi, onay
olmadan ayar değişmemesi, Normal'e geri dönüş ve kayıt hatasında tercihin
korunması için otomatik testler bulunur. Modelin mizah kalitesi deterministik
bir test sonucu değildir. Canlı Telegram kullanıcı denemesi ayrıca raporlanır.
