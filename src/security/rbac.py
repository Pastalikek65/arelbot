"""
RBAC (Role-Based Access Control) â€” Yetki kontrol sistemi.

HiyerarÅŸi:
    OWNER (3) > ADMIN (2) > MODERATOR (1) > USER (0)

Permission'lar dekoratÃ¶rlerle kontrol edilir.

KullanÄ±m:
    @rbac.require(Role.ADMIN)
    async def ban_user(self, update, context):
        ...
"""
import logging
from enum import IntEnum
from functools import wraps
from typing import Optional, Set, Dict, Callable

logger = logging.getLogger(__name__)


class Role(IntEnum):
    """Yetki seviyeleri â€” deÄŸer bÃ¼yÃ¼dÃ¼kÃ§e yetki artar."""
    USER = 0
    MODERATOR = 1
    ADMIN = 2
    OWNER = 3


class Permission(IntEnum):
    """DetaylÄ± izinler."""
    MANAGE_GROUPS = 1
    MANAGE_USERS = 2
    MANAGE_SUDOS = 3
    BROADCAST = 4
    VIEW_STATS = 5
    MANAGE_SETTINGS = 6
    BAN_UNBAN = 7
    SEE_ALL_CHATS = 8
    MANAGE_BOT_CONFIG = 9


ROLE_PERMISSIONS: Dict[Role, Set[Permission]] = {
    Role.OWNER: {
        Permission.MANAGE_GROUPS,
        Permission.MANAGE_USERS,
        Permission.MANAGE_SUDOS,
        Permission.BROADCAST,
        Permission.VIEW_STATS,
        Permission.MANAGE_SETTINGS,
        Permission.BAN_UNBAN,
        Permission.SEE_ALL_CHATS,
        Permission.MANAGE_BOT_CONFIG,
    },
    Role.ADMIN: {
        Permission.MANAGE_GROUPS,
        Permission.MANAGE_USERS,
        Permission.MANAGE_SUDOS,
        Permission.BROADCAST,
        Permission.VIEW_STATS,
        Permission.MANAGE_SETTINGS,
        Permission.BAN_UNBAN,
        Permission.SEE_ALL_CHATS,
    },
    Role.MODERATOR: {
        Permission.MANAGE_GROUPS,
        Permission.MANAGE_USERS,
        Permission.VIEW_STATS,
        Permission.MANAGE_SETTINGS,
        Permission.BAN_UNBAN,
    },
    Role.USER: set(),
}


class RBACManager:
    """
    Merkezi yetki yÃ¶neticisi.

    SudoManager ile entegre Ã§alÄ±ÅŸÄ±r.
    """

    def __init__(self, owner_id: int):
        self.owner_id = owner_id
        self._roles: Dict[int, Role] = {}
        self._roles[owner_id] = Role.OWNER

    def get_role(self, user_id: int) -> Role:
        """KullanÄ±cÄ±nÄ±n rolÃ¼nÃ¼ dÃ¶ndÃ¼rÃ¼r."""
        if user_id == self.owner_id:
            return Role.OWNER
        return self._roles.get(user_id, Role.USER)

    def set_role(self, user_id: int, role: Role, requester_id: int) -> bool:
        """
        KullanÄ±cÄ±ya rol atar.

        - OWNER baÅŸkasÄ±na ADMIN atayabilir
        - ADMIN baÅŸkasÄ±na MODERATOR atayabilir
        - Kendi rolÃ¼nden yÃ¼ksek atayamaz
        - OWNER deÄŸiÅŸtirilemez
        """
        if user_id == self.owner_id:
            logger.warning(f"Cannot change owner's role")
            return False

        requester_role = self.get_role(requester_id)

        if requester_role < Role.ADMIN:
            logger.warning("Role assignment denied: requester lacks permission")
            return False

        if role >= requester_role:
            logger.warning("Role assignment denied: requested role exceeds requester")
            return False

        self._roles[user_id] = role
        logger.info("Role assignment succeeded: role=%s", role.name)
        return True

    def remove_role(self, user_id: int, requester_id: int) -> bool:
        """KullanÄ±cÄ±nÄ±n rolÃ¼nÃ¼ kaldÄ±rÄ±r."""
        if user_id == self.owner_id:
            return False

        requester_role = self.get_role(requester_id)
        if requester_role < Role.ADMIN:
            return False

        self._roles.pop(user_id, None)
        logger.info("Role removal succeeded")
        return True

    def has_permission(self, user_id: int, permission: Permission) -> bool:
        """KullanÄ±cÄ±nÄ±n belirli bir permission'Ä± var mÄ±?"""
        role = self.get_role(user_id)
        return permission in ROLE_PERMISSIONS.get(role, set())

    def has_role(self, user_id: int, min_role: Role) -> bool:
        """Minimum role kontrolÃ¼."""
        return self.get_role(user_id) >= min_role

    def require(self, min_role: Role) -> Callable:
        """
        Decorator: Belirli bir rolden dÃ¼ÅŸÃ¼kse iÅŸlemi engeller.

        KullanÄ±m:
            @rbac.require(Role.ADMIN)
            async def admin_only_command(self, update, context):
                ...
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(self_or_handler, update, context=None, *args, **kwargs):
                if hasattr(update, 'effective_user'):
                    user_id = update.effective_user.id
                elif isinstance(update, int):
                    user_id = update
                else:
                    return None

                if not self.has_role(user_id, min_role):
                    logger.warning("Permission denied: required_role=%s", min_role.name)
                    if context and hasattr(update, 'effective_message'):
                        await update.effective_message.reply_text(
                            f"â›” Bu iÅŸlem iÃ§in {min_role.name} yetkisi gerekli.",
                            parse_mode='HTML'
                        )
                    return None

                return await func(self_or_handler, update, context, *args, **kwargs)
            return wrapper
        return decorator

    def list_roles(self) -> Dict[int, str]:
        """TÃ¼m rol atamalarÄ±nÄ± listeler."""
        result = {}
        for uid, role in self._roles.items():
            result[uid] = role.name
        return result

    def get_all_with_min_role(self, min_role: Role) -> Set[int]:
        """Belirli bir rolden yÃ¼ksek tÃ¼m kullanÄ±cÄ±larÄ± dÃ¶ndÃ¼rÃ¼r."""
        result = set()
        for uid, role in self._roles.items():
            if role >= min_role:
                result.add(uid)
        return result
