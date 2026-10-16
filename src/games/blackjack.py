
import random
from typing import List, Dict, Optional

class BlackjackGame:
    """Interactive Blackjack with Hit/Stand/Double mechanics."""
    
    CARD_VALUES = {
        '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10,
        'J': 10, 'Q': 10, 'K': 10, 'A': 11
    }
    SUITS = ['â™ ï¸', 'â™¥ï¸', 'â™£ï¸', 'â™¦ï¸']
    SUIT_EMOJI = {'â™ ï¸': 'â™ ', 'â™¥ï¸': 'â™¥', 'â™£ï¸': 'â™£', 'â™¦ï¸': 'â™¦'}

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
        
        return game_state

    def hit(self, game_state: Dict) -> Dict:
        """Player draws a card."""
        card = game_state['deck'].pop()
        game_state['player_hand'].append(card)
        
        score = self.calculate_score(game_state['player_hand'])
        if score > 21:
            game_state['status'] = 'bust'
        elif score == 21:
            game_state['status'] = 'stand'
        
        return game_state

    def stand(self, game_state: Dict) -> Dict:
        """Player stands. Dealer plays."""
        game_state['status'] = 'stand'
        
        # Dealer hits until 17+
        while self.calculate_score(game_state['dealer_hand']) < 17:
            game_state['dealer_hand'].append(game_state['deck'].pop())
        
        game_state['status'] = 'finished'
        return game_state

    def double_down(self, game_state: Dict) -> Dict:
        """Player doubles bet and gets exactly one more card."""
        game_state['bet'] *= 2
        card = game_state['deck'].pop()
        game_state['player_hand'].append(card)
        
        score = self.calculate_score(game_state['player_hand'])
        if score > 21:
            game_state['status'] = 'bust'
        else:
            # After double down, automatically stand
            return self.stand(game_state)
        
        return game_state

    def get_result(self, game_state: Dict) -> Dict:
        """Calculate final result."""
        p_score = self.calculate_score(game_state['player_hand'])
        d_score = self.calculate_score(game_state['dealer_hand'])
        
        if game_state['status'] == 'blackjack':
            return {"result": "blackjack", "message": "ğŸ”¥ BLACKJACK! ğŸ’°", "winnings": int(game_state['bet'] * 1.5)}
        elif game_state['status'] == 'bust':
            return {"result": "bust", "message": "ğŸ’€ PatladÄ±n! (Bust)", "winnings": -game_state['bet']}
        elif d_score > 21:
            return {"result": "win", "message": "ğŸ¤µ Krupiyer PatladÄ±! KAZANDIN! ğŸ†", "winnings": game_state['bet']}
        elif p_score > d_score:
            return {"result": "win", "message": "ğŸ† KAZANDIN!", "winnings": game_state['bet']}
        elif p_score < d_score:
            return {"result": "lose", "message": "âŒ KAYBETTÄ°N...", "winnings": -game_state['bet']}
        else:
            return {"result": "push", "message": "ğŸ¤ BERABERE (Push)", "winnings": 0}

    def format_game(self, game_state: Dict, reveal_dealer: bool = False) -> str:
        """Format the current game state as a visual string."""
        p_hand = game_state['player_hand']
        d_hand = game_state['dealer_hand']
        p_score = self.calculate_score(p_hand)
        
        text = f"ğŸƒ <b>Blackjack â€” {game_state['player_name']}</b>\n"
        text += f"ğŸ’° Bahis: <b>{game_state['bet']}</b> jeton\n\n"
        
        # Player hand
        text += f"ğŸ‘¤ <b>Senin Elin:</b> {self.format_hand(p_hand)} â†’ <b>{p_score}</b>\n"
        
        # Dealer hand
        if reveal_dealer:
            d_score = self.calculate_score(d_hand)
            text += f"ğŸ¤µ <b>Krupiyer:</b> {self.format_hand(d_hand)} â†’ <b>{d_score}</b>\n"
        else:
            text += f"ğŸ¤µ <b>Krupiyer:</b> {d_hand[0][0]}{d_hand[0][1]} ğŸ‚ \n"
        
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
        while self.calculate_score(game['player_hand']) < 17:
            self.hit(game)
        
        if game['status'] != 'bust':
            self.stand(game)
        
        result = self.get_result(game)
        return self.format_game(game, reveal_dealer=True) + f"\n{result['message']}"
