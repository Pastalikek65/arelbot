from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_private_roast_command_shows_warning_without_persisting():
    from src.handlers.command_handler import CommandHandler
    from src.storage.handler_data_port import HandlerDataPort

    class DB:
        def __init__(self):
            self.user = {}

        async def get_user(self, user_id):
            return dict(self.user)

        async def update_user(self, user_id, data):
            self.user.update(data)

    class Message:
        async def reply_text(self, text, **kwargs):
            self.reply = (text, kwargs)

    db = DB()
    message = Message()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
        effective_message=message,
        message=message,
    )
    handler = CommandHandler(
        SimpleNamespace(personality_policy=SimpleNamespace(sarcasm_level=1)),
        SimpleNamespace(owner_id=999), None, None,
        data_port=HandlerDataPort(db),
    )

    await handler.handle_personality_command(update, SimpleNamespace(args=["roast"]))

    assert "onay" in message.reply[0].lower()
    assert db.user == {}
    markup = message.reply[1]["reply_markup"]
    assert any(button.callback_data == "persona_user:roast_confirm" for row in markup.inline_keyboard for button in row)


def test_retro_card_renderer_creates_deterministic_png(tmp_path):
    from src.services.arel_card_renderer import ArelCardRenderer

    output = tmp_path / "profile.png"
    renderer = ArelCardRenderer()
    renderer.render_profile(output, {"level": 2, "xp": 12, "next_xp": 40, "coins": 515, "games": 3, "wins": 2}, "Arel")

    assert output.exists()
    from PIL import Image
    with Image.open(output) as image:
        assert image.size == (640, 480)
        assert image.format == "PNG"


@pytest.mark.asyncio
async def test_private_roast_callback_persists_only_after_confirmation():
    from src.handlers.callback_handler import CallbackHandler

    class DB:
        def __init__(self):
            self.user = {}

        async def update_user(self, user_id, data):
            self.user.update(data)

    class Query:
        def __init__(self):
            self.message = SimpleNamespace(chat=SimpleNamespace(id=42, type="private"))
            self.edits = []

        async def answer(self, *args, **kwargs):
            self.answer_args = (args, kwargs)

        async def edit_message_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

    db = DB()
    handler = CallbackHandler(SimpleNamespace(db=db), SimpleNamespace(owner_id=1), None)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    query = Query()
    await handler._handle_persona_user_callback(update, query, "persona_user:roast")
    assert db.user == {}
    assert query.edits and "onay" in query.edits[-1][0].lower()

    await handler._handle_persona_user_callback(update, query, "persona_user:roast_confirm")
    assert db.user["persona_mode"] == "roast"
