"""Intentional slash-menu order independent of legacy registration order."""
from src.games.puzzle_catalog import PUZZLES

CORE = ('start', 'menu', 'help', 'oyun', 'iptal', 'profile', 'leaderboard',
        'ekonomi', 'gunluk', 'magaza', 'envanter', 'basarimlar', 'sezon')
LEGACY_GAMES = ('anlat', 'adamasmaca', 'kelimeoyunu', 'connect4', 'xox', 'gomoku',
                'satranc', 'akinator', 'yas', 'uno', 'zincir', 'tabu', 'casus',
                'vampir', 'quiz', 'batak', 'blackjack', 'sisecevir')
TOOLS = ('hava', 'hava_kalite', 'sehir', 'haber', 'deprem', 'eczane', 'ezan',
         'doviz', 'akaryakit', 'bim', 'ara', 'cevir', 'indir', 'sticker',
         'not', 'notlar', 'gorev', 'gorevler', 'hatirlat', 'bildirim')
EXTRAS = ('rusruleti', 'duello', 'zar', 'yazitura', 'slot')
ADMIN = ('moderasyon', 'hizlikur', 'grupayari', 'grupistatistik', 'sohbetmodu')


def curated_rows(registry, rows, *, administrators=False):
    """Resolve aliases to canonical rows; never expose a command outside its scope.

    Every persistent game's entry is placed before optional tools. Compatibility
    names remain callable, without occupying duplicate picker positions.
    """
    available = dict(rows)
    selected = []
    seen = set()
    requested = (ADMIN if administrators else ()) + CORE + LEGACY_GAMES + tuple(
        entry.command for entry in PUZZLES) + EXTRAS + TOOLS
    for name in requested:
        resolved = registry.resolve(name)
        canonical = resolved.name if resolved else name
        if canonical in available and canonical not in seen:
            selected.append((canonical, available[canonical]))
            seen.add(canonical)
    if len(selected) > 100:
        raise ValueError("command_menu_capacity_exceeded")
    return selected
