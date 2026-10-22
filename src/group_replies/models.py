"""Domain models for group-targeted AI turns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


MessageKind = Literal[
    "text",
    "command",
    "media",
    "game",
    "system",
    "callback",
    "sticker",
    "photo",
    "video",
    "audio",
    "document",
]


@dataclass(frozen=True, slots=True)
class GroupMessageRef:
    """The immutable Telegram identity and content needed for one reply target.

    ``message_id`` is the only identity the model may target.  Display names
    are context for language generation and are deliberately never used for
    target selection.
    """

    chat_id: int
    thread_id: int | None
    message_id: int
    user_id: int
    display_name: str
    text: str
    created_at: datetime
    message_kind: str = "text"
    already_replied: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.chat_id, int):
            raise TypeError("chat_id must be an integer")
        if not isinstance(self.message_id, int) or isinstance(self.message_id, bool) or self.message_id <= 0:
            raise ValueError("message_id must be a positive integer")
        if not isinstance(self.user_id, int) or isinstance(self.user_id, bool) or self.user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("display_name must be non-empty")
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")

    @property
    def is_replyable_text(self) -> bool:
        """Whether this message can be selected as a conversational target."""

        excluded = {
            "command",
            "media",
            "game",
            "system",
            "callback",
            "sticker",
            "photo",
            "video",
            "audio",
            "document",
        }
        return self.message_kind not in excluded and bool(self.text.strip()) and not self.already_replied


@dataclass(frozen=True, slots=True)
class GroupTurnContext:
    """The bounded group context used for one Arel trigger."""

    current_message: GroupMessageRef
    candidates: tuple[GroupMessageRef, ...]
    trigger_kind: str
    max_segments: int = 3
    window_seconds: int = 300

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, tuple):
            object.__setattr__(self, "candidates", tuple(self.candidates))
        if not isinstance(self.trigger_kind, str) or not self.trigger_kind.strip():
            raise ValueError("trigger_kind must be non-empty")
        if self.max_segments < 1:
            raise ValueError("max_segments must be positive")
        # The product safety boundary is three independent replies per turn.
        object.__setattr__(self, "max_segments", min(3, self.max_segments))
        if self.window_seconds < 1:
            raise ValueError("window_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ReplySegment:
    """One text and its exact Telegram message target."""

    target_message: GroupMessageRef
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("reply text must be non-empty")
        if len(self.text) > 4096:
            raise ValueError("reply text exceeds Telegram message limit")


@dataclass(frozen=True, slots=True)
class GroupReplyPlan:
    """Validated delivery plan returned by :class:`GroupReplyRouter`."""

    segments: tuple[ReplySegment, ...] = ()
    rejected_reason: str | None = None
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.segments, tuple):
            object.__setattr__(self, "segments", tuple(self.segments))
        if len(self.segments) > 3:
            raise ValueError("a reply plan cannot contain more than three segments")
