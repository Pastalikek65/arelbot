"""Pure, bounded rules for five distinct Turkish multiplayer word games."""
from __future__ import annotations

import re
import secrets
import time
from collections import Counter
from copy import deepcopy

from .turkish_words import ORIGINAL_WORDS, canonical_word

GAME_KEYS = ("harf_maratonu", "sozcuk_avi", "yasak_harf", "ortak_hikaye", "bomba")
TERMINAL = {"finished", "cancelled"}
PANEL_WAIT_SECONDS = 300
LOBBY_SECONDS = 1200
REVEAL_SECONDS = 5
MAX_STORY_UTF16 = 3000
LIMITS = {"harf_maratonu": (2, 20), "sozcuk_avi": (1, 12),
          "yasak_harf": (2, 12), "ortak_hikaye": (3, 10), "bomba": (2, 12)}
ACTIVE_PHASES = {"collect", "hunt", "clue", "story_turn", "bomb_turn", "reveal"}

CATEGORY_LEXICON = {
    "hayvan": frozenset(["kedi", "kaplan", "karga", "karınca", "kaz", "koala", "kuğu", "aslan", "akrep", "arı", "ayı", "balık", "baykuş", "böcek", "bülbül", "deve", "fil", "geyik", "horoz", "inek", "keçi", "kirpi", "köpek", "koyun", "kuş", "leylek", "tavşan", "tilki", "yunus", "zürafa"]),
    "yiyecek": frozenset(["pasta", "pilav", "pide", "portakal", "patates", "baklava", "biber", "bal", "bulgur", "badem", "bamya", "börek", "ceviz", "çorba", "elma", "erik", "fındık", "helva", "incir", "kabak", "kayısı", "kiraz", "limon", "makarna", "marul", "nohut", "peynir", "salata", "simit", "sucuk", "üzüm"]),
    "eşya": frozenset(["kalem", "kitap", "kilit", "kamera", "kablo", "bardak", "bavul", "bıçak", "çanta", "defter", "düdük", "eldiven", "fener", "fincan", "gözlük", "halı", "havlu", "keman", "lamba", "makas", "masa", "mektup", "perde", "radyo", "saat", "şemsiye", "tarak", "tepsi", "vazo"]),
    "yer": frozenset(["bahçe", "cadde", "çarşı", "depo", "dere", "fabrika", "galeri", "garaj", "iskele", "istasyon", "kale", "kanyon", "koridor", "köprü", "liman", "orman", "park", "pazar", "sahil", "saray", "sokak", "tarla", "tünel", "vadi"]),
    "3–12 harfli sözcük": frozenset(w for w in ORIGINAL_WORDS if w.isalpha() and 3 <= len(w) <= 12),
    "3–6 harfli sözcük": frozenset(w for w in ORIGINAL_WORDS if w.isalpha() and 3 <= len(w) <= 6),
}
MARATHON_CARDS = (("k", "3–12 harfli sözcük"), ("s", "3–6 harfli sözcük"),
                  ("b", "3–12 harfli sözcük"), ("a", "3–6 harfli sözcük"),
                  ("d", "3–12 harfli sözcük"), ("t", "3–6 harfli sözcük"),
                  ("y", "3–12 harfli sözcük"), ("m", "3–6 harfli sözcük"),
                  ("p", "3–12 harfli sözcük"), ("e", "3–6 harfli sözcük"))
FORBIDDEN_CARDS = (
    ("kalem", "e"), ("şemsiye", "ş"), ("kiraz", "i"), ("bardak", "a"),
    ("bisiklet", "i"), ("fener", "e"), ("pencere", "e"), ("tavşan", "a"),
    ("portakal", "a"), ("düdük", "ü"), ("gözlük", "ö"), ("kitap", "i"),
    ("merdiven", "e"), ("papatya", "a"), ("robot", "o"), ("sandalye", "a"),
    ("terlik", "e"), ("uçurtma", "u"), ("yastık", "ı"), ("zeytin", "e"),
    ("çorap", "o"), ("oymak", "o"), ("telefon", "e"), ("mektup", "e"),
)
STORY_WORDS = ("kapı", "orman", "anahtar", "nehir", "ışık", "yol", "gemi",
               "bahçe", "kuş", "kitap", "dünya", "saat", "rüzgâr", "köprü")
BIGRAM_WORDS = {
    pair: frozenset(word for word in ORIGINAL_WORDS if pair in word)
    for pair in ("an", "ak", "ka", "ar", "al", "er", "ma", "ek", "ra",
                 "ta", "en", "ba", "sa")
}
HUNT_BOARD = ("kedi", "elma", "masa", "kale")
HUNT_WORDS = frozenset(w for w in ORIGINAL_WORDS if 3 <= len(w) <= 8 and w.isalpha())
HUNT_ROWS = tuple(sorted(w for w in HUNT_WORDS if len(w) == 4))
HUNT_VARIABLE_ROWS = tuple(w for w in HUNT_ROWS if w != "kedi")


def _hunt_trie():
    root = {}
    for word in HUNT_WORDS:
        node = root
        for letter in word:
            node = node.setdefault(letter, {})
        node[""] = True
    return root


HUNT_TRIE = _hunt_trie()


def _now(now):
    return time.time() if now is None else float(now)


def _copy(state, now):
    result = deepcopy(state)
    result["version"] += 1
    result["last_activity_at"] = _now(now)
    return result


def _member(state, user_id):
    player = next((p for p in state["players"] if p["id"] == int(user_id) and p["active"]), None)
    if player is None:
        raise ValueError("wa_not_player")
    return player


def _live(state, phase, now):
    if state["phase"] != phase:
        raise ValueError("wa_wrong_phase")
    if state["deadline"] is None:
        raise ValueError("wa_waiting_panel")
    if _now(now) >= state["deadline"]:
        raise ValueError("wa_expired")


def _word(text):
    word = canonical_word(text)
    if not word or len(word) > 32 or not word.isalpha():
        raise ValueError("wa_invalid_word")
    return word


def category_words(category, letter):
    return tuple(sorted(w for w in CATEGORY_LEXICON[category] if w.startswith(letter)))


def _utf16(text):
    return len(text.encode("utf-16-le")) // 2


def new_lobby(*, game_key, chat_id, thread_id, owner_id, owner_name,
              now=None, round_id=None):
    if game_key not in GAME_KEYS:
        raise ValueError("wa_invalid_game")
    moment = _now(now)
    return {"game_key": game_key, "chat_id": int(chat_id), "thread_id": thread_id,
            "round_id": round_id or secrets.token_hex(6), "owner_id": int(owner_id),
            "players": [{"id": int(owner_id), "name": str(owner_name)[:40], "active": True}],
            "scores": {str(owner_id): 0}, "phase": "lobby", "phase_seq": 0,
            "version": 0, "created_at": moment, "last_activity_at": moment,
            "lobby_expires_at": moment + LOBBY_SECONDS, "deadline": None,
            "panel_wait_until": None, "panel_dirty": True, "delivery_retry_at": 0,
            "panel_message_id": None, "private_dirty": False, "private_retry_at": None,
            "score_pending": False, "processed_actions": [], "winner_ids": [],
            "reason": None, "round_no": 0, "round_count": 0, "deck": [],
            "target": None, "forbidden": None, "narrator_id": None,
            "private_message_id": None, "private_ack": False,
            "invitations": {}, "dm_messages": {}, "input_floor": 0}


def join(state, *, user_id, user_name, now=None):
    if state["phase"] != "lobby" or _now(now) >= state["lobby_expires_at"]:
        raise ValueError("wa_lobby_closed")
    if any(p["id"] == int(user_id) for p in state["players"]):
        raise ValueError("wa_already_joined")
    if len(state["players"]) >= LIMITS[state["game_key"]][1]:
        raise ValueError("wa_full")
    result = _copy(state, now)
    result["players"].append({"id": int(user_id), "name": str(user_name)[:40], "active": True})
    result["scores"][str(user_id)] = 0
    return result


def _finish(state, reason, now, *, copy=True):
    result = _copy(state, now) if copy else state
    result.update(phase="finished", phase_seq=state["phase_seq"] + 1,
                  deadline=None, panel_wait_until=None, private_dirty=False,
                  private_retry_at=None, reason=reason)
    best = max((result["scores"].get(str(p["id"]), 0) for p in result["players"] if p["active"]), default=0)
    result["winner_ids"] = [p["id"] for p in result["players"] if p["active"]
                            and best > 0 and result["scores"].get(str(p["id"])) == best]
    result["score_pending"] = any(result["scores"].values())
    return result


def _open_marathon(result):
    letter, category = MARATHON_CARDS[result["deck"][result["round_no"] - 1]]
    result.update(phase="collect", phase_seq=result["phase_seq"] + 1,
                  deadline=None, letter=letter, category=category,
                  submissions={}, round_results=[])
    return result


def _open_forbidden(result):
    index = result["deck"][result["round_no"] - 1]
    active = [p for p in result["players"] if p["active"]]
    result.update(phase="secret_delivery", phase_seq=result["phase_seq"] + 1,
                  deadline=None, target=FORBIDDEN_CARDS[index][0],
                  forbidden=FORBIDDEN_CARDS[index][1],
                  narrator_id=active[(result["round_no"] - 1) % len(active)]["id"],
                  clue_count=0, clue_message_ids=[], round_winner_id=None, round_reason=None,
                  private_dirty=False, private_retry_at=None,
                  private_message_id=None, private_ack=False)
    return result


def start(state, *, user_id, now=None, rng=None, card_order=None,
          bigram=None, fuse_seconds=None):
    moment = _now(now)
    if state["phase"] != "lobby" or moment >= state["lobby_expires_at"]:
        raise ValueError("wa_lobby_closed")
    if int(user_id) != state["owner_id"]:
        raise ValueError("wa_not_owner")
    game = state["game_key"]
    if not LIMITS[game][0] <= len(state["players"]) <= LIMITS[game][1]:
        raise ValueError("wa_player_count")
    rng = rng if rng is not None else secrets.SystemRandom()
    result = _copy(state, now)
    result["round_no"] = 1
    if game == "harf_maratonu":
        order = list(card_order) if card_order is not None else rng.sample(range(len(MARATHON_CARDS)), 5)
        if len(order) != 5 or len(set(order)) != 5 or any(not isinstance(i, int) or i < 0 or i >= len(MARATHON_CARDS) for i in order):
            raise ValueError("wa_invalid_deck")
        result.update(round_count=5, deck=order)
        return _open_marathon(result)
    if game == "sozcuk_avi":
        board = generate_hunt_board(rng)
        result.update(phase="hunt", phase_seq=1, board=board, claimed={},
                      round_count=1, deadline=None)
        return result
    if game == "yasak_harf":
        count = 2 * len(result["players"])
        order = list(card_order) if card_order is not None else rng.sample(range(len(FORBIDDEN_CARDS)), count)
        if len(order) != count or len(set(order)) != count or any(not isinstance(i, int) or i < 0 or i >= len(FORBIDDEN_CARDS) for i in order):
            raise ValueError("wa_invalid_deck")
        result.update(round_count=count, deck=order)
        return _open_forbidden(result)
    if game == "ortak_hikaye":
        result.update(phase="story_turn", phase_seq=1,
                      round_count=2 * len(result["players"]), turn_index=0,
                      story=[], required_word=STORY_WORDS[0], continuity_word=None,
                      deadline=None)
        return result
    chosen = bigram if bigram is not None else rng.choice(tuple(BIGRAM_WORDS))
    fuse = fuse_seconds if fuse_seconds is not None else rng.randint(60, 120)
    if chosen not in BIGRAM_WORDS or not 60 <= int(fuse) <= 120:
        raise ValueError("wa_invalid_bomb")
    result.update(phase="bomb_turn", phase_seq=1, round_count=60,
                  turn_index=0, current_id=result["players"][0]["id"],
                  lives={str(p["id"]): 2 for p in result["players"]},
                  used_words=[], bigram=chosen, fuse_remaining=float(fuse),
                  fuse_deadline=None, passes=0, explosions=0, deadline=None)
    return result


def find_path(board, word):
    word = canonical_word(word)
    if len(board) != 4 or any(len(row) != 4 for row in board) or not 3 <= len(word) <= 8:
        return None
    for origin in range(16):
        def walk(position, index, used, path):
            r, c = divmod(position, 4)
            if board[r][c] != word[index]:
                return None
            if index == len(word) - 1:
                return path + [position]
            for nr in range(max(0, r - 1), min(3, r + 1) + 1):
                for nc in range(max(0, c - 1), min(3, c + 1) + 1):
                    neighbor = nr * 4 + nc
                    if neighbor != position and not (used & (1 << neighbor)):
                        found = walk(neighbor, index + 1, used | (1 << neighbor), path + [position])
                        if found is not None:
                            return found
            return None
        found = walk(origin, 0, 1 << origin, [])
        if found is not None:
            return found
    return None


def board_words(board):
    if len(board) != 4 or any(len(row) != 4 for row in board):
        return ()
    found = set()

    def walk(position, used, node, prefix):
        row, col = divmod(position, 4)
        letter = board[row][col]
        child = node.get(letter)
        if child is None:
            return
        word = prefix + letter
        if "" in child:
            found.add(word)
        if len(word) >= 8:
            return
        for nr in range(max(0, row - 1), min(3, row + 1) + 1):
            for nc in range(max(0, col - 1), min(3, col + 1) + 1):
                neighbor = nr * 4 + nc
                if not used & (1 << neighbor):
                    walk(neighbor, used | (1 << neighbor), child, word)

    for origin in range(16):
        walk(origin, 1 << origin, HUNT_TRIE, "")
    return tuple(sorted(found))


def generate_hunt_board(rng=None):
    """Choose authored four-letter rows, rejecting boards with sparse paths."""
    rng = rng if rng is not None else secrets.SystemRandom()
    for _ in range(32):
        board = ["kedi", *rng.sample(HUNT_VARIABLE_ROWS, 3)]
        rng.shuffle(board)
        if len(board_words(board)) >= 6:
            return board
    return list(HUNT_BOARD)


def _next_story_index(state, index):
    while index < state["round_count"] and not state["players"][index % len(state["players"])]["active"]:
        index += 1
    return index


def secret_ack(state, *, user_id, now=None):
    if state["phase"] != "secret_delivery" or int(user_id) != state["narrator_id"]:
        raise ValueError("wa_secret_forbidden")
    if not state.get("private_message_id"):
        raise ValueError("wa_private_not_delivered")
    result = _copy(state, now)
    result.update(phase="clue", phase_seq=state["phase_seq"] + 1,
                  private_ack=True, private_dirty=False, private_retry_at=None,
                  deadline=None)
    return result


def edit_clue(state, *, user_id, text, message_id, now=None):
    _live(state, "clue", now)
    if int(user_id) != state["narrator_id"] or int(message_id) not in state["clue_message_ids"]:
        raise ValueError("wa_stale_clue")
    content = canonical_word(text)
    if (not 3 <= len(content) <= 180 or state["target"] in content or
            state["forbidden"] in content):
        result = _copy(state, now)
        result.update(phase="reveal", phase_seq=state["phase_seq"] + 1,
                      deadline=None, round_winner_id=None,
                      round_reason="invalid_clue_edit")
        return result
    return _copy(state, now)


def _story_submit(state, user_id, text, now):
    _live(state, "story_turn", now)
    if state["players"][state["turn_index"] % len(state["players"])]["id"] != int(user_id):
        raise ValueError("wa_wrong_actor")
    text = str(text).strip()
    if not 20 <= _utf16(text) <= 240 or "\n" in text or any(mark in text for mark in "&<>"):
        raise ValueError("wa_story_length")
    sentences = [part.strip() for part in re.split(r"[.!?]+", text) if part.strip()]
    if len(sentences) != 2:
        raise ValueError("wa_two_sentences")
    normalized = canonical_word(text)
    words = re.findall(r"[a-zçğıöşüâîû]+", normalized)
    if state["required_word"] not in words or (state["continuity_word"] and state["continuity_word"] not in words):
        raise ValueError("wa_required_word")
    if _utf16("\n".join([*state["story"], text])) > MAX_STORY_UTF16:
        raise ValueError("wa_story_full")
    result = _copy(state, now)
    result["story"].append(text)
    result["scores"][str(user_id)] += 10
    result["continuity_word"] = words[-1]
    result["turn_index"] = _next_story_index(result, result["turn_index"] + 1)
    if result["turn_index"] >= result["round_count"] or _utf16("\n".join(result["story"])) >= MAX_STORY_UTF16 - 240:
        return _finish(result, "story_complete", now, copy=False)
    result.update(phase_seq=state["phase_seq"] + 1, deadline=None,
                  required_word=STORY_WORDS[result["turn_index"] % len(STORY_WORDS)])
    return result


def submit(state, *, user_id, text, now=None):
    game = state["game_key"]
    _member(state, user_id)
    if game == "ortak_hikaye":
        return _story_submit(state, user_id, text, now)
    if game == "harf_maratonu":
        _live(state, "collect", now)
        word = _word(text)
        if word not in CATEGORY_LEXICON[state["category"]] or not word.startswith(state["letter"]):
            raise ValueError("wa_category_word")
        if str(user_id) in state["submissions"]:
            raise ValueError("wa_already_submitted")
        result = _copy(state, now)
        result["submissions"][str(user_id)] = word
        return result
    if game == "sozcuk_avi":
        _live(state, "hunt", now)
        word = _word(text)
        if word not in HUNT_WORDS or find_path(state["board"], word) is None:
            raise ValueError("wa_invalid_path")
        if word in state["claimed"]:
            raise ValueError("wa_already_claimed")
        result = _copy(state, now)
        result["claimed"][word] = int(user_id)
        result["scores"][str(user_id)] += max(1, len(word) - 2)
        return result
    if game == "yasak_harf":
        _live(state, "clue", now)
        content = canonical_word(text)
        if int(user_id) == state["narrator_id"]:
            if not 3 <= len(content) <= 180:
                raise ValueError("wa_clue_length")
            if state["target"] in content:
                raise ValueError("wa_target_leak")
            if state["forbidden"] in content:
                raise ValueError("wa_forbidden_letter")
            result = _copy(state, now)
            result["clue_count"] += 1
            return result
        if _word(text) != state["target"] or not state["clue_count"]:
            return state
        result = _copy(state, now)
        result["scores"][str(user_id)] += 10
        result["scores"][str(state["narrator_id"])] += 5
        result.update(phase="reveal", phase_seq=state["phase_seq"] + 1,
                      deadline=None, round_winner_id=int(user_id))
        return result
    _live(state, "bomb_turn", now)
    if int(user_id) != state["current_id"]:
        raise ValueError("wa_wrong_actor")
    word = _word(text)
    if word not in ORIGINAL_WORDS or state["bigram"] not in word or word in state["used_words"]:
        raise ValueError("wa_invalid_bomb_word")
    result = _copy(state, now)
    result["used_words"].append(word)
    result["scores"][str(user_id)] += 1
    result["passes"] += 1
    if result["passes"] >= 60:
        return _finish(result, "turn_limit", now, copy=False)
    result["fuse_remaining"] = max(0.0, float(state["fuse_deadline"]) - _now(now))
    result["fuse_deadline"] = state["fuse_deadline"]
    result["turn_index"] = _next_bomb_index(result, state["turn_index"])
    result["current_id"] = result["players"][result["turn_index"]]["id"]
    result.update(phase_seq=state["phase_seq"] + 1, deadline=None)
    return result


def _next_bomb_index(state, old_index):
    count = len(state["players"])
    for offset in range(1, count + 1):
        candidate = (old_index + offset) % count
        if state["lives"][str(state["players"][candidate]["id"])] > 0:
            return candidate
    raise RuntimeError("wa_no_survivor")


def _next_bigram(used_words, rng):
    used = set(used_words)
    choices = [pair for pair, words in BIGRAM_WORDS.items()
               if len(words - used) >= 12]
    return rng.choice(choices) if choices else None


def expire(state, *, now=None, rng=None):
    moment = _now(now)
    if state["phase"] == "lobby" and moment >= state["lobby_expires_at"]:
        result = _copy(state, now)
        result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                      reason="lobby_timeout", deadline=None)
        return result
    if state["deadline"] is None or moment < state["deadline"]:
        raise ValueError("wa_not_expired")
    game = state["game_key"]
    if game == "harf_maratonu" and state["phase"] == "collect":
        result = _copy(state, now)
        counts = Counter(state["submissions"].values())
        result["round_results"] = [{"id": int(uid), "word": word, "points": 10 if counts[word] == 1 else 0}
                                   for uid, word in state["submissions"].items()]
        for row in result["round_results"]:
            result["scores"][str(row["id"])] += row["points"]
        result.update(phase="reveal", phase_seq=state["phase_seq"] + 1, deadline=None)
        return result
    if game in {"harf_maratonu", "yasak_harf"} and state["phase"] == "reveal":
        if state["round_no"] >= state["round_count"]:
            return _finish(state, "rounds_complete", now)
        result = _copy(state, now)
        result["round_no"] += 1
        return _open_marathon(result) if game == "harf_maratonu" else _open_forbidden(result)
    if game == "sozcuk_avi" and state["phase"] == "hunt":
        return _finish(state, "hunt_complete", now)
    if game == "yasak_harf" and state["phase"] == "clue":
        result = _copy(state, now)
        result.update(phase="reveal", phase_seq=state["phase_seq"] + 1,
                      deadline=None, round_winner_id=None)
        return result
    if game == "ortak_hikaye" and state["phase"] == "story_turn":
        result = _copy(state, now)
        result["turn_index"] = _next_story_index(result, result["turn_index"] + 1)
        if result["turn_index"] >= result["round_count"]:
            return _finish(result, "turn_limit", now, copy=False)
        result.update(phase_seq=state["phase_seq"] + 1, deadline=None,
                      required_word=STORY_WORDS[result["turn_index"] % len(STORY_WORDS)])
        return result
    if game == "bomba" and state["phase"] == "bomb_turn":
        rng = rng if rng is not None else secrets.SystemRandom()
        result = _copy(state, now)
        loser = str(state["current_id"])
        result["lives"][loser] -= 1
        result["explosions"] += 1
        alive = [p for p in result["players"] if result["lives"][str(p["id"])] > 0]
        if len(alive) <= 1:
            if alive:
                result["scores"][str(alive[0]["id"])] += 20
            return _finish(result, "last_standing", now, copy=False)
        result["turn_index"] = _next_bomb_index(result, state["turn_index"])
        result["current_id"] = result["players"][result["turn_index"]]["id"]
        next_pair = _next_bigram(result["used_words"], rng)
        if next_pair is None:
            return _finish(result, "word_bank_exhausted", now, copy=False)
        result.update(phase_seq=state["phase_seq"] + 1, deadline=None,
                      bigram=next_pair,
                      fuse_remaining=float(rng.randint(60, 120)), fuse_deadline=None)
        return result
    raise ValueError("wa_wrong_phase")


def leave(state, *, user_id, now=None):
    _member(state, user_id)
    if state["phase"] in TERMINAL:
        raise ValueError("wa_wrong_phase")
    if int(user_id) == state["owner_id"]:
        return cancel(state, user_id=user_id, now=now)
    result = _copy(state, now)
    if state["phase"] == "lobby":
        result["players"] = [p for p in result["players"] if p["id"] != int(user_id)]
        result["scores"].pop(str(user_id), None)
    else:
        _member(result, user_id)["active"] = False
        if state["game_key"] == "bomba":
            result["lives"][str(user_id)] = 0
            alive = [p for p in result["players"] if p["active"] and result["lives"][str(p["id"])] > 0]
            if len(alive) <= 1:
                if alive:
                    result["scores"][str(alive[0]["id"])] += 20
                return _finish(result, "last_standing", now, copy=False)
            if state["current_id"] == int(user_id):
                result["fuse_remaining"] = max(0.0, float(state.get("fuse_deadline") or _now(now)) - _now(now))
                result["fuse_deadline"] = state.get("fuse_deadline")
                result["turn_index"] = _next_bomb_index(result, state["turn_index"])
                result["current_id"] = result["players"][result["turn_index"]]["id"]
                result.update(phase_seq=state["phase_seq"] + 1, deadline=None)
        elif sum(p["active"] for p in result["players"]) < LIMITS[state["game_key"]][0]:
            result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                          deadline=None, panel_wait_until=None, private_dirty=False,
                          private_retry_at=None, score_pending=False,
                          reason="insufficient_players")
            return result
        if state["game_key"] == "yasak_harf" and state["narrator_id"] == int(user_id):
            result.update(phase="reveal", phase_seq=state["phase_seq"] + 1,
                          deadline=None, private_dirty=False, private_retry_at=None)
        if state["game_key"] == "ortak_hikaye" and state["phase"] == "story_turn":
            next_index = _next_story_index(result, result["turn_index"])
            if next_index >= result["round_count"]:
                return _finish(result, "story_complete", now, copy=False)
            if next_index != result["turn_index"]:
                result.update(turn_index=next_index, phase_seq=state["phase_seq"] + 1,
                              deadline=None)
    return result


def cancel(state, *, user_id, moderator=False, now=None):
    if state["phase"] in TERMINAL:
        raise ValueError("wa_wrong_phase")
    if int(user_id) != state["owner_id"] and not moderator:
        raise ValueError("wa_not_owner")
    result = _copy(state, now)
    result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                  deadline=None, panel_wait_until=None, private_dirty=False,
                  private_retry_at=None, score_pending=False, reason="manual_cancel")
    return result


def public_state(state):
    fields = ("game_key", "chat_id", "thread_id", "round_id", "owner_id", "players",
              "scores", "phase", "phase_seq", "version", "lobby_expires_at",
              "deadline", "panel_message_id", "winner_ids", "reason", "round_no",
              "round_count", "letter", "category", "round_results", "board",
              "claimed", "narrator_id", "clue_count", "round_winner_id",
              "story", "required_word", "continuity_word", "turn_index",
              "lives", "current_id", "bigram", "passes", "explosions")
    view = {key: deepcopy(state[key]) for key in fields if key in state}
    if state["game_key"] == "harf_maratonu" and state["phase"] == "collect":
        view["submitted_count"] = len(state["submissions"])
        view["private_ready_count"] = sum(
            p["active"] and str(p["id"]) in state["dm_messages"]
            for p in state["players"])
    if state["game_key"] == "yasak_harf" and state["phase"] in {"reveal", "finished"}:
        view["target"] = state["target"]
        view["forbidden"] = state["forbidden"]
    return view
