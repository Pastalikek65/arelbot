"""Validated runtime support for the fail-closed staging-core profile.

The profile is an operator artifact, not an approval manifest.  It is
deliberately accepted only by a staging runtime so a production process cannot
silently downgrade itself to a partially featured configuration.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
PROFILE_SCOPE = "staging-core-profile"
_FEATURE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


class StagingProfileError(ValueError):
    """Stable, secret-free profile validation error."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


def _load_template(path: Path) -> Mapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise StagingProfileError("review_template_missing")
    try:
        if path.stat().st_size > 1024 * 1024:
            raise StagingProfileError("review_template_too_large")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except StagingProfileError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise StagingProfileError("review_template_invalid") from exc
    if not isinstance(payload, Mapping):
        raise StagingProfileError("review_template_invalid")
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("kind") != "provider-approval-review-template":
        raise StagingProfileError("review_template_schema_unsupported")
    return payload


def build_profile(template: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic staging profile from a review worksheet."""

    rows = template.get("features")
    if not isinstance(rows, list) or len(rows) > 256:
        raise StagingProfileError("review_features_invalid")
    pending: set[str] = set()
    reviewed: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise StagingProfileError("review_row_invalid")
        key = str(row.get("feature_key") or "").strip().casefold()
        if not _FEATURE_KEY_RE.fullmatch(key):
            raise StagingProfileError("review_feature_key_invalid")
        if key in pending or key in reviewed:
            raise StagingProfileError("review_feature_duplicate")
        if not isinstance(row.get("review_required"), bool):
            raise StagingProfileError("review_required_invalid")
        if row["review_required"]:
            pending.add(key)
        else:
            reviewed.add(key)
    disabled = sorted(pending)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": PROFILE_SCOPE,
        "mode": "staging-core",
        "ok": True,
        "require_provider_approval": True,
        "disabled_features": disabled,
        "disabled_features_csv": ",".join(disabled),
        "pending_count": len(disabled),
        "reviewed_count": len(reviewed),
        "feature_count": len(rows),
        "secret_free": True,
        "deployment_evidence": False,
        "note": "Pending external sources are explicitly disabled; this is not a production approval.",
    }


def _load_json(path: Path, *, missing_code: str, invalid_code: str) -> Mapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise StagingProfileError(missing_code)
    try:
        if path.stat().st_size > 1024 * 1024:
            raise StagingProfileError(f"{invalid_code}_too_large")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except StagingProfileError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise StagingProfileError(invalid_code) from exc
    if not isinstance(payload, Mapping):
        raise StagingProfileError(invalid_code)
    return payload


def load_profile(path: str | Path, *, app_env: str) -> frozenset[str]:
    """Load and validate a runtime profile, allowing it only in staging."""

    if str(app_env or "").strip().casefold() != "staging":
        raise StagingProfileError("staging_profile_requires_staging_env")
    payload = _load_json(Path(path).expanduser(), missing_code="staging_profile_missing", invalid_code="staging_profile_invalid")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("scope") != PROFILE_SCOPE
        or payload.get("mode") != "staging-core"
        or payload.get("ok") is not True
        or payload.get("require_provider_approval") is not True
        or payload.get("secret_free") is not True
        or payload.get("deployment_evidence") is not False
    ):
        raise StagingProfileError("staging_profile_schema_unsupported")
    values = payload.get("disabled_features")
    if not isinstance(values, list) or len(values) > 256:
        raise StagingProfileError("staging_profile_features_invalid")
    normalized: list[str] = []
    for value in values:
        key = str(value or "").strip().casefold()
        if not _FEATURE_KEY_RE.fullmatch(key) or key in normalized:
            raise StagingProfileError("staging_profile_feature_invalid")
        normalized.append(key)
    if payload.get("pending_count") != len(normalized) or payload.get("feature_count", 0) < len(normalized):
        raise StagingProfileError("staging_profile_counts_invalid")
    if payload.get("disabled_features_csv") != ",".join(normalized):
        raise StagingProfileError("staging_profile_csv_mismatch")
    return frozenset(normalized)


def write_report(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a report atomically without following a symlink target."""

    if path.exists() and path.is_symlink():
        raise StagingProfileError("profile_report_symlink")
    if path.parent.exists() and path.parent.is_symlink():
        raise StagingProfileError("profile_report_parent_symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(path)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise StagingProfileError("profile_report_write_failed") from exc


def load_review_template(path: str | Path) -> Mapping[str, Any]:
    """Public wrapper used by the CLI and tests."""

    return _load_template(Path(path).expanduser())
