"""Public entry points for distinct persistent puzzle mechanics."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PuzzleEntry:
    key: str
    title: str
    command: str
    category: str
    family: str
    min_players: int = 1
    max_players: int = 1


PUZZLES = (
    PuzzleEntry('wordle_tr', 'Türkçe Wordle', 'wordle', 'words', 'word'),
    PuzzleEntry('karsi_kelime', 'Karşı Kelime', 'karsikelime', 'words', 'word', max_players=30),
    PuzzleEntry('anagram', 'Anagram', 'anagram', 'words', 'word', max_players=30),
    PuzzleEntry('hafiza_eslestirme', 'Hafıza Eşleştirme', 'eslestirme', 'boards', 'grid', max_players=4),
    PuzzleEntry('mayin_tarlasi', 'Mayın Tarlası', 'mayin', 'boards', 'grid'),
    PuzzleEntry('sudoku', 'Sudoku', 'sudoku', 'boards', 'logic'),
    PuzzleEntry('sayisal_tahmin', 'Sayı Tahmini', 'sayitahmin', 'guess', 'logic', max_players=30),
    PuzzleEntry('hizli_buton', 'Refleks', 'refleks', 'social', 'logic', min_players=2, max_players=30),
    PuzzleEntry('sifre_tahmin', 'Şifre Tahmin', 'sifre', 'guess', 'logic', max_players=12),
    PuzzleEntry('fark_bulmaca', 'Fark Bulmaca', 'fark', 'boards', 'logic', max_players=12),
    PuzzleEntry('harf_maratonu', 'Harf Maratonu', 'harfmaraton', 'words', 'word_arcade', min_players=2, max_players=20),
    PuzzleEntry('sozcuk_avi', 'Sözcük Avı', 'sozcukavi', 'words', 'word_arcade', max_players=12),
    PuzzleEntry('yasak_harf', 'Yasak Harf', 'yasakharf', 'words', 'word_arcade', min_players=2, max_players=12),
    PuzzleEntry('ortak_hikaye', 'Ortak Hikâye', 'ortakhikaye', 'social', 'word_arcade', min_players=3, max_players=10),
    PuzzleEntry('bomba', 'Bomba', 'bomba', 'social', 'word_arcade', min_players=2, max_players=12),
    PuzzleEntry('kor_siralama', 'Kör Sıralama', 'korsiralama', 'guess', 'reasoning_arcade'),
    PuzzleEntry('kronoloji', 'Kronoloji', 'kronoloji', 'guess', 'reasoning_arcade', max_players=12),
    PuzzleEntry('dogru_yanlis', 'Doğru–Yanlış', 'dogruyanlis', 'guess', 'reasoning_arcade', max_players=30),
    PuzzleEntry('dedektif', 'Dedektif', 'dedektif', 'guess', 'reasoning_arcade', max_players=12),
    PuzzleEntry('amiral_batti', 'Amiral Battı', 'amiral', 'boards', 'social_arcade', min_players=2, max_players=2),
    PuzzleEntry('iki_dogru_bir_yalan', 'İki Doğru Bir Yalan', 'ikidogru', 'social', 'social_arcade', min_players=3, max_players=12),
    PuzzleEntry('kimim_ben', 'Kimim Ben', 'kimimben', 'social', 'social_arcade', min_players=3, max_players=12),
    PuzzleEntry('ciz_bil', 'Çiz Bil', 'cizbil', 'social', 'social_arcade', min_players=2, max_players=12),
    PuzzleEntry('takim_bilgi_ligi', 'Takım Bilgi Ligi', 'takimligi', 'social', 'social_arcade', min_players=4, max_players=20),
    PuzzleEntry('zehirli_kadeh', 'Zehirli Kadeh', 'kadeh', 'social', 'social_arcade', min_players=3, max_players=10),
    PuzzleEntry('pisti', 'Pişti', 'pisti', 'cards', 'card_table', min_players=2, max_players=4),
    PuzzleEntry('papaz_kimde', 'Papaz Kimde', 'papazkimde', 'cards', 'card_table', min_players=3, max_players=8),
    PuzzleEntry('2048', '2048', '2048', 'boards', 'tile2048'),
    PuzzleEntry('bes_ipucu', 'Beş İpucu', 'besipucu', 'guess', 'clue_arcade', max_players=20),
    PuzzleEntry('emoji_bilmece', 'Emoji Bilmece', 'emojibilmece', 'guess', 'clue_arcade', max_players=20),
)
PUZZLE_KEYS = frozenset(entry.key for entry in PUZZLES)
TEXT_PUZZLE_KEYS = frozenset({'wordle_tr', 'karsi_kelime', 'anagram', 'sayisal_tahmin',
    'harf_maratonu','sozcuk_avi','yasak_harf','ortak_hikaye','bomba','dedektif','kimim_ben','takim_bilgi_ligi',
    'bes_ipucu','emoji_bilmece','ciz_bil'})
SECRET_GAME_KEYS = frozenset({'tabu','casus','vampir_koylu','harf_maratonu','yasak_harf',
    'amiral_batti','iki_dogru_bir_yalan','kimim_ben','takim_bilgi_ligi','zehirli_kadeh','pisti','papaz_kimde'})


def puzzle_key_from_storage(value: str) -> str | None:
    key, marker, topic = str(value).rpartition('_t')
    candidate = key if marker and topic.isdecimal() else str(value)
    return candidate if candidate in PUZZLE_KEYS else None
