"""Typed contracts for targeted group replies.

This package is Telegram-agnostic.  It converts a model's structured reply
plan into reply segments whose targets are selected exclusively by Telegram
message IDs supplied by the application.
"""

from .models import GroupMessageRef, GroupReplyPlan, GroupTurnContext, ReplySegment
from .router import GroupReplyRouter, MetricsSink

__all__ = [
    "GroupMessageRef",
    "GroupReplyPlan",
    "GroupTurnContext",
    "ReplySegment",
    "GroupReplyRouter",
    "MetricsSink",
]
