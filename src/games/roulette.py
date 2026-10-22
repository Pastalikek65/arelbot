
import random
from typing import List, Dict, Optional

class RouletteGame:
    """Russian Roulette with chamber tracking and multi-round gameplay."""
    
    def __init__(self):
        pass
    
    def create_game(self, chat_id: int) -> Dict:
        """Create a new Russian Roulette game with a real chamber."""
        chambers = [False] * 6
        bullet_pos = random.randint(0, 5)
        chambers[bullet_pos] = True
        
        return {
            'chambers': chambers,
            'current': 0,
            'players': [],
            'player_names': [],
            'current_player_idx': 0,
            'alive': [],
            'round': 1,
        }

    def spin(self) -> str:
        """Simple single-shot roulette (legacy)."""
        if random.randint(1, 6) == 1:
            return "💥 <b>GÜÜÜM!</b> Şansın yaver gitmedi, masadan çekiliyorsun. (Mermi doluydu!) 💀"
        return "💨 <i>Tık...</i> Şanslısın, bu tur boş geçti. Tabanca bir sonrakine dönüyor. 😌"

    def pull_trigger(self, game_state: Dict, player_name: str) -> Dict:
        """Pull the trigger in a multi-round game."""
        current = game_state['current']
        hit = game_state['chambers'][current]
        game_state['current'] = (current + 1) % 6
        
        if hit:
            return {
                "hit": True,
                "message": f"💥 <b>GÜÜÜM!</b>\n\n☠️ <b>{player_name}</b> masadan elendi! Mermi dolu geldi... 💀",
                "player": player_name
            }
        else:
            return {
                "hit": False,
                "message": f"💨 <i>Tık...</i>\n\n😌 <b>{player_name}</b> bu turda kurtuldu! Tabanca bir sonrakine geçiyor...",
                "player": player_name
            }

    def duel(self, p1: str, p2: str) -> str:
        """Epic duel between two players."""
        winner = random.choice([p1, p2])
        loser = p2 if winner == p1 else p1
        
        scenarios = [
            f"⚔️ <b>{p1}</b> ve <b>{p2}</b> sırt sırta verip 10 adım attılar...\n"
            f"💥 <b>BANG!</b> {winner} daha hızlı çekti! {loser} yere yığıldı.\n\n"
            f"🏆 Kazanan: <b>{winner}</b> ⚰️",
            
            f"⚔️ <b>{p1}</b> kılıcını çekti, <b>{p2}</b> gardını aldı...\n"
            f"💨 İnanılmaz bir hamleyle <b>{winner}</b> rakibini silahsız bıraktı!\n\n"
            f"🏆 Kazanan: <b>{winner}</b> 🏳️",
            
            f"⚔️ Gergin bir sessizlik... Rüzgar esti...\n"
            f"👀 <b>{loser}</b> gözünü kırptığı an <b>{winner}</b> hamlesini yaptı!\n\n"
            f"🏆 Kazanan: <b>{winner}</b> 🎯",
            
            f"⚔️ <b>{p1}</b> ile <b>{p2}</b> meydana çıktı...\n"
            f"⏳ 3... 2... 1... ÇEK!\n"
            f"💫 <b>{winner}</b> yıldırım hızıyla rakibini alt etti!\n\n"
            f"🏆 Kazanan: <b>{winner}</b> 🔥",
            
            f"⚔️ Arena sessizleşti... İki savaşçı karşı karşıya...\n"
            f"🌪️ <b>{winner}</b> inanılmaz bir hızla saldırdı!\n"
            f"<b>{loser}</b> savunamadı bile...\n\n"
            f"🏆 Kazanan: <b>{winner}</b> ⚡"
        ]
        return random.choice(scenarios)

    def dice_roll(self) -> str:
        val = random.randint(1, 6)
        dice_faces = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
        return f"🎲 Zar atıldı: {dice_faces[val]} <b>{val}</b>"

    def coin_flip(self) -> str:
        result = random.choice(["YAZI", "TURA"])
        emoji = "🪙" if result == "YAZI" else "💰"
        return f"{emoji} Yazı Tura: <b>{result}</b>"

    def slot_machine(self) -> str:
        """3-reel slot machine."""
        symbols = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣", "🔔", "⭐"]
        reels = [random.choice(symbols) for _ in range(3)]
        
        text = f"🎰 <b>Slot Makinesi</b>\n\n"
        text += f"┃ {reels[0]} ┃ {reels[1]} ┃ {reels[2]} ┃\n\n"
        
        if reels[0] == reels[1] == reels[2]:
            if reels[0] == "7️⃣":
                text += "🤑 <b>JACKPOT!</b> Üçlü 7! Büyük ödülü kazandın! 💰💰💰"
            elif reels[0] == "💎":
                text += "💎 <b>ELMAS!</b> Üçlü elmas! Müthiş ödül! 💰💰"
            else:
                text += f"🎊 <b>KAZANDIN!</b> Üçlü {reels[0]}! Tebrikler! 🏆"
        elif reels[0] == reels[1] or reels[1] == reels[2]:
            text += "😊 İkili eşleşme! Küçük bir ödül kazandın. 💰"
        else:
            text += "😔 Bu sefer olmadı... Tekrar dene! 🎲"
        
        return text
