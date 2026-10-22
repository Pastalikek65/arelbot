"""Independent regressions for player-visible logic puzzle delivery."""

from copy import deepcopy
from html import unescape
import re

import pytest

from src.games import logic_puzzles as rules
from src.services import logic_puzzles_ui as ui
from src.services.logic_puzzles_service import LogicPuzzlesService


class Repository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat_id, key):
        return deepcopy(self.rows.get((chat_id, key)))

    async def create_game_state_if_absent(self, chat_id, key, state):
        if (chat_id, key) in self.rows:
            return False
        self.rows[chat_id, key] = deepcopy(state)
        return True

    async def compare_and_save_game_state(self, chat_id, key, state, *, expected_version):
        prior = self.rows.get((chat_id, key))
        if prior is None or prior["version"] != expected_version:
            return False
        self.rows[chat_id, key] = deepcopy(state)
        return True


async def _delivered(service, game, round_id, now):
    claim = await service.claim_panel(game_key=game, chat_id=-100, now=now)
    assert claim
    assert await service.acknowledge_panel(
        game_key=game, chat_id=-100, round_id=round_id,
        expected_version=claim["version"], message_id=77, now=now)
    return await service.current(game_key=game, chat_id=-100)


@pytest.mark.asyncio
@pytest.mark.parametrize("game", ["sayisal_tahmin", "hizli_buton"])
async def test_action_clock_waits_for_visible_action_panel(game):
    """The pending panel's ACK must not consume the player's action window."""
    service = LogicPuzzlesService(Repository())
    _, created = await service.open(game_key=game, chat_id=-100,
                                    user_id=1, user_name="Bir", now=100)
    if game == "hizli_buton":
        await service.act(game_key=game, chat_id=-100, user_id=2, user_name="İki",
                          action="join", action_id="join", round_id=created["round_id"], now=101)
    await service.act(game_key=game, chat_id=-100, user_id=1, action="start",
                      action_id="start", round_id=created["round_id"],
                      secret=500, delay=1, symbol="◆", now=102)
    if game == "hizli_buton":
        await _delivered(service, game, created["round_id"], 103)
        await service.advance(game_key=game, chat_id=-100,
                              round_id=created["round_id"], now=104)
    pending = await service.current(game_key=game, chat_id=-100)
    assert pending["phase"] in {"turn_pending", "go_pending"}
    assert pending["deadline"] is None
    pending_text = ui.render(pending)
    assert ("Sıra hazırlanıyor" if game == "sayisal_tahmin" else "GO hazırlanıyor") in pending_text
    assert "Tahminini gruba" not in pending_text and "Hedef simge:" not in pending_text
    after_pending_delivery = await _delivered(service, game, created["round_id"], 105)
    # Only the preparation panel was delivered. The actual actionable panel is
    # still dirty and may fail to edit or send for the full action duration.
    assert after_pending_delivery["panel_dirty"]
    assert after_pending_delivery["deadline"] is None


@pytest.mark.parametrize("players", [19, 30])
@pytest.mark.parametrize("game", ["sayisal_tahmin", "hizli_buton"])
def test_player_limit_still_fits_telegram_panel(game, players):
    state = rules.new(game_key=game, chat_id=-100, thread_id=None,
                      owner_id=1, owner_name="😀" * 40, round_id="sample", now=100)
    for user_id in range(2, players + 1):
        state = rules.join(state, user_id=user_id, user_name="😀" * 40, now=101)
    state = rules.start(state, user_id=1, secret=500, delay=1, symbol="◆", now=102)
    plain = unescape(re.sub(r"<[^>]*>", "", ui.render(state)))
    assert len(plain.encode("utf-16-le")) // 2 <= 4096
