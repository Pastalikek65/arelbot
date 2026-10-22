"""HRMN text adapter: one bounded attempt, then let the router fall through."""
from __future__ import annotations

import asyncio

from src.ai.openai_compatible import OpenAICompatibleProvider
from src.ai.provider_base import AIProviderRequestError, CompletionRequest, CompletionResponse


class HrmnChatProvider(OpenAICompatibleProvider):
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        try:
            return await asyncio.wait_for(super().complete(request), timeout=self.timeout_seconds)
        except AIProviderRequestError:
            raise
        except Exception as error:
            # The next user request should skip a currently broken gateway.
            # Only this adapter opts into immediate circuit cooling.
            error.circuit_cooldown_seconds = 30.0
            raise
