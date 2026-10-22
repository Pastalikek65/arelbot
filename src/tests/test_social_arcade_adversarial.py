"""Cross-instance, delayed-delivery and secret-surface regressions."""
import asyncio
import re
from copy import deepcopy
from html import unescape

import pytest

from src.games import social_arcade as game
from src.services import social_arcade_ui as ui
from src.services.social_arcade_service import SocialArcadeService
from src.tests.test_social_arcade_service import Ledger, Repository, act, current, delivered, setup


@pytest.mark.asyncio
async def test_cross_instance_cas_and_stale_round_phase():
    service, repo, _ = await setup("amiral_batti", 2)
    other = SocialArcadeService(repo)
    state = await delivered(service, "amiral_batti", t=103)
    cell = next(cell for ship in state["ships"]["2"] for cell in ship)
    calls = await asyncio.gather(
        act(service, "amiral_batti", state, 1, "shot", value=cell, t=104),
        act(other, "amiral_batti", state, 1, "shot", value=cell, t=104),
        return_exceptions=True)
    assert sum(isinstance(item, tuple) and item[0] == "applied" for item in calls) == 1
    assert len((await current(service, "amiral_batti"))["shots"]["1"]) == 1
    with pytest.raises(ValueError, match="sa_stale_phase"):
        await act(other, "amiral_batti", state, 1, "shot", value=cell + 1, t=105)


@pytest.mark.asyncio
async def test_private_delivery_failure_has_no_clock_and_cancels_unrewarded():
    service, _, ledger = await setup("iki_dogru_bir_yalan", 3)
    state = await current(service, "iki_dogru_bir_yalan")
    claim = await service.claim_delivery(game_key=state["game_key"], chat_id=-100,
                                         thread_id=8, kind="panel", now=103)
    await service.acknowledge_panel(game_key=state["game_key"], chat_id=-100,
        thread_id=8, round_id=state["round_id"], expected_version=claim["version"],
        message_id=50, now=103)
    state = await current(service, state["game_key"])
    assert state["deadline"] is None and state["panel_wait_until"] == 402
    # Telegram Forbidden: the leased private message never receives ACK.
    leased = await service.claim_delivery(game_key=state["game_key"], chat_id=-100,
        thread_id=8, kind="private", user_id=1, now=104)
    assert leased and (await current(service, state["game_key"]))["deadline"] is None
    await service.advance(game_key=state["game_key"], chat_id=-100, thread_id=8,
                          round_id=state["round_id"], now=402)
    state = await current(service, state["game_key"])
    assert state["phase"] == "cancelled" and not state["score_pending"]
    await service.settle_scores(game_key=state["game_key"], chat_id=-100, thread_id=8)
    assert not ledger.events


@pytest.mark.asyncio
async def test_stale_group_ack_repairs_without_rebinding_canonical_panel():
    service, _, _ = await setup("takim_bilgi_ligi", 4)
    key = "takim_bilgi_ligi"
    first = await service.claim_delivery(game_key=key, chat_id=-100, thread_id=8,
                                         kind="panel", now=103)
    await service.acknowledge_panel(game_key=key, chat_id=-100, thread_id=8,
        round_id=first["round_id"], expected_version=first["version"],
        message_id=40, now=103)
    second = await service.claim_delivery(game_key=key, chat_id=-100, thread_id=8,
                                          kind="panel", now=104)
    # A concurrent state update arrived while editMessageText was in flight.
    state = await current(service, key)
    await act(service, key, state, 1, "wager", value=2, t=104) if state["phase_ready"] else None
    if not (await current(service, key))["phase_ready"]:
        # Use another legitimate claim/save event to invalidate the in-flight ACK.
        await service.claim_delivery(game_key=key, chat_id=-100, thread_id=8,
                                     kind="panel", now=135)
    assert not await service.acknowledge_panel(game_key=key, chat_id=-100,
        thread_id=8, round_id=second["round_id"], expected_version=second["version"],
        message_id=999, now=136)
    state = await current(service, key)
    assert state["panel_message_id"] == 40 and state["panel_dirty"]


@pytest.mark.asyncio
async def test_late_private_action_rejected_even_before_worker_advance():
    key = "iki_dogru_bir_yalan"
    service, _, _ = await setup(key, 3)
    state = await delivered(service, key, t=103)
    assert state["phase"] == "author"
    with pytest.raises(ValueError, match="sa_wrong_phase"):
        await act(service, key, state, 1, "claim", text="Geç gelen ifade",
                  t=state["deadline"] + 0.001, private=True)
    assert (await current(service, key))["claims"] == []


@pytest.mark.asyncio
async def test_private_message_same_second_uses_chat_order_not_fractional_clock():
    key = "iki_dogru_bir_yalan"
    service, _, _ = await setup(key, 3)
    state = await delivered(service, key, t=103.8)
    receipt = state["private_panels"]["1"]
    status, _ = await service.act(game_key=key, chat_id=-100, thread_id=8,
        user_id=1, action_id="fast-author-message", action="claim",
        text="Hızlı ama geçerli ifade", round_id=state["round_id"],
        phase_seq=state["phase_seq"], private_message_id=receipt["message_id"],
        input_message_id=receipt["message_id"] + 1, submitted_at=103, now=103.9)
    assert status == "applied"
    state = await current(service, key)
    with pytest.raises(ValueError, match="sa_stale_input"):
        await service.act(game_key=key, chat_id=-100, thread_id=8,
            user_id=1, action_id="old-author-message", action="claim",
            text="Eski mesajın yeniden gelişi", round_id=state["round_id"],
            phase_seq=state["phase_seq"], private_message_id=receipt["message_id"],
            input_message_id=receipt["message_id"], submitted_at=103, now=104)


@pytest.mark.asyncio
async def test_author_each_new_private_card_can_be_acknowledged_same_phase():
    key = "iki_dogru_bir_yalan"
    service, _, _ = await setup(key, 3)
    state = await delivered(service, key, t=103)
    initial_deadline = state["deadline"]
    for number in range(3):
        await act(service, key, state, 1, "claim", text=f"İfade {number + 1} doğru olabilir",
                  t=104 + number, private=True)
        claim = await service.claim_delivery(game_key=key, chat_id=-100, thread_id=8,
            kind="private", user_id=1, now=104 + number)
        assert claim is not None
        status, _ = await service.acknowledge_private(game_key=key, chat_id=-100,
            thread_id=8, round_id=claim["round_id"], phase_seq=claim["phase_seq"],
            user_id=1, message_id=200 + number, expected_version=claim["version"],
            now=104 + number)
        assert status == "applied"
        state = await current(service, key)
        assert state["private_panels"]["1"]["message_id"] == 200 + number
        assert state["deadline"] == initial_deadline


@pytest.mark.asyncio
async def test_old_inflight_private_receipt_cannot_ack_newer_claim_revision():
    key = "iki_dogru_bir_yalan"
    service, _, _ = await setup(key, 3)
    live = await delivered(service, key, t=103)
    await act(service, key, live, 1, "claim", text="Bugün çay içtim", t=104, private=True)
    old_send = await service.claim_delivery(game_key=key, chat_id=-100,
        thread_id=8, kind="private", user_id=1, now=104)
    await act(service, key, await current(service, key), 1, "claim",
              text="Bisiklet kullanırım", t=105, private=True)
    status, _ = await service.acknowledge_private(game_key=key, chat_id=-100,
        thread_id=8, round_id=old_send["round_id"], phase_seq=old_send["phase_seq"],
        user_id=1, message_id=250, expected_version=old_send["version"], now=105)
    assert status == "ignored"
    state = await current(service, key)
    assert "1" not in state["private_delivery"] and state["private_dirty"]


@pytest.mark.asyncio
async def test_team_high_wager_has_real_wrong_answer_cost():
    key = "takim_bilgi_ligi"
    service, _, _ = await setup(key, 4)
    state = await delivered(service, key, t=103)
    await act(service, key, state, 1, "wager", value=2, t=104)
    state = await current(service, key)
    correct = state["questions"][0][2]
    await act(service, key, state, 1, "answer", value=(correct + 1) % 4, t=105)
    state = await current(service, key)
    assert state["team_scores"][0] == -2
    assert state["scores"]["1"] == -2 and state["scores"]["3"] == -2


def test_question_bank_forty_distinct_prompts_validated_answers():
    expected = (
        "5", "4", "2", "3", "Ş", "Ç", "3", "4", "B", "Ç",
        "4", "3", "6", "8", "360", "180", "4", "5", "6", "5",
        "180", "240", "200", "300", "45", "35", "7", "9", "28", "21",
        "5", "8", "Memeli", "Kuş", "Yaprak", "Kök", "0", "100", "Ay", "Güneş",
    )
    assert len(game.TRIVIA) == 40
    assert len({question for question, _, _ in game.TRIVIA}) == 40
    for (question, options, answer), fact in zip(game.TRIVIA, expected):
        assert len(options) == len(set(options)) == 4
        assert 0 <= answer < 4
        assert options[answer] == fact
    for index in range(0, len(game.TRIVIA), 2):
        first, second = game.TRIVIA[index:index + 2]
        assert first[1][first[2]] != second[1][second[2]]


def test_team_seed_scrambles_options_and_keeps_matched_subject_pairs():
    positions = set()
    question_to_subject = {question: index // 10 for index, (question, _, _)
                           in enumerate(game.TRIVIA)}
    for seed in range(30):
        state = game.new(game_key="takim_bilgi_ligi", chat_id=-100,
            thread_id=None, owner_id=1, owner_name="A", round_id=f"r{seed}", now=100)
        for uid in range(2, 5):
            state = game.join(state, user_id=uid, user_name=f"P{uid}", now=100)
        for uid in range(1, 5):
            state = game.ready(state, user_id=uid)
        state = game.start(state, user_id=1, now=101, seed=seed)
        assert len(state["questions"]) == 10
        for pair in range(5):
            left, right = state["questions"][2 * pair:2 * pair + 2]
            assert question_to_subject[left[0]] == question_to_subject[right[0]]
            for question in (left, right):
                original = next(item for item in game.TRIVIA if item[0] == question[0])
                assert question[1][question[2]] == original[1][original[2]]
                positions.add(question[2])
    assert positions == {0, 1, 2, 3}


@pytest.mark.asyncio
async def test_duplicate_claim_casefold_rejected():
    key = "iki_dogru_bir_yalan"
    service, _, _ = await setup(key, 3)
    state = await delivered(service, key, t=103)
    await act(service, key, state, 1, "claim", text="Ben Ağaç Gördüm", t=104, private=True)
    with pytest.raises(ValueError, match="sa_duplicate_claim"):
        await act(service, key, await current(service, key), 1, "claim",
                  text="ben ağaç gördüm", t=105, private=True)


@pytest.mark.asyncio
async def test_wrong_identity_guess_consumes_chance_but_keeps_identity_secret():
    key = "kimim_ben"
    service, _, _ = await setup(key, 3)
    state = await delivered(service, key, t=103)
    await act(service, key, state, 1, "guess", text="Yanlış kimlik", t=104, private=True)
    state = await current(service, key)
    assert state["phase"] == "question" and state["subject_index"] == 0
    assert state["question_count"] == 1
    assert "identity" not in state["last_event"]


@pytest.mark.asyncio
async def test_active_player_leave_cannot_leave_phantom_actor():
    key = "iki_dogru_bir_yalan"
    service, _, _ = await setup(key, 3)
    state = await delivered(service, key, t=103)
    await act(service, key, state, 2, "leave", t=104)
    state = await current(service, key)
    assert state["phase"] == "cancelled" and not state["score_pending"]
    assert state["reason"] == "player_left"


@pytest.mark.asyncio
async def test_dead_poisoner_is_reassigned_before_next_private_turn():
    key = "zehirli_kadeh"
    service, _, _ = await setup(key, 3)
    state = await current(service, key)
    old_poisoner = state["poisoner_id"]
    state["health"][str(old_poisoner)] = 0
    state.update(phase="result", phase_ready=True, deadline=104)
    next_state = game.advance(state, now=105)
    assert next_state["phase"] == "poison"
    assert next_state["poisoner_id"] != old_poisoner
    assert next_state["poisoner_id"] in next_state["private_recipients"]


@pytest.mark.asyncio
async def test_pending_reward_precedes_new_lobby():
    key = "amiral_batti"
    repo, ledger = Repository(), Ledger()
    service = SocialArcadeService(repo, leaderboard=ledger)
    _, state = await service.open(game_key=key, chat_id=-100, thread_id=8,
                                  user_id=1, user_name="A", now=100)
    terminal = deepcopy(state)
    terminal["version"] += 1
    terminal["scores"]["1"] = 10
    game._finish(terminal, now=110, winners=[1])
    assert await repo.compare_and_save_game_state(-100, service.key(key, 8), terminal,
                                                  expected_version=state["version"])
    _, fresh = await service.open(game_key=key, chat_id=-100, thread_id=8,
                                  user_id=1, user_name="A", now=111)
    assert fresh["round_id"] != terminal["round_id"]
    assert len(ledger.events) == 1


@pytest.mark.asyncio
async def test_long_names_ui_within_telegram_limits_and_no_hidden_role():
    key = "zehirli_kadeh"
    service, _, _ = await setup(key, 10)
    state = await current(service, key)
    for person in state["players"]:
        person["name"] = "🧿" * 32 + "<script>"
    surface = ui.group_text(state)
    plain = unescape(re.sub("<[^>]+>", "", surface))
    assert len(plain.encode("utf-16-le")) // 2 <= 4096
    assert "poisoner_id" not in game.public_view(state)
    for row in ui.group_keyboard(state).inline_keyboard:
        for button in row:
            if button.callback_data:
                assert len(button.callback_data.encode()) <= 64
