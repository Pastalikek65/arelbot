from __future__ import annotations

import pytest

from src.games.card_engine import (
    Card,
    CardAction,
    CardActionLedger,
    CardGameState,
    Deck,
    DeckExhausted,
    evaluate_hand,
)


def test_seeded_standard_decks_are_identical_and_unique():
    first = Deck.standard(seed=42)
    second = Deck.standard(seed=42)
    assert first.to_dict() == second.to_dict()
    assert len(first.cards) == 52
    assert len(set(card.code for card in first.cards)) == 52
    assert first.remaining == 52


def test_draw_is_immutable_and_serializable():
    deck = Deck.standard(seed=7)
    drawn, after = deck.draw(3)
    assert len(drawn) == 3
    assert deck.remaining == 52
    assert after.remaining == 49
    restored = Deck.from_dict(after.to_dict())
    assert restored == after
    assert restored.draw(1)[0][0] == after.cards[after.position]


def test_deck_rejects_duplicate_or_exhausted_draw():
    with pytest.raises(ValueError, match="duplicate_card"):
        Deck.from_cards([Card("A", "S"), Card("A", "S")])
    deck = Deck.from_cards([Card("A", "S")])
    _, empty = deck.draw()
    with pytest.raises(DeckExhausted):
        empty.draw()


def test_blackjack_score_handles_soft_and_hard_aces():
    soft = evaluate_hand([Card("A", "S"), Card("6", "H")])
    assert (soft.total, soft.soft, soft.bust, soft.blackjack) == (17, True, False, False)
    hard = evaluate_hand([Card("A", "S"), Card("6", "H"), Card("10", "D")])
    assert (hard.total, hard.soft, hard.bust) == (17, False, False)
    blackjack = evaluate_hand([Card("A", "S"), Card("K", "D")])
    assert blackjack.blackjack is True


def test_action_ledger_is_idempotent_and_round_trips():
    ledger = CardActionLedger()
    action = CardAction(action_id="a-1", kind="draw", player_id=7, payload={"count": 1})
    next_ledger, accepted = ledger.accept(action)
    assert accepted is True
    replay, accepted_again = next_ledger.accept(action)
    assert accepted_again is False
    assert replay == next_ledger
    restored = CardActionLedger.from_dict(next_ledger.to_dict())
    assert restored == next_ledger


def test_card_game_draw_is_idempotent_and_restorable():
    state = CardGameState.create(game_id="bj-1", chat_id=-10, players=(7, 8), deck=Deck.standard(seed=3))
    result = state.draw(player_id=7, action_id="draw-1")
    assert result.accepted is True
    assert len(result.cards) == 1
    assert result.state.version == 1
    retry = result.state.draw(player_id=7, action_id="draw-1")
    assert retry.accepted is False
    assert retry.cards == ()
    assert retry.state == result.state
    restored = CardGameState.from_dict(result.state.to_dict())
    assert restored == result.state


def test_card_game_rejects_out_of_turn_and_invalid_scope():
    state = CardGameState.create(game_id="bj-2", chat_id=-10, players=(7, 8), deck=Deck.standard(seed=4))
    with pytest.raises(ValueError, match="not_your_turn"):
        state.draw(player_id=8, action_id="draw-1")
    with pytest.raises(ValueError, match="unknown_game_player"):
        state.draw(player_id=9, action_id="draw-2")


def test_card_and_action_inputs_are_validated():
    with pytest.raises(ValueError, match="invalid_card_rank"):
        Card("1", "S")
    with pytest.raises(ValueError, match="invalid_card_suit"):
        Card("A", "X")
    with pytest.raises(ValueError, match="invalid_card_action_id"):
        CardAction(action_id="", kind="draw", player_id=1)
