# Görsel kart oyunları

Pişti, Papaz Kimde ve Batak, botun kendi sticker dosya kimlikleriyle oyuncuya özel inline galeri kullanır. Kenney Board Game Pack CC0 kaynağından 52 yüz, 52 soluk yüz ve bir kapalı kart hazırlanmıştır; kaynaklar ve hashler assets/playing_cards/SOURCE.json içindedir.

Pişti kart seçimi güncel özel el makbuzu, tur ve kayıt sürümüne bağlıdır. Papaz galerisi yalnız rakibin kapalı kart konumlarını gösterir; seçilen konum gerçekten çekilir. Oyuncunun kendi kartları özel el panelinde kalır. Rastgele çekme seçeneği de korunur. Batak kart seçimi kalıcı grup paneli, maç kimliği, sıra belirteci ve oyuncuya bağlıdır; renk takibi motor tarafından tekrar denetlenir. Geçersiz seçimler durum değiştirmez.

Blackjack mevcut bahis ve hit/stand/double/surrender motorunu korur. Fotoğraf paneli aynı kart destesinden oluşturulur ve mevcut mesajda güncellenir. Krupiyerin ilk kartı dışındaki kartları sonuçtan önce kapalıdır; deste görsel oluşturucuya aktarılmaz. Eski metin panelleri çalışmaya devam eder. Görsel dosya okunamazsa başlangıçta metin paneli kullanılır.

Inline geri bildirim BotFather'da Enabled olmalıdır. Bot API bu ayarı okuyamaz veya değiştiremez. ChosenInlineResult hedef sohbeti bildirmediğinden, seçim yetkisi imzalı kaynak maç ve oyuncu üzerinden doğrulanır. Gerçek insanlarla tam maç kabulü, otomatik motor/arayüz testlerinden ayrı değerlendirilir.

Bu belge uygulama kapsamını kaydeder; sunucuya dağıtım ve kesin test sonuçları ayrıca sürüm kanıtına yazılır. GitHub'a gönderim yapılmaz.
