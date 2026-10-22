"""Pure Turkish rules for Wordle, antonyms and anagrams."""
from __future__ import annotations

import secrets
import time
from collections import Counter
from copy import deepcopy

from .turkish_words import ORIGINAL_WORDS, WORDS, canonical_word

GAME_KEYS = ("wordle_tr", "karsi_kelime", "anagram")
LOBBY_SECONDS = 1200
WORDLE_SECONDS = 900
ROUND_SECONDS = 60
REVEAL_SECONDS = 5
PANEL_WAIT_SECONDS = 300
ROUND_COUNT = 5
MAX_PLAYERS = 30
MAX_HINTS = 3

# Original, manually reviewed pairs. Alternative answers are true lexical
# antonyms of the prompt; spelling variants are normalized by canonical_word.
_PAIRS = """
büyük|küçük
uzun|kısa
sıcak|soğuk
açık|kapalı
doğru|yanlış
hızlı|yavaş
erken|geç
yeni|eski
genç|yaşlı
sert|yumuşak
ağır|hafif
dar|geniş
yüksek|alçak
derin|sığ
temiz|kirli
dolu|boş
aydınlık|karanlık
kolay|zor,güç
ucuz|pahalı
yakın|uzak
ıslak|kuru
tatlı|acı
aç|tok
cesur|korkak
zengin|fakir,yoksul
güçlü|zayıf
gürültülü|sessiz
kalın|ince
düz|eğri
keskin|kör
canlı|cansız
mutlu|mutsuz,üzgün
sevinçli|üzgün,kederli
iyimser|kötümser
kibar|kaba
cömert|cimri
düzenli|dağınık
şeffaf|opak
parlak|mat
sabit|hareketli
güvenli|tehlikeli
sağlıklı|hasta
başarılı|başarısız
bilgili|bilgisiz
kalabalık|tenha
gündüz|gece
kuzey|güney
doğu|batı
ileri|geri
yukarı|aşağı
iç|dış
ön|arka
başlangıç|son
artı|eksi
var|yok
evet|hayır
gelmek|gitmek
almak|vermek
girmek|çıkmak
açmak|kapatmak
başlamak|bitirmek
bulmak|kaybetmek,yitirmek
uyumak|uyanmak
oturmak|kalkmak
toplamak|dağıtmak
yükselmek|alçalmak
artmak|azalmak
önce|sonra
kazanmak|kaybetmek,yitirmek
açılmak|kapanmak
güçlenmek|zayıflamak
kısalmak|uzamak
hatırlamak|unutmak
inmek|çıkmak
büyümek|küçülmek
kurmak|yıkmak
itmek|çekmek
ayırmak|birleştirmek
sıkmak|gevşetmek
dondurmak|eritmek
yakmak|söndürmek
"""

ANTONYM_CARDS = tuple(
    {"id": index, "prompt": prompt, "answers": tuple(canonical_word(a) for a in answers.split(","))}
    for index, (prompt, answers) in enumerate(line.split("|", 1) for line in _PAIRS.splitlines() if line.strip())
)
WORDLE_WORDS = frozenset(word for word in WORDS if len(word) == 5 and word.isalpha())
WORDLE_TARGETS = tuple(sorted(word for word in ORIGINAL_WORDS if len(word) == 5 and word.isalpha()))
ANAGRAM_WORDS = tuple(sorted(word for word in ORIGINAL_WORDS
                             if 5 <= len(word) <= 9 and word.isalpha() and len(set(word)) > 1))


def _now(now):
    return time.time() if now is None else float(now)


def _next(state, now):
    result = deepcopy(state)
    result["version"] += 1
    result["last_activity_at"] = _now(now)
    return result


def _member(state, user_id):
    player = next((p for p in state["players"] if p["id"] == int(user_id) and p["active"]), None)
    if player is None:
        raise ValueError("word_puzzles_not_player")
    return player


def _playing(state, now):
    if state["phase"] != "playing":
        raise ValueError("word_puzzles_wrong_phase")
    if state["deadline"] is None:
        raise ValueError("word_puzzles_waiting_panel")
    if _now(now) >= state["deadline"]:
        raise ValueError("word_puzzles_expired")


def _word(value):
    word = canonical_word(value)
    if not word or not word.isalpha() or len(word) > 24:
        raise ValueError("word_puzzles_invalid_word")
    return word


def new_lobby(*, game_key, chat_id, thread_id, owner_id, owner_name,
              round_id=None, now=None):
    if game_key not in GAME_KEYS:
        raise ValueError("word_puzzles_invalid_game")
    moment = _now(now)
    return {"game_key": game_key, "chat_id": int(chat_id), "thread_id": thread_id,
            "owner_id": int(owner_id), "round_id": round_id or secrets.token_hex(6),
            "phase": "lobby", "phase_seq": 0, "version": 0,
            "players": [{"id": int(owner_id), "name": str(owner_name)[:60], "active": True}],
            "scores": {str(owner_id): 0}, "winner_ids": [], "created_at": moment,
            "last_activity_at": moment, "lobby_expires_at": moment + LOBBY_SECONDS,
            "deadline": None, "panel_dirty": True, "panel_wait_until": None,
            "delivery_retry_at": 0, "score_pending": False,
            "round_number": 0, "round_count": 1 if game_key == "wordle_tr" else ROUND_COUNT,
            "target": None, "answers": [], "prompt": None, "scramble": None,
            "attempts": [], "hint_count": 0, "hint_order": [], "hint_positions": [],
            "round_value": 10, "round_winner_id": None, "played_cards": [],
            "deck_order": [], "reason": None}


def join(state, *, user_id, user_name, now=None):
    if state["phase"] != "lobby" or _now(now) >= state["lobby_expires_at"]:
        raise ValueError("word_puzzles_lobby_closed")
    if state["game_key"] == "wordle_tr":
        raise ValueError("word_puzzles_solo")
    if any(p["id"] == int(user_id) for p in state["players"]):
        raise ValueError("word_puzzles_already_joined")
    if len(state["players"]) >= MAX_PLAYERS:
        raise ValueError("word_puzzles_full")
    result = _next(state, now)
    result["players"].append({"id": int(user_id), "name": str(user_name)[:60], "active": True})
    result["scores"][str(user_id)] = 0
    return result


def wordle_feedback(answer, guess):
    answer, guess = _word(answer), _word(guess)
    if len(answer) != 5 or len(guess) != 5:
        raise ValueError("word_puzzles_word_length")
    result = ["absent"] * 5
    remaining = Counter()
    for index, (actual, proposed) in enumerate(zip(answer, guess)):
        if actual == proposed:
            result[index] = "correct"
        else:
            remaining[actual] += 1
    for index, proposed in enumerate(guess):
        if result[index] == "correct":
            continue
        if remaining[proposed]:
            result[index] = "present"
            remaining[proposed] -= 1
    return tuple(result)


def _scramble(word, rng):
    chars = list(word)
    for _ in range(24):
        rng.shuffle(chars)
        shuffled = "".join(chars)
        if shuffled != word and shuffled not in ANAGRAM_WORDS:
            return shuffled
    for first in range(len(word)):
        for second in range(first + 1, len(word)):
            chars = list(word)
            chars[first], chars[second] = chars[second], chars[first]
            shuffled = "".join(chars)
            if shuffled != word and shuffled not in ANAGRAM_WORDS:
                return shuffled
    raise RuntimeError("word_puzzles_scramble_unavailable")


def _open_round(result, rng):
    index = result["deck_order"][result["round_number"] - 1]
    result.update(phase="playing", deadline=None, panel_wait_until=None,
                  round_winner_id=None, hint_count=0, hint_positions=[], round_value=10)
    result["phase_seq"] += 1
    if result["game_key"] == "karsi_kelime":
        card = ANTONYM_CARDS[index]
        result.update(target=card["answers"][0], answers=list(card["answers"]),
                      prompt=card["prompt"], scramble=None, hint_order=[])
    else:
        word = ANAGRAM_WORDS[index]
        order = list(range(len(word)))
        rng.shuffle(order)
        result.update(target=word, answers=[word], prompt=None,
                      scramble=_scramble(word, rng), hint_order=order)
    result["played_cards"].append(index)
    return result


def start(state, *, user_id, target=None, card_order=None, word_order=None,
          rng=None, now=None):
    if state["phase"] != "lobby" or _now(now) >= state["lobby_expires_at"]:
        raise ValueError("word_puzzles_lobby_closed")
    if int(user_id) != state["owner_id"]:
        raise ValueError("word_puzzles_not_owner")
    rng = rng if rng is not None else secrets.SystemRandom()
    result = _next(state, now)
    result["round_number"] = 1
    if state["game_key"] == "wordle_tr":
        chosen = canonical_word(target) if target is not None else rng.choice(WORDLE_TARGETS)
        if chosen not in WORDLE_TARGETS:
            raise ValueError("word_puzzles_invalid_target")
        result.update(phase="playing", phase_seq=1, target=chosen, answers=[chosen],
                      deadline=None, attempts=[])
        return result
    pool = ANTONYM_CARDS if state["game_key"] == "karsi_kelime" else ANAGRAM_WORDS
    supplied = card_order if state["game_key"] == "karsi_kelime" else word_order
    order = list(supplied) if supplied is not None else rng.sample(range(len(pool)), ROUND_COUNT)
    if len(order) != ROUND_COUNT or len(set(order)) != ROUND_COUNT or any(
        not isinstance(i, int) or i < 0 or i >= len(pool) for i in order
    ):
        raise ValueError("word_puzzles_invalid_deck")
    result["deck_order"] = order
    return _open_round(result, rng)


def _finish(state, reason, now, *, increment=True):
    result = _next(state, now) if increment else state
    result.update(phase="finished", phase_seq=state["phase_seq"] + 1,
                  deadline=None, panel_wait_until=None, reason=reason,
                  score_pending=any(score > 0 for score in result["scores"].values()))
    best = max(result["scores"].values(), default=0)
    result["winner_ids"] = [p["id"] for p in result["players"] if p["active"]
                            and best > 0 and result["scores"].get(str(p["id"])) == best]
    return result


def submit_text(state, *, user_id, text, now=None):
    _playing(state, now)
    _member(state, user_id)
    word = _word(text)
    if state["game_key"] == "wordle_tr":
        if int(user_id) != state["owner_id"]:
            raise ValueError("word_puzzles_not_player")
        if len(word) != 5:
            raise ValueError("word_puzzles_word_length")
        if word not in WORDLE_WORDS:
            raise ValueError("word_puzzles_unknown_word")
        result = _next(state, now)
        result["attempts"].append({"guess": word, "marks": list(wordle_feedback(state["target"], word))})
        if word == state["target"]:
            result["scores"][str(user_id)] = max(10, 70 - 10 * len(result["attempts"]))
            return _finish(result, "solved", now, increment=False)
        if len(result["attempts"]) >= 6:
            return _finish(result, "attempts_used", now, increment=False)
        return result
    if state["game_key"] == "anagram":
        accepted = word in ANAGRAM_WORDS and Counter(word) == Counter(state["target"])
    else:
        accepted = word in state["answers"]
    if not accepted:
        return state
    result = _next(state, now)
    if state["game_key"] == "anagram":
        result["target"] = word
        result["answers"] = [word]
    result["scores"][str(user_id)] += result["round_value"]
    result.update(phase="reveal", phase_seq=state["phase_seq"] + 1,
                  deadline=None, round_winner_id=int(user_id))
    return result


def hint(state, *, user_id, now=None):
    _playing(state, now)
    _member(state, user_id)
    if state["game_key"] != "anagram" or state["hint_count"] >= MAX_HINTS:
        raise ValueError("word_puzzles_hint_unavailable")
    result = _next(state, now)
    result["hint_positions"].append(result["hint_order"][result["hint_count"]])
    result["hint_count"] += 1
    result["round_value"] = 10 - 2 * result["hint_count"]
    return result


def expire(state, *, now=None, rng=None):
    moment = _now(now)
    if state["phase"] == "lobby" and moment >= state["lobby_expires_at"]:
        result = _next(state, now)
        result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                      deadline=None, reason="lobby_timeout")
        return result
    if state["deadline"] is None or moment < state["deadline"]:
        raise ValueError("word_puzzles_not_expired")
    if state["phase"] == "playing":
        if state["game_key"] == "wordle_tr":
            return _finish(state, "time_up", now)
        result = _next(state, now)
        result.update(phase="reveal", phase_seq=state["phase_seq"] + 1,
                      deadline=None, round_winner_id=None)
        return result
    if state["phase"] == "reveal":
        if state["round_number"] >= state["round_count"]:
            return _finish(state, "rounds_complete", now)
        result = _next(state, now)
        result["round_number"] += 1
        return _open_round(result, rng if rng is not None else secrets.SystemRandom())
    raise ValueError("word_puzzles_wrong_phase")


def leave(state, *, user_id, now=None):
    _member(state, user_id)
    if state["phase"] in {"finished", "cancelled"}:
        raise ValueError("word_puzzles_wrong_phase")
    result = _next(state, now)
    if int(user_id) == state["owner_id"]:
        result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                      deadline=None, reason="owner_left", score_pending=False)
    elif state["phase"] == "lobby":
        result["players"] = [p for p in result["players"] if p["id"] != int(user_id)]
        result["scores"].pop(str(user_id), None)
    else:
        _member(result, user_id)["active"] = False
    return result


def cancel(state, *, user_id, moderator=False, now=None):
    if state["phase"] in {"finished", "cancelled"}:
        raise ValueError("word_puzzles_wrong_phase")
    if int(user_id) != state["owner_id"] and not moderator:
        raise ValueError("word_puzzles_not_owner")
    result = _next(state, now)
    result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                  deadline=None, reason="manual_cancel", score_pending=False)
    return result


def public_state(state):
    fields = ("game_key", "chat_id", "thread_id", "owner_id", "round_id", "phase", "phase_seq",
              "players", "scores", "winner_ids", "version", "lobby_expires_at", "deadline",
              "round_number", "round_count", "prompt", "scramble", "attempts", "hint_count",
              "hint_positions", "round_value", "round_winner_id", "reason", "score_pending",
              "panel_message_id")
    view = {key: deepcopy(state[key]) for key in fields if key in state}
    if state["phase"] in {"reveal", "finished"}:
        view["target"] = state["target"]
        view["answers"] = deepcopy(state["answers"])
    if state["game_key"] == "anagram" and state["phase"] == "playing":
        view["hint_pattern"] = "".join(char if index in state["hint_positions"] else "·"
                                       for index, char in enumerate(state["target"]))
    return view
