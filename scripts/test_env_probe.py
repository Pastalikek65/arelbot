#!/usr/bin/env python3
"""Prove that the Compose test container receives only synthetic credentials.

The Compose ``test`` service reads ``.env.test`` through ``env_file``, so the
pytest container no longer receives the production ``.env`` with its live
Telegram token, AI provider keys and encryption key.  This probe runs inside
that container and compares the container environment with the committed
synthetic contract (``.env.test.example``).

The report contains the length and the verdict of every credential variable
and never a value, so neither the gate report nor the CI log can leak a
credential.  A credential that the contract does not define has to be absent
from the container: that is the case that the old ``env_file: .env`` broke.

A connection string carries a credential too.  Compose interpolates the
MongoDB and Redis password of the project environment into the connection
strings of the test profile, so on a machine whose project ``.env`` is the
deployment file the container receives the production password even though
the scope of the connection is the disposable test database.  The probe
therefore compares the userinfo of a connection with the contract as well and
reports a mismatch; a length difference alone would not be a proof, because
two different passwords can share a length.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import SplitResult, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = PROJECT_ROOT / ".env.test.example"

# Every variable that carries a credential.  A variable that is not listed
# here cannot be proven isolated, so the list has to grow with the
# configuration; ``src/tests/test_compose_env_isolation.py`` fails when a new
# credential variable appears in ``config.py``.
CREDENTIAL_VARIABLES = (
    "HRMN_API_KEY",
    "API_HASH",
    "API_ID",
    "CEREBRAS_KEYS",
    "CLEANAPIS_KEY",
    "CLOUDFLARE_ACCOUNTS",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "COINGECKO_API_KEY",
    "D2API_KEY",
    "ENCRYPTION_KEY",
    "EVREN_KEY",
    "EVREN_LLM_API_KEY",
    "FREEMODEL_KEY",
    "GEMINI_KEYS",
    "GROQ_KEYS",
    "IAMHC_KEY",
    "IMAGEN_KEYS",
    "MONGO_APP_PASSWORD",
    "MONGO_ROOT_PASSWORD",
    "MONGO_TEST_PASSWORD",
    "NANOBANANA_KEYS",
    "NVIDIA_API_KEY",
    "NVIDIA_KEY",
    "NINEROUTER_KEY",
    "OBSERVABILITY_TOKEN",
    "OWNER_ID",
    "REDIS_PASSWORD",
    "TAVILY_KEYS",
    "TELEGRAM_TOKEN",
    "WEBHOOK_SECRET",
)

# The Compose file overrides the two connection strings with the values it
# interpolates from the project environment, so the container cannot carry
# the committed example verbatim.  The probe therefore checks two things
# about a connection: the scope of the connection (the disposable test
# database and the disposable test Redis database, never the production
# ones) and the credential inside it.  The interpolated credential is the
# production credential on a machine whose project ``.env`` is the deployment
# file, so the scope check alone would report a green isolation while a
# production password sits in the container environment.
SCOPED_CONNECTIONS = {
    "MONGODB_URI": "/arel_bot_test",
    "REDIS_URL": "/1",
}


@dataclass(frozen=True)
class CredentialRow:
    """The verdict for one credential variable."""

    name: str
    length: int
    status: str
    ok: bool


def parse_env_file(path: Path) -> dict[str, str]:
    """Read the ``KEY=VALUE`` subset of a dotenv file."""

    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def split_connection(value: str) -> SplitResult | None:
    """Split a connection string, or return ``None`` when it is malformed."""

    try:
        return urlsplit(value)
    except ValueError:
        return None


def userinfo(value: str) -> tuple[str, str] | None:
    """Return the username and the password of a connection string.

    The comparison happens in memory; the caller never reports the values.
    A malformed connection string has no userinfo, which is a mismatch.
    """

    parts = split_connection(value)
    if parts is None:
        return None
    return parts.username or "", parts.password or ""


def evaluate(contract: Mapping[str, str], environment: Mapping[str, str]) -> list[CredentialRow]:
    """Compare the container environment with the synthetic contract.

    A credential the contract defines has to carry exactly the synthetic
    value.  A credential the contract does not define has to be absent: a
    value there can only come from the production environment.  A connection
    string has to point at the disposable scope and has to carry the
    synthetic credential of the contract.
    """

    rows: list[CredentialRow] = []
    for name in CREDENTIAL_VARIABLES:
        expected = contract.get(name)
        actual = environment.get(name, "")
        if expected is None:
            ok = not actual
            status = "absent" if ok else "unexpected"
        else:
            ok = actual == expected
            status = "match" if ok else "mismatch"
        rows.append(CredentialRow(name, len(actual), status, ok))
    for name, path in SCOPED_CONNECTIONS.items():
        actual = environment.get(name, "")
        expected = contract.get(name)
        if expected is None:
            ok = not actual
            status = "absent" if ok else "unexpected"
        else:
            parts = split_connection(actual)
            in_scope = parts is not None and parts.path == path
            actual_user = userinfo(actual)
            expected_user = userinfo(expected)
            same_credential = actual_user is not None and actual_user == expected_user
            if not in_scope:
                ok, status = False, "out_of_scope"
            elif not same_credential:
                ok, status = False, "credential_mismatch"
            else:
                ok, status = True, "scoped"
        rows.append(CredentialRow(name, len(actual), status, ok))
    return rows


def render(rows: Iterable[CredentialRow]) -> str:
    """Render the verdict of every credential without its value."""

    return "\n".join(f"{row.name}: len={row.length} status={row.status}" for row in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract",
        type=Path,
        default=None,
        help="the committed synthetic environment file the container is compared with",
    )
    args = parser.parse_args(argv)
    contract_path = args.contract or Path(os.environ.get("ARELBOT_ENV_CONTRACT", DEFAULT_CONTRACT))
    if not contract_path.is_file():
        print("the synthetic environment contract is missing")
        return 1

    rows = evaluate(parse_env_file(contract_path), os.environ)
    print(render(rows))
    failed = [row for row in rows if not row.ok]
    print(f"credential variables checked: {len(rows)} failed: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
