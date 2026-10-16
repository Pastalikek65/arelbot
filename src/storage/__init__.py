"""Persistence interfaces used by the bot's queues and conversation flows."""

from src.storage.state_store import MemoryStateStore, PersistentStateMap, RedisStateStore, StateStore
from src.storage.state_scope import (
    get_scoped_state,
    pop_scoped_state,
    scope_state,
    set_scoped_state,
    state_matches,
)

from .file_store import FileStore, LocalFileStore, StoredFile
from .migrations import Migration, MigrationRunner

__all__ = [
    "StateStore",
    "MemoryStateStore",
    "RedisStateStore",
    "PersistentStateMap",
    "scope_state",
    "state_matches",
    "get_scoped_state",
    "set_scoped_state",
    "pop_scoped_state",
    "FileStore",
    "LocalFileStore",
    "StoredFile",
    "Migration",
    "MigrationRunner",
]
