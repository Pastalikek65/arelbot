#!/usr/bin/env python3
"""Build and atomically deploy a versioned Arel release over SSH."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from pathlib import PurePosixPath

try:
    import paramiko
except ImportError:  # deployment-only dependency; runtime/test images need not carry it
    paramiko = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_source_archive(archive_path: Path, destination: Path) -> None:
    """Extract a Git archive safely on Python 3.11 and newer.

    Python 3.12 added ``filter="data"`` to ``TarFile.extractall``. Production
    VDS images may still run 3.11, so validate every member ourselves before
    using the compatibility fallback instead of weakening archive traversal
    protection.
    """

    destination = destination.resolve()
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        for member in members:
            name = str(member.name or "")
            posix_name = PurePosixPath(name)
            target = (destination / posix_name).resolve()
            if (
                not name
                or posix_name.is_absolute()
                or "\\" in name
                or any(part in {"", ".", ".."} for part in posix_name.parts if part != ".")
                or (target != destination and destination not in target.parents)
                or not (member.isdir() or member.isreg())
            ):
                raise SystemExit("unsafe source archive entry")
        try:
            archive.extractall(destination, filter="data")
        except TypeError:
            for member in members:
                target = (destination / PurePosixPath(member.name)).resolve()
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise SystemExit("invalid source archive file")
                with stream, target.open("wb") as output:
                    shutil.copyfileobj(stream, output, length=1024 * 1024)


def verify_gate_artifact() -> None:
    """Fail the release when the committed gate artifact is stale.

    The committed artifact is the evidence record of the last live gate run
    and the phase contract audit reads it.  A release must not ship with an
    artifact that no longer describes the gate.

    The audit is strict here and outside the gate on purpose: the gate has
    to stay green, and the process that repairs the drift is the gate run
    itself.  The audit only reads the artifact, so no value of a check can
    reach the release log.
    """

    result = subprocess.run(
        [sys.executable, "scripts/gate_artifact_audit.py", "--strict"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            "the committed gate artifact does not describe the gate; "
            "regenerate it with a live gate run before releasing"
        )


def build_release(version: str, output_dir: Path) -> tuple[Path, str]:
    if run_git("status", "--porcelain"):
        raise SystemExit("Refusing to build a release from a dirty worktree.")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", version):
        raise SystemExit("Version may contain only letters, numbers, dot, underscore, and dash.")

    output_dir.mkdir(parents=True, exist_ok=True)
    commit = run_git("rev-parse", "HEAD")

    with tempfile.TemporaryDirectory(prefix="arel-release-") as temp_dir:
        temp_root = Path(temp_dir)
        source_archive = temp_root / "source.tar"
        subprocess.run(
            ["git", "archive", "--format=tar", f"--output={source_archive}", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
        )

        release_root = temp_root / "release"
        release_root.mkdir()
        extract_source_archive(source_archive, release_root)

        metadata = {
            "version": version,
            "commit": commit,
        }
        (release_root / "RELEASE.json").write_text(
            json.dumps(metadata, indent=2) + "\n",
            encoding="utf-8",
        )

        build_source_offer(source_archive, release_root, commit)

        manifest_lines = _build_manifest(release_root)
        (release_root / "RELEASE_MANIFEST.sha256").write_bytes(
            ("\n".join(manifest_lines) + "\n").encode("utf-8")
        )

        archive_path = output_dir / f"arel-{version}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            for path in sorted(release_root.rglob("*")):
                archive.add(
                    path,
                    arcname=path.relative_to(release_root),
                    recursive=False,
                )

    return archive_path, sha256(archive_path)


def build_source_offer(source_archive: Path, release_root: Path, commit: str, *, chunk_bytes: int = 20 * 1024 * 1024) -> None:
    """Offer the exact Git source, including third-party notices, in DM-sized parts.

    The input is ``git archive HEAD``. Runtime env/account files and untracked
    operator files never enter it. Parts avoid Telegram's document size limit.
    """
    if chunk_bytes < 1:
        raise ValueError("invalid_source_chunk_size")
    compressed = release_root.parent / "corresponding-source.tar.gz"
    with source_archive.open("rb") as source, compressed.open("wb") as target:
        with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as zipped:
            shutil.copyfileobj(source, zipped)
    parts = []
    folder = release_root / "source"
    folder.mkdir()
    with compressed.open("rb") as source:
        index = 1
        while data := source.read(chunk_bytes):
            name = f"source/arel-source.tar.gz.part{index:03d}"
            target = release_root / name
            target.write_bytes(data)
            parts.append({"file": name, "bytes": len(data), "sha256": sha256(target)})
            index += 1
    (release_root / "SOURCE_OFFER.json").write_text(json.dumps({
        "commit": commit, "format": "concatenated-gzip-tar", "sha256": sha256(compressed),
        "parts": parts,
    }, indent=2) + "\n", encoding="utf-8")


def _build_manifest(release_root: Path) -> list[str]:
    """Return a self-consistent manifest for an extracted release tree.

    ``RELEASE_MANIFEST.sha256`` can be present in the Git archive from an
    earlier release.  Including that stale file in the new manifest makes
    every remote install fail its integrity check after extraction.  The
    manifest is generated after the file scan, so it must always be excluded
    explicitly.
    """

    lines: list[str] = []
    for file_path in sorted(
        path
        for path in release_root.rglob("*")
        if path.is_file() and path.name != "RELEASE_MANIFEST.sha256"
    ):
        relative_path = file_path.relative_to(release_root).as_posix()
        lines.append(f"{sha256(file_path)}  {relative_path}")
    return lines


def remote_install_script() -> str:
    return r"""set -euo pipefail
version="$1"
archive_sha="$2"
app_root="$3"
nginx_config="$4"
bootstrap_env="${5:-}"
upload="/tmp/arel-${version}.tar.gz"
release_dir="${app_root}/releases/${version}"
shared_dir="${app_root}/shared"
previous=""
project="arelbot-prod"
compose_file="${release_dir}/docker-compose.yml"
env_file="${shared_dir}/.env"
compose_started=0
release_created=0
release_committed=0

cleanup_failed_release() {
    status=$?
    if [ "${release_committed}" -ne 1 ]; then
        if [ "${compose_started}" -eq 1 ] && declare -F compose >/dev/null 2>&1; then
            compose down --remove-orphans >/dev/null 2>&1 || true
        fi
        if [ "${release_created}" -eq 1 ]; then
            rm -rf -- "${release_dir}" || true
        fi
    fi
    rm -f -- "${upload}" || true
    if [ -n "${bootstrap_env}" ]; then
        rm -f -- "${bootstrap_env}" || true
    fi
    trap - EXIT
    exit "${status}"
}
trap cleanup_failed_release EXIT

mkdir -p "${app_root}/releases" "${shared_dir}"
test "$(sha256sum "${upload}" | awk '{print $1}')" = "${archive_sha}"
test ! -e "${release_dir}"
mkdir -p "${release_dir}"
release_created=1
tar -xzf "${upload}" -C "${release_dir}"
(cd "${release_dir}" && sha256sum -c RELEASE_MANIFEST.sha256)

if [ ! -f "${env_file}" ]; then
    if [ -n "${bootstrap_env}" ] && [ -f "${bootstrap_env}" ]; then
        install -m 600 "${bootstrap_env}" "${env_file}"
    elif [ -f "${app_root}/.env" ]; then
        install -m 600 "${app_root}/.env" "${env_file}"
    else
        echo "No production .env found in shared state" >&2
        exit 20
    fi
fi
chmod 600 "${env_file}"
ln -sfn "${env_file}" "${release_dir}/.env"

if [ -L "${app_root}/current" ]; then
    previous="$(readlink -f "${app_root}/current")"
fi

if grep -Eiq '^[[:space:]]*TELEGRAM_MODE[[:space:]]*=[[:space:]]*polling([[:space:]]*#.*)?$' "${env_file}"; then
    python3 "${release_dir}/scripts/production_preflight.py" \
        --env-file "${env_file}" \
        --compose-file "${compose_file}" \
        --require-provider-approval
else
    python3 "${release_dir}/scripts/production_preflight.py" \
        --env-file "${env_file}" \
        --compose-file "${compose_file}" \
        --nginx-config "${nginx_config}" \
        --require-provider-approval
fi

compose() {
    docker compose \
        --project-name "${project}" \
        --env-file "${env_file}" \
        --file "${compose_file}" \
        --profile prod \
        "$@"
}

wait_for_services() {
    deadline="$(( $(date +%s) + 900 ))"
    while [ "$(date +%s)" -lt "${deadline}" ]; do
        all_healthy=1
        for service in bot worker; do
            container_id="$(compose ps -q "${service}" 2>/dev/null | head -n 1 || true)"
            if [ -z "${container_id}" ] || [ "$(docker inspect -f '{{.State.Status}}' "${container_id}" 2>/dev/null || true)" != "running" ]; then
                all_healthy=0
                continue
            fi
            probe="$(timeout 8s docker exec "${container_id}" python scripts/healthcheck.py 2>/dev/null || true)"
            case "${probe}" in
                healthy:*) ;;
                *) all_healthy=0 ;;
            esac
        done
        if [ "${all_healthy}" -eq 1 ]; then
            return 0
        fi
        sleep 5
    done
    return 1
}

activate_release() {
    compose_started=1
    # Build and start first, then run the bounded in-container readiness probe.
    # This avoids Compose-version differences around --wait while preserving a
    # strict application-level health gate.
    compose up -d --build
    wait_for_services
    state="$(compose ps --format '{{.Service}} {{.State}} {{.Health}}')"
    printf '%s\n' "${state}"
}

rollback_previous() {
    if [ -n "${previous}" ] && [ -d "${previous}" ]; then
        previous_compose="${previous}/docker-compose.yml"
        ln -sfn "${previous}" "${app_root}/current.next"
        mv -Tf "${app_root}/current.next" "${app_root}/current"
        docker compose \
            --project-name "${project}" \
            --env-file "${env_file}" \
            --file "${previous_compose}" \
            --profile prod \
            up -d --build
        compose_file="${previous_compose}"
        wait_for_services
        compose_started=0
    fi
}

if ! activate_release; then
    echo "New production release did not become healthy; rolling back" >&2
    # Keep rollback automatic, but expose bounded, secret-safe diagnostics so a
    # failed health gate can be diagnosed without leaving the failed release
    # active.  The healthcheck only emits dependency booleans; container logs
    # are intentionally omitted because they can contain user content.
    echo "failed_release_state=" >&2
    compose ps --all --format '{{.Service}} {{.State}} {{.Health}}' >&2 || true
    for service in bot worker; do
        container_id="$(compose ps -aq "${service}" 2>/dev/null | head -n 1 || true)"
        if [ -n "${container_id}" ]; then
            docker inspect -f "${service} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}" "${container_id}" >&2 || true
            docker inspect -f '{{range .State.Health.Log}}{{.Output}}{{end}}' "${container_id}" >&2 || true
            probe="$(timeout 8s docker exec "${container_id}" python scripts/healthcheck.py 2>&1 || true)"
            printf '%s\n' "health_probe=${service}:${probe}" >&2
        fi
    done
    rollback_previous || echo "previous release rollback failed; operator action required" >&2
    exit 30
fi

ln -sfn "${release_dir}" "${app_root}/current.next"
mv -Tf "${app_root}/current.next" "${app_root}/current"
release_committed=1
(cd "${release_dir}" && sha256sum -c RELEASE_MANIFEST.sha256)
printf 'deployment=healthy project=%s release=%s\n' "${project}" "${version}"
"""


def connect_ssh(client: "paramiko.SSHClient", args: argparse.Namespace, password: str | None) -> None:
    """Connect with verified host keys and support PAM keyboard-interactive auth."""
    try:
        client.connect(
            hostname=args.host,
            port=args.port,
            username=args.user,
            password=password,
            key_filename=args.key_file,
            timeout=20,
        )
    except paramiko.AuthenticationException as password_error:
        # Some hardened sshd/PAM configurations expose only
        # keyboard-interactive even when they display a password prompt.
        # Reuse the same operator-supplied secret without logging prompts or
        # responses; if the fallback fails, preserve the original auth error.
        transport = client.get_transport()
        if not password or transport is None or not transport.is_active():
            raise

        def answer_password(_title: str, _instructions: str, prompts: list[tuple[str, bool]]) -> list[str]:
            return [password for _prompt, _echo in prompts]

        try:
            transport.auth_interactive(args.user, answer_password)
        except paramiko.SSHException:
            raise password_error from None


def deploy(args: argparse.Namespace, archive: Path, archive_sha: str) -> str:
    if paramiko is None:
        raise SystemExit("Install requirements-deploy.txt before using the SSH deployer.")
    password = os.getenv("AREL_SSH_PASSWORD")
    if not password and not args.key_file:
        raise SystemExit("Set AREL_SSH_PASSWORD or pass --key-file.")

    known_hosts = Path(args.known_hosts).expanduser().resolve()
    if not known_hosts.is_file():
        raise SystemExit("SSH known_hosts file is required; verify and add the VDS host key first.")

    client = paramiko.SSHClient()
    client.load_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    connect_ssh(client, args, password)
    try:
        remote_archive = f"/tmp/arel-{args.version}.tar.gz"
        remote_bootstrap_env = ""
        if args.bootstrap_env_file is not None:
            bootstrap = Path(args.bootstrap_env_file).expanduser()
            if not bootstrap.is_file() or bootstrap.is_symlink():
                raise SystemExit("Bootstrap env file must be a regular, non-symlink file.")
            if bootstrap.stat().st_size > 128 * 1024:
                raise SystemExit("Bootstrap env file is too large.")
            payload = bootstrap.read_bytes()
            if not payload or b"\x00" in payload:
                raise SystemExit("Bootstrap env file is empty or contains invalid bytes.")
            try:
                payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SystemExit("Bootstrap env file must be UTF-8.") from exc
            remote_bootstrap_env = f"/tmp/arel-{args.version}.env"
        with client.open_sftp() as sftp:
            sftp.put(str(archive), remote_archive)
            if remote_bootstrap_env:
                sftp.put(str(args.bootstrap_env_file), remote_bootstrap_env)

        command = "bash -s -- " + " ".join(
            shlex.quote(value)
            for value in (
                args.version,
                archive_sha,
                args.app_root,
                args.nginx_config,
                remote_bootstrap_env,
            )
        )
        stdin, stdout, stderr = client.exec_command(command, timeout=args.timeout)
        stdin.write(remote_install_script())
        stdin.channel.shutdown_write()
        output = stdout.read().decode("utf-8", errors="replace")
        remote_stderr = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        if exit_code:
            # Remote Docker/journal output can contain arbitrary provider
            # diagnostics. Keep secrets out of terminal logs while retaining
            # the bounded health state emitted by the installer itself.
            safe_diagnostics = [
                line.strip()
                for line in remote_stderr.splitlines()
                if line.strip() == "failed_release_state="
                or line.strip().startswith(("bot status=", "worker status="))
                or line.strip().startswith(("healthy:", "unhealthy:"))
                or line.strip().startswith("health_probe=")
            ]
            suffix = f" ({'; '.join(safe_diagnostics)})" if safe_diagnostics else ""
            raise RuntimeError(
                f"Remote production deployment failed with exit code {exit_code}{suffix}; inspect VDS logs securely."
            )
        return output
    finally:
        client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--key-file")
    parser.add_argument("--known-hosts", default=str(Path.home() / ".ssh" / "known_hosts"))
    parser.add_argument("--app-root", default="/opt/arel")
    parser.add_argument("--nginx-config", default="/etc/nginx/sites-enabled/arelbot")
    parser.add_argument(
        "--bootstrap-env-file",
        type=Path,
        help="secure local production .env to install only when the remote shared env is absent",
    )
    parser.add_argument("--version")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dist")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Build and verify the immutable archive without opening an SSH connection.",
    )
    args = parser.parse_args()
    if not args.version:
        args.version = f"{run_git('rev-parse', '--short=12', 'HEAD')}"
    return args


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = parse_args()
    verify_gate_artifact()
    archive, archive_sha = build_release(args.version, args.output_dir)
    print(f"archive={archive}")
    print(f"sha256={archive_sha}")
    if args.build_only:
        return
    try:
        print(deploy(args, archive, archive_sha), end="")
    except Exception as exc:
        # Deployment failures must have a non-zero exit status without
        # echoing Paramiko/network internals or remote diagnostics.
        if paramiko is not None and isinstance(exc, paramiko.AuthenticationException):
            raise SystemExit("SSH authentication failed; verify the deploy credential.") from None
        if paramiko is not None and isinstance(exc, paramiko.SSHException):
            raise SystemExit("SSH connection failed; verify the host and deploy configuration.") from None
        if isinstance(exc, OSError):
            raise SystemExit("SSH deployment could not access the local or remote transport.") from None
        if isinstance(exc, RuntimeError):
            raise SystemExit(str(exc)) from None
        raise


if __name__ == "__main__":
    main()
