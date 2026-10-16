"""
SudoManager - Yetki ve kara liste yÃ¶netimi.

JSON dosyasÄ± veya MongoDB backend ile Ã§alÄ±ÅŸabilir.
"""
import json
import os
import logging
from src.security.log_redaction import summarize_exception
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .database import Database

logger = logging.getLogger(__name__)


class SudoManager:
    """Yetki ve kara liste yÃ¶neticisi."""

    DATA_FILE = "sudo_data.json"

    def __init__(self, owner_id: int, db: Optional['Database'] = None):
        self.owner_id = owner_id
        self.db = db
        self.sudos = set([owner_id])
        self.blacklist_users = set()
        self.blacklist_groups = set()
        
        # JSON'dan yÃ¼kle (backward compatibility)
        self._load_from_json()

    def _load_from_json(self):
        """JSON dosyasÄ±ndan veri yÃ¼kler (geriye dÃ¶nÃ¼k uyumluluk)."""
        if os.path.exists(self.DATA_FILE):
            try:
                with open(self.DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.sudos.update(data.get("sudos", []))
                    self.blacklist_users.update(data.get("blacklist_users", []))
                    self.blacklist_groups.update(data.get("blacklist_groups", []))
                logger.info("Sudo data loaded from JSON.")
            except Exception as e:
                logger.error("Failed to load sudo data from JSON: %s", summarize_exception(e, label="sudo_load"))

    def _save_to_json(self):
        """JSON dosyasÄ±na veri yazar (geriye dÃ¶nÃ¼k uyumluluk)."""
        try:
            with open(self.DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    "sudos": list(self.sudos),
                    "blacklist_users": list(self.blacklist_users),
                    "blacklist_groups": list(self.blacklist_groups)
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Failed to save sudo data to JSON: %s", summarize_exception(e, label="sudo_save"))

    def is_owner(self, user_id: int) -> bool:
        """KullanÄ±cÄ± owner mÄ±?"""
        return user_id == self.owner_id

    def is_sudo(self, user_id: int) -> bool:
        """KullanÄ±cÄ± sudo mu?"""
        return user_id in self.sudos

    def is_blacklisted(self, user_id: int, chat_id: int = None) -> bool:
        """KullanÄ±cÄ± veya grup kara listede mi?"""
        if user_id in self.blacklist_users:
            return True
        if chat_id and chat_id in self.blacklist_groups:
            return True
        return False

    def add_sudo(self, user_id: int):
        """Sudo kullanÄ±cÄ± ekler."""
        self.sudos.add(user_id)
        self._save_to_json()

    def remove_sudo(self, user_id: int):
        """Sudo kullanÄ±cÄ± siler."""
        if user_id != self.owner_id:
            self.sudos.discard(user_id)
            self._save_to_json()

    def ban_user(self, user_id: int):
        """KullanÄ±cÄ±yÄ± kara listeye ekler."""
        self.blacklist_users.add(user_id)
        self._save_to_json()

    def unban_user(self, user_id: int):
        """KullanÄ±cÄ±yÄ± kara listeden Ã§Ä±karÄ±r."""
        self.blacklist_users.discard(user_id)
        self._save_to_json()

    def ban_group(self, chat_id: int):
        """Grubu kara listeye ekler."""
        self.blacklist_groups.add(chat_id)
        self._save_to_json()

    def unban_group(self, chat_id: int):
        """Grubu kara listeden Ã§Ä±karÄ±r."""
        self.blacklist_groups.discard(chat_id)
        self._save_to_json()
