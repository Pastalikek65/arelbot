
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import random

logger = logging.getLogger(__name__)

class BatakGame:
    """Batak card game with lobby system and simplified gameplay."""
    
    SUITS = {'♠️': 'Maça', '♥️': 'Kupa', '♦️': 'Karo', '♣️': 'Sinek'}
    RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    RANK_VALUES = {r: i for i, r in enumerate(RANKS)}
    
    def __init__(self, chat_engine=None):
        self.chat_engine = chat_engine

    def join_game(self, chat_data: dict, user_id: int, user_name: str) -> dict:
        """Handles a player joining the Batak lobby."""
        players = chat_data.get('batak_players', [])
        player_names = chat_data.get('batak_names', [])

        if user_id in players:
            return {"status": "already_joined", "message": "Zaten masadasın! 🃏"}
            
        if len(players) >= 4:
            return {"status": "full", "message": "⚠️ Masa dolu! (4/4)"}
            
        players.append(user_id)
        player_names.append(user_name)
        
        chat_data['batak_players'] = players
        chat_data['batak_names'] = player_names
        
        return {
            "status": "success",
            "message": "Masaya katıldınız! 🃏",
            "player_count": len(players),
            "player_names": player_names
        }

    def get_lobby_text(self, player_names: list) -> str:
        """Generates the lobby status text."""
        names_text = ""
        emojis = ["🔴", "🔵", "🟢", "🟡"]
        for i, name in enumerate(player_names):
            names_text += f"{emojis[i]} {name}\n"
             
        remaining = 4 - len(player_names)
        status_text = f"🃏 <b>Batak Masası</b>\n\n"
        
        if remaining > 0:
            status_text += f"⏳ <b>{remaining}</b> kişi daha bekleniyor...\n\n"
        else:
            status_text += f"✅ Masa doldu! Oyunu başlatabilirsiniz.\n\n"
            
        status_text += f"👥 <b>Oyuncular:</b>\n{names_text}"
        status_text += f"\n📊 Doluluk: {'🟩' * len(player_names)}{'⬜' * remaining} ({len(player_names)}/4)"
        return status_text

    def get_lobby_keyboard(self, player_count: int) -> InlineKeyboardMarkup:
        """Generates the lobby keyboard."""
        keyboard = [[InlineKeyboardButton("🃏 Masaya Katıl", callback_data="game_batak_join")]]
        if player_count >= 2:  # Allow starting with 2+ players for testing
            keyboard.append([InlineKeyboardButton("🏁 Oyunu Başlat", callback_data="game_batak_start")])
        return InlineKeyboardMarkup(keyboard)

    def start_game(self, chat_data: dict) -> dict:
        """Starts the Batak game - simulated round with scoring."""
        players = chat_data.get('batak_players', [])
        names = chat_data.get('batak_names', [])
        
        if len(players) < 2:
            return {"status": "error", "message": "⚠️ En az 2 kişi gerekiyor!"}
        
        # Determine trump suit
        suits = list(self.SUITS.values())
        trump = random.choice(suits)
        trump_emoji = [k for k, v in self.SUITS.items() if v == trump][0]
        
        # Create deck and deal
        deck = [(r, s) for s in self.SUITS.keys() for r in self.RANKS]
        random.shuffle(deck)
        
        cards_per_player = 52 // len(players)
        hands = {}
        for i, name in enumerate(names):
            hand = deck[i * cards_per_player:(i + 1) * cards_per_player]
            # Sort hand by suit then rank
            hand.sort(key=lambda c: (list(self.SUITS.keys()).index(c[1]), self.RANK_VALUES[c[0]]))
            hands[name] = hand
        
        # Simulate bidding
        bids = {}
        for name in names:
            hand = hands[name]
            # Simple AI: count high cards and trump cards
            high_count = sum(1 for r, s in hand if self.RANK_VALUES[r] >= 10)  # J, Q, K, A
            trump_count = sum(1 for r, s in hand if self.SUITS[s] == trump)
            bid = max(1, high_count + trump_count // 2)
            bid = min(bid, cards_per_player)
            bids[name] = bid
        
        # Find winner of bid
        declarer = max(bids, key=bids.get)
        
        # Simulate tricks
        tricks_won = {name: 0 for name in names}
        for trick_num in range(cards_per_player):
            # Each player plays a card (simplified)
            played = {}
            for name in names:
                if hands[name]:
                    card = hands[name].pop(random.randint(0, len(hands[name]) - 1))
                    played[name] = card
            
            if not played:
                break
                
            # Determine winner of trick
            lead_suit = list(played.values())[0][1]
            best_player = None
            best_value = -1
            
            for name, (rank, suit) in played.items():
                value = self.RANK_VALUES[rank]
                if suit == lead_suit:
                    if value > best_value:
                        best_value = value
                        best_player = name
                elif self.SUITS[suit] == trump:
                    # Trump beats non-trump
                    if best_player is None or list(played.values())[list(played.keys()).index(best_player)][1] != suit:
                        best_value = value + 100  # Trump priority
                        best_player = name
            
            if best_player:
                tricks_won[best_player] = tricks_won.get(best_player, 0) + 1
        
        # Calculate scores
        scores = {}
        for name in names:
            bid = bids[name]
            won = tricks_won[name]
            if won >= bid:
                scores[name] = won * 10 + (won - bid) * 2  # Bonus for extra tricks
            else:
                scores[name] = -bid * 10  # Penalty for failing bid
        
        # Build result message
        msg = f"🃏 <b>Batak Sonuçları</b> 🏆\n\n"
        msg += f"🎯 Koz: {trump_emoji} <b>{trump}</b>\n\n"
        
        # Sort by score descending
        sorted_players = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        medals = ["🥇", "🥈", "🥉", "4️⃣"]
        
        for i, (name, score) in enumerate(sorted_players):
            bid = bids[name]
            won = tricks_won[name]
            status = "✅" if won >= bid else "❌"
            medal = medals[i] if i < len(medals) else "  "
            msg += f"{medal} <b>{name}</b>\n"
            msg += f"   📣 İhale: {bid} | 🏆 Kazanılan: {won} | {status} Puan: <b>{score:+d}</b>\n\n"
        
        winner = sorted_players[0][0]
        msg += f"👑 <b>Masa Galibi: {winner}!</b> 🎉"
        
        return {
            "status": "success",
            "message": msg,
            "scores": scores,
            "winner": winner
        }

    def distribute_cards(self, players: list) -> str:
        """Legacy: Simulates dealing cards for Batak."""
        result = self.start_game({
            'batak_players': players,
            'batak_names': [f"Oyuncu {i+1}" for i in range(len(players))]
        })
        return result.get('message', 'Hata oluştu.')
