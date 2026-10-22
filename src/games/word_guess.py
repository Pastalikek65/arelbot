
import random
import time
import json
import os
import re
import logging
from typing import Dict, Optional, List, Any
from src.security.log_redaction import summarize_exception
from src.games.vocabulary import vocabulary_entries
from src.games.engine import normalize_difficulty
from src.games.catalog import WORD_GAME_CATEGORY_LABELS, normalize_word_game_category
from src.games.word_catalog_adapter import WordCatalogAdapter

logger = logging.getLogger(__name__)

class WordGuessGame:
    """Tabu-style word guessing game. Descriptions never contain the answer."""
    
    def __init__(self, chat_engine=None, *, seed: int = 0):
        self.chat_engine = chat_engine
        self.words = self._load_word_list()
        # The adapter owns deterministic category/difficulty selection and the
        # per-scope repeat guard.  ``self.words`` remains public for legacy
        # tooling and tests, while active rounds use the typed catalog.
        try:
            self.catalog_adapter = WordCatalogAdapter(self.words, seed=int(seed))
        except ValueError:
            # Keep the old empty-list error path intact when a corrupt snapshot
            # leaves no safe entries to adapt.
            self.catalog_adapter = None
        # Track recently used words per chat to avoid repeats
        self._recent: Dict[int, List[str]] = {}

    @staticmethod
    def _category(value: object) -> str:
        """Map legacy labels and Turkish aliases to one stable game id."""
        normalized = normalize_word_game_category(value)
        if normalized:
            return normalized
        folded = str(value or "").strip().casefold()
        # Older words.json snapshots used display labels.  Keep those rounds
        # playable while preventing the legacy "Eski Kelimeler" bucket from
        # swallowing the modern category picker.
        legacy = {
            "genel kültür": "genel",
            "genel kultur": "genel",
            "eski kelimeler": "tarih",
            "eğitim": "genel",
            "egitim": "genel",
            "sağlık": "genel",
            "saglik": "genel",
        }
        return legacy.get(folded, "genel")

    @staticmethod
    def _display_category(category: str) -> str:
        """Return a readable label even for snapshots with mojibake ids."""
        labels = dict(WORD_GAME_CATEGORY_LABELS)
        labels.update({"şehirler": "Şehirler", "doğa": "Doğa"})
        return labels.get(category, category.title())

    @classmethod
    def _hint_chain(cls, word: str, category: str, existing: object) -> List[str]:
        """Build a deterministic, progressive clue chain for every answer.

        Hints reveal increasingly useful structure but never echo the answer.
        This keeps the game useful for locally curated and vocabulary-derived
        entries alike, without an AI/network call during a round.
        """
        target = str(word or "").strip()
        letters = re.sub(r"[^\wÇĞİÖŞÜçğıöşü]", "", target, flags=re.UNICODE)
        if not letters:
            return [str(item).strip() for item in (existing or []) if str(item).strip()]
        existing_items = [str(item).strip() for item in (existing or []) if str(item).strip()]
        generated = [
            f"{len(letters)} harflidir.",
            f"{letters[0].upper()} harfi ile başlar.",
            f"{letters[-1].upper()} harfi ile biter.",
            f"İçinde {sum(ch.casefold() in 'aeıioöuü' for ch in letters)} sesli harf bulunur.",
            f"{cls._display_category(category)} kategorisiyle bağlantılıdır.",
        ]
        if len(letters) >= 5:
            generated.append(f"İlk iki harfi {letters[:2].upper()} şeklindedir.")
            generated.append(f"Ortadaki harfi {letters[len(letters) // 2].upper()} harfidir.")
        else:
            generated.append(f"İlk iki harfi {letters[:2].upper()} şeklindedir.")
        merged: List[str] = []
        seen = set()
        for hint in [*existing_items, *generated]:
            normalized = hint.casefold()
            if normalized and normalized not in seen and target.casefold() not in normalized:
                seen.add(normalized)
                merged.append(hint)
        return merged

    def _load_word_list(self) -> List[Dict]:
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            json_path = os.path.join(os.path.dirname(current_dir), "data", "words.json")
            
            if not os.path.exists(json_path):
                json_path = os.path.join(os.getcwd(), "src", "data", "words.json")

            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Keep the hand-written clue file as the premium source, then
            # extend it with the shared local vocabulary so restart-safe
            # rounds do not cycle through a tiny list of answers.  The
            # vocabulary entries use generic, answer-safe clues and therefore
            # never require a network or an AI call.
            data.extend(vocabulary_entries())
                
            # STRICT FILTER: Valid description, no placeholders, answer NOT in description
            valid_words = []
            seen_words = set()
            for raw_item in data:
                item = dict(raw_item)
                desc = item.get('description', '')
                word = item.get('word', '')
                normalized_word = str(word).strip().casefold()
                if normalized_word in seen_words:
                    continue
                if not desc or len(desc) < 15:
                    continue
                if "hazırlanıyor" in desc.lower():
                    continue
                # CRITICAL: Answer must NOT appear in the description
                if word.lower() in desc.lower():
                    continue
                # Check individual word parts (for multi-word answers)
                parts = word.split()
                leaked = False
                for part in parts:
                    clean = part.strip("()")
                    if len(clean) >= 3 and re.search(r'\b' + re.escape(clean) + r'\b', desc, re.IGNORECASE):
                        leaked = True
                        break
                if leaked:
                    continue
                item["category"] = self._category(item.get("category"))
                item["hints"] = self._hint_chain(
                    str(word), item["category"], item.get("hints", [])
                )
                valid_words.append(item)
                seen_words.add(normalized_word)
            
            logger.info(f"Loaded {len(valid_words)} valid words from {len(data)} total entries.")
            return valid_words
        except Exception as e:
            logger.error("Error loading word list: %s", summarize_exception(e, label="word_list"))
            return []

    def _sanitize_text(self, text: str, word: str) -> str:
        """Remove any trace of the answer word from text."""
        if not text:
            return text
        # Full word replacement
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        text = pattern.sub("█████", text)
        # Individual significant parts
        for part in word.split():
            clean = part.strip("()")
            if len(clean) >= 3:
                part_pattern = re.compile(r'\b' + re.escape(clean) + r'\b', re.IGNORECASE)
                text = part_pattern.sub("█████", text)
        # Clean AI prefixes
        text = re.sub(r'^(Terim|Kelime|Term|Cevap|Tanım)(\s*:\s*)', '', text, flags=re.IGNORECASE).strip()
        return text

    def start(
        self,
        chat_id: int,
        difficulty: object = "normal",
        category: object | None = None,
    ) -> Dict[str, Any]:
        """Starts a new game. Fully synchronous — instant start."""
        if not self.words:
            return {"error": "Kelime listesi yüklenemedi."}

        profile = normalize_difficulty(difficulty)
        requested_category = self._category(category) if category else None
        category_pool = [
            item for item in self.words
            if requested_category is None or self._category(item.get("category")) == requested_category
        ]
        if not category_pool:
            category_pool = self.words
        eligible = [
            item for item in category_pool
            if profile.min_word_length <= len(str(item.get("word", "")).strip()) <= profile.max_word_length
        ]
        if not eligible:
            eligible = category_pool

        picked: Dict[str, Any]
        catalog_metadata: Dict[str, Any] = {}
        recent = self._recent.get(chat_id, [])
        if self.catalog_adapter is not None:
            try:
                draw = self.catalog_adapter.draw(
                    scope=f"chat:{int(chat_id)}",
                    category=requested_category,
                    difficulty=profile.key,
                )
                picked = draw.to_legacy(self.catalog_adapter.catalog)
                catalog_metadata = {
                    "catalog_key": draw.entry.key,
                    "catalog_cycle_reset": draw.cycle_reset,
                    "catalog_remaining": draw.remaining,
                    "catalog_scope": f"chat:{int(chat_id)}",
                }
            except LookupError:
                # A tiny custom/legacy snapshot may not have a matching bucket;
                # retain the historical selection path as a safe fallback.
                picked = {}
        else:
            picked = {}

        if not picked:
            # Avoid recently used words in the legacy fallback path.
            available = [w for w in eligible if w['word'] not in recent]
            if not available:
                available = eligible
                self._recent[chat_id] = []
            picked = random.choice(available)
        
        # Track recent
        recent.append(picked['word'])
        if len(recent) > 20:
            recent = recent[-20:]
        self._recent[chat_id] = recent
        
        target_word = picked['word']
        description = self._sanitize_text(picked.get('description', ''), target_word)
        
        # Sanitize hints
        hints = self._hint_chain(target_word, self._category(picked.get("category")), picked.get("hints", []))
        sanitized_hints = [self._sanitize_text(h, target_word) for h in hints]

        # Generate letter hint (first and last letter + length)
        clean_word = target_word.strip()
        letter_hint = f"🔤 {len(clean_word)} harf: {clean_word[0]}{'_' * (len(clean_word)-2)}{clean_word[-1]}" if len(clean_word) > 2 else ""
        
        # Add letter hint at the end of hints list
        if letter_hint:
            sanitized_hints.append(letter_hint)

        game_state = {
            'word': target_word,
            'category': self._category(picked.get('category', 'genel')),
            'description': description,
            'hints': sanitized_hints,
            'hint_index': 0,
            'start_time': time.time(),
            'attempts': 0,
            'difficulty': profile.key,
            'time_limit': profile.time_limit_seconds,
            'score_multiplier': profile.score_multiplier,
        }
        game_state.update(catalog_metadata)
        
        return {"game_state": game_state}

    def check_guess(self, guess: str, game_state: Dict) -> Dict[str, Any]:
        """Check a guess and return result with feedback."""
        target = game_state['word'].lower().strip()
        guess_clean = guess.lower().strip()
        game_state['attempts'] = game_state.get('attempts', 0) + 1
        
        # Exact match
        if guess_clean == target:
            elapsed = time.time() - game_state.get('start_time', time.time())
            return {"correct": True, "elapsed": elapsed, "attempts": game_state['attempts']}
        
        # Match without parenthetical part: "MR (MANYETİK REZONANS)" matches "mr"
        if "(" in target:
            main_part = target.split("(")[0].strip()
            if guess_clean == main_part:
                elapsed = time.time() - game_state.get('start_time', time.time())
                return {"correct": True, "elapsed": elapsed, "attempts": game_state['attempts']}
        
        return {"correct": False}

    def get_next_hint(self, game_state: Dict) -> Optional[str]:
        """Returns the next hint and updates state. Returns None if no hints left."""
        hints = game_state.get('hints', [])
        idx = game_state.get('hint_index', 0)
        
        if idx < len(hints):
            hint = hints[idx]
            game_state['hint_index'] = idx + 1
            return hint
        return None
