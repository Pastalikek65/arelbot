from dataclasses import FrozenInstanceError
from pathlib import Path
import hashlib
import json

import pytest

from src.games.werewolf.roles import ROLES, role_strength


def test_all_45_upstream_roles_have_unique_ids_and_turkish_cards():
    assert len(ROLES) == 45
    assert len({r.source_id for r in ROLES.values()}) == 45
    assert ROLES['barkeep'].source_id == 2**45
    assert ROLES['seer'].name == 'Kâhin'
    for key, role in ROLES.items():
        assert role.key == key and role.name and role.description and role.emoji
        assert role.team in {'village', 'wolves', 'cult', 'tanner', 'serial_killer', 'arsonist', 'shifter'}
        assert '{0}' not in role.description
    with pytest.raises(FrozenInstanceError):
        ROLES['seer'].name = 'changed'


def test_vendored_sources_match_the_pinned_upstream_bytes():
    root = Path('src/vendor/greywolf')
    manifest = json.loads((root/'UPSTREAM.json').read_text(encoding='utf-8'))
    assert manifest['commit'] == 'bac172a29f8b394ebbb4822c6f244d769fac21ac'
    for name, item in manifest['files'].items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest() == item['sha256']


def test_role_strength_preserves_upstream_contextual_rules():
    assert role_strength('seer', ['seer','lycan','wolf_man']) == 4
    assert role_strength('mason', ['mason']) == 1
    assert role_strength('mason', ['mason','mason']) == 5
    assert role_strength('cultist_hunter', ['villager']) == 1
    assert role_strength('cultist_hunter', ['cultist']) == 7
    assert role_strength('barkeep', ['villager','villager','barkeep']) == 3
