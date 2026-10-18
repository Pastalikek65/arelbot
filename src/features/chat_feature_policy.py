"""Chat-scoped feature policy shared by commands, callbacks and media flows.

The policy deliberately lives outside Telegram handlers.  Handlers only ask
whether a feature is enabled; persistence remains behind ``HandlerDataPort``
or another repository-compatible object.
"""

from __future__ import annotations

from typing import Any


FEATURE_SETTING_KEYS: dict[str, str] = {
    "quote": "q_enabled",
    "quotes": "q_enabled",
    "q": "q_enabled",
    "q_enabled": "q_enabled",
    "media": "media_enabled",
    "medya": "media_enabled",
    "video": "media_enabled",
    "indirici": "media_enabled",
    "media_enabled": "media_enabled",
}


class ChatFeaturePolicy:
    """Resolve and persist chat-level feature switches through a data port."""

    def __init__(self, data_port: Any) -> None:
        self.data_port = data_port

    @staticmethod
    def setting_key(feature: str) -> str:
        normalized = str(feature or "").strip().casefold()
        return FEATURE_SETTING_KEYS.get(normalized, normalized)

    async def is_enabled(self, chat_id: int, feature: str, default: bool = True) -> bool:
        """Read a switch without making a storage outage fail a user update.

        A missing repository is treated as the historical default (enabled),
        preserving existing deployments while Mongo is starting.  Explicitly
        stored ``False`` values are always respected.
        """

        key = self.setting_key(feature)
        try:
            settings = await self.data_port.get_chat_settings(chat_id)
        except Exception:
            return bool(default)
        if not isinstance(settings, dict):
            return bool(default)
        return bool(settings.get(key, default))

    async def set_enabled(
        self,
        chat_id: int,
        feature: str,
        enabled: bool,
        *,
        actor_id: int | None = None,
    ) -> None:
        """Persist a switch and optionally append a bounded audit record."""

        key = self.setting_key(feature)
        await self.data_port.update_chat_setting_key(chat_id, key, bool(enabled))
        if actor_id is None:
            return
        audit = getattr(self.data_port, "add_audit_log", None)
        if not callable(audit):
            return
        try:
            await audit(
                int(actor_id),
                "chat_feature_toggle",
                feature=key,
                enabled=bool(enabled),
            )
        except Exception:
            # A telemetry failure must not undo a successfully persisted user
            # setting or turn a Telegram callback into a visible error.
            return


async def is_chat_feature_enabled(
    data_port: Any,
    chat_id: int,
    key: str,
    default: bool = True,
) -> bool:
    """Compatibility function for existing handler call sites and tests."""

    return await ChatFeaturePolicy(data_port).is_enabled(chat_id, key, default)

