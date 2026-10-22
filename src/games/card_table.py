"""Deterministic Pişti and Papaz Kimde rules; no Telegram or database imports."""
from __future__ import annotations

from copy import deepcopy
import random
import secrets
import time

GAME_KEYS = ("pisti", "papaz_kimde")
TERMINAL = {"finished", "cancelled"}
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUITS = ("S", "H", "D", "C")
DECK = tuple(f"{rank}{suit}" for suit in SUITS for rank in RANKS)
LIMITS = {"pisti": (2, 4), "papaz_kimde": (3, 8)}
LOBBY_SECONDS = 1200
PREPARE_SECONDS = 300
TURN_SECONDS = 60


def _time(now):
    return time.time() if now is None else float(now)


def _copy(state):
    result = deepcopy(state)
    result["version"] += 1
    return result


def _player(state, user_id):
    player = next((p for p in state["players"] if p["id"] == int(user_id) and p["active"]), None)
    if player is None:
        raise ValueError("ct_not_player")
    return player


def rank(card):
    return card[:-1]


def new(*, game_key, chat_id, thread_id=None, owner_id, owner_name, round_id=None, now=None):
    if game_key not in GAME_KEYS:
        raise ValueError("ct_invalid_game")
    moment = _time(now)
    return {"game_key": game_key, "chat_id": int(chat_id), "thread_id": thread_id,
            "owner_id": int(owner_id), "round_id": round_id or secrets.token_hex(6),
            "version": 0, "phase_seq": 0, "phase": "lobby", "created_at": moment,
            "players": [{"id": int(owner_id), "name": str(owner_name)[:32],
                         "ready": False, "active": True}],
            "deadline": None, "lobby_expires_at": moment + LOBBY_SECONDS,
            "panel_wait_until": None, "panel_dirty": True, "delivery_retry_at": 0,
            "panel_message_id": None, "private_dirty": False, "private_retry_at": None,
            "private_retry": {}, "private_delivery": {}, "private_recipients": [],
            "private_panels": {}, "group_ready": False, "phase_ready": False,
            "processed_actions": [], "dm_invites": {}, "score_pending": False,
            "scores": {str(owner_id): 0}, "winner_ids": [], "loser_id": None,
            "reason": None, "turn_index": 0, "turn_no": 0,
            "timeouts": {str(owner_id): 0}, "hands": {}, "last_event": None}


def join(state, *, user_id, user_name, now=None):
    if state["phase"] != "lobby" or _time(now) >= state["lobby_expires_at"]:
        raise ValueError("ct_lobby_closed")
    if any(p["id"] == int(user_id) for p in state["players"]):
        raise ValueError("ct_already_joined")
    if len(state["players"]) >= LIMITS[state["game_key"]][1]:
        raise ValueError("ct_full")
    result = _copy(state)
    result["players"].append({"id": int(user_id), "name": str(user_name)[:32],
                              "ready": False, "active": True})
    result["scores"][str(user_id)] = 0
    result["timeouts"][str(user_id)] = 0
    return result


def ready(state, *, user_id):
    if state["phase"] != "lobby":
        raise ValueError("ct_lobby_closed")
    _player(state, user_id)
    if next(p for p in state["players"] if p["id"] == int(user_id))["ready"]:
        raise ValueError("ct_already_ready")
    result = _copy(state)
    next(p for p in result["players"] if p["id"] == int(user_id))["ready"] = True
    return result


def _phase(state, now):
    state.update(phase="playing", phase_seq=state["phase_seq"] + 1,
                 deadline=None, panel_wait_until=_time(now) + PREPARE_SECONDS,
                 panel_dirty=True, delivery_retry_at=0, phase_ready=False,
                 group_ready=False, private_delivery={}, private_retry={},
                 private_recipients=[p["id"] for p in state["players"] if p["active"]],
                 private_dirty=True, private_retry_at=0)


def _finish(state, *, now, winners=(), loser=None, reason="completed"):
    state.update(phase="finished", phase_seq=state["phase_seq"] + 1,
                 deadline=None, panel_wait_until=None, panel_dirty=True,
                 delivery_retry_at=0, private_dirty=False, private_retry_at=None,
                 private_recipients=[], private_delivery={}, group_ready=False,
                 phase_ready=False, winner_ids=list(winners), loser_id=loser,
                 score_pending=reason in {"cards_exhausted", "single_king"},
                 reason=reason)


def _cancel(state, *, reason):
    state.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                 deadline=None, panel_wait_until=None, panel_dirty=True,
                 delivery_retry_at=0, private_dirty=False, private_retry_at=None,
                 private_recipients=[], private_delivery={}, group_ready=False,
                 phase_ready=False, score_pending=False, winner_ids=[], reason=reason)


def _deal_four(state):
    if not state["deck"]:
        return
    for _ in range(4):
        for player in state["players"]:
            state["hands"][str(player["id"])].append(state["deck"].pop(0))
    state["deal_no"] += 1


def _drop_pairs(cards):
    remaining, discarded = [], []
    for current in cards:
        match = next((i for i, prior in enumerate(remaining) if rank(prior) == rank(current)), None)
        if match is None:
            remaining.append(current)
        else:
            discarded.extend((remaining.pop(match), current))
    return remaining, discarded


def start(state, *, user_id, now=None, seed=None):
    moment = _time(now)
    if state["phase"] != "lobby" or moment >= state["lobby_expires_at"]:
        raise ValueError("ct_lobby_closed")
    if int(user_id) != state["owner_id"]:
        raise ValueError("ct_not_owner")
    count = len(state["players"])
    if state["game_key"] == "pisti":
        if count not in (2, 4):
            raise ValueError("ct_player_count")
    elif not 3 <= count <= 8:
        raise ValueError("ct_player_count")
    if not all(p["ready"] for p in state["players"]):
        raise ValueError("ct_dm_not_ready")
    rng = random.Random(seed if seed is not None else secrets.randbits(128))
    result = _copy(state)
    result["lobby_expires_at"] = None
    cards = list(DECK)
    if state["game_key"] == "papaz_kimde":
        cards = [card for card in cards if rank(card) != "K" or card == "KS"]
    rng.shuffle(cards)
    result["hands"] = {str(p["id"]): [] for p in state["players"]}
    if state["game_key"] == "pisti":
        result.update(deck=cards[4:], table=cards[:4], captured={str(side): [] for side in range(2)},
                      pisti_points=[0, 0], team_scores=[0, 0], last_capture_side=None,
                      deal_no=0, turn_index=0)
        _deal_four(result)
    else:
        for index, card in enumerate(cards):
            uid = state["players"][index % count]["id"]
            result["hands"][str(uid)].append(card)
        discarded = []
        for uid, hand in result["hands"].items():
            result["hands"][uid], removed = _drop_pairs(hand)
            discarded.extend(removed)
        result.update(discarded=discarded, turn_index=0, draw_count=0,
                      last_draw=None)
        result["turn_index"] = _next_holding(result, -1)
    _phase(result, moment)
    return result


def _next_holding(state, index):
    count = len(state["players"])
    for step in range(1, count + 1):
        candidate = (index + step) % count
        person = state["players"][candidate]
        if person["active"] and state["hands"][str(person["id"])]:
            return candidate
    raise ValueError("ct_no_holding_player")


def draw_target(state):
    if state["game_key"] != "papaz_kimde" or state["phase"] != "playing":
        return None
    actor_index = state["turn_index"]
    candidate = _next_holding(state, actor_index)
    if candidate == actor_index:
        return None
    return state["players"][candidate]["id"]


def _finish_pisti(state, *, now):
    if state["table"]:
        side = state["last_capture_side"]
        if side is None:
            side = (state["turn_index"] - 1) % len(state["players"]) % 2
        state["captured"][str(side)].extend(state["table"])
        state["table"] = []
    counts = [len(state["captured"][str(side)]) for side in (0, 1)]
    scores = list(state["pisti_points"])
    breakdown = []
    for side in (0, 1):
        card_points = 0
        special_points = 0
        for card in state["captured"][str(side)]:
            card_points += 1 if rank(card) in {"A", "J"} else 0
            special_points += 2 if card == "2C" else 0
            special_points += 3 if card == "10D" else 0
        scores[side] += card_points + special_points
        breakdown.append({"pisti": state["pisti_points"][side],
                          "aces_jacks": card_points, "special_cards": special_points,
                          "card_majority": 0})
    if counts[0] != counts[1]:
        majority_side = 0 if counts[0] > counts[1] else 1
        scores[majority_side] += 3
        breakdown[majority_side]["card_majority"] = 3
    state["team_scores"] = scores
    state["score_breakdown"] = breakdown
    for player_index, player in enumerate(state["players"]):
        state["scores"][str(player["id"])] = scores[player_index % 2]
    # Equal team scores are a draw: both sides keep their earned match score,
    # but neither receives a winner-only economy reward.
    winners = ([p["id"] for index, p in enumerate(state["players"])
                if index % 2 == (0 if scores[0] > scores[1] else 1)]
               if scores[0] != scores[1] else [])
    _finish(state, now=now, winners=winners, reason="cards_exhausted")


def _play_pisti(state, *, index, now, timeout=False):
    actor = state["players"][state["turn_index"]]["id"]
    hand = state["hands"][str(actor)]
    if not 0 <= int(index) < len(hand):
        raise ValueError("ct_invalid_card")
    result = _copy(state)
    card = result["hands"][str(actor)].pop(int(index))
    pile = result["table"]
    capture = bool(pile) and (rank(card) == "J" or rank(card) == rank(pile[-1]))
    pisti = bool(capture and len(pile) == 1 and rank(card) == rank(pile[0]))
    side = state["turn_index"] % 2
    if capture:
        result["captured"][str(side)].extend([*pile, card])
        result["table"] = []
        result["last_capture_side"] = side
        if pisti:
            result["pisti_points"][side] += 20 if rank(card) == "J" else 10
    else:
        pile.append(card)
    result["last_event"] = {"actor": actor, "card": card, "capture": capture,
                            "pisti": pisti, "timeout": timeout}
    result["turn_no"] += 1
    result["turn_index"] = (state["turn_index"] + 1) % len(state["players"])
    if not any(result["hands"].values()) and result["deck"]:
        _deal_four(result)
    if not any(result["hands"].values()) and not result["deck"]:
        _finish_pisti(result, now=now)
    else:
        _phase(result, now)
    return result


def _draw_oldmaid(state, *, now, rng=None, timeout=False, index=None):
    actor = state["players"][state["turn_index"]]["id"]
    target = draw_target(state)
    if target is None:
        raise ValueError("ct_no_target")
    result = _copy(state)
    randomizer = rng if rng is not None else secrets.SystemRandom()
    target_hand = result["hands"][str(target)]
    if index is None:
        chosen_index = randomizer.randrange(len(target_hand))
    elif isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(target_hand):
        chosen_index = index
    else:
        raise ValueError("ct_invalid_card")
    card = target_hand.pop(chosen_index)
    result["hands"][str(actor)].append(card)
    result["hands"][str(actor)], discarded = _drop_pairs(result["hands"][str(actor)])
    result["discarded"].extend(discarded)
    result["last_draw"] = {"actor": actor, "card": card, "target": target}
    result["last_event"] = {"actor": actor, "target": target,
                            "pairs_removed": len(discarded) // 2, "timeout": timeout}
    result["draw_count"] += 1
    result["turn_no"] += 1
    holders = [p["id"] for p in result["players"] if p["active"] and result["hands"][str(p["id"])]]
    if len(holders) == 1:
        loser = holders[0]
        winners = [p["id"] for p in result["players"] if p["active"] and p["id"] != loser]
        for uid in winners:
            result["scores"][str(uid)] = 10
        _finish(result, now=now, winners=winners, loser=loser, reason="single_king")
    elif result["draw_count"] >= 240:
        _cancel(result, reason="turn_cap")
    else:
        result["turn_index"] = _next_holding(result, state["turn_index"])
        _phase(result, now)
    return result


def act(state, *, user_id, action, value=None, user_name="Oyuncu", now=None,
        moderator=False, rng=None):
    moment = _time(now)
    if action == "join":
        return join(state, user_id=user_id, user_name=user_name, now=moment)
    if action == "start":
        return start(state, user_id=user_id, now=moment)
    if action == "stop":
        if state["phase"] in TERMINAL:
            raise ValueError("ct_closed")
        if int(user_id) != state["owner_id"] and not moderator:
            raise ValueError("ct_not_owner")
        result = _copy(state)
        _cancel(result, reason="manual")
        return result
    if action == "leave":
        _player(state, user_id)
        if state["phase"] == "lobby":
            if int(user_id) == state["owner_id"]:
                result = _copy(state)
                _cancel(result, reason="owner_left")
                return result
            result = _copy(state)
            result["players"] = [p for p in result["players"] if p["id"] != int(user_id)]
            result["scores"].pop(str(user_id), None)
            result["timeouts"].pop(str(user_id), None)
            return result
        if state["phase"] != "playing":
            raise ValueError("ct_closed")
        result = _copy(state)
        if state["game_key"] == "pisti":
            losing_side = next(i for i, p in enumerate(state["players"]) if p["id"] == int(user_id)) % 2
            winners = [p["id"] for i, p in enumerate(state["players"]) if i % 2 != losing_side]
        else:
            winners = [p["id"] for p in state["players"] if p["id"] != int(user_id)]
        _finish(result, now=moment, winners=winners, loser=int(user_id), reason="forfeit")
        return result
    if state["phase"] != "playing" or not state["phase_ready"] or moment >= state["deadline"]:
        raise ValueError("ct_wrong_phase")
    actor = state["players"][state["turn_index"]]["id"]
    if int(user_id) != actor:
        raise ValueError("ct_not_turn")
    if action == "play" and state["game_key"] == "pisti":
        result = _play_pisti(state, index=value, now=moment)
    elif action == "draw" and state["game_key"] == "papaz_kimde":
        result = _draw_oldmaid(state, now=moment, rng=rng, index=value)
    else:
        raise ValueError("ct_invalid_action")
    result["timeouts"][str(actor)] = 0
    return result


def advance(state, *, now=None, rng=None):
    moment = _time(now)
    if state["phase"] in TERMINAL:
        return state
    if state["phase"] == "lobby":
        if moment < state["lobby_expires_at"]:
            return state
        result = _copy(state)
        _cancel(result, reason="lobby_timeout")
        return result
    if not state["phase_ready"]:
        if moment < state["panel_wait_until"]:
            return state
        result = _copy(state)
        _cancel(result, reason="delivery_timeout")
        return result
    if moment < state["deadline"]:
        return state
    actor = state["players"][state["turn_index"]]["id"]
    result = _copy(state)
    result["timeouts"][str(actor)] += 1
    if result["timeouts"][str(actor)] >= 3:
        if state["game_key"] == "pisti":
            loser_side = state["turn_index"] % 2
            winners = [p["id"] for i, p in enumerate(state["players"])
                       if i % 2 != loser_side]
        else:
            winners = [p["id"] for p in state["players"] if p["id"] != actor]
        _finish(result, now=moment, winners=winners, loser=actor, reason="three_timeouts")
        return result
    if state["game_key"] == "pisti":
        next_state = _play_pisti(result, index=0, now=moment, timeout=True)
    else:
        next_state = _draw_oldmaid(result, now=moment, rng=rng, timeout=True)
    # The helper already cloned the input. Keep exactly one externally visible
    # version increment so every worker can verify transitions consistently.
    next_state["version"] = state["version"] + 1
    return next_state


def public_state(state):
    hidden = {"hands", "deck", "discarded", "captured", "dm_invites",
              "processed_actions", "private_panels", "private_delivery", "private_retry"}
    view = {key: deepcopy(value) for key, value in state.items() if key not in hidden}
    if state["game_key"] == "pisti" and state["phase"] != "lobby":
        view["top_card"] = state["table"][-1] if state["table"] else None
        view["table_count"] = len(state["table"])
        view.pop("table", None)
    if state["game_key"] == "papaz_kimde" and state["phase"] != "lobby":
        view["hand_counts"] = {uid: len(hand) for uid, hand in state["hands"].items()}
        view.pop("last_draw", None)
    return view
