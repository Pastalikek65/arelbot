
import random
from typing import List

class TruthDareGame:
    """Rich Truth or Dare with categories and intensity levels."""
    
    TRUTHS = [
        # Hafif
        "En son ne zaman ve niçin yalan söyledin? 🤫",
        "Hayatındaki en büyük pişmanlığın nedir? 🧐",
        "İlk görüşte aşka inanır mısın? ❤️",
        "Bugüne kadar yaptığın en utanç verici şey neydi? 🫣",
        "Hayatında bir şeyi değiştirebilseydin bu ne olurdu? ✨",
        "En garip alışkanlığın nedir? 🌀",
        "Birisi hakkında beslediğin en büyük sır nedir? 🤫",
        "Eğer bir görünmezlik pelerinin olsaydı ilk nereye giderdin? 🕵️‍♂️",
        "Telefonundaki son fotoğraf ne? 📸",
        "Hiç birini gözetledin mi? (Stalk) 👀",
        "Eğer bir hayvan olsaydın hangisi olurdun ve neden? 🦁",
        "Çocukluğundan kalma en komik anın ne? 👶",
        "Asla yapmam dediğin ama yaptığın bir şey var mı? 🚫",
        # Orta 
        "Aşk için yapabileceğin en çılgınca şey ne olurdu? 💘",
        "En son kimi düşünerek uyudun? 🌙",
        "Hiç bir arkadaşının sevgilisinden hoşlandın mı? 😳",
        "Birinden çok utandığın bir anını anlat 😬",
        "Hayatında hiç kopya çektin mi? Nasıl? 📝",
        "Hiç birinin sırrını başkasına söyledin mi? 🗣️",
        "En çok kıskandığın kişi kim? Neden? 💚",
        "Hiç yalan söyleyip yakalandın mı? Ne oldu? 🎭",
        "Kendine en çok kızdığın an hangisiydi? 😤",
        "Hiç birinin mesajlarını gizlice okudun mu? 📱",
        # Ağır
        "Hayatında en çok kimi kırdın ve pişman mısın? 💔",
        "Hiç kimseye söylemediğin bir şeyi şimdi söyle! 🔥",
        "Bu gruptaki en yakışıklı/güzel kişi kim? 😏",
        "Hiç sevgilinle ayrılıp barışmayı düşündün mü? 💕",
        "En büyük korkun ne? Gerçekten ne? 👻",
        "Borcunu hiç ödemedin mi? 💸",
        "Hiç birini güzel/yakışıklı bulup söyleyemedin mi? 😍",
    ]
    
    DARES = [
        # Hafif
        "En son mesajlaştığın kişiye 'Seni seviyorum' yaz! (Şaka olduğunu sonra söyle 😉)",
        "Bulunduğun odada 360 derece dönerek şarkı söyle! 🎶",
        "Cebindeki en garip eşyayı göster! 🎒",
        "En sevdiğin emojiyi taklit et ve fotoğrafını at! 📸",
        "Gruba bir ses dosyası at ve en sevdiğin şarkıyı mırıldan! 🎤",
        "Bir bardak suyu tek dikişte bitir! 💧",
        "Sıradaki 3 mesajını sadece emoji kullanarak yaz! 🤐",
        "Telefonundaki arama geçmişinin ekran görüntüsünü at! 📞",
        # Orta
        "10 tane şınav çek ve videosunu at! 💪",
        "Buzdolabını aç ve içindeki ilk şeyi ye! 🥒",
        "Taklit yeteneğini konuştur: Bir ünlü taklidi yap ve ses at! 🎭",
        "Son selfie'ni çek ve gruba at! Filtre yasak! 🤳",
        "Bir dakika boyunca tavuk gibi gıdakla! 🐔",
        "Gözlerini kapat ve 30 saniye boyunca tek ayak üstünde dur! 🧘‍♂️",
        "Telefondaki en son aramanı açıkla! Kim, neden? 📞",
        "Profil fotoğrafını 1 saat boyunca komik bir fotoğrafla değiştir! 🤡",
        # Ağır
        "Instagram/Twitter hikayesine 'Bu grupta en çok sevdiğim kişi ...' diye paylaş! 📱",
        "Gruba en son çektiğin selfie'yi at! (Düzenleme yok!) 📸",
        "En son arananlar geçmişinin ekran görüntüsünü at! 🔍",
        "30 saniye boyunca komik bir dans yap ve videosunu at! 💃",
        "Sesli mesajla 'Ben bir tavuğum' diye bağır! 🐔",
        "WhatsApp durumuna 'Satılık: kalp, az kullanılmış' yaz! 💔",
        "Bir arkadaşını ara ve 'Seni çok özledim' de! Videosunu at! 📹",
    ]

    def get_truth(self) -> str:
        return random.choice(self.TRUTHS)

    def get_dare(self) -> str:
        return random.choice(self.DARES)

    def get_random(self) -> dict:
        """Get a random truth or dare."""
        if random.random() > 0.5:
            return {"type": "truth", "text": self.get_truth(), "emoji": "🕵️‍♂️"}
        else:
            return {"type": "dare", "text": self.get_dare(), "emoji": "🔥"}

    def spin_bottle(self, users: List[str]) -> str:
        if not users:
            users = ["Sen"]
        
        if len(users) == 1:
            spinner = users[0]
            target = "Arel (Yani Ben! 😊)"
        else:
            spinner = random.choice(users)
            target = random.choice([u for u in users if u != spinner])
        
        # Get a random truth or dare to go with it
        choice = self.get_random()
        td_label = "Doğruluk" if choice["type"] == "truth" else "Cesaret"
        
        outcomes = [
            f"🍾 Şişe hızlıca dönüyor...\n\n"
            f"🌀 <b>{spinner}</b> çevirdi!\n"
            f"👉 Şişenin ucu: <b>{target}</b>\n\n"
            f"{choice['emoji']} <b>{td_label}:</b>\n{choice['text']}",
            
            f"🍾 Şişe masada bir tur atıyor...\n\n"
            f"🌀 <b>{spinner}</b> çevirdi!\n"
            f"🎯 Kurban: <b>{target}</b>\n\n"
            f"{choice['emoji']} <b>{td_label}:</b>\n{choice['text']}",
            
            f"🍾 Şişe yavaşlıyor... ve...\n\n"
            f"👉 <b>{target}</b> üzerinde durdu!\n"
            f"<b>{spinner}</b> küçük bir tebessümle bakıyor... 😏\n\n"
            f"{choice['emoji']} <b>{td_label}:</b>\n{choice['text']}"
        ]
        return random.choice(outcomes)
