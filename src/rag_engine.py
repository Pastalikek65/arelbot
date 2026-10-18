"""User-isolated document ingestion and retrieval.

The RAG boundary treats uploaded files as hostile input. Files are validated
before parsing, DOCX archives are inspected without extraction, and all
records carry a Telegram user id. Chroma is an implementation detail; callers
receive stable domain messages instead of parser exceptions.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import uuid
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List

try:
    import chromadb
except ImportError:
    chromadb = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

from .key_manager import KeyManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentValidation:
    path: Path
    filename: str
    extension: str
    mime_type: str | None
    size: int
    sha256: str


class DocumentIngestionPolicy:
    """Shared validation policy for Telegram document uploads."""

    ALLOWED_MIME = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    MAX_ARCHIVE_ENTRIES = 512
    MAX_ARCHIVE_ENTRY_BYTES = 8 * 1024 * 1024
    MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
    MAX_TEXT_CHARS = 8_000_000

    def __init__(self, max_bytes: int = 20 * 1024 * 1024,
                 allowed_roots: Iterable[str | os.PathLike[str]] | None = None):
        if int(max_bytes) < 1:
            raise ValueError("invalid_document_limit")
        self.max_bytes = int(max_bytes)
        self.allowed_roots = tuple(Path(root).resolve() for root in (allowed_roots or ()))

    def _safe_path(self, file_path: str | os.PathLike[str]) -> Path:
        raw = Path(file_path)
        if raw.is_symlink():
            raise ValueError("DOCUMENT_SYMLINK_REJECTED")
        try:
            path = raw.resolve(strict=True)
        except (FileNotFoundError, OSError):
            raise ValueError("DOCUMENT_NOT_FOUND") from None
        if not path.is_file():
            raise ValueError("DOCUMENT_NOT_FOUND")
        if self.allowed_roots and not any(path == root or root in path.parents for root in self.allowed_roots):
            raise ValueError("DOCUMENT_PATH_OUTSIDE_ROOT")
        if any(parent.is_symlink() for parent in (path.parent, *path.parents)):
            raise ValueError("DOCUMENT_SYMLINK_REJECTED")
        return path

    @staticmethod
    def _signature(path: Path) -> bytes:
        try:
            with path.open("rb") as stream:
                return stream.read(8)
        except OSError:
            raise ValueError("DOCUMENT_READ_FAILED") from None

    def validate(self, file_path: str | os.PathLike[str], mime_type: str | None = None) -> DocumentValidation:
        path = self._safe_path(file_path)
        try:
            size = path.stat().st_size
        except OSError:
            raise ValueError("DOCUMENT_READ_FAILED") from None
        if size > self.max_bytes:
            raise ValueError("DOCUMENT_TOO_LARGE")
        extension = path.suffix.casefold()
        if extension not in self.ALLOWED_MIME:
            raise ValueError("DOCUMENT_UNSUPPORTED_FORMAT")
        expected_mime = self.ALLOWED_MIME[extension]
        if mime_type and mime_type != expected_mime:
            raise ValueError("DOCUMENT_MIME_MISMATCH")
        signature = self._signature(path)
        if extension == ".pdf" and not signature.startswith(b"%PDF-"):
            raise ValueError("DOCUMENT_SIGNATURE_INVALID")
        if extension == ".docx" and not signature.startswith(b"PK"):
            raise ValueError("DOCUMENT_SIGNATURE_INVALID")
        if extension == ".txt":
            try:
                with path.open("rb") as stream:
                    stream.read(min(size, 64 * 1024)).decode("utf-8")
            except (UnicodeDecodeError, OSError):
                raise ValueError("DOCUMENT_ENCODING_INVALID") from None
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        except OSError:
            raise ValueError("DOCUMENT_READ_FAILED") from None
        return DocumentValidation(path, path.name, extension, mime_type or expected_mime, size, digest.hexdigest())

    def validate_docx_archive(self, path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > self.MAX_ARCHIVE_ENTRIES:
                    raise ValueError("DOCUMENT_ARCHIVE_TOO_MANY_ENTRIES")
                total = 0
                names: set[str] = set()
                for info in entries:
                    name = info.filename.replace("\\", "/")
                    parts = name.split("/")
                    if not name or "\x00" in name or name.startswith("/") or ".." in parts or ":" in name:
                        raise ValueError("DOCUMENT_ARCHIVE_PATH_INVALID")
                    if name in names:
                        raise ValueError("DOCUMENT_ARCHIVE_DUPLICATE_ENTRY")
                    names.add(name)
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise ValueError("DOCUMENT_ARCHIVE_SYMLINK")
                    if info.file_size > self.MAX_ARCHIVE_ENTRY_BYTES:
                        raise ValueError("DOCUMENT_ARCHIVE_ENTRY_TOO_LARGE")
                    total += info.file_size
                    if total > self.MAX_ARCHIVE_BYTES:
                        raise ValueError("DOCUMENT_ARCHIVE_TOO_LARGE")
                    if info.file_size > 0 and info.compress_size == 0:
                        raise ValueError("DOCUMENT_ARCHIVE_COMPRESSION_INVALID")
                    if info.compress_size and info.file_size > info.compress_size * 200:
                        raise ValueError("DOCUMENT_ARCHIVE_COMPRESSION_RATIO")
                if "word/document.xml" not in names:
                    raise ValueError("DOCUMENT_DOCX_XML_MISSING")
                if archive.testzip() is not None:
                    raise ValueError("DOCUMENT_ARCHIVE_CORRUPT")
        except ValueError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError):
            raise ValueError("DOCUMENT_ARCHIVE_INVALID") from None


class RAGEngine:
    def __init__(self, key_manager: KeyManager, db_path: str = "./chroma_db",
                 max_file_bytes: int = 20 * 1024 * 1024,
                 allowed_roots: Iterable[str | os.PathLike[str]] | None = None,
                 embedding_service: Any | None = None,
                 use_provider_embeddings: bool = False,
                 database: Any | None = None):
        self.key_manager = key_manager
        self.database = database
        self.policy = DocumentIngestionPolicy(max_file_bytes, allowed_roots)
        self.embedding_service = embedding_service
        self.use_provider_embeddings = bool(use_provider_embeddings)
        self.client = None
        self.collection = None
        if chromadb is None:
            logger.warning("ChromaDB is not installed; document search is disabled")
            return
        try:
            self.client = chromadb.PersistentClient(path=db_path)
            collection_name = "documents_provider_v1" if self.use_provider_embeddings else "documents_v2"
            self.collection = self.client.get_or_create_collection(name=collection_name)
            logger.info("RAGEngine initialized with ChromaDB")
        except Exception as exc:
            logger.error("RAG engine initialization failed: %s", type(exc).__name__)
            raise

    def _get_embedding(self, text: str, retries: int = 2) -> List[float]:
        dimensions = 256
        vector = [0.0] * dimensions
        for token in re.findall(r"\w+", text.casefold()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % dimensions
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / norm for value in vector]

    async def _resolve_embedding(self, text: str) -> list[float] | None:
        if getattr(self, "use_provider_embeddings", False):
            if getattr(self, "embedding_service", None) is None:
                raise ValueError("DOCUMENT_EMBEDDING_UNAVAILABLE")
            try:
                vector = await self.embedding_service.embed_one(text)
            except Exception:
                logger.warning("Provider embedding failed", exc_info=True)
                raise ValueError("DOCUMENT_EMBEDDING_UNAVAILABLE") from None
            if not vector:
                raise ValueError("DOCUMENT_EMBEDDING_UNAVAILABLE")
            return vector
        return self._get_embedding(text)

    @classmethod
    def _read_docx(cls, file_path: str | os.PathLike[str], policy: DocumentIngestionPolicy | None = None) -> str:
        active_policy = policy or DocumentIngestionPolicy()
        path = Path(file_path)
        active_policy.validate_docx_archive(path)
        try:
            with zipfile.ZipFile(path) as archive:
                xml = archive.read("word/document.xml")
            upper_xml = xml[:1024].upper()
            if b"<!DOCTYPE" in upper_xml or b"<!ENTITY" in upper_xml:
                raise ValueError("DOCUMENT_DOCX_XML_UNSAFE")
            root = ET.fromstring(xml)
        except (OSError, zipfile.BadZipFile, ET.ParseError):
            raise ValueError("DOCUMENT_DOCX_XML_INVALID") from None
        text = "\n".join(
            "".join(node.text or "" for node in paragraph.iter())
            for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p")
        )
        if len(text) > active_policy.MAX_TEXT_CHARS:
            raise ValueError("DOCUMENT_TEXT_TOO_LARGE")
        return text

    @staticmethod
    def _chunks(text: str, source: str, page: int = 1) -> list[dict[str, Any]]:
        chunk_size, overlap = 1000, 100
        result = []
        for start in range(0, len(text), chunk_size - overlap):
            chunk = text[start:start + chunk_size]
            if chunk.strip():
                result.append({"text": chunk, "page": page, "source": source})
        return result

    def _has_duplicate(self, user_id: int, sha256: str) -> bool:
        if self.collection is None:
            return False
        try:
            found = self.collection.get(
                where={"$and": [{"user_id": {"$eq": int(user_id)}}, {"sha256": {"$eq": sha256}}]},
                include=["metadatas"],
            )
            return bool(found and found.get("ids"))
        except Exception as exc:
            logger.warning("RAG duplicate check failed: %s", type(exc).__name__)
            return False

    async def _has_metadata_duplicate(self, user_id: int, sha256: str) -> bool:
        finder = getattr(self.database, "list_rag_documents", None)
        if finder is None:
            return False
        try:
            rows = await finder(int(user_id), limit=100)
            return any(str(row.get("sha256")) == str(sha256) for row in rows)
        except Exception as exc:
            logger.warning("RAG metadata duplicate check failed: %s", type(exc).__name__)
            return False

    async def ingest_file(self, file_path: str, user_id: int | None = None,
                          mime_type: str | None = None) -> str:
        if user_id is None:
            return "Belge eklemek i\u00e7in kullan\u0131c\u0131 ba\u011flam\u0131 gerekli (DOCUMENT_USER_REQUIRED)."
        try:
            validation = self.policy.validate(file_path, mime_type=mime_type)
            if validation.extension == ".docx":
                self.policy.validate_docx_archive(validation.path)
            chunks: list[dict[str, Any]] = []
            if validation.extension == ".pdf":
                if PdfReader is None:
                    return "PDF deste\u011fi kullan\u0131lam\u0131yor (DOCUMENT_PDF_UNAVAILABLE)."
                reader = PdfReader(str(validation.path), strict=False)
                if len(reader.pages) > 2000:
                    return "PDF sayfa s\u0131n\u0131r\u0131n\u0131 a\u015f\u0131yor (DOCUMENT_PAGE_LIMIT)."
                for index, page in enumerate(reader.pages):
                    page_text = page.extract_text() or ""
                    if page_text:
                        chunks.extend(self._chunks(page_text[:self.policy.MAX_TEXT_CHARS], validation.filename, index + 1))
            elif validation.extension == ".txt":
                text = validation.path.read_text(encoding="utf-8")
                if len(text) > self.policy.MAX_TEXT_CHARS:
                    return "Belge metin s\u0131n\u0131r\u0131n\u0131 a\u015f\u0131yor (DOCUMENT_TEXT_TOO_LARGE)."
                chunks = self._chunks(text, validation.filename)
            else:
                chunks = self._chunks(self._read_docx(validation.path, self.policy), validation.filename)
            if not chunks:
                return "Belgeden metin \u00e7\u0131kar\u0131lamad\u0131 (DOCUMENT_EMPTY)."
            if self.collection is None:
                return "Belge arama depolamas\u0131 kullan\u0131lam\u0131yor (DOCUMENT_STORAGE_UNAVAILABLE)."
            if self._has_duplicate(int(user_id), validation.sha256) or await self._has_metadata_duplicate(int(user_id), validation.sha256):
                return "Bu belge zaten haf\u0131zan\u0131zda (DOCUMENT_DUPLICATE)."
            ids, embeddings, documents, metadatas = [], [], [], []
            document_id = uuid.uuid4().hex
            for chunk in chunks:
                ids.append(uuid.uuid4().hex)
                embeddings.append(await self._resolve_embedding(chunk["text"]))
                documents.append(chunk["text"])
                metadatas.append({
                    "source": chunk["source"], "page": int(chunk["page"]),
                    "user_id": int(user_id), "document_id": document_id,
                    "sha256": validation.sha256,
                })
            self.collection.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
            saver = getattr(self.database, "save_rag_document", None)
            if saver is not None:
                try:
                    from datetime import datetime, timezone
                    await saver({
                        "document_id": document_id,
                        "user_id": int(user_id),
                        "filename": validation.filename,
                        "extension": validation.extension,
                        "mime_type": validation.mime_type,
                        "size": validation.size,
                        "sha256": validation.sha256,
                        "chunk_count": len(chunks),
                        "created_at": datetime.now(timezone.utc),
                        "embedding_mode": "provider" if self.use_provider_embeddings else "local",
                    })
                except Exception:
                    # Vectors without owner-scoped metadata cannot be listed
                    # or deleted safely, so roll them back before reporting.
                    try:
                        self.collection.delete(ids=ids)
                    except Exception:
                        logger.warning("Could not roll back Chroma vectors after metadata failure", exc_info=True)
                    return "Belge i\u015flenmedi (DOCUMENT_METADATA_UNAVAILABLE)."
            return f"{validation.filename} haf\u0131zaya al\u0131nd\u0131; {len(chunks)} par\u00e7a haz\u0131rland\u0131 (DOCUMENT_INGESTED)."
        except ValueError as exc:
            code = str(exc) or "DOCUMENT_INVALID"
            return f"Belge i\u015flenmedi ({code})."
        except Exception as exc:
            logger.warning("RAG ingestion failed: %s", type(exc).__name__)
            return "Belge i\u015flenmedi (DOCUMENT_PROCESSING_ERROR)."

    async def search(self, query: str, n_results: int = 3, user_id: int | None = None) -> str:
        if self.collection is None:
            return "Belge arama bu ortamda etkin de\u011fil (DOCUMENT_STORAGE_UNAVAILABLE)."
        if user_id is None:
            return "Belge aramak i\u00e7in kullan\u0131c\u0131 ba\u011flam\u0131 gerekli (DOCUMENT_USER_REQUIRED)."
        query = (query or "").strip()
        if not query:
            return "Arama metni bo\u015f olamaz (DOCUMENT_QUERY_REQUIRED)."
        if len(query) > 500:
            return "Arama metni \u00e7ok uzun (DOCUMENT_QUERY_TOO_LONG)."
        try:
            result = self.collection.query(
                query_embeddings=[await self._resolve_embedding(query)],
                n_results=max(1, min(10, int(n_results))),
                include=["documents", "metadatas", "distances"],
                where={"user_id": {"$eq": int(user_id)}},
            )
            documents = (result.get("documents") or [[]])[0]
            metadatas = (result.get("metadatas") or [[]])[0]
            if not documents:
                return "E\u015fle\u015fen belge bulunamad\u0131 (DOCUMENT_NO_MATCH)."
            return "\n\n".join(
                f"[{meta.get('source', 'belge')} / sayfa {meta.get('page', 1)}]\n{doc}"
                for doc, meta in zip(documents, metadatas)
            )
        except Exception as exc:
            logger.warning("RAG search failed: %s", type(exc).__name__)
            return "Belge aramas\u0131 ba\u015far\u0131s\u0131z (DOCUMENT_SEARCH_ERROR)."

    async def delete_user_documents(self, user_id: int) -> int:
        if self.collection is None:
            remover = getattr(self.database, "delete_rag_documents", None)
            if remover is not None:
                try:
                    await remover(int(user_id))
                except Exception:
                    logger.warning("Could not delete RAG metadata without Chroma", exc_info=True)
            return 0
        try:
            existing = self.collection.get(where={"user_id": {"$eq": int(user_id)}}, include=["metadatas"])
            ids = existing.get("ids", []) if isinstance(existing, dict) else []
            if ids:
                self.collection.delete(ids=ids)
            remover = getattr(self.database, "delete_rag_documents", None)
            if remover is not None:
                await remover(int(user_id))
            return len(ids)
        except Exception as exc:
            logger.warning("Could not delete RAG documents: %s", type(exc).__name__)
            return 0

    async def list_documents(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        """List owner-scoped metadata without exposing document text."""
        finder = getattr(self.database, "list_rag_documents", None)
        if finder is not None:
            try:
                rows = await finder(int(user_id), limit=max(1, min(int(limit), 100)))
                return [dict(row) for row in rows]
            except Exception:
                logger.warning("Could not list RAG metadata", exc_info=True)
        if self.collection is None:
            return []
        try:
            existing = self.collection.get(
                where={"user_id": {"$eq": int(user_id)}},
                include=["metadatas"],
            )
            rows: dict[str, dict[str, Any]] = {}
            for metadata in existing.get("metadatas") or []:
                document_id = str(metadata.get("document_id") or "")
                if not document_id:
                    continue
                row = rows.setdefault(document_id, {
                    "document_id": document_id,
                    "user_id": int(user_id),
                    "filename": str(metadata.get("source") or "belge")[:255],
                    "chunk_count": 0,
                    "created_at": None,
                })
                row["chunk_count"] += 1
            return list(rows.values())[: max(1, min(int(limit), 100))]
        except Exception:
            logger.warning("Could not derive RAG metadata from Chroma", exc_info=True)
            return []

    async def delete_document(self, user_id: int, document_id: str, *, actor_id: int | None = None) -> int:
        """Delete exactly one owner-scoped document and its metadata."""
        if self.collection is None:
            remover = getattr(self.database, "delete_rag_document", None)
            if remover is not None:
                try:
                    return 1 if await remover(int(user_id), str(document_id or "")[:64]) else 0
                except Exception:
                    logger.warning("Could not delete RAG metadata without Chroma", exc_info=True)
            return 0
        safe_id = str(document_id or "").strip()[:64]
        if not safe_id:
            return 0
        try:
            existing = self.collection.get(
                where={"$and": [
                    {"user_id": {"$eq": int(user_id)}},
                    {"document_id": {"$eq": safe_id}},
                ]},
                include=["metadatas"],
            )
            ids = list(existing.get("ids") or [])
            if not ids:
                return 0
            self.collection.delete(ids=ids)
            remover = getattr(self.database, "delete_rag_document", None)
            if remover is not None:
                await remover(int(user_id), safe_id)
            audit = getattr(self.database, "add_audit_log", None)
            if audit is not None and actor_id is not None:
                try:
                    await audit(
                        int(actor_id),
                        "rag_document_deleted",
                        document_id=safe_id,
                        chunks=len(ids),
                    )
                except Exception:
                    logger.warning("Could not audit RAG document deletion", exc_info=True)
            return len(ids)
        except Exception:
            logger.warning("Could not delete RAG document", exc_info=True)
            return 0
