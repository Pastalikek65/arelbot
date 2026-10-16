
import random
from typing import List

class TruthDareGame:
    """Rich Truth or Dare with categories and intensity levels."""
    
    TRUTHS = [
        # Hafif
        "En son ne zaman ve niÃ§in yalan sÃ¶yledin? ğŸ¤«",
        "HayatÄ±ndaki en bÃ¼yÃ¼k piÅŸmanlÄ±ÄŸÄ±n nedir? ğŸ§",
        "Ä°lk gÃ¶rÃ¼ÅŸte aÅŸka inanÄ±r mÄ±sÄ±n? â¤ï¸",
        "BugÃ¼ne kadar yaptÄ±ÄŸÄ±n en utanÃ§ verici ÅŸey neydi? ğŸ«£",
        "HayatÄ±nda bir ÅŸeyi deÄŸiÅŸtirebilseydin bu ne olurdu? âœ¨",
        "En garip alÄ±ÅŸkanlÄ±ÄŸÄ±n nedir? ğŸŒ€",
        "Birisi hakkÄ±nda beslediÄŸin en bÃ¼yÃ¼k sÄ±r nedir? ğŸ¤«",
        "EÄŸer bir gÃ¶rÃ¼nmezlik pelerinin olsaydÄ± ilk nereye giderdin? ğŸ•µï¸â€â™‚ï¸",
        "Telefonundaki son fotoÄŸraf ne? ğŸ“¸",
        "HiÃ§ birini gÃ¶zetledin mi? (Stalk) ğŸ‘€",
        "EÄŸer bir hayvan olsaydÄ±n hangisi olurdun ve neden? ğŸ¦",
        "Ã‡ocukluÄŸundan kalma en komik anÄ±n ne? ğŸ‘¶",
        "Asla yapmam dediÄŸin ama yaptÄ±ÄŸÄ±n bir ÅŸey var mÄ±? ğŸš«",
        # Orta 
        "AÅŸk iÃ§in yapabileceÄŸin en Ã§Ä±lgÄ±nca ÅŸey ne olurdu? ğŸ’˜",
        "En son kimi dÃ¼ÅŸÃ¼nerek uyudun? ğŸŒ™",
        "HiÃ§ bir arkadaÅŸÄ±nÄ±n sevgilisinden hoÅŸlandÄ±n mÄ±? ğŸ˜³",
        "Birinden Ã§ok utandÄ±ÄŸÄ±n bir anÄ±nÄ± anlat ğŸ˜¬",
        "HayatÄ±nda hiÃ§ kopya Ã§ektin mi? NasÄ±l? ğŸ“",
        "HiÃ§ birinin sÄ±rrÄ±nÄ± baÅŸkasÄ±na sÃ¶yledin mi? ğŸ—£ï¸",
        "En Ã§ok kÄ±skandÄ±ÄŸÄ±n kiÅŸi kim? Neden? ğŸ’š",
        "HiÃ§ yalan sÃ¶yleyip yakalandÄ±n mÄ±? Ne oldu? ğŸ­",
        "Kendine en Ã§ok kÄ±zdÄ±ÄŸÄ±n an hangisiydi? ğŸ˜¤",
        "HiÃ§ birinin mesajlarÄ±nÄ± gizlice okudun mu? ğŸ“±",
        # AÄŸÄ±r
        "HayatÄ±nda en Ã§ok kimi kÄ±rdÄ±n ve piÅŸman mÄ±sÄ±n? ğŸ’”",
        "HiÃ§ kimseye sÃ¶ylemediÄŸin bir ÅŸeyi ÅŸimdi sÃ¶yle! ğŸ”¥",
        "Bu gruptaki en yakÄ±ÅŸÄ±klÄ±/gÃ¼zel kiÅŸi kim? ğŸ˜",
        "HiÃ§ sevgilinle ayrÄ±lÄ±p barÄ±ÅŸmayÄ± dÃ¼ÅŸÃ¼ndÃ¼n mÃ¼? ğŸ’•",
        "En bÃ¼yÃ¼k korkun ne? GerÃ§ekten ne? ğŸ‘»",
        "Borcunu hiÃ§ Ã¶demedin mi? ğŸ’¸",
        "HiÃ§ birini gÃ¼zel/yakÄ±ÅŸÄ±klÄ± bulup sÃ¶yleyemedin mi? ğŸ˜",
    ]
    
    DARES = [
        # Hafif
        "En son mesajlaÅŸtÄ±ÄŸÄ±n kiÅŸiye 'Seni seviyorum' yaz! (Åaka olduÄŸunu sonra sÃ¶yle ğŸ˜‰)",
        "BulunduÄŸun odada 360 derece dÃ¶nerek ÅŸarkÄ± sÃ¶yle! ğŸ¶",
        "Cebindeki en garip eÅŸyayÄ± gÃ¶ster! ğŸ’",
        "En sevdiÄŸin emojiyi taklit et ve fotoÄŸrafÄ±nÄ± at! ğŸ“¸",
        "Gruba bir ses dosyasÄ± at ve en sevdiÄŸin ÅŸarkÄ±yÄ± mÄ±rÄ±ldan! ğŸ¤",
        "Bir bardak suyu tek dikiÅŸte bitir! ğŸ’§",
        "SÄ±radaki 3 mesajÄ±nÄ± sadece emoji kullanarak yaz! ğŸ¤",
        "Telefonundaki arama geÃ§miÅŸinin ekran gÃ¶rÃ¼ntÃ¼sÃ¼nÃ¼ at! ğŸ“",
        # Orta
        "10 tane ÅŸÄ±nav Ã§ek ve videosunu at! ğŸ’ª",
        "BuzdolabÄ±nÄ± aÃ§ ve iÃ§indeki ilk ÅŸeyi ye! ğŸ¥’",
        "Taklit yeteneÄŸini konuÅŸtur: Bir Ã¼nlÃ¼ taklidi yap ve ses at! ğŸ­",
        "Son selfie'ni Ã§ek ve gruba at! Filtre yasak! ğŸ¤³",
        "Bir dakika boyunca tavuk gibi gÄ±dakla! ğŸ”",
        "GÃ¶zlerini kapat ve 30 saniye boyunca tek ayak Ã¼stÃ¼nde dur! ğŸ§˜â€â™‚ï¸",
        "Telefondaki en son aramanÄ± aÃ§Ä±kla! Kim, neden? ğŸ“",
        "Profil fotoÄŸrafÄ±nÄ± 1 saat boyunca komik bir fotoÄŸrafla deÄŸiÅŸtir! ğŸ¤¡",
        # AÄŸÄ±r
        "Instagram/Twitter hikayesine 'Bu grupta en Ã§ok sevdiÄŸim kiÅŸi ...' diye paylaÅŸ! ğŸ“±",
        "Gruba en son Ã§ektiÄŸin selfie'yi at! (DÃ¼zenleme yok!) ğŸ“¸",
        "En son arananlar geÃ§miÅŸinin ekran gÃ¶rÃ¼ntÃ¼sÃ¼nÃ¼ at! ğŸ”",
        "30 saniye boyunca komik bir dans yap ve videosunu at! ğŸ’ƒ",
        "Sesli mesajla 'Ben bir tavuÄŸum' diye baÄŸÄ±r! ğŸ”",
        "WhatsApp durumuna 'SatÄ±lÄ±k: kalp, az kullanÄ±lmÄ±ÅŸ' yaz! ğŸ’”",
        "Bir arkadaÅŸÄ±nÄ± ara ve 'Seni Ã§ok Ã¶zledim' de! Videosunu at! ğŸ“¹",
    ]

    def get_truth(self) -> str:
        return random.choice(self.TRUTHS)

    def get_dare(self) -> str:
        return random.choice(self.DARES)

    def get_random(self) -> dict:
        """Get a random truth or dare."""
        if random.random() > 0.5:
            return {"type": "truth", "text": self.get_truth(), "emoji": "ğŸ•µï¸â€â™‚ï¸"}
        else:
            return {"type": "dare", "text": self.get_dare(), "emoji": "ğŸ”¥"}

    def spin_bottle(self, users: List[str]) -> str:
        if not users:
            users = ["Sen"]
        
        if len(users) == 1:
            spinner = users[0]
            target = "Arel (Yani Ben! ğŸ˜Š)"
        else:
            spinner = random.choice(users)
            target = random.choice([u for u in users if u != spinner])
        
        # Get a random truth or dare to go with it
        choice = self.get_random()
        td_label = "DoÄŸruluk" if choice["type"] == "truth" else "Cesaret"
        
        outcomes = [
            f"ğŸ¾ ÅiÅŸe hÄ±zlÄ±ca dÃ¶nÃ¼yor...\n\n"
            f"ğŸŒ€ <b>{spinner}</b> Ã§evirdi!\n"
            f"ğŸ‘‰ ÅiÅŸenin ucu: <b>{target}</b>\n\n"
            f"{choice['emoji']} <b>{td_label}:</b>\n{choice['text']}",
            
            f"ğŸ¾ ÅiÅŸe masada bir tur atÄ±yor...\n\n"
            f"ğŸŒ€ <b>{spinner}</b> Ã§evirdi!\n"
            f"ğŸ¯ Kurban: <b>{target}</b>\n\n"
            f"{choice['emoji']} <b>{td_label}:</b>\n{choice['text']}",
            
            f"ğŸ¾ ÅiÅŸe yavaÅŸlÄ±yor... ve...\n\n"
            f"ğŸ‘‰ <b>{target}</b> Ã¼zerinde durdu!\n"
            f"<b>{spinner}</b> kÃ¼Ã§Ã¼k bir tebessÃ¼mle bakÄ±yor... ğŸ˜\n\n"
            f"{choice['emoji']} <b>{td_label}:</b>\n{choice['text']}"
        ]
        return random.choice(outcomes)
