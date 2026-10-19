"""Recover game timers and pending Telegram delivery from durable state."""

import asyncio
import logging
import time
import weakref

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.error import BadRequest

from src.games.guess_age import round_expired
from src.services.guess_age_ui import guess_age_keyboard, render_guess_age_text, render_guess_age_round_result
from src.services.uno_ui import render_uno_text, uno_keyboard

logger = logging.getLogger(__name__)


class GameRuntime:
    def __init__(self, repository, *, guess_age=None, uno=None):
        self.repository, self.guess_age, self.uno = repository, guess_age, uno
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
                else:
                    prompt = await self._photo(bot, state)
                    await self.guess_age.set_prompt_message(chat_id, int(prompt.message_id), reset_clock=False)
                return
            state = await self.guess_age.start(chat_id=chat_id, owner_id=owner_id, owner_name=owner_name, thread_id=thread_id)
            await self.publish_age_initial(bot, chat_id, state)

    async def _process(self, bot, row):
        chat_id = int(row["chat_id"])
        if float(row.get("state", {}).get("delivery_retry_at", 0)) > time.time():
            return
        async with self._capacity, self._game_lock(row["game_type"], chat_id):
            try:
                if row["game_type"] == "guess_age" and self.guess_age:
                    await self._age(bot, chat_id)
                elif row["game_type"] == "uno" and self.uno:
                    state = await self.uno.current(chat_id)
                    if state and state.get("panel_dirty"):
                        await self.publish_uno(bot, chat_id, state)
                self.processed += 1
            except Exception as error:
                self.delivery_failures += 1
                service = self.guess_age if row["game_type"] == "guess_age" else self.uno
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
        await self.guess_age.set_prompt_message(chat_id, int(prompt.message_id))

    async def publish_age(self, bot, chat_id, state):
        delivery_id = state["delivery_id"]
        if int(state.get("delivery_stage", 0)) == 0:
            await bot.send_message(chat_id=chat_id, text=render_guess_age_round_result(state, state["history"][-1].get("ranked", [])),
                                   parse_mode="HTML", message_thread_id=state.get("thread_id"))
            state = await self.guess_age.mark_delivery(chat_id, delivery_id, 1)
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
        for choice in state.get("choice_prompts", {}).values():
            try:
                await bot.delete_message(chat_id=chat_id, message_id=int(choice))
            except Exception:
                pass
        if previous:
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
        kwargs = {"chat_id": chat_id, "text": render_uno_text(state), "reply_markup": uno_keyboard(state), "parse_mode": "HTML"}
        prompt_id = int(state.get("prompt_message_id", 0) or 0)
        if prompt_id:
            try:
                await bot.edit_message_text(message_id=prompt_id, **kwargs)
            except BadRequest as error:
                if "not modified" not in str(error).casefold():
                    if not any(code in str(error).casefold() for code in ("not found", "can't be edited", "cannot be edited")):
                        raise
                    message = await bot.send_message(**kwargs, message_thread_id=state.get("thread_id"))
                    prompt_id = int(message.message_id)
                    await self.uno.set_prompt_message(chat_id=chat_id, message_id=prompt_id, expected_revision=int(state.get("revision", 0)))
        else:
            message = await bot.send_message(**kwargs, message_thread_id=state.get("thread_id"))
            prompt_id = int(message.message_id)
            await self.uno.set_prompt_message(chat_id=chat_id, message_id=prompt_id, expected_revision=int(state.get("revision", 0)))
        await self.uno.mark_panel_delivered(chat_id, int(state.get("revision", 0)), prompt_id)
