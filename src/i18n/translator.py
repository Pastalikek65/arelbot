"""Basit i18n çeviri katmanı."""
import json
import logging
from pathlib import Path
from typing import Any, Dict
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)


class Translator:
    def __init__(self, default_language: str = "tr"):
        self.default_language = default_language
        self._messages: Dict[str, Dict[str, str]] = {}
        self._load_messages()

    def _load_messages(self):
        base = Path(__file__).parent
        for lang_file in base.glob("*.json"):
            try:
                self._messages[lang_file.stem] = json.loads(lang_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(
                    "Dil dosyası okunamadı: %s (%s)",
                    lang_file.name,
                    summarize_exception(exc, label="translation_file"),
                )

    def t(self, key: str, language: str | None = None, **kwargs: Any) -> str:
        lang = language or self.default_language
        template = (
            self._messages.get(lang, {}).get(key)
            or self._messages.get(self.default_language, {}).get(key)
            or key
        )
        try:
            return template.format(**kwargs)
        except Exception:
            return template

    def available_languages(self) -> list[str]:
        return sorted(self._messages.keys())


translator = Translator()
