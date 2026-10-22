"""Route puzzle adapters without assigning an ambiguous answer to a game."""
import asyncio

from src.games.puzzle_catalog import PUZZLES, TEXT_PUZZLE_KEYS, puzzle_key_from_storage
from src.storage.game_scope import forum_thread_id


class PuzzleSuite:
    def __init__(self, adapters, *, repository=None):
        self.adapters = dict(adapters)
        self.repository = repository

    async def command(self, update, context, game_key=None):
        if game_key is None:
            text = str(getattr(update.effective_message, 'text', '') or '')
            command = text.split()[0].split('@')[0].lstrip('/') if text.split() else ''
            game_key = next((p.key for p in PUZZLES if p.command == command), None)
        adapter = self.adapters.get(game_key)
        if adapter:
            await adapter.command(update, context, game_key=game_key)
            return True
        return False

    async def callback(self, update, context):
        data = str(update.callback_query.data or '')
        if data.startswith('game_start_'):
            key = data.removeprefix('game_start_')
            if key not in self.adapters:
                return False
            await update.callback_query.answer()
            return await self.command(update, context, key)
        family = {'wp:': 'word', 'gp:': 'grid', 'lp:': 'logic',
                  'wa:':'word_arcade', 'ra:':'reasoning_arcade', 'sa:':'social_arcade',
                  'ct:':'card_table', 't2:':'tile2048', 'ca:':'clue_arcade'}
        for prefix, name in family.items():
            if data.startswith(prefix):
                entry = next((p for p in PUZZLES if p.family == name and p.key in self.adapters), None)
                if entry:
                    await self.adapters[entry.key].callback(update, context)
                    return True
        return False

    async def process(self, bot, row):
        key = puzzle_key_from_storage(row['game_type'])
        adapter = self.adapters.get(key)
        if adapter:
            await adapter.process(bot, row)

    async def cancel_for(self, update, context):
        # Family adapters cancel their own keys; invoke each family once.
        cancelled = False
        for adapter in {id(a): a for a in self.adapters.values()}.values():
            cancelled = bool(await adapter.cancel_for(update, context)) or cancelled
        return cancelled

    async def on_private_text(self, update, context, text, *, routes):
        # Each family sees only its own safe chat/key routes. Content is never
        # forwarded to the AI when a private flow is ambiguous or inactive.
        if len(routes) > 1 and not getattr(update.effective_message, 'reply_to_message', None):
            await update.effective_message.reply_text('Birden fazla özel oyunun açık. Cevabını ilgili güncel özel oyun mesajına yanıt olarak yaz.')
            return True
        for family in ('word_arcade', 'social_arcade'):
            keys = {p.key for p in PUZZLES if p.family == family}
            relevant = [row for row in routes if puzzle_key_from_storage(row.get('game_type','')) in keys]
            if not relevant:
                continue
            adapter = next((self.adapters[key] for key in keys if key in self.adapters), None)
            consume = getattr(adapter, 'on_private_text', None)
            if callable(consume) and await consume(update, context, text, routes=relevant):
                return True
        return False

    async def on_text(self, update, context, text):
        message = update.effective_message
        if not message or not str(text).strip() or str(text).startswith('/'):
            return False
        chat_id, thread_id = int(update.effective_chat.id), forum_thread_id(message)
        user_id = int(update.effective_user.id)
        keys = [key for key in self.adapters if key in TEXT_PUZZLE_KEYS]
        batch = getattr(self.repository, 'list_active_puzzle_routes', None)
        rows = await batch(chat_id, thread_id, user_id) if callable(batch) else None
        if rows is not None:
            by_key = {puzzle_key_from_storage(row['game_type']): row['state'] for row in rows}
            states = [by_key.get(key) for key in keys]
        else:
            states = await asyncio.gather(*(self.adapters[key].service.current(
                game_key=key, chat_id=chat_id, thread_id=thread_id) for key in keys))
        candidates = []
        reply = getattr(message, 'reply_to_message', None)
        for key, state in zip(keys, states):
            if not state:
                continue
            accepts = getattr(self.adapters[key], 'accepts_text', None)
            if not (accepts(state) if callable(accepts) else state.get('phase') in {'playing', 'guessing', 'turn'}):
                continue
            members = [int(p['id']) for p in state.get('players', []) if p.get('active', True)]
            if user_id not in members and user_id != state.get('owner_id'):
                continue
            if reply and int(getattr(reply, 'message_id', 0)) != int(state.get('panel_message_id') or 0):
                continue
            candidates.append((key, state))
        if not candidates:
            return False
        legacy_active = False
        if not reply and self.repository is not None:
            legacy_keys = ['guess_age', 'word_guess', 'word_game', 'hangman']
            legacy_keys.extend(key if thread_id is None else f'{key}_t{thread_id}' for key in ('kelime_zinciri', 'tabu', 'casus'))
            legacy_states = await asyncio.gather(*(self.repository.get_game_state(chat_id, key) for key in legacy_keys))
            for key, state in zip(legacy_keys, legacy_states):
                if not state:
                    continue
                if key in {'guess_age', 'word_guess', 'word_game', 'hangman'} and state.get('thread_id') != thread_id:
                    continue
                if (key.startswith(('kelime_zinciri', 'tabu', 'casus')) and state.get('players')
                        and not any(int(p['id']) == user_id and p.get('active', True) for p in state['players'])):
                    continue
                phase = state.get('phase', state.get('status', 'playing'))
                if phase in {'playing', 'active', 'question'}:
                    legacy_active = True
                    break
        if len(candidates) > 1 or legacy_active:
            await message.reply_text('Birden fazla kelime veya sayı oyunun açık. Cevabını oynamak istediğin güncel oyun paneline yanıt olarak yaz.')
            return True
        key, _ = candidates[0]
        return bool(await self.adapters[key].on_text(update, context, text))
