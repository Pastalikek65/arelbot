"""Narrow repository port exposed to Telegram handlers.

Handlers should not know that the current persistence implementation is
MongoDB.  This port deliberately exposes only the operations needed by the
Telegram surfaces and keeps collection handles, cursors, and database
internals outside the update path.
"""

from __future__ import annotations

from typing import Any


class HandlerDataPort:
    """Typed-ish async repository faÃ§ade for handler-owned persistence calls."""

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    @property
    def available(self) -> bool:
        return bool(getattr(self._repository, "available", True)) if self._repository is not None else False

    @property
    def reachable(self) -> bool:
        return bool(getattr(self._repository, "reachable", True)) if self._repository is not None else False

    def supports(self, operation: str) -> bool:
        """Report capabilities without exposing the wrapped repository object."""

        return self._repository is not None and callable(getattr(self._repository, operation, None))

    @classmethod
    def from_engine(cls, engine: Any) -> "HandlerDataPort | Any":
        """Resolve the explicit port, retaining a controlled test fallback."""

        explicit = getattr(engine, "data_port", None)
        if explicit is not None:
            return explicit
        return cls(getattr(engine, "db", None))

    async def _invoke(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if self._repository is None:
            raise RuntimeError("HANDLER_DATA_UNAVAILABLE")
        operation = getattr(self._repository, name)
        return await operation(*args, **kwargs)

    async def get_user(self, user_id: int) -> Any:
        return await self._invoke("get_user", user_id)

    async def update_user(self, user_id: int, data: dict[str, Any]) -> Any:
        return await self._invoke("update_user", user_id, data)

    async def set_memory_consent(self, user_id: int, enabled: bool) -> Any:
        return await self._invoke("set_memory_consent", user_id, enabled)

    async def get_memory_consent(self, user_id: int) -> Any:
        return await self._invoke("get_memory_consent", user_id)

    async def delete_user_facts(self, user_id: int) -> Any:
        return await self._invoke("delete_user_facts", user_id)

    async def delete_user_fact(self, user_id: int, fact_id: str) -> Any:
        return await self._invoke("delete_user_fact", user_id, fact_id)

    async def list_user_facts(self, user_id: int, limit: int = 50) -> Any:
        return await self._invoke("list_user_facts", user_id, limit=limit)

    async def add_audit_log(self, actor_id: int, action: str, **details: Any) -> Any:
        if not self.supports("add_audit_log"):
            return None
        return await self._invoke("add_audit_log", actor_id, action, **details)

    async def get_audit_logs(self, limit: int = 10) -> Any:
        return await self._invoke("get_audit_logs", limit=limit)

    async def get_creation_cache(self, unique_id: str) -> Any:
        return await self._invoke("get_creation_cache", unique_id)

    async def ping(self) -> Any:
        return await self._invoke("ping")

    async def ensure_indexes(self) -> Any:
        return await self._invoke("ensure_indexes")

    async def get_chat_settings(self, chat_id: int) -> Any:
        return await self._invoke("get_chat_settings", chat_id)

    async def update_chat_setting_key(self, chat_id: int, key: str, value: Any) -> Any:
        return await self._invoke("update_chat_setting_key", chat_id, key, value)

    async def save_game_state(self, chat_id: int, game_type: str, state: dict[str, Any]) -> Any:
        return await self._invoke("save_game_state", chat_id, game_type, state)

    async def get_game_state(self, chat_id: int, game_type: str) -> Any:
        return await self._invoke("get_game_state", chat_id, game_type)

    async def delete_game_state(self, chat_id: int, game_type: str) -> Any:
        return await self._invoke("delete_game_state", chat_id, game_type)

    async def claim_game_action(self, **kwargs: Any) -> Any:
        return await self._invoke("claim_game_action", **kwargs)

    async def release_game_action(self, action_key: str) -> Any:
        return await self._invoke("release_game_action", action_key)

    async def save_quote_template(self, user_id: int, name: str, options: dict[str, Any]) -> Any:
        return await self._invoke("save_quote_template", user_id, name, options)

    async def get_quote_templates(self, user_id: int) -> Any:
        return await self._invoke("get_quote_templates", user_id)

    async def get_quote_template(self, user_id: int, name: str) -> Any:
        return await self._invoke("get_quote_template", user_id, name)

    async def delete_quote_template(self, user_id: int, name: str) -> Any:
        return await self._invoke("delete_quote_template", user_id, name)

    async def delete_quotes(self, chat_id: int, limit: int | None = None) -> Any:
        return await self._invoke("delete_quotes", chat_id, limit=limit)

    async def get_top_quotes(self, chat_id: int, offset: int = 0, limit: int = 50) -> Any:
        return await self._invoke("get_top_quotes", chat_id, offset=offset, limit=limit)

    async def get_random_quote(self, chat_id: int) -> Any:
        return await self._invoke("get_random_quote", chat_id)

    async def update_quote_rating(self, sticker_file_id: str, field: str, inc: int = 1) -> Any:
        return await self._invoke("update_quote_rating", sticker_file_id, field, inc)

    async def get_recent_messages_with_users(self, chat_id: int, limit: int = 150) -> Any:
        return await self._invoke("get_recent_messages_with_users", chat_id, limit=limit)

    async def get_messages_by_ids(self, message_ids: list[str]) -> Any:
        return await self._invoke("get_messages_by_ids", message_ids)

    async def find_latest_message_for_quote(self, chat_id: int, user_id: int, content: str) -> Any:
        return await self._invoke("find_latest_message_for_quote", chat_id, user_id, content)

    async def get_sequential_messages(self, chat_id: int, start_timestamp: Any, limit: int) -> Any:
        return await self._invoke("get_sequential_messages", chat_id, start_timestamp, limit)

    async def create_sticker_pack(self, pack_data: dict[str, Any]) -> Any:
        return await self._invoke("create_sticker_pack", pack_data)

    async def register_sticker(
        self,
        pack_id: Any,
        file_id: str,
        emojis: str,
        tags: list[str] | None = None,
    ) -> Any:
        return await self._invoke("register_sticker", pack_id, file_id, emojis, tags)

    async def get_user_packs(self, user_id: int, pack_type: str | None = None) -> Any:
        return await self._invoke("get_user_packs", user_id, pack_type=pack_type)

    async def get_top_packs(self, limit: int = 5) -> Any:
        return await self._invoke("get_top_packs", limit=limit)

    async def get_pack_by_name(self, pack_name: str, user_id: int | None = None) -> Any:
        return await self._invoke("get_pack_by_name", pack_name, user_id=user_id)

    async def get_pack_by_id(self, pack_id: Any, user_id: int | None = None) -> Any:
        return await self._invoke("get_pack_by_id", pack_id, user_id=user_id)

    async def search_stickers(self, query_text: str, limit: int = 10, offset: int = 0) -> Any:
        return await self._invoke("search_stickers", query_text, limit=limit, offset=offset)

    async def delete_sticker_from_db(self, pack_id: Any, file_id: str) -> Any:
        return await self._invoke("delete_sticker_from_db", pack_id, file_id)

    async def save_creation_cache(self, unique_id: str, data: Any) -> Any:
        return await self._invoke("save_creation_cache", unique_id, data)

    async def get_recent_active_user_names(self, chat_id: int, limit: int = 20) -> Any:
        return await self._invoke("get_recent_active_user_names", chat_id, limit=limit)
