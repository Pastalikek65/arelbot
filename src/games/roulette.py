
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
            return "ğŸ’¥ <b>GÃœÃœÃœM!</b> ÅansÄ±n yaver gitmedi, masadan Ã§ekiliyorsun. (Mermi doluydu!) ğŸ’€"
        return "ğŸ’¨ <i>TÄ±k...</i> ÅanslÄ±sÄ±n, bu tur boÅŸ geÃ§ti. Tabanca bir sonrakine dÃ¶nÃ¼yor. ğŸ˜Œ"

    def pull_trigger(self, game_state: Dict, player_name: str) -> Dict:
        """Pull the trigger in a multi-round game."""
        current = game_state['current']
        hit = game_state['chambers'][current]
        game_state['current'] = (current + 1) % 6
        
        if hit:
            return {
                "hit": True,
                "message": f"ğŸ’¥ <b>GÃœÃœÃœM!</b>\n\nâ˜ ï¸ <b>{player_name}</b> masadan elendi! Mermi dolu geldi... ğŸ’€",
                "player": player_name
            }
        else:
            return {
                "hit": False,
                "message": f"ğŸ’¨ <i>TÄ±k...</i>\n\nğŸ˜Œ <b>{player_name}</b> bu turda kurtuldu! Tabanca bir sonrakine geÃ§iyor...",
                "player": player_name
            }

    def duel(self, p1: str, p2: str) -> str:
        """Epic duel between two players."""
        winner = random.choice([p1, p2])
        loser = p2 if winner == p1 else p1
        
        scenarios = [
            f"âš”ï¸ <b>{p1}</b> ve <b>{p2}</b> sÄ±rt sÄ±rta verip 10 adÄ±m attÄ±lar...\n"
            f"ğŸ’¥ <b>BANG!</b> {winner} daha hÄ±zlÄ± Ã§ekti! {loser} yere yÄ±ÄŸÄ±ldÄ±.\n\n"
            f"ğŸ† Kazanan: <b>{winner}</b> âš°ï¸",
            
            f"âš”ï¸ <b>{p1}</b> kÄ±lÄ±cÄ±nÄ± Ã§ekti, <b>{p2}</b> gardÄ±nÄ± aldÄ±...\n"
            f"ğŸ’¨ Ä°nanÄ±lmaz bir hamleyle <b>{winner}</b> rakibini silahsÄ±z bÄ±raktÄ±!\n\n"
            f"ğŸ† Kazanan: <b>{winner}</b> ğŸ³ï¸",
            
            f"âš”ï¸ Gergin bir sessizlik... RÃ¼zgar esti...\n"
            f"ğŸ‘€ <b>{loser}</b> gÃ¶zÃ¼nÃ¼ kÄ±rptÄ±ÄŸÄ± an <b>{winner}</b> hamlesini yaptÄ±!\n\n"
            f"ğŸ† Kazanan: <b>{winner}</b> ğŸ¯",
            
            f"âš”ï¸ <b>{p1}</b> ile <b>{p2}</b> meydana Ã§Ä±ktÄ±...\n"
            f"â³ 3... 2... 1... Ã‡EK!\n"
            f"ğŸ’« <b>{winner}</b> yÄ±ldÄ±rÄ±m hÄ±zÄ±yla rakibini alt etti!\n\n"
            f"ğŸ† Kazanan: <b>{winner}</b> ğŸ”¥",
            
            f"âš”ï¸ Arena sessizleÅŸti... Ä°ki savaÅŸÃ§Ä± karÅŸÄ± karÅŸÄ±ya...\n"
            f"ğŸŒªï¸ <b>{winner}</b> inanÄ±lmaz bir hÄ±zla saldÄ±rdÄ±!\n"
            f"<b>{loser}</b> savunamadÄ± bile...\n\n"
            f"ğŸ† Kazanan: <b>{winner}</b> âš¡"
        ]
        return random.choice(scenarios)

    def dice_roll(self) -> str:
        val = random.randint(1, 6)
        dice_faces = {1: "âš€", 2: "âš", 3: "âš‚", 4: "âšƒ", 5: "âš„", 6: "âš…"}
        return f"ğŸ² Zar atÄ±ldÄ±: {dice_faces[val]} <b>{val}</b>"

    def coin_flip(self) -> str:
        result = random.choice(["YAZI", "TURA"])
        emoji = "ğŸª™" if result == "YAZI" else "ğŸ’°"
        return f"{emoji} YazÄ± Tura: <b>{result}</b>"

    def slot_machine(self) -> str:
        """3-reel slot machine."""
        symbols = ["ğŸ’", "ğŸ‹", "ğŸŠ", "ğŸ‡", "ğŸ’", "7ï¸âƒ£", "ğŸ””", "â­"]
        reels = [random.choice(symbols) for _ in range(3)]
        
        text = f"ğŸ° <b>Slot Makinesi</b>\n\n"
        text += f"â”ƒ {reels[0]} â”ƒ {reels[1]} â”ƒ {reels[2]} â”ƒ\n\n"
        
        if reels[0] == reels[1] == reels[2]:
            if reels[0] == "7ï¸âƒ£":
                text += "ğŸ¤‘ <b>JACKPOT!</b> ÃœÃ§lÃ¼ 7! BÃ¼yÃ¼k Ã¶dÃ¼lÃ¼ kazandÄ±n! ğŸ’°ğŸ’°ğŸ’°"
            elif reels[0] == "ğŸ’":
                text += "ğŸ’ <b>ELMAS!</b> ÃœÃ§lÃ¼ elmas! MÃ¼thiÅŸ Ã¶dÃ¼l! ğŸ’°ğŸ’°"
            else:
                text += f"ğŸŠ <b>KAZANDIN!</b> ÃœÃ§lÃ¼ {reels[0]}! Tebrikler! ğŸ†"
        elif reels[0] == reels[1] or reels[1] == reels[2]:
            text += "ğŸ˜Š Ä°kili eÅŸleÅŸme! KÃ¼Ã§Ã¼k bir Ã¶dÃ¼l kazandÄ±n. ğŸ’°"
        else:
            text += "ğŸ˜” Bu sefer olmadÄ±... Tekrar dene! ğŸ²"
        
        return text
