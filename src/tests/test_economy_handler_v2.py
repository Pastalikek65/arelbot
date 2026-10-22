"""Scoped economy UI, confirmation and canonical Telegram callback checks."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.handlers.economy_handler import EconomyHandler, _callback, _parse
from src.services import economy_ui as ui
from src.services.economy_service import EconomyService


class Economy:
    def __init__(self):
        self.accounts = {}
        self.items = [{"key": "rozet", "name": "Yıldız rozeti", "price": 30,
                       "description": "Profilinde görünen bir rozet.",
                       "effect": "Yalnızca görünüş", "slot": "badge"},
                      {"key": "cerceve", "name": "Mavi çerçeve", "price": 80,
                       "description": "Profil süslemesi.",
                       "effect": "Yalnızca görünüş", "slot": "frame"}]
        self.purchases = []
        self.equips = []
        self.prepared = []

    def account(self, uid, cid):
        return self.accounts.setdefault((uid, cid), {"balance": 50, "streak": 2,
            "daily_available": True, "next_claim_at": None, "owned": [],
            "equipped": {}, "recent": [], "game_earned_today": 10,
            "game_daily_limit": 150, "daily_amount": 25})

    async def snapshot(self, uid, cid):
        return deepcopy(self.account(uid, cid))

    async def catalog(self):
        return deepcopy(self.items)

    async def prepare_purchase(self, uid, cid, key):
        item = next(item for item in self.items if item["key"] == key)
        token = f"quote.{uid}.{cid}.{key}.{item['price']}"
        self.prepared.append((uid, cid, key, token))
        return {"item": deepcopy(item), "token": token,
                "balance": self.account(uid, cid)["balance"]}

    async def purchase(self, uid, cid, key, *, event_id, confirmation_token):
        self.purchases.append((uid, cid, key, event_id, confirmation_token))
        account = self.account(uid, cid)
        item = next(item for item in self.items if item["key"] == key)
        if confirmation_token != f"quote.{uid}.{cid}.{key}.{item['price']}":
            return {"accepted": False, "status": "price_changed"}
        if key in account["owned"]:
            return {"accepted": False, "status": "already_owned"}
        if account["balance"] < item["price"]:
            return {"accepted": False, "status": "insufficient_funds"}
        account["balance"] -= item["price"]
        account["owned"].append(key)
        return {"accepted": True, "status": "purchased", "balance": account["balance"]}

    async def equip(self, uid, cid, key, *, event_id):
        self.equips.append((uid, cid, key, event_id))
        account = self.account(uid, cid)
        if key not in account["owned"]:
            raise ValueError("eco_not_owned")
        item = next(item for item in self.items if item["key"] == key)
        account["equipped"][item["slot"]] = key
        return {"accepted": True}

    async def claim_daily(self, uid, cid):
        account = self.account(uid, cid)
        if not account["daily_available"]:
            return {"accepted": False}
        account["daily_available"] = False
        account["balance"] += 25
        account["streak"] += 1
        account["next_claim_at"] = datetime(2026, 10, 23, tzinfo=timezone.utc)
        return {"accepted": True, "amount": 25, "balance": account["balance"]}


class Bot:
    id = 900

    def __init__(self):
        self.sent = []
        self.edited = []
        self.messages = {}

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        message_id = len(self.sent) + 100
        message = SimpleNamespace(message_id=message_id,
            chat=SimpleNamespace(id=kwargs["chat_id"]),
            is_topic_message=kwargs.get("message_thread_id") is not None,
            message_thread_id=kwargs.get("message_thread_id"),
            from_user=SimpleNamespace(id=self.id))
        self.messages[(kwargs["chat_id"], message_id)] = message
        return message

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)
        return True

    def latest_markup(self):
        return (self.edited[-1] if self.edited else self.sent[-1])["reply_markup"]


def button_data(markup, label):
    return next(button.callback_data for row in markup.inline_keyboard for button in row
                if label in button.text)


def command_update(uid=1, cid=-100, topic=None):
    replies = []
    async def reply_text(text):
        replies.append(text)
    return SimpleNamespace(effective_chat=SimpleNamespace(id=cid, type="supergroup", title="Deneme grubu"),
        effective_user=SimpleNamespace(id=uid, first_name="Bir"),
        effective_message=SimpleNamespace(reply_text=reply_text,
            is_topic_message=topic is not None, message_thread_id=topic)), replies


def callback_update(bot, cid, uid, data, message_id=101):
    answers = []
    async def answer(*args, **kwargs):
        answers.append((args, kwargs))
    query = SimpleNamespace(data=data, id="q1", message=bot.messages[(cid, message_id)], answer=answer)
    update = SimpleNamespace(callback_query=query,
        effective_chat=SimpleNamespace(id=cid, type="supergroup", title="Deneme grubu"),
        effective_user=SimpleNamespace(id=uid, first_name="Bir"))
    return update, answers


@pytest.mark.asyncio
async def test_group_commands_edit_same_owner_panel_and_scope_balance():
    service, bot = Economy(), Bot()
    handler = EconomyHandler(service)
    update, _ = command_update()
    assert await handler.command(update, SimpleNamespace(bot=bot), "wallet")
    assert await handler.command(update, SimpleNamespace(bot=bot), "magaza")
    assert len(bot.sent) == 1 and len(bot.edited) == 1
    assert "Deneme grubu bakiyen" in bot.edited[-1]["text"]
    assert "50 jeton" in bot.edited[-1]["text"]
    assert _parse(button_data(bot.latest_markup(), "Yıldız"))[0] == 1
    other, _ = command_update(uid=1, cid=-200)
    await handler.command(other, SimpleNamespace(bot=bot), "wallet")
    assert len(bot.sent) == 2
    assert service.account(1, -200)["balance"] == 50


@pytest.mark.asyncio
async def test_detail_confirmation_price_change_purchase_and_owned_equip():
    service, bot = Economy(), Bot()
    handler = EconomyHandler(service)
    update, _ = command_update()
    await handler.command(update, SimpleNamespace(bot=bot), "shop")
    detail_data = button_data(bot.latest_markup(), "Yıldız")
    detail_update, detail_answers = callback_update(bot, -100, 1, detail_data)
    assert await handler.callback(detail_update, SimpleNamespace(bot=bot))
    assert detail_answers and not detail_answers[0][1].get("show_alert")
    detail = bot.latest_markup()
    buy_data = button_data(detail, "Satın al")
    assert len(buy_data.encode()) <= 64
    assert "Fiyat: <b>30 jeton</b>" in bot.edited[-1]["text"]
    assert "Yalnızca görünüş" in bot.edited[-1]["text"]
    service.items[0]["price"] = 35
    stale, _ = callback_update(bot, -100, 1, buy_data)
    await handler.callback(stale, SimpleNamespace(bot=bot))
    assert not service.purchases
    assert "Fiyat güncellendi" in bot.edited[-1]["text"]
    fresh_buy = button_data(bot.latest_markup(), "Satın al")
    valid, _ = callback_update(bot, -100, 1, fresh_buy)
    await handler.callback(valid, SimpleNamespace(bot=bot))
    assert len(service.purchases) == 1
    assert service.account(1, -100)["balance"] == 15
    assert "Sahipsin" in ui.render("shop", await service.snapshot(1, -100),
                                    await service.catalog())
    equip_data = button_data(bot.latest_markup(), "kuşan")
    equip, _ = callback_update(bot, -100, 1, equip_data)
    await handler.callback(equip, SimpleNamespace(bot=bot))
    assert service.account(1, -100)["equipped"] == {"badge": "rozet"}
    assert "Kuşanıldı" in bot.edited[-1]["text"]


@pytest.mark.asyncio
async def test_foreign_actor_forgery_and_stale_button_cannot_spend():
    service, bot = Economy(), Bot()
    handler = EconomyHandler(service)
    update, _ = command_update()
    await handler.command(update, SimpleNamespace(bot=bot), "shop")
    shop_data = button_data(bot.latest_markup(), "Yıldız")
    foreign, answers = callback_update(bot, -100, 2, shop_data)
    await handler.callback(foreign, SimpleNamespace(bot=bot))
    assert answers[0][1]["show_alert"] and not service.purchases
    wrong_chat, answers = callback_update(bot, -100, 1, shop_data)
    wrong_chat.effective_chat.id = -200
    await handler.callback(wrong_chat, SimpleNamespace(bot=bot))
    assert answers[0][1]["show_alert"] and not service.purchases
    spoofed, answers = callback_update(bot, -100, 1, shop_data)
    spoofed.callback_query.message.from_user.id = 901
    await handler.callback(spoofed, SimpleNamespace(bot=bot))
    assert answers[0][1]["show_alert"] and not service.purchases
    spoofed.callback_query.message.from_user.id = bot.id
    forged = shop_data.replace(":v:", ":b:")
    owner, answers = callback_update(bot, -100, 1, forged)
    await handler.callback(owner, SimpleNamespace(bot=bot))
    assert answers[0][1]["show_alert"] and not service.purchases
    valid, _ = callback_update(bot, -100, 1, shop_data)
    await handler.callback(valid, SimpleNamespace(bot=bot))
    stale, answers = callback_update(bot, -100, 1, shop_data)
    await handler.callback(stale, SimpleNamespace(bot=bot))
    assert answers[0][1]["show_alert"] and not service.purchases


@pytest.mark.asyncio
async def test_underfunded_item_and_daily_series_render_in_istanbul():
    service, bot = Economy(), Bot()
    handler = EconomyHandler(service)
    update, _ = command_update()
    await handler.command(update, SimpleNamespace(bot=bot), "shop")
    detail, _ = callback_update(bot, -100, 1,
        button_data(bot.latest_markup(), "Mavi çerçeve"))
    await handler.callback(detail, SimpleNamespace(bot=bot))
    assert "30 jeton daha gerekiyor" in bot.edited[-1]["text"]
    assert all("Satın al" not in button.text for row in bot.latest_markup().inline_keyboard
               for button in row)
    await handler.command(update, SimpleNamespace(bot=bot), "daily")
    claim, _ = callback_update(bot, -100, 1, button_data(bot.latest_markup(), "Ödülü al"))
    await handler.callback(claim, SimpleNamespace(bot=bot))
    assert "3 gün" in bot.edited[-1]["text"]
    assert "23.10.2026 03:00" in bot.edited[-1]["text"]
    assert "İstanbul saati" in bot.edited[-1]["text"]


def test_callback_fits_telegram_64_byte_limit_with_52_bit_actor():
    actor = (1 << 52) - 1
    token = "zzzzzz.zzz." + "a" * 16
    data = _callback(actor, "abc123", "b", "abcdefgh", purchase_token=token)
    assert len(data.encode()) <= 64
    assert _parse(data) == (actor, token, "b", "abcdefgh")


@pytest.mark.asyncio
async def test_real_signed_purchase_flow_uses_account_and_current_price():
    service = EconomyService(intent_signing_key="test-only-key")
    await service.credit(1, -100, 100, reason="test_credit", event_id="seed")
    bot, handler = Bot(), EconomyHandler(service)
    update, _ = command_update()
    await handler.command(update, SimpleNamespace(bot=bot), "shop")
    item_data = button_data(bot.latest_markup(), "Meraklı")
    detail, _ = callback_update(bot, -100, 1, item_data)
    await handler.callback(detail, SimpleNamespace(bot=bot))
    confirm = button_data(bot.latest_markup(), "Satın al")
    foreign, answers = callback_update(bot, -100, 2, confirm)
    await handler.callback(foreign, SimpleNamespace(bot=bot))
    assert answers[0][1]["show_alert"]
    assert (await service.snapshot(1, -100))["balance"] == 100
    buy, answers = callback_update(bot, -100, 1, confirm)
    await handler.callback(buy, SimpleNamespace(bot=bot))
    assert not answers[0][1].get("show_alert")
    snapshot = await service.snapshot(1, -100)
    assert snapshot["balance"] == 25 and "merakli" in snapshot["owned"]
    repeat, answers = callback_update(bot, -100, 1, confirm)
    await handler.callback(repeat, SimpleNamespace(bot=bot))
    assert answers[0][1]["show_alert"]
    assert (await service.snapshot(1, -100))["balance"] == 25
    assert (await service.snapshot(1, -200))["balance"] == 0


@pytest.mark.asyncio
async def test_same_account_has_separate_panels_in_two_forum_topics():
    service, bot = Economy(), Bot()
    handler = EconomyHandler(service)
    first, _ = command_update(topic=7)
    second, _ = command_update(topic=8)
    await handler.command(first, SimpleNamespace(bot=bot), "wallet")
    await handler.command(second, SimpleNamespace(bot=bot), "shop")
    assert len(bot.sent) == 2 and not bot.edited
    assert [row["message_thread_id"] for row in bot.sent] == [7, 8]
    await handler.command(first, SimpleNamespace(bot=bot), "inventory")
    assert bot.edited[-1]["message_id"] == 101
    assert handler._panels[(-100, 7, 1)] == 101
    assert handler._panels[(-100, 8, 1)] == 102
    second_data = button_data(bot.sent[1]["reply_markup"], "Yıldız")
    wrong_topic, answers = callback_update(bot, -100, 1, second_data, message_id=102)
    wrong_topic.callback_query.message.message_thread_id = 7
    await handler.callback(wrong_topic, SimpleNamespace(bot=bot))
    assert answers[0][1]["show_alert"]
    wrong_topic.callback_query.message.message_thread_id = 8
    correct, answers = callback_update(bot, -100, 1, second_data, message_id=102)
    await handler.callback(correct, SimpleNamespace(bot=bot))
    assert not answers[0][1].get("show_alert")
    assert bot.edited[-1]["message_id"] == 102


def test_wallet_explains_cap_zero_reward_and_selected_cosmetics():
    service = Economy()
    service.items.append({"key": "unvan", "name": "Meraklı", "price": 75,
                          "description": "Profil unvanı.", "effect": "Yalnızca görünüş",
                          "slot": "title"})
    account = service.account(1, -100)
    account.update(balance=200, game_earned_today=150,
                   owned=["rozet", "cerceve", "unvan"],
                   equipped={"badge": "rozet", "frame": "cerceve", "title": "unvan"},
                   recent=[{"direction": "credit", "amount": 0,
                            "reason": "quiz_reward"}])
    wallet = ui.render("wallet", account, service.items)
    assert "150/150" in wallet
    assert "Bu sınır oyun ödülleri içindir; günlük ödül ve bahis iadeleri ayrıdır" in wallet
    assert "Seçili rozet: Yıldız rozeti" in wallet
    assert "Seçili unvan: Meraklı" in wallet
    assert "Günlük oyun sınırı: +0 (oyun ödülü sınırına ulaşıldı)" in wallet
    assert "oyun gücünü değiştirmez" in wallet


def test_shop_affordability_and_daily_series_are_explicit():
    service = Economy()
    account = service.account(1, -100)
    shop = ui.render("shop", account, service.items)
    assert "Yıldız rozeti · 30 jeton · Alınabilir" in shop
    assert "Mavi çerçeve · 80 jeton · Bakiye yetersiz" in shop
    daily = ui.render("daily", account, service.items)
    assert "25 / 30 / 35 / 40 / 45 / 50" in daily
    assert "6. günden sonra 50" in daily
    assert "Bir gün atlarsan seri 1. günden başlar" in daily
