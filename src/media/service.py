"""Telegram-facing media download orchestration."""

import hashlib
import json
import logging
import asyncio
import time
from contextlib import asynccontextmanager
from collections import defaultdict
from collections import deque
from pathlib import Path
from typing import Any, Dict

from config import Config
from src.jobs.delivery_ledger import JobDeliveryLedger
from src.media.cookie_vault import CookieVault
from src.media.job_queue import MediaJobQueue
from src.media.providers.ytdlp_provider import MediaDownloadError, YtDlpProvider
from src.media.providers.base import MediaRequest
from src.media.providers.registry import ProviderRegistry, YtDlpAdapter
from src.media.providers.http_fallback import HttpFallbackProvider
from src.media.transcoder import MediaTranscoder, MediaTranscodeError
from src.media.error_messages import media_delivery_error_message
from src.media.url_normalizer import normalize_media_url, platform_for_url
from src.database import Database
from src.storage.media_job_store import MediaJobStore
from src.observability import MetricsRegistry
from src.security.log_redaction import summarize_exception, summarize_private_text

logger = logging.getLogger(__name__)

# A timed-out download hands its job directory to a background reaper.  The
# reaper is not awaited by the job itself, so the service drains it on a
# schedule and at shutdown.
MAINTENANCE_INTERVAL_SECONDS = 3600
BACKGROUND_CLEANUP_TIMEOUT_SECONDS = 30
# The shutdown drain stays inside the container stop grace period, so a
# provider thread that never returns cannot hold the process open.
SHUTDOWN_CLEANUP_TIMEOUT_SECONDS = 5


class MediaDownloadService:
    def __init__(self, delivery_ledger: JobDeliveryLedger | None = None):
        self.database = Database()
        required = Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}
        self.job_store = MediaJobStore(self.database, required=required)
        self.queue = MediaJobQueue(store=self.job_store)
        self.cookie_vault = CookieVault(Config.MEDIA_COOKIE_STORAGE_DIR, Config.ENCRYPTION_KEY)
        self.provider = YtDlpProvider(
            Config.MEDIA_DOWNLOAD_DIR,
            Config.MEDIA_MAX_FILE_BYTES,
            Config.MEDIA_MAX_VIDEO_DURATION_SECONDS,
            Config.MEDIA_JOB_TIMEOUT_SECONDS,
            self.cookie_vault,
            Config.MEDIA_COOKIE_ENABLED,
            Config.MEDIA_ARIA2_ENABLED,
        )
        provider_list = [YtDlpAdapter(self.provider)]
        provider_list.extend(
            HttpFallbackProvider(f"fallback-{index}", endpoint, Config.MEDIA_DOWNLOAD_DIR, Config.MEDIA_MAX_FILE_BYTES)
            for index, endpoint in enumerate(Config.MEDIA_FALLBACK_URLS, start=1)
        )
        self.providers = ProviderRegistry(provider_list)
        self.transcoder = MediaTranscoder(Config.MEDIA_MAX_FILE_BYTES)
        self.bot = None
        # Split production roles cannot safely fall back to a process-local
        # queue: the bot enqueues work while a separate worker consumes it.
        # Keep the memory backend available only for combined local/test runs.
        self.redis_required = Config.MEDIA_REDIS_REQUIRED or Config.APP_ROLE in {"bot", "worker"}
        self.cookie_enabled = Config.MEDIA_COOKIE_ENABLED
        self.mongo_available = False
        self.maintenance = False
        self._user_limits = defaultdict(lambda: asyncio.Semaphore(2))
        self._chat_limits = defaultdict(lambda: asyncio.Semaphore(2))
        self._request_times = defaultdict(deque)
        self.metrics_registry = MetricsRegistry()
        # The shared send-boundary ledger keeps a media job from being
        # delivered twice when a worker dies between the upload and the
        # status write.
        self._injected_delivery_ledger = delivery_ledger
        self.delivery_ledger = delivery_ledger or JobDeliveryLedger(
            self.database, required=required
        )
        self._maintenance_task: asyncio.Task | None = None

    async def start(self, bot):
        self.bot = bot
        self.load_persisted_settings()
        self.mongo_available = await self.database.ping()
        if not self.mongo_available:
            self.job_store._disabled = True
            if Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}:
                raise RuntimeError("MongoDB is required for the selected production role")
            # Degraded development has no durable delivery state either, so
            # the service falls back to a process-local ledger. A ledger that
            # the caller supplied belongs to the caller and is used as it is.
            if self._injected_delivery_ledger is None:
                self.delivery_ledger = JobDeliveryLedger(None)
                logger.warning(
                    "MongoDB unavailable; the media delivery ledger runs in process-local mode"
                )
        await self.delivery_ledger.ensure_indexes()
        self.queue.processor = self._process_job
        worker_count = 0 if Config.APP_ROLE == "bot" else Config.MEDIA_QUEUE_WORKERS
        await self.queue.start(Config.REDIS_URL, worker_count, self.redis_required)
        self._maintenance_task = asyncio.create_task(
            self._maintenance_loop(), name="arel-media-maintenance"
        )

    async def close(self):
        await self._stop_maintenance_task()
        await self.queue.close()

    async def _maintenance_loop(self, interval: float = MAINTENANCE_INTERVAL_SECONDS) -> None:
        """Drain the provider's deferred cleanup workers on a schedule."""
        while True:
            try:
                await self.wait_for_background_cleanup(timeout=BACKGROUND_CLEANUP_TIMEOUT_SECONDS)
            except Exception as exc:
                logger.warning(
                    "Media maintenance failed: %s",
                    summarize_exception(exc, label="media_maintenance"),
                )
            await asyncio.sleep(interval)

    async def _stop_maintenance_task(self) -> None:
        """Cancel the periodic maintenance task of this service."""
        task = self._maintenance_task
        self._maintenance_task = None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def wait_for_background_cleanup(
        self, timeout: float = BACKGROUND_CLEANUP_TIMEOUT_SECONDS
    ) -> bool:
        """Drain the provider's deferred cleanup workers within a bound.

        The wait never cancels a pending reaper: the reaper owns the job
        directory and has to survive until its worker thread stopped.  The
        caller that owns the process lifecycle decides the bound, so a stuck
        provider thread cannot hold the shutdown open.
        """
        try:
            return await self.provider.wait_for_background_cleanup(timeout=timeout)
        except Exception as exc:
            logger.warning(
                "Media background cleanup failed: %s",
                summarize_exception(exc, label="media_background_cleanup"),
            )
            return False

    async def save_cookie_file(self, platform: str, file_id: str, actor_id: int) -> None:
        """Download and encrypt an administrator cookie inside the service boundary."""
        if self.bot is None:
            raise CookieVaultError("MEDIA_SERVICE_UNAVAILABLE")
        telegram_file = await self.bot.get_file(file_id)
        content = bytes(await telegram_file.download_as_bytearray())
        self.cookie_vault.save(platform, content)
        if self.mongo_available:
            await self.database.add_audit_log(int(actor_id), "media_cookie_saved", platform=platform)

    async def submit(
        self,
        url: str,
        chat_id: int,
        user_id: int,
        status_message_id: int,
        chat_type: str | None = None,
    ) -> tuple[str, bool]:
        if self.maintenance:
            raise ValueError("maintenance")
        normalized = normalize_media_url(url)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        now = time.monotonic()
        recent = self._request_times[user_id]
        while recent and now - recent[0] > 60:
            recent.popleft()
        if len(recent) >= 10:
            raise ValueError("rate_limited")
        recent.append(now)
        payload = {
            "url": normalized,
            "url_hash": digest,
            "chat_id": chat_id,
            "user_id": user_id,
            "status_message_id": status_message_id,
            "chat_type": str(chat_type or "private"),
            "source": platform_for_url(normalized),
        }
        return await self.queue.enqueue(payload)

    async def _process_job(self, payload: Dict[str, Any]):
        user_limit = self._user_limits[payload["user_id"]]
        chat_limit = self._chat_limits[payload["chat_id"]]
        async with user_limit, chat_limit:
            async with self._media_inflight_lock(str(payload.get("url_hash") or "unknown")):
                await self._process_job_inner(payload)

    @asynccontextmanager
    async def _media_inflight_lock(self, url_hash: str):
        """Coalesce identical active downloads across users and workers.

        A per-chat queue deduplication key prevents accidental duplicate jobs
        for one user, but it cannot stop ten chats from downloading the same
        public URL at once. Redis is the cross-process lock in production;
        the instance lock is explicit for the combined/test backend. The
        lock covers Telegram delivery so the next waiter can use the freshly
        written ``file_id`` cache instead of touching the upstream provider.
        """

        queue = getattr(self, "queue", None)
        redis = getattr(queue, "redis", None)
        lock_name = f"arel:media:inflight:{str(url_hash)[:128]}"
        if redis is not None:
            lock = redis.lock(
                lock_name,
                timeout=max(60, int(getattr(Config, "MEDIA_JOB_TIMEOUT_SECONDS", 20)) + 60),
                blocking_timeout=max(60, int(getattr(Config, "MEDIA_JOB_TIMEOUT_SECONDS", 20)) + 60),
            )
            acquired = await lock.acquire()
            if not acquired:
                raise MediaDownloadError("inflight_busy", "Aynı medya zaten hazırlanıyor.")
            try:
                yield
            finally:
                try:
                    await lock.release()
                except Exception:
                    logger.debug("Media inflight lock release failed", exc_info=True)
            return

        locks = getattr(self, "_media_inflight_locks", None)
        if locks is None:
            locks = {}
            self._media_inflight_locks = locks
        entry = locks.get(lock_name)
        if entry is None:
            entry = {"lock": asyncio.Lock(), "users": 0}
            locks[lock_name] = entry
        entry["users"] += 1
        lock = entry["lock"]
        if lock.locked():
            registry = getattr(self, "metrics_registry", None)
            if registry is not None:
                registry.inc("media_inflight_waiters")
        try:
            async with lock:
                yield
        finally:
            entry["users"] -= 1
            if entry["users"] <= 0 and locks.get(lock_name) is entry:
                locks.pop(lock_name, None)

    async def _process_job_inner(self, payload: Dict[str, Any]):
        chat_id = payload["chat_id"]
        status_id = payload["status_message_id"]
        job_id = payload["job_id"]
        delivery_key = f"media:{job_id}"
        result = None
        used_cookie = False
        started = time.perf_counter()
        progress_task = None
        delivery_claimed = False
        self.metrics_registry.inc("media_jobs_total")
        try:
            # A job whose send boundary is already closed must not run the
            # whole pipeline again: the upload would be refused later and the
            # progress text would replace the honest message of the failed
            # attempt. The claim in front of the upload stays the authority.
            boundary_status = await self._delivery_status(delivery_key)
            if boundary_status in {"sent", "uncertain", "sending"}:
                await self._close_suppressed_job(payload, delivery_key, boundary_status)
                return
            cache_key = "arel:media:cache:" + payload["url_hash"]
            cached_file_id = await self._cache_get(cache_key)
            if cached_file_id:
                if not await self._claim_delivery(delivery_key, job_id=job_id, chat_id=chat_id):
                    await self._close_suppressed_job(payload, delivery_key)
                    return
                delivery_claimed = True
                try:
                    sent = await self.bot.send_video(
                        chat_id=chat_id, video=cached_file_id, reply_to_message_id=status_id,
                        caption="Medya hazir."
                    )
                except Exception as exc:
                    if not self._is_invalid_cached_file_id(exc):
                        raise
                    # Telegram definitively refused the stale reference, so no
                    # upload was delivered. The send boundary goes back to the
                    # claimable state and the download path starts clean; a
                    # provider failure there still allows a later delivery.
                    await self._release_started_delivery(delivery_key)
                    delivery_claimed = False
                    await self._cache_delete(cache_key)
                    self.metrics_registry.inc("media_cache_invalidations")
                else:
                    await self._mark_delivery_sent(delivery_key, sent)
                    await self._edit_status(chat_id, status_id, "Video gonderildi.")
                    await self.job_store.update(job_id, telegram_file_id=cached_file_id, finished_at=time.time())
                    return

            progress_task = asyncio.create_task(
                self._media_progress_loop(chat_id, status_id, job_id),
                name=f"media-progress-{job_id}",
            )
            await self.queue._set_status(payload, "probing")
            request = MediaRequest(
                url=payload["url"],
                job_id=job_id,
                platform=payload.get("source", "unknown"),
                timeout_seconds=Config.MEDIA_JOB_TIMEOUT_SECONDS,
            )
            metadata = await self.providers.probe(request)
            if Config.MEDIA_MAX_VIDEO_DURATION_SECONDS and metadata.duration > Config.MEDIA_MAX_VIDEO_DURATION_SECONDS:
                raise MediaDownloadError(
                    "duration_limit",
                    f"Video {Config.MEDIA_MAX_VIDEO_DURATION_SECONDS} saniyeden uzun",
                )
            await self.job_store.update(
                job_id,
                title=metadata.title,
                duration=metadata.duration,
                file_size=metadata.size,
            )
            result, provider_name = await self.providers.download(request)
            payload["provider"] = provider_name
            await self.job_store.update(
                job_id,
                provider=provider_name,
                duration=result.get("duration", 0),
                file_size=result.get("size", 0),
                output_type=result.get("extension"),
            )
            await self.queue._set_status(payload, "transcoding")
            await self._edit_status(chat_id, status_id, "⚙️ Telegram formatı hazırlanıyor…")
            result = await self.transcoder.prepare(result)
            used_cookie = bool(result.get("used_cookie"))
            caption = f"📹 {(result.get('title') or 'Sosyal medya videosu')[:850]}"
            if not delivery_claimed:
                if not await self._claim_delivery(delivery_key, job_id=job_id, chat_id=chat_id):
                    await self._close_suppressed_job(payload, delivery_key)
                    return
            await self.queue._set_status(payload, "sending")
            with open(result["path"], "rb") as media_file:
                if result["extension"] == ".mp4":
                    sent = await self.bot.send_video(
                        chat_id=chat_id, video=media_file, caption=caption,
                        reply_to_message_id=status_id, supports_streaming=True,
                        read_timeout=45, write_timeout=45, connect_timeout=15,
                    )
                    file_id = sent.video.file_id if sent.video else None
                    if file_id and not used_cookie:
                        await self._cache_set(cache_key, file_id, payload.get("source", "unknown"))
                else:
                    sent = await self.bot.send_document(
                        chat_id=chat_id, document=media_file, caption=caption,
                        reply_to_message_id=status_id,
                        read_timeout=45, write_timeout=45, connect_timeout=15,
                    )
            await self._mark_delivery_sent(delivery_key, sent)
            await self._edit_status(chat_id, status_id, "✅ Medya gönderildi.")
            await self.job_store.update(job_id, telegram_file_id=file_id if result.get("extension") == ".mp4" else None,
                                        finished_at=time.time())
        except asyncio.CancelledError as exc:
            # A cancelled worker leaves the send outcome unknown; the ledger
            # must not keep the boundary in flight for the next worker. The
            # write is shielded, because a second cancellation must not
            # interrupt it and strand the row until the expiry reaper.
            await asyncio.shield(self._mark_delivery_uncertain(delivery_key, exc))
            raise
        except MediaTranscodeError as exc:
            await self._mark_delivery_uncertain(delivery_key, exc)
            await self._report_failure(payload, exc.code, exc)
            raise MediaDownloadError(exc.code, str(exc)) from exc
        except MediaDownloadError as exc:
            await self._mark_delivery_uncertain(delivery_key, exc)
            await self._report_failure(payload, exc.code, exc)
            raise
        except Exception as exc:
            await self._mark_delivery_uncertain(delivery_key, exc)
            logger.error(
                "Media job %s failed (%s; %s)",
                job_id,
                summarize_private_text(payload.get("source"), label="media_source"),
                summarize_exception(exc, label="media_delivery"),
            )
            await self._report_failure(payload, "delivery_failed", exc)
            raise MediaDownloadError("delivery_failed", "Telegram medya gönderimi başarısız") from exc
        finally:
            if progress_task is not None:
                progress_task.cancel()
                await asyncio.gather(progress_task, return_exceptions=True)
            # The provider removes the job tree.  The removal is blocking
            # work, so it runs outside the event loop.
            await asyncio.to_thread(
                self.provider.cleanup, result, job_id, Config.MEDIA_DOWNLOAD_DIR
            )
            elapsed = time.perf_counter() - started
            self.metrics_registry.observe("media_job_seconds", elapsed)
            self.metrics_registry.observe("media_completion_seconds", elapsed)
            if elapsed > Config.MEDIA_COMPLETION_SLO_SECONDS:
                self.metrics_registry.inc("media_completion_slo_breaches")

    async def _claim_delivery(self, delivery_key: str, *, job_id: str, chat_id: int) -> bool:
        """Claim the send boundary so that one job is delivered at most once.

        The claim happens immediately before the Telegram upload. Claiming
        earlier would block a legitimate retry after a provider failure.
        """

        if not await self.delivery_ledger.claim(delivery_key, job_id=job_id, chat_id=chat_id):
            return False
        try:
            started = await self.delivery_ledger.begin_send(delivery_key)
        except Exception:
            # The send never started, so the claim is given back. Otherwise
            # the row would refuse every retry for the next five minutes.
            await self._release_delivery(delivery_key, "send_not_started")
            raise
        if not started:
            # The send never started, so the claim can be given back.
            await self._release_delivery(delivery_key, "send_not_started")
            return False
        return True

    async def _release_delivery(self, delivery_key: str, error: str) -> None:
        """Give a claim back without hiding the original failure."""

        try:
            await self.delivery_ledger.release(delivery_key, error=error)
        except Exception as exc:
            logger.warning(
                "Media delivery ledger release failed: %s",
                summarize_exception(exc, label="media_delivery_ledger"),
            )

    async def _release_started_delivery(self, delivery_key: str) -> None:
        """Return a refused send boundary to the claimable state.

        Only a delivery that definitively did not happen may be released
        here. An unknown outcome stays ``uncertain`` and is never replayed.
        """

        try:
            await self.delivery_ledger.release_started(delivery_key, error="delivery_refused")
        except Exception as exc:
            logger.warning(
                "Media delivery ledger release failed: %s",
                summarize_exception(exc, label="media_delivery_ledger"),
            )

    async def _mark_delivery_sent(self, delivery_key: str, sent: Any) -> None:
        """Record the finished delivery and report a lost boundary record."""

        if not await self.delivery_ledger.mark_sent(delivery_key, getattr(sent, "message_id", None)):
            logger.warning(
                "Media delivery ledger did not record the sent delivery: %s",
                delivery_key,
            )

    async def _close_suppressed_job(self, payload: Dict[str, Any], delivery_key: str, status: str | None = None) -> None:
        """Close a job whose delivery was already claimed by another worker.

        The row must not stay in an in-flight status and the user must not
        keep waiting for a delivery that is no longer in progress. The user
        message must never claim a delivery that did not happen, and the
        honest error text of a failed attempt must survive the suppression.
        """

        await self.job_store.update(
            payload["job_id"],
            status="completed",
            finished_at=time.time(),
        )
        if status is None:
            status = await self._delivery_status(delivery_key)
        message = self._suppressed_delivery_message(status)
        if message is not None:
            await self._edit_status(
                payload["chat_id"], payload["status_message_id"], message
            )

    @staticmethod
    def _suppressed_delivery_message(status: str | None) -> str | None:
        """Describe a suppressed delivery without claiming a delivery.

        ``None`` means the existing message must stay as it is. An uncertain
        boundary already carries the honest error text of the failed attempt,
        and replacing it would hide the operational signal.
        """

        if status == "sent":
            return "✅ Medya gönderildi."
        if status == "sending":
            return "⏳ Medya gönderimi sürüyor; tekrar gönderim yapılmadı."
        if status == "uncertain":
            return None
        return "ℹ️ Medya teslimatı zaten başlatılmıştı; tekrar gönderim yapılmadı."

    async def _delivery_status(self, delivery_key: str) -> str | None:
        """Read the recorded state of a delivery boundary.

        A failed read is not fatal: the claim in front of the upload stays
        the authority and decides whether the delivery may go out.
        """

        try:
            return await self.delivery_ledger.delivery_status(delivery_key)
        except Exception as exc:
            logger.warning(
                "Media delivery ledger read failed: %s",
                summarize_exception(exc, label="media_delivery_ledger"),
            )
            return None

    async def _mark_delivery_uncertain(self, delivery_key: str, error: BaseException) -> None:
        """Record that the Telegram send outcome for this job is unknown."""

        try:
            await self.delivery_ledger.mark_uncertain(delivery_key, type(error).__name__)
        except Exception as exc:
            logger.warning(
                "Media delivery ledger update failed: %s",
                summarize_exception(exc, label="media_delivery_ledger"),
            )

    async def _media_progress_loop(self, chat_id: int, status_id: int, job_id: str) -> None:
        """Keep a slow external provider visible without cancelling the job."""

        try:
            await asyncio.sleep(Config.MEDIA_COMPLETION_SLO_SECONDS)
            await self._edit_status(
                chat_id,
                status_id,
                "⏳ Kaynak beklenenden yavaş; iş iptal edilmedi. Biraz naz yapıyor. "
                f"İş: <code>{job_id}</code>",
            )
            while True:
                await asyncio.sleep(15)
                await self._edit_status(
                    chat_id,
                    status_id,
                    "⏳ Medya hala hazırlanıyor; dış kaynakla pazarlık sürüyor. "
                    f"İş: <code>{job_id}</code>",
                )
        except asyncio.CancelledError:
            return

    async def _cache_get(self, key: str):
        if not self.queue.redis:
            return await self.database.get_media_cache(key.rsplit(":", 1)[-1]) if self.mongo_available else None
        value = await self.queue.redis.get(key)
        if value:
            return value
        return await self.database.get_media_cache(key.rsplit(":", 1)[-1]) if self.mongo_available else None

    async def _cache_set(self, key: str, value: str, platform: str = "unknown"):
        if self.queue.redis:
            await self.queue.redis.set(key, value, ex=24 * 60 * 60)
        if self.mongo_available:
            await self.database.save_media_cache(key.rsplit(":", 1)[-1], value, platform)

    async def _cache_delete(self, key: str) -> None:
        if self.queue.redis:
            await self.queue.redis.delete(key)
        if self.mongo_available and hasattr(self.database, "delete_media_cache"):
            await self.database.delete_media_cache(key.rsplit(":", 1)[-1])

    @staticmethod
    def _is_invalid_cached_file_id(error: Exception) -> bool:
        """Identify Telegram's stale/invalid file reference errors only.

        Only a definite "this reference cannot be used" answer may release
        the send boundary and trigger a re-download. A broad BadRequest match
        would release the boundary for unrelated errors and turn them into a
        full re-download and re-send instead of failing the job.
        """
        text = str(error).lower()
        markers = (
            "wrong file identifier",
            "file reference",
            "file_id_invalid",
            "invalid file_id",
            "file id is invalid",
            "file_id is invalid",
        )
        if any(marker in text for marker in markers):
            return True
        try:
            from telegram.error import BadRequest
        except ImportError:
            return False
        if not isinstance(error, BadRequest):
            return False
        # Telegram names the unusable reference explicitly. An unrelated
        # BadRequest keeps failing the job instead of releasing the send
        # boundary.
        invalid_words = ("invalid", "expired", "wrong", "not found", "unknown")
        reference_words = ("file identifier", "file_id", "file id", "file reference")
        return (
            any(word in text for word in invalid_words)
            and any(word in text for word in reference_words)
        )

    async def _edit_status(self, chat_id: int, message_id: int, text: str):
        try:
            await self.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML")
        except Exception:
            pass

    @staticmethod
    def _is_group_payload(payload: Dict[str, Any]) -> bool:
        return str(payload.get("chat_type") or "").casefold() in {"group", "supergroup"}

    @staticmethod
    def _read_attempt(payload: Dict[str, Any]) -> int:
        """Read the attempt counter; a malformed value must not kill the job."""
        raw = payload.get("attempt", 0)
        try:
            attempt = int(raw)
        except (TypeError, ValueError):
            logger.warning(
                "Media job %s has an unusable attempt counter; the retry check starts from the first attempt",
                payload.get("job_id"),
            )
            return 0
        return max(0, attempt)

    def _job_will_retry(self, payload: Dict[str, Any], error_code: str) -> bool:
        policy = getattr(getattr(self, "queue", None), "retry_policy", None)
        should_retry = getattr(policy, "should_retry", None)
        if callable(should_retry):
            return bool(should_retry(error_code, self._read_attempt(payload)))
        return False

    async def _clear_status(self, chat_id: int, message_id: int) -> None:
        """Remove a group progress message instead of broadcasting an error."""

        if self.bot is None:
            return
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
            return
        except Exception:
            # Deletion can fail when a message is already gone or the bot has
            # insufficient rights. Keep the fallback neutral and bounded.
            await self._edit_status(chat_id, message_id, "Medya işlemi sonlandı.")

    async def _report_failure(self, payload: Dict[str, Any], error_code: str, error: Exception) -> None:
        """Keep media failures private while preserving bounded retries."""

        chat_id = int(payload["chat_id"])
        status_id = int(payload["status_message_id"])
        if self._is_group_payload(payload):
            if self._job_will_retry(payload, error_code):
                await self._edit_status(
                    chat_id,
                    status_id,
                    "Medya kaynağı yanıt vermedi; başka bir yöntem deneniyor…",
                )
                self.metrics_registry.inc("media_group_failures_suppressed")
            else:
                await self._clear_status(chat_id, status_id)
                self.metrics_registry.inc("media_group_failures_cleared")
            return
        await self._edit_status(
            chat_id,
            status_id,
            f"❌ {media_delivery_error_message(error)}\nİş: <code>{payload['job_id']}</code>",
        )

    async def get_status(self, job_id: str):
        return await self.queue.get_job(job_id)

    async def get_metrics(self):
        metrics = await self.queue.metrics()
        metrics["maintenance"] = self.maintenance
        metrics.update(self.metrics_registry.snapshot())
        providers = await self.providers.health()
        metrics["providers"] = [item.__dict__ for item in providers]
        if self.mongo_available:
            for item in providers:
                try:
                    await self.database.save_provider_health(item.__dict__)
                except Exception:
                    logger.debug("Provider health persistence failed", exc_info=True)
        return metrics

    async def clear_cache(self) -> int:
        removed = 0
        if self.queue.redis:
            keys = [key async for key in self.queue.redis.scan_iter(match="arel:media:cache:*")]
            if keys:
                removed += int(await self.queue.redis.delete(*keys))
        if self.mongo_available and self.database.media_cache is not None:
            result = await self.database.media_cache.delete_many({})
            removed += int(result.deleted_count)
        return removed

    def set_cookie_enabled(self, enabled: bool):
        self.cookie_enabled = enabled
        self.provider.cookie_enabled = enabled
        path = Path(Config.MEDIA_COOKIE_STORAGE_DIR).parent / "media_settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        current = self._read_settings()
        current["cookie_enabled"] = enabled
        path.write_text(json.dumps(current), encoding="utf-8")

    def set_maintenance(self, enabled: bool):
        self.maintenance = bool(enabled)
        path = Path(Config.MEDIA_COOKIE_STORAGE_DIR).parent / "media_settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        current = self._read_settings()
        current["maintenance"] = self.maintenance
        path.write_text(json.dumps(current), encoding="utf-8")

    @staticmethod
    def _read_settings() -> dict:
        path = Path(Config.MEDIA_COOKIE_STORAGE_DIR).parent / "media_settings.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def load_persisted_settings(self):
        path = Path(Config.MEDIA_COOKIE_STORAGE_DIR).parent / "media_settings.json"
        try:
            settings = self._read_settings()
            self.cookie_enabled = bool(settings.get("cookie_enabled", False))
            self.maintenance = bool(settings.get("maintenance", False))
        except (OSError, ValueError):
            self.cookie_enabled = Config.MEDIA_COOKIE_ENABLED
        self.provider.cookie_enabled = self.cookie_enabled

    def cookie_status(self):
        return {platform: self.cookie_vault.has(platform) for platform in sorted(self.cookie_vault.PLATFORMS)}
