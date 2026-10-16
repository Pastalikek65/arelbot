
from typing import Dict, Any, List
import logging
import random
import time
from .base import BaseTool
from ..games.truth_dare import TruthDareGame
from ..games.roulette import RouletteGame
from ..games.blackjack import BlackjackGame
from ..games.quiz import QuizGame
from ..games.catalog import normalize_word_game_category
from ..games.engine import normalize_difficulty
from ..games.vocabulary import WORD_GAME_RACKS, all_game_words, words_for_rack
from ..security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)


# Curated letter racks keep the game playable while giving each round a real
# theme.  The bank is deliberately local and deterministic in shape so a
# provider outage can never make a game unusable.
WORD_GAME_CATEGORIES = {
    "hayvanlar": ("KEDÄ°LER", "Evcil ve vahÅŸi canlÄ±larÄ± Ã§aÄŸrÄ±ÅŸtÄ±ran kelimeler"),
    "yemekler": ("MAKARNASI", "Mutfak, tat ve sofra kelimeleri"),
    "ÅŸehirler": ("Ä°STANBUL", "TÃ¼rkiye ÅŸehirleri ve ÅŸehir yaÅŸamÄ±"),
    "doÄŸa": ("ORMANLAR", "DoÄŸa, iklim ve arazi kelimeleri"),
    "bilim": ("BÄ°LÄ°MLER", "Bilim, teknoloji ve keÅŸif kelimeleri"),
    "spor": ("SPORCULAR", "Spor dallarÄ± ve saha terimleri"),
    "sanat": ("RESÄ°MLER", "Sanat, mÃ¼zik ve sahne kelimeleri"),
    "tarih": ("OSMANLILAR", "Tarih, toplum ve medeniyet kelimeleri"),
    "teknoloji": ("BÄ°LGÄ°SAYAR", "YazÄ±lÄ±m, internet ve donanÄ±m kelimeleri"),
    "meslekler": ("DOKTORLAR", "Meslekler ve Ã§alÄ±ÅŸma hayatÄ±"),
    "uzay": ("YILDIZLAR", "Uzay, gÃ¶k cisimleri ve astronomi"),
    "genel": ("KELÄ°MELER", "Her konudan dengeli bir kelime havuzu"),
}

class GamesTool(BaseTool):
    """Tool for Arel to handle backend logic for group games."""
    
    def __init__(self):
        super().__init__(
            name="games_tool",
            description="Use this for game logic like picking random users or generating game outcomes."
        )
        self.td_game = TruthDareGame()
        self.roulette_game = RouletteGame()
        self.blackjack_game = BlackjackGame()
        self.quiz_game = QuizGame()

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "game_type": {
                    "type": "string",
                    "enum": ["bottle_spin", "truth", "dare", "roulette", "duel", "dice_roll", "coin_flip", "slot_machine", "hangman", "word_game", "batak", "blackjack", "quiz"],
                    "description": "The specific game to calculate."
                },
                "users_list": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of user names/IDs to pick from (for bottle_spin)."
                },
                "duel_participants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The two names involved in a duel."
                },
                "category": {
                    "type": "string",
                    "description": "Category for quiz game."
                },
                "difficulty": {
                    "type": "string",
                    "enum": ["kolay", "normal", "zor", "uzman"],
                    "description": "Difficulty for word games; Turkish names are preferred."
                },
                "players": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of player IDs for complex games."
                }
            },
            "required": ["game_type"]
        }

    async def execute(self, game_type: str, users_list: list = None, duel_participants: list = None, category: str = None, players: list = None, difficulty: str = None, user_id: int = None, **kwargs) -> Any:
        try:
            if game_type == "bottle_spin":
                return self.td_game.spin_bottle(users_list)
            
            elif game_type == "truth":
                return self.td_game.get_truth()
                
            elif game_type == "dare":
                return self.td_game.get_dare()

            elif game_type == "roulette":
                return self.roulette_game.spin()

            elif game_type == "duel":
                if not duel_participants or len(duel_participants) < 2:
                    return "DÃ¼ello iÃ§in bir rakip seÃ§melisin!"
                return self.roulette_game.duel(duel_participants[0], duel_participants[1])

            elif game_type == "dice_roll":
                return self.roulette_game.dice_roll()

            elif game_type == "coin_flip":
                return self.roulette_game.coin_flip()

            elif game_type == "slot_machine":
                items = ["ğŸ’", "ğŸ‹", "ğŸ‡", "ğŸ’", "7ï¸âƒ£"]
                result = [random.choice(items) for _ in range(3)]
                
                msg = f"ğŸ° <b>SLOT MAKÄ°NESÄ°</b> ğŸ°\n\n"
                msg += f"| {result[0]} | {result[1]} | {result[2]} |\n\n"
                
                if result[0] == result[1] == result[2]:
                    msg += "ğŸ‰ <b>JACKPOT! KAZANDIN!</b> ğŸ’°"
                elif result[0] == result[1] or result[1] == result[2]:
                    msg += "âœ¨ <b>KÃ¼Ã§Ã¼k Ã–dÃ¼l!</b> Ä°kili yakaladÄ±n!"
                else:
                    msg += "âŒ <b>Kaybettin...</b> Tekrar dene! ğŸ€"
                return msg

            elif game_type == "hangman":
                # The Telegram handler owns interaction; this tool only builds
                # a bounded, restart-safe round state.
                profile = normalize_difficulty(difficulty)
                word_list = [
                    word.upper()
                    for word in all_game_words()
                    if profile.min_word_length <= len(word) <= profile.max_word_length
                ]
                if not word_list:
                    word_list = [word.upper() for word in all_game_words() if len(word) >= 3]
                word = random.choice(word_list)
                max_lives = {"easy": 8, "normal": 6, "hard": 5, "expert": 4}[profile.key]
                return {
                    'word': word,
                    'hidden': ["_" for _ in word],
                    'lives': max_lives,
                    'max_lives': max_lives,
                    'guessed_letters': [],
                    'wrong_guesses': 0,
                    'hints_used': 0,
                    'owner_id': int(user_id) if user_id is not None else None,
                    'start_time': time.time(),
                    'difficulty': profile.key,
                    'difficulty_label': profile.label,
                    'time_limit': profile.time_limit_seconds,
                    'score_multiplier': profile.score_multiplier,
                }

            elif game_type == "word_game":
                requested = normalize_word_game_category(category or kwargs.get("word_category"))
                if requested not in WORD_GAME_CATEGORIES:
                    requested = random.choice(tuple(WORD_GAME_CATEGORIES))
                rack, category_hint = WORD_GAME_CATEGORIES[requested]
                # Keep the rack metadata backwards compatible while sourcing
                # accepted answers from the shared local vocabulary.  The
                # shuffled display is cosmetic; validation uses the same
                # unshuffled rack and therefore stays deterministic.
                rack = WORD_GAME_RACKS.get(requested, rack)
                profile = normalize_difficulty(difficulty)
                all_valid_words = words_for_rack(requested, rack)
                valid_words = [
                    word for word in all_valid_words
                    if profile.min_word_length <= len(word) <= profile.max_word_length
                ]
                if len(valid_words) < profile.min_target:
                    valid_words = all_valid_words
                letters = list(rack)
                random.shuffle(letters)
                return {
                    'letters': "".join(letters),
                    'found_words': [],
                    'target_count': min(
                        random.randint(profile.min_target, profile.max_target),
                        len(valid_words),
                    ),
                    'category': requested,
                    'category_hint': category_hint,
                    'available_categories': sorted(WORD_GAME_CATEGORIES),
                    'valid_words': valid_words,
                    'dictionary_size': len(valid_words),
                    'difficulty': profile.key,
                    'difficulty_label': profile.label,
                    'time_limit': profile.time_limit_seconds,
                    'score_multiplier': profile.score_multiplier,
                    'hints_used': 0,
                    'hint_history': [],
                    'shuffle_count': 0,
                    'owner_id': int(user_id) if user_id is not None else None,
                }

            elif game_type == "batak":
                # Logic is now mostly in BatakGame, this might be a placeholder or extra logic
                # Orchestrator calls BatakGame directly for start/join, this might be unused or for different flow
                return "Batak oyunu baÅŸlatÄ±ldÄ±!"

            elif game_type == "blackjack":
                # Returns 1-round simulation result for now
                return self.blackjack_game.play_round("Oyuncu")

            elif game_type == "quiz":
                if not category: category = "genel"
                qa = self.quiz_game.get_question(category)
                # Keep the answer out of the AI/tool surface.  Interactive
                # Telegram quiz callbacks keep the answer in their durable
                # session state and reveal it only after a selection.
                return (
                    f"â“ <b>SORU:</b> {qa['question']}\n\n"
                    f"<i>Kategori: {qa['category']} Â· CevabÄ±nÄ± butondan seÃ§.</i>"
                )
                
            return "Oyun tÃ¼rÃ¼ bulunamadÄ±."
            
        except Exception as e:
            logger.error("GamesTool execution failed: %s", summarize_exception(e, label="games"))
            return "Oyun servisi geÃ§ici olarak kullanÄ±lamÄ±yor."
