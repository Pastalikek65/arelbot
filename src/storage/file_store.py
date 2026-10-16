"""Ephemeral file storage used by media, document and render jobs.

The interface deliberately stores paths and metadata rather than binary blobs
in MongoDB.  ``LocalFileStore`` is the pilot implementation; an S3/MinIO
adapter can implement the same protocol without changing handlers.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class StoredFile:
    key: str
    path: str
    size: int
    sha256: str
    created_at: float


class FileStore(Protocol):
    async def put(self, source: str | os.PathLike[str] | bytes | BinaryIO,
                  key: str | None = None, max_bytes: int | None = None) -> StoredFile: ...
    async def delete(self, key: str) -> None: ...
    async def cleanup(self, max_age_seconds: int) -> int: ...


class LocalFileStore:
    """Safe, quota-aware local spool for temporary artifacts."""

    def __init__(self, root: str = "temp/spool", quota_bytes: int = 2 * 1024**3):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.quota_bytes = max(1, int(quota_bytes))
        self._lock = asyncio.Lock()

    def _safe_key(self, key: str) -> str:
        value = str(key).replace("\\", "/").lstrip("/")
        candidate = (self.root / value).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("invalid_file_key")
        return value

    async def put(self, source, key: str | None = None,
                  max_bytes: int | None = None) -> StoredFile:
        async with self._lock:
            source_path = None
            if isinstance(source, (str, os.PathLike)):
                source_path = Path(source)
                if not source_path.is_file():
                    raise FileNotFoundError(str(source_path))
                size = source_path.stat().st_size
            elif isinstance(source, bytes):
                size = len(source)
            else:
                size = None

            limit = max_bytes or self.quota_bytes
            if size is not None and size > limit:
                raise ValueError("file_too_large")
            used = sum(item.stat().st_size for item in self.root.rglob("*") if item.is_file())
            if size is not None and used + size > self.quota_bytes:
                raise ValueError("storage_quota_exceeded")

            safe_key = self._safe_key(key or f"{time.time_ns()}_{secrets.token_hex(6)}")
            target = (self.root / safe_key).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            written = 0

            def copy_stream() -> None:
                nonlocal written
                if source_path is not None:
                    stream = source_path.open("rb")
                    close = True
                elif isinstance(source, bytes):
                    import io
                    stream = io.BytesIO(source)
                    close = True
                else:
                    stream = source
                    close = False
                try:
                    with target.open("wb") as output:
                        while True:
                            chunk = stream.read(1024 * 1024)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > limit or used + written > self.quota_bytes:
                                raise ValueError("file_too_large")
                            digest.update(chunk)
                            output.write(chunk)
                finally:
                    if close:
                        stream.close()

            try:
                await asyncio.to_thread(copy_stream)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            return StoredFile(safe_key, str(target), written, digest.hexdigest(), time.time())

    async def delete(self, key: str) -> None:
        target = (self.root / self._safe_key(key)).resolve()
        if target.is_file():
            target.unlink(missing_ok=True)

    async def cleanup(self, max_age_seconds: int) -> int:
        cutoff = time.time() - max(0, max_age_seconds)
        removed = 0
        async with self._lock:
            for item in self.root.rglob("*"):
                if item.is_file() and item.stat().st_mtime < cutoff:
                    item.unlink(missing_ok=True)
                    removed += 1
            for directory in sorted((p for p in self.root.rglob("*") if p.is_dir()), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
        return removed
