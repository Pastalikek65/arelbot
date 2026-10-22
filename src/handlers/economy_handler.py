"""Telegram presentation for scoped wallets, daily rewards and cosmetic items."""
from __future__ import annotations

import logging
import re
import secrets
import time

from telegram.error import BadRequest, TelegramError

from src.services import economy_ui as ui
from src.storage.game_scope import forum_thread_id

logger = logging.getLogger(__name__)
_ITEM = re.compile(r"[a-z][a-z0-9_]{0,23}\Z")
_ALLOWED = {"wallet": {"w", "d", "s", "i"}, "daily": {"c", "w"},
            "shop": {"v", "w", "i"}, "inventory": {"e", "x", "s", "w"},
            "detail": {"b", "s", "i"}}
_VIEWS = {"wallet": "wallet", "economy": "wallet", "cuzdan": "wallet", "cüzdan": "wallet",
          "gunluk": "daily", "günlük": "daily", "daily": "daily",
          "magaza": "shop", "mağaza": "shop", "shop": "shop",
          "envanter": "inventory", "inventory": "inventory"}


def _b36(value):
    value = int(value)
    if value < 0:
        raise ValueError("eco_invalid_callback")
    answer = ""
    while value:
        value, digit = divmod(value, 36)
        answer = "0123456789abcdefghijklmnopqrstuvwxyz"[digit] + answer
    return answer or "0"


def _callback(user_id, nonce, action, item_key="", purchase_token=None):
    if action not in {"w", "d", "s", "i", "v", "e", "b", "c", "x"}:
        raise ValueError("eco_invalid_callback")
    if item_key and not _ITEM.fullmatch(str(item_key)):
        raise ValueError("eco_invalid_item")
    token = purchase_token if action == "b" else nonce
    data = f"eco:{_b36(user_id)}:{action}:{item_key or '-'}:{token}"
    if len(data.encode()) > 64:
        raise ValueError("eco_callback_too_long")
    return data


def _parse(data):
    if not isinstance(data, str) or len(data.encode()) > 64:
        raise ValueError("eco_invalid_callback")
    parts = data.split(":")
    if len(parts) != 5 or parts[0] != "eco":
        raise ValueError("eco_invalid_callback")
    _, uid, action, item_key, token = parts
    if (not token or action not in {"w", "d", "s", "i", "v", "e", "b", "c", "x"}
            or item_key != "-" and not _ITEM.fullmatch(item_key)):
        raise ValueError("eco_invalid_callback")
    return int(uid, 36), token, action, "" if item_key == "-" else item_key


def _item(catalog, key):
    rows = ui._items(catalog)
    return next((row for row in rows if ui._item_key(row) == key), None)


class EconomyHandler:
    def __init__(self, service):
        self.service = service
        # Transport-only panel leases. After restart, old buttons fail closed;
        # durable balances and purchases remain in EconomyService.
        self._panels = {}  # (chat, topic, user) -> message id
        self._sessions = {}  # (chat, message) -> current owner/view/nonce/price

    async def _data(self, user_id, chat_id):
        snapshot = await self.service.snapshot(user_id, chat_id)
        catalog = await self.service.catalog()
        return snapshot, catalog

    def _markup(self, view, snapshot, catalog, *, user_id,
                nonce, item_key=None, purchase_token=None):
        return ui.keyboard(view, snapshot, catalog,
            callback=lambda action, key="": _callback(user_id, nonce,
                action, key, purchase_token=purchase_token),
            item_key=item_key)

    async def _show(self, bot, *, user_id, chat_id, view, message=None,
                    item_key=None, notice=None, chat_name=None, thread_id=None):
        snapshot, catalog = await self._data(user_id, chat_id)
        nonce = secrets.token_urlsafe(4).replace("-", "a").replace("_", "b")
        prepared = None
        listed = _item(catalog, item_key) if item_key else None
        listed_price = int(ui._item_value(listed, "price", 0) or 0) if listed else 0
        if (view == "detail" and item_key and listed
                and item_key not in ui._owned(snapshot)
                and int(snapshot.get("balance", 0) or 0) >= listed_price):
            prepared = await self.service.prepare_purchase(user_id, chat_id, item_key)
            if isinstance(prepared, dict) and prepared.get("item"):
                catalog = [prepared["item"] if ui._item_key(item) == item_key else item
                           for item in ui._items(catalog)]
        text = ui.render(view, snapshot, catalog, chat_name=chat_name,
                         notice=notice, item_key=item_key)
        markup = self._markup(view, snapshot, catalog,
            user_id=user_id, nonce=nonce, item_key=item_key,
            purchase_token=prepared.get("token") if isinstance(prepared, dict) else None)
        panel_key = (chat_id, thread_id, user_id)
        message_id = int(message.message_id) if message is not None else self._panels.get(panel_key)
        if message_id:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                            text=text, parse_mode="HTML", reply_markup=markup)
            except BadRequest as exc:
                reason = str(exc).lower()
                if "message is not modified" not in reason:
                    if "message to edit not found" in reason or "message can't be edited" in reason:
                        message_id = None
                    else:
                        raise
        if not message_id:
            sent = await bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=text,
                parse_mode="HTML", reply_markup=markup)
            message_id = int(sent.message_id)
        prior = self._panels.get(panel_key)
        if prior and prior != message_id:
            self._sessions.pop((chat_id, prior), None)
        self._panels[panel_key] = message_id
        item = _item(catalog, item_key) if item_key else None
        self._sessions[(chat_id, message_id)] = {
            "user_id": user_id, "nonce": nonce, "view": view,
            "item_key": item_key, "price": int(ui._item_value(item, "price", 0) or 0) if item else None,
            "purchase_token": prepared.get("token") if isinstance(prepared, dict) else None,
            "thread_id": thread_id,
            "created_at": time.time()}
        return message_id

    async def command(self, update, context, view="wallet"):
        chat, user = update.effective_chat, update.effective_user
        if not chat or not user:
            return False
        selected = _VIEWS.get(str(view).casefold(), "wallet")
        thread_id = forum_thread_id(update.effective_message) if chat.type in {"group", "supergroup"} else None
        try:
            await self._show(context.bot, user_id=int(user.id), chat_id=int(chat.id),
                view=selected, chat_name=getattr(chat, "title", None), thread_id=thread_id)
        except (TelegramError, RuntimeError, ValueError) as exc:
            logger.warning("Economy panel deferred: %s", type(exc).__name__)
            await update.effective_message.reply_text("Ekonomi paneli açılamadı. Biraz sonra tekrar dene.")
        return True

    async def callback(self, update, context) -> bool:
        query = getattr(update, "callback_query", None)
        if not query or not str(getattr(query, "data", "")).startswith("eco:"):
            return False
        try:
            uid, token, action, item_key = _parse(query.data)
            chat, user, message = update.effective_chat, update.effective_user, query.message
            cid = int(chat.id) if chat else 0
            sender = getattr(message, "from_user", None)
            bot_id = getattr(context.bot, "id", None)
            if (not chat or not user or not message or int(user.id) != uid
                    or int(getattr(message.chat, "id", 0)) != cid
                    or not sender or bot_id is None or int(sender.id) != int(bot_id)):
                raise ValueError("eco_wrong_owner")
            session = self._sessions.get((cid, int(message.message_id)))
            if (not session or session["user_id"] != uid
                    or token != (session["purchase_token"] if action == "b" else session["nonce"])
                    or session["thread_id"] != forum_thread_id(message)
                    or action not in _ALLOWED[session["view"]]
                    or time.time() - session["created_at"] > 900
                    or (action in {"v", "e", "b"} and not item_key)
                    or (action == "b" and item_key != session["item_key"])):
                raise ValueError("eco_stale_panel")
            _snapshot, catalog = await self._data(uid, cid)
            if action in {"v", "e", "b"} and _item(catalog, item_key) is None:
                raise ValueError("eco_item_missing")
            notice = None
            view = {"w": "wallet", "d": "daily", "s": "shop", "i": "inventory",
                    "v": "detail", "e": "inventory", "x": "inventory", "b": "detail", "c": "daily"}[action]
            detail_item = item_key if view == "detail" else None
            if action == "b":
                live_price = int(ui._item_value(_item(catalog, item_key), "price", 0) or 0)
                if live_price != session["price"]:
                    notice = "Fiyat güncellendi. Yeni fiyatı inceleyip yeniden onayla."
                else:
                    outcome = await self.service.purchase(uid, cid, item_key,
                        event_id=f"shop:{cid}:{uid}:{session['nonce']}:{item_key}",
                        confirmation_token=token)
                    if isinstance(outcome, dict) and not outcome.get("accepted", False):
                        notice = {"insufficient_funds": "Bakiyen bu alışveriş için yeterli değil.",
                                  "already_owned": "Bu eşyaya zaten sahipsin.",
                                  "price_changed": "Fiyat güncellendi. Yeni fiyatı inceleyip yeniden onayla."}.get(
                            str(outcome.get("status", "")), "Alışveriş tamamlanamadı. Güncel ürünü incele.")
                    else:
                        view, detail_item = "inventory", None
                        notice = "Alışveriş tamamlandı. Eşyan envanterine eklendi."
            elif action == "e":
                await self.service.equip(uid, cid, item_key,
                    event_id=f"equip:{cid}:{uid}:{session['nonce']}:{item_key}")
                notice = "Eşya kuşanıldı."
            elif action == "x":
                await self.service.unequip(uid, cid, item_key)
                notice = "Profil seçimi kaldırıldı. Eşyan envanterinde duruyor."
            elif action == "c":
                result = await self.service.claim_daily(uid, cid)
                accepted = bool(result.get("accepted", False)) if isinstance(result, dict) else bool(result)
                notice = "Günlük ödül bakiyene eklendi." if accepted else "Bugünkü ödülünü zaten aldın."
            await self._show(context.bot, user_id=uid, chat_id=cid, view=view,
                item_key=detail_item, notice=notice, message=message,
                chat_name=getattr(chat, "title", None), thread_id=session["thread_id"])
            await query.answer()
        except ValueError as exc:
            code = str(exc)
            message = {
                "eco_wrong_owner": "Bu panel sana ait değil.",
                "eco_stale_panel": "Bu panel güncel değil. /ekonomi veya /magaza ile yeniden aç.",
                "eco_insufficient_funds": "Bakiyen bu alışveriş için yeterli değil.",
                "eco_already_owned": "Bu eşyaya zaten sahipsin.",
                "eco_not_owned": "Önce eşyayı satın al.",
                "eco_item_missing": "Bu ürün artık mağazada yok.",
                "economy_item_unknown": "Bu ürün artık mağazada yok.",
                "economy_item_not_owned": "Önce eşyayı satın al.",
                "economy_confirmation_invalid": "Onay süresi doldu veya fiyat değişti. Ürünü yeniden aç.",
            }.get(code, "İşlem yapılamadı. Paneli yenileyip tekrar dene.")
            await query.answer(message, show_alert=True)
        except Exception as exc:  # noqa: BLE001 - callback must always be answered
            logger.warning("Economy callback deferred: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Biraz sonra tekrar dene.", show_alert=True)
            except TelegramError:
                pass
        return True
