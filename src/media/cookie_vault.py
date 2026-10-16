"""Encrypted administrator-managed yt-dlp cookie files."""

import base64
import hashlib
import json
import os
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class CookieVaultError(ValueError):
    pass


class CookieVault:
    PLATFORMS = {"instagram", "tiktok", "youtube", "x", "facebook", "reddit"}
    PLATFORM_COOKIE_DOMAINS = {
        "instagram": {"instagram.com"},
        "tiktok": {"tiktok.com"},
        "youtube": {"youtube.com", "google.com", "googleusercontent.com"},
        "x": {"x.com", "twitter.com"},
        "facebook": {"facebook.com", "fbcdn.net"},
        "reddit": {"reddit.com"},
    }
    MAX_COOKIE_COUNT = 5000

    def __init__(self, storage_dir: str, secret: str | None):
        self.storage_dir = Path(storage_dir)
        self.secret = secret

    def _fernet(self) -> Fernet:
        if not self.secret or len(self.secret) < 32:
            raise CookieVaultError("ENCRYPTION_KEY must contain at least 32 characters")
        key = base64.urlsafe_b64encode(hashlib.sha256(self.secret.encode("utf-8")).digest())
        return Fernet(key)

    @staticmethod
    def _platform(platform: str) -> str:
        value = platform.strip().lower()
        if value not in CookieVault.PLATFORMS:
            raise CookieVaultError("Desteklenmeyen platform")
        return value

    def _path(self, platform: str) -> Path:
        return self.storage_dir / f"{self._platform(platform)}.cookies.enc"

    @classmethod
    def _domain_allowed(cls, platform: str, domain: str) -> bool:
        clean = domain.strip().lower().lstrip(".").rstrip(".")
        if not clean or not re.fullmatch(r"[a-z0-9.-]+", clean) or ".." in clean:
            return False
        return any(clean == root or clean.endswith("." + root) for root in cls.PLATFORM_COOKIE_DOMAINS[platform])

    @classmethod
    def _normalize_json_export(cls, platform: str, decoded: str) -> bytes:
        try:
            cookies = json.loads(decoded)
        except (json.JSONDecodeError, TypeError) as exc:
            raise CookieVaultError("Cookie JSON okunamadÄ±") from exc
        if not isinstance(cookies, list) or not cookies or len(cookies) > cls.MAX_COOKIE_COUNT:
            raise CookieVaultError("Cookie JSON listesi geÃ§ersiz veya Ã§ok bÃ¼yÃ¼k")
        rows = ["# Netscape HTTP Cookie File"]
        for cookie in cookies:
            if not isinstance(cookie, dict):
                raise CookieVaultError("Cookie kaydÄ± geÃ§ersiz")
            domain = str(cookie.get("domain") or "").strip().lower()
            if not cls._domain_allowed(platform, domain):
                raise CookieVaultError("Cookie seÃ§ilen platforma ait deÄŸil")
            name = str(cookie.get("name") or "")
            value = str(cookie.get("value") or "")
            path = str(cookie.get("path") or "/")
            if (
                not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                or not path.startswith("/")
                or any(ch in value for ch in "\t\r\n\x00")
                or any(ch in path for ch in "\t\r\n\x00")
            ):
                raise CookieVaultError("Cookie kaydÄ± geÃ§ersiz")
            host_only = bool(cookie.get("hostOnly", not domain.startswith(".")))
            include_subdomains = "FALSE" if host_only else "TRUE"
            secure = "TRUE" if bool(cookie.get("secure", False)) else "FALSE"
            try:
                expiry = 0 if bool(cookie.get("session", False)) else int(float(cookie.get("expirationDate") or 0))
            except (TypeError, ValueError, OverflowError) as exc:
                raise CookieVaultError("Cookie sÃ¼resi geÃ§ersiz") from exc
            if expiry < 0:
                raise CookieVaultError("Cookie sÃ¼resi geÃ§ersiz")
            row_domain = domain if domain.startswith(".") or host_only else f".{domain}"
            if bool(cookie.get("httpOnly", False)):
                row_domain = f"#HttpOnly_{row_domain}"
            rows.append("\t".join((row_domain, include_subdomains, path, secure, str(expiry), name, value)))
        return ("\n".join(rows) + "\n").encode("utf-8")

    @classmethod
    def _validate_netscape(cls, platform: str, decoded: str) -> bytes:
        if not (decoded.startswith("# Netscape HTTP Cookie File") or decoded.startswith("# HTTP Cookie File")):
            raise CookieVaultError("Cookie biÃ§imi desteklenmiyor")
        rows = ["# Netscape HTTP Cookie File"]
        cookies = 0
        for line in decoded.splitlines():
            if not line.strip() or (line.startswith("#") and not line.startswith("#HttpOnly_")):
                continue
            http_only = line.startswith("#HttpOnly_")
            fields = line[len("#HttpOnly_"):].split("\t") if http_only else line.split("\t")
            if len(fields) != 7:
                raise CookieVaultError("Netscape cookie satÄ±rÄ± geÃ§ersiz")
            domain, include_subdomains, path, secure, expiry, name, value = fields
            if (
                not cls._domain_allowed(platform, domain)
                or include_subdomains not in {"TRUE", "FALSE"}
                or secure not in {"TRUE", "FALSE"}
                or not path.startswith("/")
                or not expiry.isdigit()
                or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                or any(ch in value for ch in "\r\n\x00")
            ):
                raise CookieVaultError("Netscape cookie satÄ±rÄ± geÃ§ersiz")
            prefix = "#HttpOnly_" if http_only else ""
            rows.append("\t".join((prefix + domain, include_subdomains, path, secure, expiry, name, value)))
            cookies += 1
            if cookies > cls.MAX_COOKIE_COUNT:
                raise CookieVaultError("Cookie sayÄ±sÄ± sÄ±nÄ±rÄ± aÅŸÄ±yor")
        if cookies == 0:
            raise CookieVaultError("Cookie kaydÄ± bulunamadÄ±")
        return ("\n".join(rows) + "\n").encode("utf-8")

    def save(self, platform: str, content: bytes) -> None:
        platform = self._platform(platform)
        if len(content) > 256 * 1024:
            raise CookieVaultError("Cookie dosyasÄ± 256 KiB sÄ±nÄ±rÄ±nÄ± aÅŸÄ±yor")
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CookieVaultError("Cookie dosyasÄ± UTF-8 olmalÄ±") from exc
        if decoded.lstrip().startswith("["):
            normalized = self._normalize_json_export(platform, decoded)
        else:
            normalized = self._validate_netscape(platform, decoded)
        path = self._path(platform)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._fernet().encrypt(normalized))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def load_to_file(self, platform: str, target_path: str) -> bool:
        source = self._path(platform)
        if not source.exists():
            return False
        try:
            plaintext = self._fernet().decrypt(source.read_bytes())
        except InvalidToken as exc:
            raise CookieVaultError("Cookie kasasÄ± aÃ§Ä±lamadÄ±; ENCRYPTION_KEY deÄŸiÅŸmiÅŸ olabilir") from exc
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(plaintext)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        return True

    def delete(self, platform: str) -> bool:
        path = self._path(platform)
        if not path.exists():
            return False
        path.unlink()
        return True

    def has(self, platform: str) -> bool:
        return self._path(platform).exists()
