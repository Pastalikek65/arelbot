"""Completed/cancelled games must leave the durable timer queue."""
import pytest

from src.games import logic_puzzles as rules


@pytest.mark.parametrize('finish_with_hint', [False, True])
def test_completed_sudoku_disarms_old_deadline(finish_with_hint):
    state = rules.new(game_key='sudoku', chat_id=-100, thread_id=None,
        owner_id=1, owner_name='Bir', round_id='bound', now=1, seed=0)
    state.update(panel_ready=True, deadline=1801, panel_wait_until=None)
    cells = [(r,c) for r in range(4) for c in range(4) if not state['givens'][r][c]]
    for index, (row, col) in enumerate(cells):
        if finish_with_hint and index == len(cells)-1:
            state = rules.hint(state, user_id=1, now=10)
        else:
            state = rules.select(state, user_id=1, row=row, col=col, now=10)
            state = rules.digit(state, user_id=1, value=state['solution'][row][col], expected_cell=(row,col), now=10)
    assert state['phase'] == 'finished'
    assert state['score_pending']
    assert state['deadline'] is None
    assert state['panel_wait_until'] is None


@pytest.mark.parametrize('game', rules.GAME_KEYS)
def test_failed_preparation_cancels_and_clears_all_timer_fields(game):
    state = rules.new(game_key=game, chat_id=-100, thread_id=None,
        owner_id=1, owner_name='Bir', round_id='bound', now=1, seed=0)
    if game == 'hizli_buton':
        state = rules.join(state, user_id=2, user_name='İki', now=2)
    if game != 'sudoku':
        state = rules.start(state, user_id=1, now=3)
    result = rules.advance(state, now=state['panel_wait_until'])
    assert result['phase'] == 'cancelled'
    assert not result['score_pending']
    assert result['deadline'] is None
    assert result['panel_wait_until'] is None
