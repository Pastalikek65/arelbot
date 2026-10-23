"""Two public personas, backward-compatible settings, explicit group consent."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ai.conversation_style import chat_style_instruction, is_normal_mode
from src.ai.personality import PersonalityPolicy
from src.handlers.callback_handler import CallbackHandler
from src.services.menu_service import MenuService


@pytest.mark.parametrize("old", [None, "serious", "balanced", "needle", "CİDDİ", "İĞNELEYİCİ", "suç ortağı", "unknown"])
def test_legacy_preferences_resolve_to_normal(old):
    policy = PersonalityPolicy()
    assert policy.normalize_mode(old) == "normal"
    assert policy.mode_label(old) == "Normal"
    scoped = policy.for_mode(old)
    prompt = chat_style_instruction(scoped, old, "yine işi son güne bıraktım", 0)
    assert prompt.count("AREL NORMAL SOHBET STİLİ") == 1
    assert "Şakacı, esprili ve kinayeli" in prompt
    assert "yağ çekme, ahlak dersi verme" in prompt
    assert "nokta, virgül" in prompt
    assert "her mesaja espri" in prompt
    assert "Gündelik bir paylaşım yardım talebi değildir" in prompt
    assert "ezber cevap olarak tekrar etme" in prompt


def test_main_persona_menu_has_exactly_two_choices():
    assert set(PersonalityPolicy.MODE_LEVELS) == {"normal", "roast"}
    _, markup = MenuService().persona_menu()
    choices = {b.callback_data: b.text for row in markup.inline_keyboard for b in row
               if str(b.callback_data).startswith("persona_user:")}
    assert choices == {"persona_user:normal": "🤝 Normal", "persona_user:roast": "🔥 Roast"}
    assert not is_normal_mode("roast")


def test_normal_does_not_stack_legacy_advice_or_cadence_guidance():
    def unexpected(*args):
        raise AssertionError("Normal must use its own complete tone card")

    policy = SimpleNamespace(sarcasm_level=2, turn_guidance=unexpected)
    prompt = chat_style_instruction(policy, "normal", "çayım soğudu", 3)
    assert "Gündelik bir paylaşım yardım talebi değildir" in prompt
    assert "Hassas, üzgün veya teknik" in prompt
    assert "hedefli taciz" in prompt


def _group(admin=True):
    db = SimpleNamespace(
        settings={"persona_mode": "needle"},
        get_chat_settings=AsyncMock(), update_chat_setting_key=AsyncMock(),
    )
    db.get_chat_settings.side_effect = lambda _: dict(db.settings)

    async def save(_, key, value):
        db.settings[key] = value

    db.update_chat_setting_key.side_effect = save
    chat = SimpleNamespace(id=-100, type="supergroup")
    query = SimpleNamespace(message=SimpleNamespace(chat=chat), answer=AsyncMock(),
                            edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_chat=chat)
    context = SimpleNamespace(bot=SimpleNamespace(get_chat_member=AsyncMock(
        return_value=SimpleNamespace(status="administrator" if admin else "member"))))
    handler = CallbackHandler(SimpleNamespace(db=db), SimpleNamespace(owner_id=999), None)
    return handler, db, update, query, context


@pytest.mark.asyncio
async def test_group_selection_requires_confirmation_and_can_return_to_normal():
    handler, db, update, query, context = _group()
    await handler._handle_ayar_callback(update, query, context, "ayar_persona_cycle")
    assert db.settings["persona_mode"] == "needle"
    await handler._handle_ayar_callback(update, query, context, "ayar_persona_roast_request")
    assert db.settings["persona_mode"] == "needle"
    markup = query.edit_message_text.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "ayar_persona_roast_confirm"
    await handler._handle_ayar_callback(update, query, context, "ayar_persona_roast_confirm")
    assert db.settings["persona_mode"] == "roast"
    await handler._handle_ayar_callback(update, query, context, "ayar_persona_normal")
    assert db.settings["persona_mode"] == "normal"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["ayar_persona_cycle", "ayar_persona_normal", "ayar_persona_roast_request", "ayar_persona_roast_confirm"])
async def test_group_persona_actions_reject_non_admin(action):
    handler, db, update, query, context = _group(admin=False)
    await handler._handle_ayar_callback(update, query, context, action)
    db.update_chat_setting_key.assert_not_called()
    query.edit_message_text.assert_not_called()


@pytest.mark.asyncio
async def test_failed_normal_save_preserves_previous_choice():
    handler, db, update, query, context = _group()
    db.settings["persona_mode"] = "roast"
    db.update_chat_setting_key.side_effect = RuntimeError("fixture persistence error")
    await handler._handle_ayar_callback(update, query, context, "ayar_persona_normal")
    assert db.settings["persona_mode"] == "roast"
    query.edit_message_reply_markup.assert_not_called()
    assert query.answer.call_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_group_message_does_not_override_persisted_roast_selection():
    from src.handlers.message_handler import MessageHandler

    handler = MessageHandler.__new__(MessageHandler)
    handler.data_port = SimpleNamespace(
        get_chat_settings=AsyncMock(return_value={"persona_mode": "roast"}))
    for message in ("böyle konuşma", "küfür etme", "normal konuş"):
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=-100, type="supergroup"),
            effective_user=SimpleNamespace(id=42),
            effective_message=SimpleNamespace(text=message),
        )
        assert await handler._resolve_persona_mode(update, -100) == "roast"
    handler.data_port.get_chat_settings.assert_awaited()


@pytest.mark.asyncio
async def test_roast_selection_is_scoped_to_the_chat_where_it_was_enabled():
    from src.handlers.message_handler import MessageHandler

    handler = MessageHandler.__new__(MessageHandler)
    handler.data_port = SimpleNamespace(
        get_chat_settings=AsyncMock(side_effect=lambda chat_id: {
            "persona_mode": "roast" if chat_id == -100 else "normal"}),
        get_user=AsyncMock(side_effect=lambda user_id: {
            "persona_mode": "roast" if user_id == 42 else "normal"}),
    )
    def update(chat_id, chat_type, user_id):
        return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
                               effective_user=SimpleNamespace(id=user_id),
                               effective_message=SimpleNamespace(text="böyle konuşma"))

    assert await handler._resolve_persona_mode(update(-100, "supergroup", 42), -100) == "roast"
    assert await handler._resolve_persona_mode(update(-101, "supergroup", 42), -101) == "normal"
    assert await handler._resolve_persona_mode(update(42, "private", 42), 42) == "roast"
    assert await handler._resolve_persona_mode(update(43, "private", 43), 43) == "normal"
