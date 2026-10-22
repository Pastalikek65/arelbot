"""
SudoManager - Yetki ve kara liste yönetimi.

JSON dosyası veya MongoDB backend ile çalışabilir.
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
    """Yetki ve kara liste yöneticisi."""

    DATA_FILE = "sudo_data.json"

    def __init__(self, owner_id: int, db: Optional['Database'] = None):
        self.owner_id = owner_id
        self.db = db
        self.sudos = set([owner_id])
        self.blacklist_users = set()
        self.blacklist_groups = set()
        
        # JSON'dan yükle (backward compatibility)
        self._load_from_json()

    def _load_from_json(self):
        """JSON dosyasından veri yükler (geriye dönük uyumluluk)."""
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
        """JSON dosyasına veri yazar (geriye dönük uyumluluk)."""
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
        """Kullanıcı owner mı?"""
        return user_id == self.owner_id

    def is_sudo(self, user_id: int) -> bool:
        """Kullanıcı sudo mu?"""
        return user_id in self.sudos

    def is_blacklisted(self, user_id: int, chat_id: int = None) -> bool:
        """Kullanıcı veya grup kara listede mi?"""
        if user_id in self.blacklist_users:
            return True
        if chat_id and chat_id in self.blacklist_groups:
            return True
        return False

    def add_sudo(self, user_id: int):
        """Sudo kullanıcı ekler."""
        self.sudos.add(user_id)
        self._save_to_json()

    def remove_sudo(self, user_id: int):
        """Sudo kullanıcı siler."""
        if user_id != self.owner_id:
            self.sudos.discard(user_id)
            self._save_to_json()

    def ban_user(self, user_id: int):
        """Kullanıcıyı kara listeye ekler."""
        self.blacklist_users.add(user_id)
        self._save_to_json()

    def unban_user(self, user_id: int):
        """Kullanıcıyı kara listeden çıkarır."""
        self.blacklist_users.discard(user_id)
        self._save_to_json()

    def ban_group(self, chat_id: int):
        """Grubu kara listeye ekler."""
        self.blacklist_groups.add(chat_id)
        self._save_to_json()

    def unban_group(self, chat_id: int):
        """Grubu kara listeden çıkarır."""
        self.blacklist_groups.discard(chat_id)
        self._save_to_json()
