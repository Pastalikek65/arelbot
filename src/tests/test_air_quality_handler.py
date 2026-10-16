from types import SimpleNamespace

import pytest

from src.features.city_directory import CityDirectory
from src.features.contracts import FeatureResult
from src.handlers.command_handler import CommandHandler
from src.handlers.callback_handler import CallbackHandler


@pytest.mark.asyncio
async def test_hava_quality_alias_uses_typed_feature_and_navigation():
    sent = []

    class Message:
        async def reply_text(self, text, **kwargs):
            sent.append((text, kwargs))

    class Executor:
        def __init__(self):
            self.calls = []

        async def execute(self, feature_key, params):
            self.calls.append((feature_key, params))
            return FeatureResult.success(
                {"content": "<b>ğŸŒ¿ AQI</b>"},
                display_blocks=("<b>ğŸŒ¿ AQI</b>",),
            )

    executor = Executor()
    engine = SimpleNamespace(city_directory=CityDirectory(), feature_executor=executor)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=7, type="private"),
        effective_message=Message(),
    )
    context = SimpleNamespace(args=["kalite", "Ä°stanbul"])
    handler = CommandHandler(engine, SimpleNamespace(), None, None)

    await handler.handle_hava_command(update, context)

    assert executor.calls == [("air_quality", {"city": "Ä°stanbul"})]
    assert sent and "AQI" in sent[0][0]
    callbacks = [
        button.callback_data
        for row in sent[0][1]["reply_markup"].inline_keyboard
        for button in row
    ]
    assert "weather_show:7:34" in callbacks


@pytest.mark.asyncio
async def test_air_quality_callback_is_owner_scoped_in_private_chats():
    class Executor:
        async def execute(self, feature_key, params):
            assert feature_key == "air_quality"
            return FeatureResult.success(
                {"content": "<b>ğŸŒ¿ AQI</b>"},
                display_blocks=("<b>ğŸŒ¿ AQI</b>",),
            )

    class Query:
        def __init__(self):
            self.answers = []
            self.edited = None

        async def answer(self, *args, **kwargs):
            self.answers.append((args, kwargs))

        async def edit_message_text(self, text, **kwargs):
            self.edited = (text, kwargs)

    engine = SimpleNamespace(city_directory=CityDirectory(), feature_executor=Executor())
    handler = CallbackHandler(engine, SimpleNamespace(), None)
    query = Query()
    denied = SimpleNamespace(
        effective_user=SimpleNamespace(id=8),
        effective_chat=SimpleNamespace(id=8, type="private"),
    )
    await handler._handle_air_quality_callback(denied, query, SimpleNamespace(), "air_quality:7:34")
    assert query.edited is None
    assert query.answers and "baÅŸka" in query.answers[-1][0][0]

    allowed = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=7, type="private"),
    )
    await handler._handle_air_quality_callback(allowed, query, SimpleNamespace(), "air_quality:7:34")
    assert query.edited is not None
    assert "AQI" in query.edited[0]
