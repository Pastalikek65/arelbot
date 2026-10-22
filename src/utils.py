import os
import time
import logging
import re
import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Any
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)

# The periodic sweep runs every CLEANUP_INTERVAL_SECONDS and reclaims the
# entries that are older than CLEANUP_MAX_AGE_SECONDS.
CLEANUP_INTERVAL_SECONDS = 3600
CLEANUP_MAX_AGE_SECONDS = 3600

# Global thread pool for blocking operations
_thread_pool = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="arel_blocking"
)


async def run_async(func: Callable, *args, **kwargs) -> Any:
    """
    Blocking bir fonksiyonu thread pool'da çalıştırır.

    PIL, moviepy, matplotlib gibi async olmayan kütüphaneler
    için event loop'u bloklamamak adına kullanılır.

    Kullanım:
        img = await run_async(Image.open, "path.jpg")
        chart = await run_async(plt.savefig, buf, format='png')
    """
    loop = asyncio.get_running_loop()
    fn = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(_thread_pool, fn)

def clean_text_for_tts(text: str) -> str:
    """
    Cleans text for TTS by removing emojis but keeping punctuation.
    """
    if not text:
        return ""
    
    # Remove emojis using a regex pattern that covers most emoji ranges
    # This keeps standard alphanumeric characters and punctuation
    emoji_pattern = re.compile(
        "["
        "\U00010000-\U0010ffff"  # Supplemental Planes
        "\u2600-\u27bf"          # Miscellaneous Symbols and Dingbats
        "\u2300-\u23ff"          # Miscellaneous Technical
        "]+", flags=re.UNICODE
    )
    
    cleaned = emoji_pattern.sub(r'', text)
    
    # Also clean up multiple spaces that might result from removal
    cleaned = " ".join(cleaned.split())
    
    return cleaned

def cleanup_old_files(directory: str, max_age_seconds: int = 3600) -> int:
    """
    Deletes stale files and stale directories below ``directory``.

    Media artifacts live in ``<download dir>/<job id>/`` directories, so a
    sweeper that only unlinks files never reclaims them.  Every reclaimed
    file and every reclaimed directory is counted in the return value.

    The sweep stays inside the directory it was given.  A symlink, a
    Windows junction and a mount point are never followed, and an entry
    whose resolved path leaves the root is never removed, so a link inside
    the spool cannot delete files outside it.  A directory is only removed
    when it is empty afterwards: an in-flight download keeps writing into
    its own job directory and must not lose it.
    """
    if not directory or not os.path.isdir(directory):
        return 0

    root = os.path.abspath(directory)
    now = time.time()
    count = 0
    try:
        count = _sweep_directory(root, root, now, max_age_seconds)
    except Exception as e:
        logger.error("Error during cleanup: %s", summarize_exception(e, label="file_cleanup"))

    if count > 0:
        logger.info("Cleanup removed entries count=%s", count)
    return count


def cleanup_directories() -> tuple[str, ...]:
    """Return the configured directories the periodic sweep has to reclaim.

    The producers of temporary artifacts read their location from the
    configuration, so the sweep has to read the same values.  A hard coded
    path would leave the artifacts of a relocated directory behind.
    """
    from config import Config

    targets = (
        Config.MEDIA_DOWNLOAD_DIR,
        Config.RAG_UPLOAD_DIR,
        Config.TTS_OUTPUT_DIR,
        Config.QUOTE_OUTPUT_DIR,
        Config.STICKER_TEMP_DIR,
        Config.ATTACHMENT_TEMP_DIR,
        Config.STICKER_TAG_TEMP_DIR,
    )
    directories: list[str] = []
    for target in targets:
        value = str(target or "").strip()
        if value and value not in directories:
            directories.append(value)
    return tuple(directories)


def _sweep_directory(path: str, root: str, now: float, max_age_seconds: int) -> int:
    """Remove stale entries below ``path`` and count what was reclaimed."""
    count = 0
    try:
        entries = list(os.scandir(path))
    except OSError:
        return 0

    for entry in entries:
        child = entry.path
        try:
            if _leaves_root(child, root):
                # A symlink, a Windows junction or a mount point points
                # somewhere else.  The sweeper never follows it and never
                # removes it, so it cannot reach outside its own root.
                continue
            if entry.is_dir(follow_symlinks=False):
                # The age of the directory is read before the sweep: removing
                # a file inside it refreshes its own modification time.
                stale_before = _is_stale(child, now, max_age_seconds)
                count += _sweep_directory(child, root, now, max_age_seconds)
                if stale_before and _is_empty_directory(child):
                    if _remove_directory(child, root):
                        count += 1
                continue
            if _is_stale(child, now, max_age_seconds) and _remove_file(child, root):
                count += 1
        except OSError:
            continue
    return count


def _leaves_root(path: str, root: str) -> bool:
    """Report whether an entry points outside the directory being swept.

    A Windows junction is not a symlink, so ``os.path.islink`` alone does
    not catch it and a tree removal would traverse it.  The resolved path
    catches a junction and every other reparse point, and
    ``os.path.ismount`` catches a mount point inside the spool.
    """
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None and isjunction(path):
        return True
    if os.path.ismount(path):
        return True
    return not _is_below(root, path)


def _is_stale(path: str, now: float, max_age_seconds: int) -> bool:
    """Report whether the entry itself is older than the allowed age."""
    try:
        modified = os.lstat(path).st_mtime
    except OSError:
        return False
    return now - modified > max_age_seconds


def _is_below(root: str, path: str) -> bool:
    """Keep every deletion inside the requested root directory.

    The comparison uses the resolved path of both sides: a lexical
    comparison cannot see that an entry points somewhere else.
    """
    try:
        root_real = os.path.normcase(os.path.realpath(root))
        path_real = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([root_real, path_real]) == root_real
    except (OSError, ValueError):
        return False


def _is_empty_directory(path: str) -> bool:
    """Report whether a directory holds no entries any more."""
    try:
        return not os.listdir(path)
    except OSError:
        return False


def _remove_file(path: str, root: str) -> bool:
    """Remove one stale file inside the root directory."""
    if not _is_below(root, path):
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def _remove_directory(path: str, root: str) -> bool:
    """Remove one empty stale directory inside the root directory."""
    if os.path.abspath(path) == root or not _is_below(root, path):
        return False
    try:
        os.rmdir(path)
        return True
    except OSError:
        return False


def tr_upper(text: str) -> str:
    """Turkish aware upper()"""
    if not text:
        return ""
    return text.translate(str.maketrans({"i": "\u0130", "\u0131": "I"})).upper()

def tr_lower(text: str) -> str:
    """Turkish aware lower()"""
    if not text:
        return ""
    return text.translate(str.maketrans({"\u0130": "i", "I": "\u0131"})).lower()
