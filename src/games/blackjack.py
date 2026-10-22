
import random
from copy import deepcopy
from typing import List, Dict, Optional

class BlackjackGame:
    """Interactive Blackjack with Hit/Stand/Double mechanics."""

    CARD_VALUES = {
        '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10,
        'J': 10, 'Q': 10, 'K': 10, 'A': 11
    }
    SUITS = ['♠️', '♥️', '♣️', '♦️']
    SUIT_EMOJI = {'♠️': '♠', '♥️': '♥', '♣️': '♣', '♦️': '♦'}

    def create_game(self, player_name: str, player_id: int) -> Dict:
        """Create a new interactive blackjack game state."""
        deck = self._create_deck()
        player_hand = [deck.pop(), deck.pop()]
        dealer_hand = [deck.pop(), deck.pop()]

        p_score = self.calculate_score(player_hand)

        game_state = {
            'deck': deck,
            'player_hand': player_hand,
            'dealer_hand': dealer_hand,
            'player_name': player_name,
            'player_id': player_id,
            'status': 'playing',  # playing, stand, bust, blackjack, finished
            'bet': 100,
        }

        # Check for natural blackjack
        if p_score == 21:
            game_state['status'] = 'blackjack'
        elif self.calculate_score(dealer_hand) == 21:
            game_state['status'] = 'finished'

        return game_state

    def hit(self, game_state: Dict) -> Dict:
        """Player draws a card."""
        self._require_active(game_state)
        candidate = deepcopy(game_state)
        candidate['player_hand'].append(self._draw(candidate))

        score = self.calculate_score(candidate['player_hand'])
        if score > 21:
            candidate['status'] = 'bust'
        elif score == 21:
            self._dealer_turn(candidate)
        game_state.update(candidate)
        return game_state

    def stand(self, game_state: Dict) -> Dict:
        """Player stands. Dealer plays."""
        self._require_active(game_state, allow_stand=True)
        candidate = deepcopy(game_state)
        self._dealer_turn(candidate)
        game_state.update(candidate)
        return game_state

    def double_down(self, game_state: Dict) -> Dict:
        """Player doubles bet and gets exactly one more card."""
        self._require_active(game_state)
        if len(game_state['player_hand']) != 2:
            raise ValueError('double_only_initial_hand')
        candidate = deepcopy(game_state)
        candidate['bet'] *= 2
        candidate['player_hand'].append(self._draw(candidate))

        score = self.calculate_score(candidate['player_hand'])
        if score > 21:
            candidate['status'] = 'bust'
        else:
            self._dealer_turn(candidate)
        game_state.update(candidate)
        return game_state

    def can_surrender(self, game_state: Dict) -> bool:
        """Late surrender is available before the player's first decision."""
        return (
            game_state.get('status') == 'playing'
            and len(game_state.get('player_hand', [])) == 2
            and len(game_state.get('dealer_hand', [])) == 2
            and self.calculate_score(game_state['player_hand']) != 21
            and self.calculate_score(game_state['dealer_hand']) != 21
        )

    def surrender(self, game_state: Dict) -> Dict:
        """End the untouched hand without drawing; integer refunds round down."""
        if not self.can_surrender(game_state):
            raise ValueError('surrender_only_initial_hand')
        game_state['status'] = 'surrendered'
        return game_state

    @staticmethod
    def _require_active(game_state: Dict, *, allow_stand: bool = False) -> None:
        allowed = {'playing', 'stand'} if allow_stand else {'playing'}
        if game_state.get('status') not in allowed:
            raise ValueError('game_finished')

    @staticmethod
    def _draw(game_state: Dict) -> tuple:
        if not game_state.get('deck'):
            raise ValueError('deck_exhausted')
        return game_state['deck'].pop()

    def _dealer_turn(self, game_state: Dict) -> None:
        # Stand on every 17, including a soft 17. Only called on a private
        # candidate so exhaustion cannot persist a partially played hand.
        while self.calculate_score(game_state['dealer_hand']) < 17:
            game_state['dealer_hand'].append(self._draw(game_state))
        game_state['status'] = 'finished'

    def get_result(self, game_state: Dict) -> Dict:
        """Calculate final result."""
        p_score = self.calculate_score(game_state['player_hand'])
        d_score = self.calculate_score(game_state['dealer_hand'])

        if game_state['status'] == 'surrendered':
            refund = int(game_state['bet']) // 2
            return {"result": "surrender", "message": f"🏳️ Pes ettin. İade: {refund} jeton.", "winnings": refund - int(game_state['bet'])}

        player_natural = len(game_state['player_hand']) == 2 and p_score == 21
        dealer_natural = len(game_state['dealer_hand']) == 2 and d_score == 21
        if player_natural and dealer_natural:
            return {"result": "push", "message": "🤝 İki doğal 21 — BERABERE", "winnings": 0}
        if dealer_natural:
            return {"result": "lose", "message": "❌ Krupiyerde doğal 21.", "winnings": -game_state['bet']}
        if player_natural:
            return {"result": "blackjack", "message": "🔥 BLACKJACK! 💰", "winnings": int(game_state['bet'] * 1.5)}
        elif game_state['status'] == 'bust':
            return {"result": "bust", "message": "💀 Patladın! (Bust)", "winnings": -game_state['bet']}
        elif d_score > 21:
            return {"result": "win", "message": "🤵 Krupiyer Patladı! KAZANDIN! 🏆", "winnings": game_state['bet']}
        elif p_score > d_score:
            return {"result": "win", "message": "🏆 KAZANDIN!", "winnings": game_state['bet']}
        elif p_score < d_score:
            return {"result": "lose", "message": "❌ KAYBETTİN...", "winnings": -game_state['bet']}
        else:
            return {"result": "push", "message": "🤝 BERABERE (Push)", "winnings": 0}

    def format_game(self, game_state: Dict, reveal_dealer: bool = False) -> str:
        """Format the current game state as a visual string."""
        p_hand = game_state['player_hand']
        d_hand = game_state['dealer_hand']
        p_score = self.calculate_score(p_hand)

        text = f"🃏 <b>Blackjack — {game_state['player_name']}</b>\n"
        text += f"💰 Bahis: <b>{game_state['bet']}</b> jeton\n\n"

        # Player hand
        text += f"👤 <b>Senin Elin:</b> {self.format_hand(p_hand)} → <b>{p_score}</b>\n"

        # Dealer hand
        if reveal_dealer:
            d_score = self.calculate_score(d_hand)
            text += f"🤵 <b>Krupiyer:</b> {self.format_hand(d_hand)} → <b>{d_score}</b>\n"
        else:
            text += f"🤵 <b>Krupiyer:</b> {d_hand[0][0]}{d_hand[0][1]} 🂠\n"

        if not reveal_dealer and self.can_surrender(game_state):
            text += f"\n🏳️ <b>Pes et:</b> Yalnız ilk hamlede; {int(game_state['bet']) // 2} jeton iade. Yarım bahis aşağı yuvarlanır.\n"

        return text

    def _create_deck(self) -> List[tuple]:
        deck = []
        for suit in self.SUITS:
            for rank in self.CARD_VALUES.keys():
                deck.append((rank, suit))
        random.shuffle(deck)
        return deck

    def calculate_score(self, hand: List[tuple]) -> int:
        score = 0
        aces = 0
        for rank, suit in hand:
            score += self.CARD_VALUES[rank]
            if rank == 'A':
                aces += 1
        while score > 21 and aces:
            score -= 10
            aces -= 1
        return score

    def format_hand(self, hand: List[tuple]) -> str:
        return " ".join([f"{r}{s}" for r, s in hand])

    # Legacy method for backward compatibility
    def play_round(self, player_name: str) -> str:
        """Simple auto-play round for quick games."""
        game = self.create_game(player_name, 0)

        if game['status'] == 'blackjack':
            result = self.get_result(game)
            return self.format_game(game, reveal_dealer=True) + f"\n{result['message']}"

        # Simple AI: Stand on 17+
        while game['status'] == 'playing' and self.calculate_score(game['player_hand']) < 17:
            self.hit(game)

        if game['status'] in {'playing', 'stand'}:
            self.stand(game)

        result = self.get_result(game)
        return self.format_game(game, reveal_dealer=True) + f"\n{result['message']}"
