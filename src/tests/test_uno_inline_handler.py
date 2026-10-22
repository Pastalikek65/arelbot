"""Telegram wrappers delegate inline updates without owning game state."""

from types import SimpleNamespace

import pytest

from src.handlers.uno_inline import answer_uno_inline, chosen_uno_inline


class Service:
    async def answer_query(self, update, context):
        return update is self.update and context is self.context

    async def chosen_result(self, update, context):
        return update is self.update and context is self.context


@pytest.mark.asyncio
async def test_wrappers_delegate_exact_update_and_context():
    service = Service()
    service.update = SimpleNamespace()
    service.context = SimpleNamespace()
    assert await answer_uno_inline(service, service.update, service.context)
    assert await chosen_uno_inline(service, service.update, service.context)
