"""Durable Blackjack payout/refund recovery with idempotent economy events."""
from copy import deepcopy

import pytest

from src.services.blackjack_service import BlackjackService
from src.services.economy_service import EconomyService
from src.games.blackjack import BlackjackGame


class Database:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat_id, key):
        return deepcopy(self.rows.get((chat_id, key)))

    async def save_game_state(self, chat_id, key, state):
        self.rows[(chat_id, key)] = deepcopy(state)

    async def delete_game_state(self, chat_id, key):
        self.rows.pop((chat_id, key), None)


class FailSave(Database):
    def __init__(self, predicate):
        super().__init__()
        self.predicate = predicate
        self.failed = False

    async def save_game_state(self, chat_id, key, state):
        if not self.failed and self.predicate(state):
            self.failed = True
            raise RuntimeError("write failed after debit")
        await super().save_game_state(chat_id, key, state)


class Game:
    def create_game(self, name, player_id):
        return {"player_id": player_id, "player_name": name,
                "player_hand": [("10", "♠"), ("7", "♥")],
                "dealer_hand": [("8", "♦"), ("8", "♣")],
                "deck": [("4", "♠")], "status": "playing"}

    def stand(self, state):
        state["status"] = "finished"

    def get_result(self, state):
        return {"result": "win", "message": "Kazandın", "winnings": int(state["bet"])}

    def format_game(self, state, reveal_dealer=False):
        return "Blackjack"


class NaturalBlackjack(BlackjackGame):
    def create_game(self, name, player_id):
        return {"player_id": player_id, "player_name": name,
                "player_hand": [("A", "♠"), ("K", "♥")],
                "dealer_hand": [("9", "♦"), ("7", "♣")],
                "deck": [], "status": "blackjack"}


class DoubleWin(BlackjackGame):
    def create_game(self, name, player_id):
        return {"player_id": player_id, "player_name": name,
                "player_hand": [("5", "♠"), ("6", "♥")],
                "dealer_hand": [("9", "♦"), ("7", "♣")],
                "deck": [("10", "♥"), ("10", "♠")], "status": "playing"}


class Scores:
    def __init__(self, *, fail_once=False):
        self.fail_once = fail_once
        self.events = set()

    async def record_score(self, *, event_id, **_):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary score outage")
        if event_id in self.events:
            return False
        self.events.add(event_id)
        return True


class Coins:
    def __init__(self, *, fail_credit_once=False):
        self.fail_credit_once = fail_credit_once
        self.events = set()
        self.balance = 1000

    async def debit(self, user_id, chat_id, amount, *, reason, event_id):
        if event_id in self.events:
            return False
        self.events.add(event_id)
        self.balance -= amount
        return True

    async def credit(self, user_id, chat_id, amount, *, reason, event_id):
        if self.fail_credit_once:
            self.fail_credit_once = False
            raise RuntimeError("temporary credit outage")
        if event_id in self.events:
            return False
        self.events.add(event_id)
        self.balance += amount
        return True


@pytest.mark.asyncio
async def test_real_economy_max_bet_payout_survives_score_failure_and_restart():
    db, money, scores = Database(), EconomyService(), Scores(fail_once=True)
    assert await money.credit(7, -10, 10_000, reason="opening_balance", event_id="seed")
    first = BlackjackService(db, economy_service=money,
                             leaderboard_service=scores, game_factory=Game)
    await first.start(chat_id=-10, user_id=7, user_name="Yedi",
                      bet=10_000, event_id="hand-1")
    assert await money.balance(7, -10) == 0
    with pytest.raises(RuntimeError, match="score outage"):
        await first.action(chat_id=-10, user_id=7, action="stand", action_id="stand-1")
    row = db.rows[(-10, "blackjack:7")]
    assert row["settlement_pending"] and row["status"] == "finished"
    assert await money.balance(7, -10) == 20_000
    restarted = BlackjackService(db, economy_service=money,
                                 leaderboard_service=scores, game_factory=Game)
    recovered = await restarted.resume_pending(chat_id=-10, user_id=7)
    assert db.rows[(-10, "blackjack:7")]["terminal_panel_pending"]
    assert await restarted.acknowledge_terminal_panel(chat_id=-10, user_id=7,
        event_id=recovered["state"]["event_id"], message_id=91)
    assert (-10, "blackjack:7") not in db.rows
    assert await money.balance(7, -10) == 20_000
    assert scores.events == {"score:hand-1"}


@pytest.mark.asyncio
async def test_refund_failure_keeps_pending_state_until_restart_then_once():
    db, money = Database(), Coins(fail_credit_once=True)
    first = BlackjackService(db, economy_service=money, game_factory=Game)
    await first.start(chat_id=-10, user_id=7, user_name="Yedi", bet=100, event_id="refund-1")
    with pytest.raises(RuntimeError, match="credit outage"):
        await first.cancel(chat_id=-10, user_id=7)
    assert db.rows[(-10, "blackjack:7")]["refund_pending"]
    assert money.balance == 900
    restarted = BlackjackService(db, economy_service=money, game_factory=Game)
    await restarted.resume_pending(chat_id=-10, user_id=7)
    await restarted.resume_pending(chat_id=-10, user_id=7)
    assert (-10, "blackjack:7") not in db.rows
    assert money.balance == 1000
    assert "cancel:refund-1" in money.events


@pytest.mark.asyncio
async def test_new_hand_cannot_overwrite_unpaid_terminal_hand():
    db, money, scores = Database(), Coins(), Scores(fail_once=True)
    first = BlackjackService(db, economy_service=money,
                             leaderboard_service=scores, game_factory=Game)
    await first.start(chat_id=-10, user_id=7, user_name="Yedi", bet=100, event_id="old")
    with pytest.raises(RuntimeError, match="score outage"):
        await first.action(chat_id=-10, user_id=7, action="stand", action_id="stand-old")
    scores.fail_once = True
    with pytest.raises(ValueError, match="settlement_pending"):
        await first.start(chat_id=-10, user_id=7, user_name="Yedi", bet=100, event_id="new")
    assert db.rows[(-10, "blackjack:7")]["event_id"] == "old"
    assert money.balance == 1100
    recovered = await first.resume_pending(chat_id=-10, user_id=7)
    with pytest.raises(ValueError, match="result_pending"):
        await first.start(chat_id=-10, user_id=7, user_name="Yedi", bet=100, event_id="new")
    assert await first.acknowledge_terminal_panel(chat_id=-10, user_id=7,
        event_id=recovered["state"]["event_id"], message_id=91)
    await first.start(chat_id=-10, user_id=7, user_name="Yedi", bet=100, event_id="new")
    assert db.rows[(-10, "blackjack:7")]["event_id"] == "new"
    assert money.balance == 1000


@pytest.mark.asyncio
async def test_real_economy_natural_blackjack_pays_25000_once():
    db, money = Database(), EconomyService()
    assert await money.credit(7, -10, 10_000, reason="opening_balance", event_id="natural-seed")
    service = BlackjackService(db, economy_service=money, game_factory=NaturalBlackjack)
    result = await service.start(chat_id=-10, user_id=7, user_name="Yedi",
                                 bet=10_000, event_id="natural-hand")
    assert result["result"]["result"] == "blackjack"
    assert await money.balance(7, -10) == 25_000
    assert await service.acknowledge_terminal_panel(chat_id=-10, user_id=7,
        event_id=result["state"]["event_id"], message_id=91)
    assert await service.resume_pending(chat_id=-10, user_id=7) is None
    assert await money.balance(7, -10) == 25_000


@pytest.mark.asyncio
async def test_real_economy_double_win_pays_40000_once():
    db, money = Database(), EconomyService()
    assert await money.credit(7, -10, 10_000, reason="opening_balance", event_id="double-seed-1")
    assert await money.credit(7, -10, 10_000, reason="opening_balance", event_id="double-seed-2")
    service = BlackjackService(db, economy_service=money, game_factory=DoubleWin)
    started = await service.start(chat_id=-10, user_id=7, user_name="Yedi",
                                  bet=10_000, event_id="double-hand")
    result = await service.action(chat_id=-10, user_id=7, action="double",
                                  action_id="double-action", round_token=started["state"]["round_token"])
    assert result["result"]["result"] == "win"
    assert result["state"]["bet"] == 20_000
    assert await money.balance(7, -10) == 40_000
    assert await service.acknowledge_terminal_panel(chat_id=-10, user_id=7,
        event_id=result["state"]["event_id"], message_id=91)
    assert await service.resume_pending(chat_id=-10, user_id=7) is None
    assert await money.balance(7, -10) == 40_000


@pytest.mark.asyncio
async def test_real_economy_legacy_20000_refund_receipt_replays_once():
    """A persisted charged double-hand refund must fit the settlement allowance."""
    db, money = Database(), EconomyService()
    assert await money.credit(7, -10, 10_000, reason="opening_balance", event_id="refund-seed-1")
    assert await money.credit(7, -10, 10_000, reason="opening_balance", event_id="refund-seed-2")
    assert await money.debit(7, -10, 10_000, reason="blackjack_bet", event_id="bet:refund-hand")
    assert await money.debit(7, -10, 10_000, reason="blackjack_double", event_id="double:refund-hand")
    db.rows[(-10, "blackjack:7")] = {
        "player_id": 7, "event_id": "refund-hand", "status": "cancelled",
        "base_bet": 10_000, "bet": 20_000, "charged_bet": 20_000,
        "economy_required": True, "refund_pending": True,
    }
    service = BlackjackService(db, economy_service=money, game_factory=Game)
    result = await service.resume_pending(chat_id=-10, user_id=7)
    assert result["refunded"] == 20_000
    assert await money.balance(7, -10) == 20_000
    assert await service.resume_pending(chat_id=-10, user_id=7) is None
    assert await money.balance(7, -10) == 20_000


@pytest.mark.asyncio
async def test_paid_initial_bet_resumes_after_charge_write_failure():
    db = FailSave(lambda state: state.get("bet_status") == "charged")
    money = EconomyService()
    assert await money.credit(7, -10, 10_000, reason="opening_balance", event_id="start-seed")
    first = BlackjackService(db, economy_service=money, game_factory=Game)
    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await first.start(chat_id=-10, user_id=7, user_name="Yedi",
                          bet=10_000, event_id="start-hand")
    assert db.rows[(-10, "blackjack:7")]["status"] == "pending_bet"
    assert await money.balance(7, -10) == 0
    restarted = BlackjackService(db, economy_service=money, game_factory=Game)
    result = await restarted.resume_pending(chat_id=-10, user_id=7)
    assert result["status"] == "playing"
    assert db.rows[(-10, "blackjack:7")]["bet_status"] == "charged"
    assert await money.balance(7, -10) == 0


@pytest.mark.asyncio
async def test_double_draw_and_debit_resume_without_second_charge_after_save_failure():
    db = FailSave(lambda state: state.get("charged_bet") == 20_000
                  and state.get("status") in {"finished", "bust"})
    money = EconomyService()
    for index in (1, 2):
        assert await money.credit(7, -10, 10_000, reason="opening_balance",
                                  event_id=f"double-restart-seed-{index}")
    first = BlackjackService(db, economy_service=money, game_factory=DoubleWin)
    started = await first.start(chat_id=-10, user_id=7, user_name="Yedi",
                                bet=10_000, event_id="double-restart-hand")
    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await first.action(chat_id=-10, user_id=7, action="double", action_id="double-before-crash",
                           round_token=started["state"]["round_token"])
    pending = db.rows[(-10, "blackjack:7")]
    assert pending["status"] == "pending_double"
    assert pending["pending_double_candidate"]["player_hand"][-1] == ("10", "♠")
    assert await money.balance(7, -10) == 0
    restarted = BlackjackService(db, economy_service=money, game_factory=DoubleWin)
    result = await restarted.resume_pending(chat_id=-10, user_id=7)
    assert result["result"]["result"] == "win"
    assert result["state"]["player_hand"][-1] == ("10", "♠")
    assert await money.balance(7, -10) == 40_000
    assert await restarted.acknowledge_terminal_panel(chat_id=-10, user_id=7,
        event_id=result["state"]["event_id"], message_id=91)
    assert await restarted.resume_pending(chat_id=-10, user_id=7) is None
    assert await money.balance(7, -10) == 40_000


@pytest.mark.asyncio
async def test_insufficient_double_restores_old_hand_without_extra_draw():
    db, money = Database(), EconomyService()
    assert await money.credit(7, -10, 10_000, reason="opening_balance", event_id="short-seed")
    service = BlackjackService(db, economy_service=money, game_factory=DoubleWin)
    started = await service.start(chat_id=-10, user_id=7, user_name="Yedi",
                                  bet=10_000, event_id="short-hand")
    original = deepcopy(db.rows[(-10, "blackjack:7")])
    with pytest.raises(ValueError, match="insufficient_balance"):
        await service.action(chat_id=-10, user_id=7, action="double", action_id="short-double",
                             round_token=started["state"]["round_token"])
    assert db.rows[(-10, "blackjack:7")] == original
    assert await money.balance(7, -10) == 0
    assert await service.resume_pending(chat_id=-10, user_id=7) is None
    assert await money.balance(7, -10) == 0


@pytest.mark.asyncio
async def test_free_hand_cannot_mint_coins_after_economy_is_attached():
    db, money, scores = Database(), EconomyService(), Scores()
    free = BlackjackService(db, game_factory=Game)
    await free.start(chat_id=-10, user_id=7, user_name="Yedi", bet=10_000,
                     event_id="unfunded-hand")
    paid = BlackjackService(db, economy_service=money,
                            leaderboard_service=scores, game_factory=Game)
    result = await paid.action(chat_id=-10, user_id=7, action="stand", action_id="unfunded-stand")
    assert result["terminal"]
    assert await money.balance(7, -10) == 0
    assert scores.events == set()
    assert await paid.acknowledge_terminal_panel(chat_id=-10, user_id=7,
        event_id=result["state"]["event_id"], message_id=91)
    assert (-10, "blackjack:7") not in db.rows


@pytest.mark.asyncio
async def test_free_hand_double_after_economy_attach_never_debits_or_pays():
    db, money, scores = Database(), EconomyService(), Scores()
    assert await money.credit(7, -10, 10_000, reason="opening_balance", event_id="free-double-seed")
    free = BlackjackService(db, game_factory=DoubleWin)
    started = await free.start(chat_id=-10, user_id=7, user_name="Yedi",
                               bet=10_000, event_id="free-double-hand")
    assert db.rows[(-10, "blackjack:7")]["economy_required"] is False
    paid = BlackjackService(db, economy_service=money,
                            leaderboard_service=scores, game_factory=DoubleWin)
    result = await paid.action(chat_id=-10, user_id=7, action="double",
                               action_id="free-double-action",
                               round_token=started["state"]["round_token"])
    assert result["terminal"] and result["result"]["result"] == "win"
    assert result["state"]["bet"] == 20_000
    assert await money.balance(7, -10) == 10_000
    assert scores.events == set()
    assert await paid.acknowledge_terminal_panel(chat_id=-10, user_id=7,
        event_id="free-double-hand", message_id=91)
    assert await money.balance(7, -10) == 10_000


@pytest.mark.asyncio
async def test_legacy_hand_without_bet_receipt_remains_pending_for_manual_review():
    db, money = Database(), EconomyService()
    db.rows[(-10, "blackjack:7")] = {
        "player_id": 7, "player_name": "Yedi", "event_id": "legacy-unproven",
        "status": "finished", "bet": 100, "base_bet": 100,
        "player_hand": [("10", "♠"), ("7", "♥")],
        "dealer_hand": [("8", "♦"), ("8", "♣")], "deck": [],
    }
    service = BlackjackService(db, economy_service=money, game_factory=Game)
    with pytest.raises(RuntimeError, match="game_payment_evidence_missing"):
        await service.action(chat_id=-10, user_id=7, action="stand", action_id="legacy-action")
    with pytest.raises(RuntimeError, match="game_payment_evidence_missing"):
        await service.resume_pending(chat_id=-10, user_id=7)
    assert db.rows[(-10, "blackjack:7")]["settlement_pending"]
    assert await money.balance(7, -10) == 0


@pytest.mark.asyncio
async def test_terminal_panel_survives_delivery_failure_and_rejects_stale_ack():
    db, money = Database(), EconomyService()
    assert await money.credit(7, -10, 100, reason="opening_balance", event_id="panel-seed")
    first = BlackjackService(db, economy_service=money, game_factory=Game)
    await first.start(chat_id=-10, user_id=7, user_name="Yedi", bet=100,
                      event_id="panel-hand")
    result = await first.action(chat_id=-10, user_id=7, action="stand",
                                action_id="panel-stand", panel_message_id=77)
    assert result["terminal"] and await money.balance(7, -10) == 200
    restarted = BlackjackService(db, economy_service=money, game_factory=Game)
    claim = await restarted.claim_terminal_panel(chat_id=-10, user_id=7, now=1000)
    assert claim["panel_message_id"] == 77
    assert claim["terminal_text"] == result["text"]
    assert await restarted.claim_terminal_panel(chat_id=-10, user_id=7, now=1001) is None
    assert not await restarted.acknowledge_terminal_panel(chat_id=-10, user_id=7,
        event_id="wrong-hand", message_id=77)
    assert db.rows[(-10, "blackjack:7")]["terminal_panel_pending"]
    assert await restarted.claim_terminal_panel(chat_id=-10, user_id=7, now=1031)
    assert await restarted.acknowledge_terminal_panel(chat_id=-10, user_id=7,
        event_id="panel-hand", message_id=77)
    assert (-10, "blackjack:7") not in db.rows
    assert await money.balance(7, -10) == 200
