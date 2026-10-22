"""Player-facing puzzle panels must agree with the domain state."""

from src.games import grid_puzzles as grid
from src.games import logic_puzzles as logic
from src.games import word_puzzles as word
from src.services import grid_puzzles_ui, logic_puzzles_ui, word_puzzles_ui


def test_wordle_panel_tracks_waiting_attempts_and_result_without_early_answer():
    state = word.new_lobby(game_key="wordle_tr", chat_id=-100, thread_id=None,
                           owner_id=1, owner_name="Oyuncu", now=100)
    lobby = word_puzzles_ui.render_game(state)
    assert "Tek oyunculu" in lobby and "Başlat" in lobby
    state = word.start(state, user_id=1, target="kalem", now=101)
    pending = word_puzzles_ui.render_game(state)
    assert "Panel teslimi bekleniyor" in pending
    assert "kalem" not in pending
    state["deadline"] = 500
    state = word.submit_text(state, user_id=1, text="kalem", now=102)
    finished = word_puzzles_ui.render_game(state)
    assert "kalem" in finished and "Yeni maç" in finished


def test_anagram_panel_uses_public_hints_and_hides_solution_until_reveal():
    state = word.new_lobby(game_key="anagram", chat_id=-100, thread_id=None,
                           owner_id=1, owner_name="Oyuncu", now=100)
    state = word.start(state, user_id=1, word_order=list(range(5)), now=101)
    state["deadline"] = 500
    playing = word_puzzles_ui.render_game(state)
    assert "İpucu: 0/3" in playing and "10 puan" in playing
    assert state["target"] not in playing
    hinted = word.hint(state, user_id=1, now=102)
    assert "İpucu: 1/3" in word_puzzles_ui.render_game(hinted)
    assert "8 puan" in word_puzzles_ui.render_game(hinted)
    hinted = word.hint(hinted, user_id=1, now=103)
    hinted = word.hint(hinted, user_id=1, now=104)
    labels = [button.text for row in word_puzzles_ui.keyboard(hinted).inline_keyboard for button in row]
    assert not any("İpucu" in label for label in labels)
    hinted.update(phase="reveal", round_number=5, deadline=500)
    final_reveal = word_puzzles_ui.render_game(hinted)
    assert "Maç sonucu otomatik açılır" in final_reveal
    assert "Sonraki tur" not in final_reveal


def test_number_lobby_allows_one_player_and_turn_panel_shows_budget():
    state = logic.new(game_key="sayisal_tahmin", chat_id=-100, thread_id=None,
                      owner_id=1, owner_name="Oyuncu", round_id="r", now=100)
    assert "tek kişiyle" in logic_puzzles_ui.render(state)
    state = logic.start(state, user_id=1, secret=500, now=101)
    assert "henüz yanıt yazma" in logic_puzzles_ui.render(state)
    state.update(phase="turn", deadline=500)
    text = logic_puzzles_ui.render(state)
    assert "kalan hakkı: 10" in text and "aralıktaki tahminini" in text
    assert "500 doğru" not in text


def test_logic_max_roster_panels_fit_telegram_with_escaped_names():
    for game in ("sayisal_tahmin", "hizli_buton"):
        state = logic.new(game_key=game, chat_id=-100, thread_id=None,
                          owner_id=1, owner_name="<&>" * 20, round_id="r", now=100)
        for user_id in range(2, 31):
            state = logic.join(state, user_id=user_id, user_name="<&>" * 20, now=101)
        state = logic.start(state, user_id=1, now=102)
        for phase in (state["phase"], "finished"):
            state["phase"] = phase
            panel = logic_puzzles_ui.render(state)
            assert len(panel.encode("utf-16-le")) // 2 <= 4096


def test_reflex_target_is_only_shown_during_go():
    state = logic.new(game_key="hizli_buton", chat_id=-100, thread_id=None,
                      owner_id=1, owner_name="Bir", round_id="r", now=100)
    state = logic.join(state, user_id=2, user_name="İki", now=101)
    state = logic.start(state, user_id=1, symbol="◆", delay=3, now=102)
    assert "Hedef simge" not in logic_puzzles_ui.render(state)
    state.update(phase="go", deadline=500)
    assert "Hedef simge: ◆" in logic_puzzles_ui.render(state)
    assert "Hakkını kullanan: 0/2" in logic_puzzles_ui.render(state)


def test_memory_shows_selection_and_turn_progress_without_hiding_secret_leak():
    cards = list(grid.CARD_SYMBOLS * 2)
    state = grid.new_memory(chat_id=-100, thread_id=None, owner_id=1,
                            owner_name="Bir", round_id="r", now=100, cards=cards)
    assert "Tek kişiyle başlanabilir" in grid_puzzles_ui.render(state, now=101)
    state = grid.memory_start(state, user_id=1, now=102)
    state = grid.activate(state, now=103)
    state = grid.advance(state, now=111)
    state = grid.activate(state, now=112)
    assert "Sıradaki oyuncu" in grid_puzzles_ui.render(state, now=113)
    state = grid.memory_pick(state, user_id=1, index=0, now=113)
    text = grid_puzzles_ui.render(state, now=114)
    assert "İlk kart seçildi" in text and "Bulunan çift: 0/8" in text
    assert "01:A" in text and "02:■" in text


def test_mines_mode_button_names_next_action_and_uses_visible_progress():
    state = grid.new_mines(chat_id=-100, thread_id=None, owner_id=1,
                           owner_name="Bir", round_id="r", now=100, seed=42)
    assert "Panel teslimi bekleniyor" in grid_puzzles_ui.render(state, now=101)
    state = grid.activate(state, now=102)
    assert "Açılan güvenli hücre: 0/30" in grid_puzzles_ui.render(state, now=103)
    labels = [button.text for row in grid_puzzles_ui.keyboard(state).inline_keyboard for button in row]
    assert "Bayrak moduna geç" in labels
    state = grid.mine_mode(state, user_id=1, enabled=True, now=103)
    labels = [button.text for row in grid_puzzles_ui.keyboard(state).inline_keyboard for button in row]
    assert "Aç moduna geç" in labels
    board = grid_puzzles_ui.render(state, now=104).split("<pre>", 1)[1].split("</pre>", 1)[0]
    assert "01:■" in board and "36:■" in board and "✖" not in board
