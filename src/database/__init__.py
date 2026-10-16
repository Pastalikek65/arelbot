"""Veri katmanÄ± uyumluluk paketi.

Not: Projede eski MongoDB sÄ±nÄ±fÄ± `src/database.py` dosyasÄ±nda bulunuyor.
Yeni `src/database/` paketi eklendiÄŸi iÃ§in Python import Ã¶nceliÄŸi deÄŸiÅŸti ve
`from src.database import Database` Ã§aÄŸrÄ±larÄ± bu pakete dÃ¼ÅŸmeye baÅŸladÄ±.
Bu dosya eski sÄ±nÄ±fÄ± gÃ¼venli ÅŸekilde yeniden export ederek geriye dÃ¶nÃ¼k uyumu korur.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from .redis_cache import RedisCache

_legacy_database_path = Path(__file__).resolve().parents[1] / "database.py"
_legacy_spec = spec_from_file_location("_arel_legacy_database", _legacy_database_path)

if not _legacy_spec or not _legacy_spec.loader:
    raise ImportError(f"Legacy Database module could not be loaded: {_legacy_database_path}")

_legacy_module = module_from_spec(_legacy_spec)
_legacy_spec.loader.exec_module(_legacy_module)

Database = _legacy_module.Database
_database_name_from_url = _legacy_module._database_name_from_url

__all__ = ["Database", "RedisCache", "_database_name_from_url"]
