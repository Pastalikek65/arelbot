"""Complete, paginated player guide from the canonical persistent-game catalog."""
from html import escape

from src.games.catalog import GAME_DEFINITIONS
from src.games.puzzle_catalog import PUZZLES
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

COMMANDS = {
    'word_game': 'kelimeoyunu', 'word_guess': 'anlat', 'hangman': 'adamasmaca',
    'connect4': 'connect4', 'gomoku': 'gomoku', 'chess': 'satranc', 'akinator': 'akinator',
    'guess_age': 'yas', 'uno': 'uno', 'kelime_zinciri': 'zincir', 'tabu': 'tabu',
    'casus': 'casus', 'vampir_koylu': 'vampir', 'tictactoe': 'xox', 'quiz': 'quiz',
    'batak': 'batak', 'blackjack': 'blackjack', 'bottle': 'sisecevir',
    **{entry.key: entry.command for entry in PUZZLES},
}
PAGE_SIZE = 7
CATEGORIES = {'word': 'Kelime', 'board': 'Tahta ve bulmaca', 'quiz': 'Tahmin ve bilgi',
              'party': 'Grup', 'card': 'Kart', 'social': 'Sosyal', 'general': 'Diğer'}


def page(number=0):
    games = tuple(GAME_DEFINITIONS.values())
    total = (len(games) + PAGE_SIZE - 1) // PAGE_SIZE
    index = max(0, min(total - 1, int(number)))
    lines = [f'🎮 <b>Oyun rehberi · {index + 1}/{total}</b>',
             f'{len(games)} oyun. Başlatma komutu ve oyuncu sayısını buradan görebilirsin.', '']
    for game in games[index * PAGE_SIZE:(index + 1) * PAGE_SIZE]:
        count = ('2 veya 4' if game.key == 'pisti' else str(game.min_players)
                 if game.min_players == game.max_players else f'{game.min_players}–{game.max_players}')
        lines.extend([f'<b>{escape(game.label)}</b> · /{COMMANDS[game.key]}',
                      f'{count} oyuncu · {CATEGORIES.get(game.category, "Diğer")}', ''])
    lines.extend(['Grup oyunlarında lobiye katıl. Gizli kart veya rol istenirse botun özel sohbetini aç.',
                  'Hamle ve yanıt kuralları güncel oyun panelinde gösterilir.',
                  'Blackjack bahisle başlar: bakiyeni kontrol et ve /blackjack 100 gibi açık bir tutar kullan.',
                  'Kendi oyununu kapatmak için /iptal; kazançlarını görmek için /ekonomi.'])
    navigation = []
    if index:
        navigation.append(InlineKeyboardButton('Önceki', callback_data=f'help_feat_catalog_{index - 1}'))
    if index + 1 < total:
        navigation.append(InlineKeyboardButton('Sonraki', callback_data=f'help_feat_catalog_{index + 1}'))
    rows = [navigation] if navigation else []
    rows.extend([[InlineKeyboardButton('Oyun salonu', callback_data='menu_games'),
                  InlineKeyboardButton('Ekonomi rehberi', callback_data='help_feat_economy')],
                 [InlineKeyboardButton('Oyun yardımına dön', callback_data='help_cat_games')]])
    return '\n'.join(lines), InlineKeyboardMarkup(rows)


def economy_help():
    text = ('🪙 <b>Jeton ve profil süsleri</b>\n\n'
            '/ekonomi · Bu sohbetteki bakiye, kalan oyun ödülü ve son hareketler.\n'
            '/gunluk · Günlük ödülünü al; ardışık günlerde 25’ten 50 jetona çıkar.\n'
            '/magaza · Ürünün etkisini ve fiyatını gör, ardından satın almayı onayla.\n'
            '/envanter · Unvan ve rozet kuşan veya seçimini kaldır.\n\n'
            'Oyun ödülü sınırı kişi ve sohbet başına günlük 150 jetondur. '
            'Günlük hak ve bahis iadeleri bu sınıra dahil değildir. Gün İstanbul saatine göre yenilenir.\n'
            'Profil süsleri oyun avantajı sağlamaz. Jetonların ve eşyaların bu sohbete özeldir.')
    return text, InlineKeyboardMarkup([
        [InlineKeyboardButton('Cüzdanı aç', callback_data='menu_economy')],
        [InlineKeyboardButton('Oyun rehberi', callback_data='help_feat_catalog_0'),
         InlineKeyboardButton('Yardıma dön', callback_data='help_main')]])
