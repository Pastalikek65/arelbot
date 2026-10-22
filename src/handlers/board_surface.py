"""Shared transaction and transport boundary for persistent board surfaces."""
import logging
from functools import wraps

from src.services.board_panel_service import BoardPanelService

logger = logging.getLogger(__name__)


def board_panels(handler):
    service = getattr(handler, 'board_panels', None)
    if service is None:
        service = BoardPanelService(handler.data_port,
            leaderboard=getattr(handler, 'leaderboard_service', None),
            economy=getattr(handler, 'economy_service', None))
        handler.board_panels = service
    return service


class BoardTransport:
    """Keep Telegram reply context while always editing the canonical panel."""
    def __init__(self, context, *, message=None, query=None):
        self.bot = getattr(context, 'bot', None)
        self.message = message
        self.query = query

    async def send_message(self, **kwargs):
        if self.message is not None and callable(getattr(self.message, 'reply_text', None)):
            kwargs.pop('chat_id', None)
            return await self.message.reply_text(**kwargs)
        if self.bot is None:
            raise RuntimeError('board_transport_unavailable')
        return await self.bot.send_message(**kwargs)

    async def edit_message_text(self, **kwargs):
        if (self.query is not None and int(kwargs.get('message_id') or 0)
                == int(getattr(self.query.message, 'message_id', 0) or 0)):
            kwargs.pop('chat_id', None)
            kwargs.pop('message_id', None)
            text = kwargs.pop('text')
            return await self.query.edit_message_text(text, **kwargs)
        if self.bot is None:
            raise RuntimeError('board_transport_unavailable')
        return await self.bot.edit_message_text(**kwargs)


def board_action(game_key):
    """Read authoritative state and serialize each callback across workers."""
    def decorate(function):
        @wraps(function)
        async def call(handler, update, query, context, data):
            service = board_panels(handler)
            chat_id = int(update.effective_chat.id)
            try:
                async with service.guard(chat_id, game_key):
                    state = await service.current(chat_id, game_key)
                    if state is not None:
                        message = query.message
                        thread = getattr(message, 'message_thread_id', None)
                        expected = state.get('thread_id')
                        if thread != expected:
                            await handler._safe_query_answer(query,
                                'Bu oyun başka bir sohbet konusunda açık.', show_alert=True)
                            return
                        actual_chat = getattr(getattr(message, 'chat', None), 'id', chat_id)
                        if int(actual_chat) != chat_id:
                            await handler._safe_query_answer(query, 'Bu panel başka bir sohbete ait.', show_alert=True)
                            return
                        parts = str(data).split(':')
                        if (not state.get('prompt_message_id') and len(parts) >= 2
                                and parts[1] == str(state.get('round_id'))):
                            await service.adopt_callback(chat_id, game_key,
                                int(message.message_id), parts[1], thread_id=thread)
                            state = await service.current(chat_id, game_key)
                        if state.get('board_panel_dirty'):
                            await service.process(BoardTransport(context, query=query), chat_id, game_key)
                            state = await service.current(chat_id, game_key)
                            if state and state.get('board_panel_dirty'):
                                context.chat_data[game_key] = state
                                await handler._safe_query_answer(query,
                                    'Tahta güncelleniyor. Güncel panel geldikten sonra tekrar dene.', show_alert=True)
                                return
                        context.chat_data[game_key] = state
                    else:
                        context.chat_data.pop(game_key, None)
                    await function(handler, update, query, context, data)
                    fresh = await service.current(chat_id, game_key)
                    if fresh is not None:
                        context.chat_data[game_key] = fresh
            except (RuntimeError, ValueError, TimeoutError) as error:
                logger.warning('Board action deferred: %s', type(error).__name__)
                await handler._safe_query_answer(query,
                    'Tahta şu anda güncelleniyor. Biraz sonra tekrar dene.', show_alert=True)
        return call
    return decorate


async def open_board(handler, update, context, game_key):
    message = update.effective_message or update.message
    if message is None or update.effective_chat is None or update.effective_user is None:
        return
    service = board_panels(handler)
    chat_id = int(update.effective_chat.id)
    thread_id = getattr(message, 'message_thread_id', None)
    transport = BoardTransport(context, message=message)
    try:
        async with service.guard(chat_id, game_key):
            existing = await service.current(chat_id, game_key)
            if existing and existing.get('thread_id') != thread_id:
                await message.reply_text('Bu oyun başka bir konuda açık. Güncel panelin bulunduğu konudan devam et.')
                return
            if existing and (existing.get('status') in {'lobby', 'playing'}
                             or existing.get('board_panel_dirty') or existing.get('board_score_pending')
                             or game_key == 'chess' and existing.get('status') == 'finished'):
                await service.process(transport, chat_id, game_key, force=True)
                context.chat_data[game_key] = await service.current(chat_id, game_key)
                return
            from src.games.chess import new_chess_state
            from src.services.gomoku_ui import new_gomoku_state
            from src.services.tictactoe_ui import new_tictactoe_state
            from src.services.word_game_ui import new_connect4_state
            factory = {'connect4': new_connect4_state, 'tictactoe': new_tictactoe_state,
                       'gomoku': new_gomoku_state, 'chess': new_chess_state}[game_key]
            kwargs = {'owner_id': int(update.effective_user.id),
                      'owner_name': str(update.effective_user.first_name or 'Oyuncu'), 'chat_id': chat_id}
            if game_key != 'tictactoe':
                kwargs['thread_id'] = thread_id
            else:
                kwargs['board_size'] = 6
            state = factory(**kwargs)
            state['thread_id'] = thread_id
            if existing:
                for key in ('version', 'board_panel_revision', 'prompt_message_id'):
                    if key in existing:
                        state[key] = existing[key]
            await service.save(chat_id, game_key, state)
            await service.publish(transport, chat_id, game_key, force=True)
            context.chat_data[game_key] = await service.current(chat_id, game_key)
    except (RuntimeError, ValueError, TimeoutError) as error:
        logger.warning('Board lobby deferred: %s', type(error).__name__)
        await message.reply_text('Tahta kaydı şu anda kullanılamıyor. Biraz sonra tekrar dene.')
