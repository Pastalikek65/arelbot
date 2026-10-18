
import logging
from telegram import Bot
from config import Config
from src.security.log_redaction import summarize_exception
import asyncio

logger = logging.getLogger(__name__)

class UnifiedLogger:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(UnifiedLogger, cls).__new__(cls)
            cls._instance.bot = Bot(token=Config.TELEGRAM_TOKEN)
        return cls._instance

    def __init__(self):
        self.last_log_time = 0
        self.log_cooldown = 2 # Minimum seconds between logs

    async def log(self, message: str, topic_id: int = None, photo_path: str = None):
        """
        Logs a message (and optional photo) to the configured LOG_CHANNEL_ID.
        Implements a simple cooldown to avoid flood control.
        """
        import time
        current_time = time.time()
        if current_time - self.last_log_time < self.log_cooldown:
             logger.warning(f"UnifiedLogger: Cooldown active, skipping log message.")
             return

        try:
            target_chat_id = Config.LOG_CHANNEL_ID
            
            # If topic_id is 1 (General), typically we send as None to post in main
            if topic_id == 1:
                topic_id = None
                
            if photo_path:
                with open(photo_path, 'rb') as f:
                    await self.bot.send_photo(
                        chat_id=target_chat_id,
                        photo=f,
                        caption=message,
                        parse_mode='Markdown',
                        message_thread_id=topic_id
                    )
            else:
                await self.bot.send_message(
                    chat_id=target_chat_id,
                    text=message,
                    parse_mode='Markdown',
                    message_thread_id=topic_id
                )
            self.last_log_time = time.time()
        except Exception as e:
            logger.error("UnifiedLogger failed to log: %s", summarize_exception(e, label="unified_logger"))
