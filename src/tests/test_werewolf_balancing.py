import random

import pytest

from src.games.werewolf.balancing import assign_roles
from src.games.werewolf.roles import NON_VILLAGE, ROLES, role_strength


@pytest.mark.parametrize('count', [5, 7, 10, 15, 20, 30, 35])
@pytest.mark.parametrize('chaos', [False, True])
def test_upstream_distributions_are_playable_and_bounded(count, chaos):
    for seed in range(20):
        roles, possible = assign_roles(count, rng=random.Random(seed), chaos=chaos)
        assert len(roles) == count
        assert set(roles) <= set(ROLES)
        enemies = [r for r in roles if r in NON_VILLAGE]
        village = [r for r in roles if r not in NON_VILLAGE]
        assert 0 < len(enemies) < len(village)
        assert not ('apprentice_seer' in roles and 'seer' not in roles)
        assert not ('cultist' in roles and 'cultist_hunter' not in roles)
        assert all(r in possible for r in roles)
        if not chaos:
            assert abs(sum(role_strength(r, roles) for r in village) - sum(role_strength(r, roles) for r in enemies)) <= count // 4 + 1


def test_role_disable_constraints_cannot_create_a_no_enemy_game():
    with pytest.raises(ValueError):
        assign_roles(10, disabled=NON_VILLAGE, rng=random.Random(1))


def test_distribution_is_reproducible_and_avoids_seasonal_roles_normally():
    first = assign_roles(20, rng=random.Random(42), halloween=False)
    assert first == assign_roles(20, rng=random.Random(42), halloween=False)
    assert 'spumpkin' not in first[1]
