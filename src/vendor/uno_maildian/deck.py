#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Telegram bot to play UNO in group chats
# Copyright (c) 2016 Jannes Höke <uno@jhoeke.de>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.


from random import shuffle
import hashlib
import hmac
import logging
import random

from . import card as c
from .card import Card
from .errors import DeckEmptyError


class Deck(object):
    """ This class represents a deck of cards """

    def __init__(self, rng=None):
        self.rng = rng
        self.shuffle_seed = None
        self.shuffle_count = 0
        self.cards = list()
        self.graveyard = list()
        self.logger = logging.getLogger(__name__)

        # Fill deck
        for color in c.COLORS:
            for value in c.VALUES:
                self.cards.append(Card(color, value))
                if not value == c.ZERO:
                    self.cards.append(Card(color, value))

        for special in c.SPECIALS * 4:
            self.cards.append(Card(None, None, special=special))

        self.logger.debug("UNO deck initialized with %d cards", len(self.cards))
        self.shuffle()

    def shuffle(self):
        """Shuffles the deck"""
        self.logger.debug("Shuffling Deck")
        if self.shuffle_seed is not None:
            digest = hmac.new(bytes.fromhex(self.shuffle_seed), str(self.shuffle_count).encode(), hashlib.sha256).digest()
            random.Random(int.from_bytes(digest, "big")).shuffle(self.cards)
            self.shuffle_count += 1
        else:
            (self.rng.shuffle if self.rng is not None else shuffle)(self.cards)

    def draw(self):
        """Draws a card from this deck"""
        try:
            card = self.cards.pop()
            self.logger.debug("UNO deck draw; %d cards remain", len(self.cards))
            return card
        except IndexError:
            if len(self.graveyard):
                while len(self.graveyard):
                    self.cards.append(self.graveyard.pop())
                self.shuffle()
                return self.draw()
            else:
                raise DeckEmptyError()

    def dismiss(self, card):
        """Returns a card to the deck"""
        self.graveyard.append(card)
