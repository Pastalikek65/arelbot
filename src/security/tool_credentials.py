"""Hand a database credential to a command line tool without leaking it.

Every process on a host can read the argument vector of another process, so
a connection string that carries a password in ``argv`` publishes the
credential to every local user.  The MongoDB database tools, ``mongosh`` and
``redis-cli`` all offer a channel that keeps the secret out of the command
line, and this module is the single place that implements them.

The channels are:

* ``mongodump`` and ``mongorestore`` read a YAML configuration file that
  carries the connection string, so the file is written with owner-only
  permissions and removed as soon as the command returns.
* ``mongosh`` has neither a configuration file nor an environment variable
  for the connection string, so the shell authenticates the session with a
  small script that is delivered to the container instead of the argument
  vector.
* ``redis-cli`` reads the password from the ``REDISCLI_AUTH`` environment
  variable.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlsplit, urlunsplit


@contextmanager
def mongo_tool_config(uri: str) -> Iterator[Path]:
    """Provide the MongoDB tools' connection string through a private file.

    ``mongodump`` and ``mongorestore`` read the connection string either
    from the ``--uri`` option or from a YAML configuration file.  The
    MongoDB documentation recommends the file for exactly this reason: a
    password in the command line is visible in the process list.  The file
    is created with owner-only permissions outside the backup tree and is
    removed as soon as the command returns.

    The owner-only permission is a POSIX guarantee.  On Windows the file
    inherits the permissions of the temporary directory, so the secret is
    only protected from other users by the directory itself.
    """

    handle, name = tempfile.mkstemp(prefix="arel-mongo-tool-", suffix=".yaml")
    path = Path(name)
    try:
        try:
            stream = os.fdopen(handle, "w", encoding="utf-8")
        except BaseException:
            # The descriptor is not owned by a file object yet, so it has
            # to be closed here instead of by the context manager.
            os.close(handle)
            raise
        with stream:
            stream.write(mongo_tool_config_document(uri))
        if os.name != "nt":
            os.chmod(path, 0o600)
        yield path
    finally:
        path.unlink(missing_ok=True)


def mongo_tool_config_document(uri: str) -> str:
    """Return the YAML document the MongoDB database tools read.

    The document is the same one the tools receive through ``--config`` and
    the same one the container-bound tools receive on their standard input.
    A single-quoted YAML scalar keeps the reserved characters of the
    connection string from ending the value.
    """

    escaped = str(uri).replace("'", "''")
    return f"uri: '{escaped}'\n"


def mongosh_auth_script(username: str, password: str, *, auth_source: str) -> str:
    """Return the ``mongosh`` snippet that authenticates the session.

    ``mongosh`` reads neither a configuration file nor an environment
    variable for the connection string, so the shell authenticates the
    current session instead.  The authentication database has to be named
    explicitly, because the default database of the shell is not the
    database that holds the credentials.
    """

    return (
        f"db.getSiblingDB({json.dumps(str(auth_source))})"
        f".auth({json.dumps(str(username))}, {json.dumps(str(password))});\n"
    )


def mongo_tool_environment(uri: str) -> dict[str, str]:
    """Return the environment the MongoDB database tools run with."""

    environment = dict(os.environ)
    environment["MONGODB_URI"] = str(uri)
    return environment


def redis_tool_endpoint(redis_url: str) -> tuple[str, str]:
    """Split a Redis connection string into the tool target and the password.

    ``redis-cli`` only reads ``REDISCLI_AUTH`` when the connection string on
    the command line carries no password, so the password is removed from
    the URL and handed over through the environment instead.  The user name
    stays in the URL because an account name is not a secret.
    """

    parsed = urlsplit(str(redis_url))
    secret = unquote(parsed.password) if parsed.password else ""
    userinfo, _, hostinfo = parsed.netloc.rpartition("@")
    username = userinfo.partition(":")[0]
    netloc = f"{username}@{hostinfo}" if username else hostinfo
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)), secret


def redis_tool_environment(redis_url: str) -> tuple[str, dict[str, str]]:
    """Return the ``redis-cli`` target and the environment it runs with.

    The connection string is parsed once, so the caller cannot end up with
    a target and a password that belong to different URLs.
    """

    target, secret = redis_tool_endpoint(redis_url)
    environment = dict(os.environ)
    if secret:
        environment["REDISCLI_AUTH"] = secret
    else:
        environment.pop("REDISCLI_AUTH", None)
    return target, environment
