"""Recover game timers and pending Telegram delivery from durable state."""

import asyncio
import logging
import time
import weakref

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.error import BadRequest

from src.security.output_sanitizer import sanitize_html
from src.games.guess_age import round_expired
from src.services.guess_age_ui import guess_age_keyboard, render_guess_age_text, render_guess_age_round_result, render_guess_age_photo_answer
from src.services.uno_panel import publish_uno_panel
from src.services.word_chain_service import WordChainService
from src.services.word_chain_ui import render_word_chain, word_chain_keyboard

logger = logging.getLogger(__name__)


class GameRuntime:
    def __init__(self, repository, *, guess_age=None, uno=None, leaderboard=None, economy=None,
                 roulette=None, blackjack=None):
        self.repository, self.guess_age, self.uno = repository, guess_age, uno
        self.roulette, self.blackjack = roulette, blackjack
        self.word_chain = WordChainService(repository, leaderboard=leaderboard, economy=economy)
        self.tabu_handler = None
        self.spy_handler = None
        self.vampire_handler = None
        self.puzzle_suite = None
        self.board_panels = None
        self._lock = asyncio.Lock()
        self._game_locks = weakref.WeakValueDictionary()
        self._capacity = asyncio.Semaphore(4)
        self.processed = 0
        self.delivery_failures = 0

    async def tick(self, context):
        if self._lock.locked():
            return
        async with self._lock:
            rows = await self.repository.list_due_game_states(now=time.time(), limit=100)
            await asyncio.gather(*(self._process(context.bot, row) for row in rows))

    def _game_lock(self, game, chat_id):
        if game == "guess_age" and self.guess_age:
            return self.guess_age.presentation_lock(chat_id)
        return self._game_locks.setdefault((game, chat_id), asyncio.Lock())

    async def open_age(self, bot, *, chat_id, owner_id, owner_name, thread_id=None):
        from src.services.guess_age_service import GuessAgeServiceError
        async with self._game_lock("guess_age", chat_id):
            state = await self.guess_age.current(chat_id)
            if state and (state.get("status") in {"playing", "expired"} or state.get("delivery_pending")):
                if state.get("thread_id") != thread_id:
                    raise GuessAgeServiceError("GUESS_AGE_OTHER_TOPIC")
                if state.get("delivery_pending") or round_expired(state) or state.get("status") == "expired":
                    await self._age(bot, chat_id)
                elif not state.get("prompt_message_id"):
                    await self.publish_age_initial(bot, chat_id, state)
                return await self.guess_age.current(chat_id), False
            state = await self.guess_age.start(chat_id=chat_id, owner_id=owner_id, owner_name=owner_name, thread_id=thread_id)
            await self.publish_age_initial(bot, chat_id, state)
            return await self.guess_age.current(chat_id), True

    async def _process(self, bot, row):
        chat_id = int(row["chat_id"])
        from src.games.puzzle_catalog import puzzle_key_from_storage
        puzzle_key = puzzle_key_from_storage(row["game_type"])
        # Persistent puzzle adapters lease public/private deliveries separately.
        # A public panel lease must not delay their clocks, private hands or scores.
        if not puzzle_key and float(row.get("state", {}).get("delivery_retry_at", 0)) > time.time():
            return
        async with self._capacity, self._game_lock(row["game_type"], chat_id):
            try:
                if row["game_type"] in {"connect4", "tictactoe", "gomoku", "chess"} and self.board_panels:
                    await self.board_panels.process(bot, chat_id, row["game_type"])
                elif puzzle_key and self.puzzle_suite:
                    await self.puzzle_suite.process(bot, row)
                elif row['game_type'] == 'roulette' and self.roulette:
                    await self.roulette.expire(chat_id=chat_id)
                    await self.roulette.settle_pending(chat_id=chat_id)
                    pending = await self.roulette.claim_result_panel(chat_id=chat_id)
                    if pending:
                        payload = {'chat_id': chat_id, 'text': self.roulette.result_text(pending),
                                   'parse_mode': 'HTML'}
                        message_id = pending.get('panel_message_id')
                        if message_id:
                            try:
                                await bot.edit_message_text(message_id=message_id, reply_markup=None, **payload)
                            except BadRequest as error:
                                detail = str(error).lower()
                                if 'message is not modified' in detail:
                                    pass
                                elif 'message to edit not found' in detail or "message can't be edited" in detail:
                                    message_id = None
                                else:
                                    raise
                        if not message_id:
                            sent = await bot.send_message(**payload)
                            message_id = sent.message_id
                        await self.roulette.acknowledge_result_panel(chat_id=chat_id,
                            game_id=pending['game_id'], message_id=message_id)
                elif str(row['game_type']).startswith('blackjack:') and self.blackjack:
                    user_id = int(str(row['game_type']).split(':', 1)[1])
                    await self.blackjack.resume_pending(chat_id=chat_id, user_id=user_id)
                    pending = await self.blackjack.claim_terminal_panel(chat_id=chat_id, user_id=user_id)
                    if pending:
                        payload = {'chat_id': chat_id, 'text': sanitize_html(str(pending['terminal_text'])),
                                   'parse_mode': 'HTML'}
                        message_id = pending.get('panel_message_id')
                        if message_id:
                            try:
                                await bot.edit_message_text(message_id=message_id, reply_markup=None, **payload)
                            except BadRequest as error:
                                detail = str(error).lower()
                                if 'message is not modified' in detail:
                                    pass
                                elif 'message to edit not found' in detail or "message can't be edited" in detail:
                                    message_id = None
                                else:
                                    raise
                        if not message_id:
                            sent = await bot.send_message(**payload)
                            message_id = sent.message_id
                        await self.blackjack.acknowledge_terminal_panel(chat_id=chat_id, user_id=user_id,
                            event_id=pending['event_id'], message_id=message_id)
                elif row["game_type"] == "guess_age" and self.guess_age:
                    await self._age(bot, chat_id)
                elif row["game_type"] == "uno" and self.uno:
                    state = await self.uno.current(chat_id)
                    if state and state.get("panel_dirty"):
                        await self.publish_uno(bot, chat_id, state)
                elif str(row["game_type"]).startswith("tabu") and self.tabu_handler:
                    await self.tabu_handler.process(bot, row)
                elif str(row["game_type"]).startswith("casus") and self.spy_handler:
                    await self.spy_handler.process(bot, row)
                elif str(row["game_type"]).startswith("vampir_koylu") and self.vampire_handler:
                    await self.vampire_handler.process(bot, row)
                elif str(row["game_type"]).startswith("kelime_zinciri"):
                    state = row["state"]
                    thread_id = state.get("thread_id")
                    await self.word_chain.settle_scores(chat_id=chat_id, thread_id=thread_id)
                    expired = await self.word_chain.expire(
                        chat_id=chat_id, thread_id=thread_id,
                        expected_round_id=str(state["round_id"]), now=time.time(),
                    )
                    current = await self.word_chain.claim_panel(chat_id=chat_id, thread_id=thread_id, now=time.time())
                    if current and current.get("panel_dirty"):
                        await bot.send_message(
                            chat_id=chat_id, text=render_word_chain(current, notice="Süre doldu; sıra geçti." if expired else None),
                            parse_mode="HTML", message_thread_id=thread_id,
                            reply_markup=word_chain_keyboard(current),
                        )
                        await self.word_chain.acknowledge_panel(
                            chat_id=chat_id, thread_id=thread_id, round_id=str(current["round_id"]),
                            expected_version=int(current["version"]),
                        )
                self.processed += 1
            except Exception as error:
                self.delivery_failures += 1
                if row["game_type"] in {"connect4", "tictactoe", "gomoku", "chess"}:
                    defer = getattr(self.repository, "defer_board_recovery", None)
                    if callable(defer):
                        try:
                            await defer(chat_id, row["game_type"], time.time() + 10)
                        except Exception:
                            logger.warning("Board recovery backoff unavailable")
                service = self.guess_age if row["game_type"] == "guess_age" else self.uno if row["game_type"] == "uno" else None
                if service:
                    try:
                        await service.defer_delivery(chat_id)
                    except Exception:
                        pass
                logger.warning("Game runtime operation failed: %s", type(error).__name__)

    async def _age(self, bot, chat_id):
        await self.guess_age.settle_scores(chat_id)
        state = await self.guess_age.current(chat_id)
        if not state:
            return
        if state.get("delivery_pending"):
            await self.publish_age(bot, chat_id, state)

        elif state.get("status") == "playing" and not state.get("prompt_message_id"):
            await self.publish_age_initial(bot, chat_id, state)
        elif state.get("status") == "expired" or round_expired(state):
            state, _ = await self.guess_age.close_round(chat_id=chat_id, expected_round_id=state["round_id"], expected_round=int(state["round"]))
            await self.publish_age(bot, chat_id, state)

    async def resume_age(self, bot, chat_id):
        async with self._game_lock("guess_age", chat_id):
            await self._age(bot, chat_id)

    async def advance_age(self, bot, chat_id, round_id, round_no):
        # The periodic sweep owns timeouts; this entry point also accepts old
        # in-memory jobs during a rolling upgrade without racing their delivery.
        async with self._game_lock("guess_age", chat_id):
            state = await self.guess_age.current(chat_id)
            if not state or state.get("round_id") != round_id:
                return
            if state.get("delivery_pending"):
                await self.publish_age(bot, chat_id, state)
            elif int(state["round"]) == int(round_no):
                state, _ = await self.guess_age.close_round(chat_id=chat_id, expected_round_id=round_id, expected_round=round_no)
                await self.publish_age(bot, chat_id, state)

    async def _photo(self, bot, state):
        path = self.guess_age.asset_path(state)
        content = await asyncio.to_thread(path.read_bytes)
        return await bot.send_photo(chat_id=int(state["chat_id"]), photo=InputFile(content, filename="yas_tahmini" + path.suffix),
                                    caption=render_guess_age_text(state), reply_markup=guess_age_keyboard(state),
                                    parse_mode="HTML", message_thread_id=state.get("thread_id"))

    async def publish_age_initial(self, bot, chat_id, state):
        prompt = await self._photo(bot, state)
        await self.guess_age.set_prompt_message(
            chat_id, int(prompt.message_id), expected_round_id=state["round_id"], expected_round=int(state["round"]),
        )

    async def publish_age(self, bot, chat_id, state):
        delivery_id = state["delivery_id"]
        choice_prompts = dict(state.get("choice_prompts", {}))
        if int(state.get("delivery_stage", 0)) == 0:
            await bot.send_message(chat_id=chat_id, text=render_guess_age_round_result(state, state["history"][-1].get("ranked", [])),
                                   parse_mode="HTML", message_thread_id=state.get("thread_id"))
            state = await self.guess_age.mark_delivery(chat_id, delivery_id, 1)
        # Results must be readable before another tall photo takes the screen.
        # The deadline is durable, so /yas and a restart cannot skip this pause.
        if int(state.get("delivery_stage", 0)) == 1 and time.time() < float(state.get("result_visible_until", 0)):
            await self._close_age_photo(bot, chat_id, state)
            return
        if int(state.get("delivery_stage", 0)) == 1:
            if state.get("status") == "finished":
                await bot.send_message(chat_id=chat_id, text=render_guess_age_text(state, result=self.guess_age.ranking(state)),
                                       parse_mode="HTML", message_thread_id=state.get("thread_id"),
                                       reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Yeni maç", callback_data="game_start_guess_age"),
                                                                           InlineKeyboardButton("🎮 Oyun salonu", callback_data="menu_games")]]))
                await self.guess_age.mark_delivery(chat_id, delivery_id, 2)
            else:
                prompt = await self._photo(bot, state)
                await self.guess_age.mark_delivery(chat_id, delivery_id, 2, int(prompt.message_id))
        previous = int(state.get("previous_prompt_message_id", 0))
        for choice in choice_prompts.values():
            try:
                await bot.delete_message(chat_id=chat_id, message_id=int(choice))
            except Exception:
                pass
        if previous:
            try:
                await bot.edit_message_reply_markup(chat_id=chat_id, message_id=previous, reply_markup=None)
            except Exception:
                pass

    async def _close_age_photo(self, bot, chat_id, state):
        previous = int(state.get("previous_prompt_message_id", 0) or 0)
        if not previous:
            return
        try:
            # This also replaces the original anonymous credit with the full
            # source and reveals the answer on the photo after guessing closes.
            await bot.edit_message_caption(
                chat_id=chat_id, message_id=previous,
                caption=render_guess_age_photo_answer(state), parse_mode="HTML", reply_markup=None,
            )
        except Exception:
            try:
                await bot.edit_message_reply_markup(chat_id=chat_id, message_id=previous, reply_markup=None)
            except Exception:
                pass

    async def publish_uno(self, bot, chat_id, state):
        async with self.uno.presentation_lock(chat_id):
            current = await self.uno.current(chat_id)
            if current:
                await self._publish_uno(bot, chat_id, current)

    async def _publish_uno(self, bot, chat_id, state):
        await publish_uno_panel(bot, self.uno, chat_id, state)
