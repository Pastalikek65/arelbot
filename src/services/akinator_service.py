"""Bounded async adapter around the third-party ``akipy`` client."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from src.games.akinator import ANSWER_CHOICES, sync_akinator_state

try:  # pragma: no cover - import availability is covered by deployment checks
    from akipy.async_akinator import Akinator
except ImportError:  # pragma: no cover
    Akinator = None  # type: ignore[assignment,misc]


class AkinatorServiceError(RuntimeError):
    """Stable, user-safe service error with no upstream exception text."""

    def __init__(self, code: str = "AKINATOR_UNAVAILABLE") -> None:
        self.code = code
        super().__init__(code)


class AkinatorService:
    def __init__(
        self,
        *,
        aki_factory: Callable[[], Any] | None = None,
        max_concurrency: int = 4,
        timeout_seconds: int = 45,
        solver_url: str | None = None,
        solver_timeout_ms: int | None = None,
    ) -> None:
        self._factory = aki_factory
        self._semaphore = asyncio.Semaphore(max(1, min(16, int(max_concurrency))))
        self.timeout_seconds = max(5, min(180, int(timeout_seconds)))
        self.solver_url = solver_url
        self.solver_timeout_ms = solver_timeout_ms
        self._clients: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._presentation_locks: dict[int, asyncio.Lock] = {}
        self._revisions: dict[str, int] = {}

    def _lock_for(self, state: dict[str, Any]) -> asyncio.Lock:
        return self._locks.setdefault(str(state["round_id"]), asyncio.Lock())

    def presentation_lock(self, chat_id: int) -> asyncio.Lock:
        """Serialize one chat's start, callback mutation and panel delivery."""
        return self._presentation_locks.setdefault(int(chat_id), asyncio.Lock())

    def has_session(self, state: dict[str, Any]) -> bool:
        """A persisted question needs its matching live upstream client."""
        round_id = str(state.get("round_id") or "")
        return round_id in self._clients and round_id in self._revisions

    def _new_client(self) -> Any:
        if self._factory is not None:
            return self._factory()
        if Akinator is None:
            raise AkinatorServiceError("AKINATOR_DEPENDENCY_MISSING")
        kwargs: dict[str, Any] = {}
        if self.solver_url:
            kwargs["solver_url"] = self.solver_url
        if self.solver_timeout_ms is not None:
            # akipy forwards this value as FlareSolverr ``maxTimeout`` in
            # milliseconds; the service timeout above remains seconds.
            kwargs["solver_timeout"] = max(1000, int(self.solver_timeout_ms))
        return Akinator(**kwargs)

    async def _call(self, method, *args, **kwargs):
        try:
            async with self._semaphore:
                return await asyncio.wait_for(
                    method(*args, **kwargs), timeout=self.timeout_seconds
                )
        except asyncio.TimeoutError as exc:
            raise AkinatorServiceError("AKINATOR_TIMEOUT") from exc
        except AkinatorServiceError:
            raise
        except Exception as exc:
            raise AkinatorServiceError("AKINATOR_PROVIDER_ERROR") from exc

    async def start(self, state: dict[str, Any]) -> dict[str, Any]:
        async with self._lock_for(state):
            client = self._new_client()
            try:
                await self._call(
                    client.start_game,
                    language="tr",
                    child_mode=bool(state.get("child_mode", False)),
                    game_mode=str(state.get("theme") or "c"),
                )
                self._clients[str(state["round_id"])] = client
                self._revisions[str(state["round_id"])] = 0
                # The first revision represents the initial question.  Action
                # revisions start at zero so the first button press is 1.
                state["revision"] = int(state.get("revision", 0))
                sync_akinator_state(state, client)
                return state
            except AkinatorServiceError:
                await self._close_quietly(client)
                raise

    async def answer(self, state: dict[str, Any], choice: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        normalized = str(choice).strip().casefold()
        async with self._lock_for(state):
            client = self._client(state)
            self._check_revision(state, expected_revision)
            if normalized == "b":
                await self._call(client.back)
                state["questions"] = max(0, int(state.get("questions", 0)) - 1)
            elif normalized in ANSWER_CHOICES:
                await self._call(client.answer, normalized)
                state["questions"] = int(state.get("questions", 0)) + 1
            else:
                raise AkinatorServiceError("AKINATOR_INVALID_CHOICE")
            state["revision"] = int(state.get("revision", 0)) + 1
            self._revisions[str(state["round_id"])] = int(state["revision"])
            state["last_action"] = normalized
            return sync_akinator_state(state, client)

    async def confirm(self, state: dict[str, Any], yes: bool, *, expected_revision: int | None = None) -> dict[str, Any]:
        async with self._lock_for(state):
            client = self._client(state)
            self._check_revision(state, expected_revision)
            await self._call(client.choose if yes else client.exclude)
            state["revision"] = int(state.get("revision", 0)) + 1
            self._revisions[str(state["round_id"])] = int(state["revision"])
            state["last_action"] = "guess_yes" if yes else "guess_no"
            return sync_akinator_state(state, client)

    async def close(self, state: dict[str, Any]) -> None:
        async with self._lock_for(state):
            client = self._clients.pop(str(state["round_id"]), None)
            self._revisions.pop(str(state["round_id"]), None)
            self._locks.pop(str(state["round_id"]), None)
            if client is not None:
                await self._close_quietly(client)

    async def _close_quietly(self, client: Any) -> None:
        try:
            close = getattr(client, "close", None)
            if close is not None:
                await close()
        except Exception:
            return

    def _client(self, state: dict[str, Any]) -> Any:
        client = self._clients.get(str(state["round_id"]))
        if client is None:
            raise AkinatorServiceError("AKINATOR_SESSION_EXPIRED")
        return client

    def _check_revision(self, state: dict[str, Any], expected_revision: int | None) -> None:
        current = self._revisions.get(str(state["round_id"]))
        if current is None:
            raise AkinatorServiceError("AKINATOR_SESSION_EXPIRED")
        if expected_revision is not None and int(expected_revision) != current:
            raise AkinatorServiceError("AKINATOR_STALE_ACTION")
        state["revision"] = current
