from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.culture_ui import culture_category_help, culture_category_panel


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_culture_category_panel_is_owner_scoped_and_bounded():
    text, markup = culture_category_panel(123)
    assert "KÃ¼ltÃ¼r Ã¶nerileri" in text
    callbacks = [button.callback_data for button in _buttons(markup)]
    assert callbacks == ["culture:123:movie", "culture:123:series", "culture:123:book", "culture:123:music"]
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)


def test_culture_category_help_has_safe_command_hint():
    text, markup = culture_category_help(123, "book")
    assert "/kultur kitap fantastik" in text
    assert _buttons(markup)[0].callback_data == "culture_menu:123"


@pytest.mark.asyncio
async def test_culture_callback_rejects_other_owner():
    from src.handlers.callback_handler import CallbackHandler

    class Query:
        def __init__(self):
            self.answer_args = None
            self.edited = None

        async def answer(self, *args, **kwargs):
            self.answer_args = (args, kwargs)

        async def edit_message_text(self, text, **kwargs):
            self.edited = (text, kwargs)

    query = Query()
    update = SimpleNamespace(effective_user=SimpleNamespace(id=456))
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=1), None)
    await handler._handle_culture_callback(update, query, "culture:123:book")
    assert query.edited is None
    assert query.answer_args and "baÅŸka bir kullanÄ±cÄ±ya" in query.answer_args[0][0]


@pytest.mark.asyncio
async def test_culture_callback_renders_selected_category_without_provider_call():
    from src.handlers.callback_handler import CallbackHandler

    class Query:
        def __init__(self):
            self.edited = None

        async def answer(self, *args, **kwargs):
            raise AssertionError("valid callback should not show an error")

        async def edit_message_text(self, text, **kwargs):
            self.edited = (text, kwargs)

    query = Query()
    update = SimpleNamespace(effective_user=SimpleNamespace(id=456))
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=1), None)
    await handler._handle_culture_callback(update, query, "culture:456:series")
    assert query.edited is not None
    assert "/kultur dizi" in query.edited[0]
