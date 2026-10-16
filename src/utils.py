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

# Global thread pool for blocking operations
_thread_pool = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="arel_blocking"
)


async def run_async(func: Callable, *args, **kwargs) -> Any:
    """
    Blocking bir fonksiyonu thread pool'da Ã§alÄ±ÅŸtÄ±rÄ±r.

    PIL, moviepy, matplotlib gibi async olmayan kÃ¼tÃ¼phaneler
    iÃ§in event loop'u bloklamamak adÄ±na kullanÄ±lÄ±r.

    KullanÄ±m:
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

def cleanup_old_files(directory: str, max_age_seconds: int = 3600):
    """
    Deletes files in a directory that are older than max_age_seconds.
    """
    if not os.path.exists(directory):
        return

    now = time.time()
    count = 0
    try:
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            if os.path.isfile(filepath):
                file_age = now - os.path.getmtime(filepath)
                if file_age > max_age_seconds:
                    os.remove(filepath)
                    count += 1
        
        if count > 0:
            logger.info("Cleanup removed files count=%s", count)
    except Exception as e:
        logger.error("Error during cleanup: %s", summarize_exception(e, label="file_cleanup"))

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
