"""Shared detection of the durable production database boundary.

Several modules decide whether a database object is the real, durable
production database.  The decision must not be duplicated: a wrong answer
either loses durable state silently or blocks a development run.

The legacy database module is loaded under a private name by
``src/database/__init__.py`` so the compatibility package can re-export the
class without colliding with the module file of the same name.  The class
therefore reports its module as ``_arel_legacy_database``, and a plain
module-name comparison against ``src.database`` never matches.  The helper
below asks the object first, checks the class identity next, and only then
falls back to the module name.

Importing this module never opens a connection: only constructing a
``Database`` does.
"""

from __future__ import annotations

from typing import Any

# The module names the production database class can report: the package
# re-export and the private name the legacy module is loaded under.
PRODUCTION_DATABASE_MODULES = frozenset({"src.database", "_arel_legacy_database"})


def is_production_database(database: Any | None) -> bool:
    """Return whether this database is the durable production boundary.

    A production database must not degrade to process-local state: a restart
    would lose the account balance, a paid reward or a running game round.
    The object's own marker wins, because it knows the deployment policy;
    the class identity comes next, because the class is the production
    facade even when it is loaded under the legacy module name; the module
    name is the last resort.
    """
    if database is None:
        return False
    marker = getattr(database, "is_production_database", None)
    if isinstance(marker, bool):
        return marker
    if _is_database_instance(database):
        return True
    return type(database).__module__ in PRODUCTION_DATABASE_MODULES


def _is_database_instance(database: Any) -> bool:
    # The import stays inside the function so the storage layer does not
    # import the database layer at module load time.
    from src.database import Database

    return isinstance(database, Database)


__all__ = ["PRODUCTION_DATABASE_MODULES", "is_production_database"]
