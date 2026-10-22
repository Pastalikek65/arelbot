from types import SimpleNamespace

import pytest


class _Auth:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    async def is_admin(self, chat_id, user_id):
        self.calls.append((chat_id, user_id))
        return self.result


class _BotThatMustNotBeCalled:
    async def get_chat_member(self, *args, **kwargs):
        raise AssertionError("handler bypassed injected authorization")


def _update(chat_type="supergroup", chat_id=-100, user_id=42):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(type=chat_type, id=chat_id),
        effective_user=SimpleNamespace(id=user_id),
    )


@pytest.mark.asyncio
async def test_is_admin_routes_through_injected_authorization():
    from src.handlers.handler_utils import is_admin

    auth = _Auth()
    context = SimpleNamespace(bot=_BotThatMustNotBeCalled())
    result = await is_admin(_update(), context, authorization=auth)

    assert result is True
    assert auth.calls == [(-100, 42)]


@pytest.mark.asyncio
async def test_group_admin_helper_fails_closed_for_private_chat():
    from src.handlers.handler_utils import is_group_admin

    auth = _Auth()
    result = await is_group_admin(
        _update(chat_type="private", chat_id=42),
        SimpleNamespace(bot=_BotThatMustNotBeCalled()),
        authorization=auth,
    )

    assert result is False
    assert auth.calls == []


@pytest.mark.asyncio
async def test_command_handler_group_admin_uses_injected_boundary():
    from src.handlers.command_handler import CommandHandler

    auth = _Auth()
    handler = CommandHandler(
        SimpleNamespace(),
        SimpleNamespace(owner_id=999),
        None,
        None,
        authorization=auth,
    )

    result = await handler._is_group_admin(
        _update(),
        SimpleNamespace(bot=_BotThatMustNotBeCalled()),
    )

    assert result is True
    assert auth.calls == [(-100, 42)]


@pytest.mark.asyncio
async def test_callback_handler_group_admin_uses_injected_boundary():
    from src.handlers.callback_handler import CallbackHandler

    auth = _Auth()
    handler = CallbackHandler(
        SimpleNamespace(),
        SimpleNamespace(owner_id=999),
        None,
        authorization=auth,
    )

    result = await handler._is_group_admin(
        _update(),
        SimpleNamespace(bot=_BotThatMustNotBeCalled()),
    )

    assert result is True
    assert auth.calls == [(-100, 42)]

