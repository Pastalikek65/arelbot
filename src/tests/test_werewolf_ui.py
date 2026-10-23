from copy import deepcopy

import pytest

from src.games.werewolf.roles import ROLES
from src.services.vampire_ui import private_text, private_keyboard, render_vampire, group_keyboard
from src.tests.test_werewolf_engine import started


@pytest.mark.parametrize('role', list(ROLES))
def test_every_role_has_a_recipient_safe_turkish_card(role):
    state=started(['wolf',role,'seer','villager','villager'])
    state['phase']='role_delivery'
    text=private_text(state,2)
    if role=='fool':
        assert 'Sahte Kâhin' not in text and 'Kâhin' in text
    else:assert ROLES[role].name in text
    assert 'rng_seed' not in text and 'role_history' not in text
    keyboard=private_keyboard(state,2)
    assert all(len(b.callback_data.encode())<=64 for row in keyboard.inline_keyboard for b in row if b.callback_data)


def test_advanced_group_panel_has_phase_rules_and_no_hidden_roles():
    state=started(['wolf','mayor','seer','villager','villager'])
    text=render_vampire(state,now=6)
    assert 'Kurt Adam' in text and 'Normal' in text
    assert 'Belediye Başkanı' not in text
    before=deepcopy(state)
    group_keyboard(state)
    assert state==before


def test_cupid_second_selection_excludes_the_first_player_and_shows_progress():
    from src.games.werewolf import engine
    state=started(['wolf','cupid','seer','villager','villager'])
    state=engine.night_action(state,user_id=2,target_id=4,now=6)
    text=private_text(state,2)
    assert '1/2' in text
    keyboard=private_keyboard(state,2)
    assert not any(b.text=='P4' for row in keyboard.inline_keyboard for b in row)
