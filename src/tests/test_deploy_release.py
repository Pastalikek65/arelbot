from __future__ import annotations

import io
import hashlib
import os
import shutil
import subprocess
import tarfile
import uuid
from pathlib import Path
from scripts.deploy_release import _build_manifest
from scripts.deploy_release import remote_install_script
from scripts.deploy_release import extract_source_archive

import pytest


def test_ssh_release_deploy_uses_split_compose_topology_and_bounded_probe():
    script = remote_install_script()

    assert 'project="arelbot-prod"' in script
    assert 'profile prod' in script
    assert "compose up -d --build" in script
    assert "wait_for_services()" in script
    assert 'docker exec "${container_id}" python scripts/healthcheck.py' in script
    assert "for service in bot worker" in script
    assert "healthy:*" in script
    assert 'docker inspect -f' in script
    assert "production_preflight.py" in script
    assert "--require-provider-approval" in script
    assert '--nginx-config "${nginx_config}"' in script
    assert "TELEGRAM_MODE" in script
    assert "polling" in script
    assert 'nginx_config="$4"' in script
    assert "systemctl" not in script
    assert "/etc/systemd/system" not in script


def test_deploy_command_accepts_explicit_bootstrap_env_file():
    from scripts.deploy_release import parse_args
    import sys

    original = sys.argv
    try:
        sys.argv = [
            "deploy_release.py",
            "--host",
            "example.test",
            "--version",
            "test-version",
            "--bootstrap-env-file",
            "C:/secure/arelbot.env",
        ]
        args = parse_args()
    finally:
        sys.argv = original

    assert str(args.bootstrap_env_file).endswith("arelbot.env")


def test_remote_installer_supports_bootstrap_env_without_overwriting_existing_state():
    script = remote_install_script()

    assert 'bootstrap_env="${5:-}"' in script
    assert 'install -m 600 "${bootstrap_env}" "${env_file}"' in script
    assert 'if [ ! -f "${env_file}" ]' in script
    assert 'rm -f -- "${bootstrap_env}"' in script


def test_deploy_command_exposes_remote_nginx_config_path():
    from scripts.deploy_release import parse_args
    import sys

    original = sys.argv
    try:
        sys.argv = ["deploy_release.py", "--host", "example.test", "--version", "test-version"]
        args = parse_args()
    finally:
        sys.argv = original

    assert args.nginx_config == "/etc/nginx/sites-enabled/arelbot"


def test_deploy_command_can_build_an_archive_without_ssh():
    from scripts.deploy_release import parse_args
    import sys

    original = sys.argv
    try:
        # The Docker test image intentionally omits git.  The deploy command
        # can still parse/build with an explicit release version, while the
        # normal operator path derives a version from the checked-out commit.
        sys.argv = [
            "deploy_release.py",
            "--host",
            "example.test",
            "--version",
            "test-version",
            "--build-only",
        ]
        args = parse_args()
    finally:
        sys.argv = original

    assert args.build_only is True


def test_release_manifest_excludes_stale_manifest_file(tmp_path):
    release_root = tmp_path / "release"
    release_root.mkdir()
    payload = release_root / "payload.txt"
    payload.write_text("payload\n", encoding="utf-8")
    # A previous checked-in/generated manifest must not poison the new one.
    (release_root / "RELEASE_MANIFEST.sha256").write_text(
        "stale  payload.txt\n", encoding="utf-8"
    )

    manifest = _build_manifest(release_root)

    assert any(line.endswith("  payload.txt") for line in manifest)
    assert not any(line.endswith("  RELEASE_MANIFEST.sha256") for line in manifest)


def test_ssh_release_deploy_restores_previous_compose_release_on_health_failure():
    script = remote_install_script()

    assert "rollback_previous()" in script
    assert 'previous_compose="${previous}/docker-compose.yml"' in script
    assert "New production release did not become healthy; rolling back" in script
    assert "wait_for_services()" in script
    assert "compose down --remove-orphans" in script


def test_ssh_release_deploy_cleans_uncommitted_release_on_preflight_failure():
    script = remote_install_script()

    assert "trap cleanup_failed_release EXIT" in script
    assert 'release_created=0' in script
    assert 'release_created=1' in script
    assert 'release_committed=0' in script
    assert 'rm -rf -- "${release_dir}"' in script
    assert 'rm -f -- "${upload}"' in script
    assert 'release_committed=1' in script


def test_remote_preflight_failure_removes_partial_release_and_uploaded_archive(tmp_path):
    if os.name == "nt":
        pytest.skip("remote /tmp path replay runs in the Linux Docker gate")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is required for the remote installer replay")

    version = f"cleanup-{uuid.uuid4().hex[:12]}"
    app_root = tmp_path / "arel"
    upload = Path("/tmp") / f"arel-{version}.tar.gz"
    staging = tmp_path / "archive"
    staging.mkdir()
    compose_bytes = b"services: {}\n"
    (staging / "docker-compose.yml").write_bytes(compose_bytes)
    compose_hash = hashlib.sha256(compose_bytes).hexdigest()
    (staging / "RELEASE_MANIFEST.sha256").write_text(
        f"{compose_hash}  docker-compose.yml\n",
        encoding="utf-8",
    )
    with tarfile.open(upload, "w:gz") as archive:
        archive.add(staging / "docker-compose.yml", arcname="docker-compose.yml")
        archive.add(staging / "RELEASE_MANIFEST.sha256", arcname="RELEASE_MANIFEST.sha256")

    archive_hash = hashlib.sha256(upload.read_bytes()).hexdigest()
    script = remote_install_script()
    result = subprocess.run(
        [bash, "-s", "--", version, archive_hash, str(app_root), "/missing/nginx"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 20
    assert "No production .env found" in result.stderr
    assert not (app_root / "releases" / version).exists()
    assert not upload.exists()


def test_remote_bootstrap_env_is_installed_before_preflight_and_preserved_on_failure(tmp_path):
    if os.name == "nt":
        pytest.skip("remote /tmp path replay runs in the Linux Docker gate")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is required for the remote installer replay")

    version = f"bootstrap-{uuid.uuid4().hex[:12]}"
    app_root = tmp_path / "arel"
    upload = Path("/tmp") / f"arel-{version}.tar.gz"
    bootstrap = Path("/tmp") / f"arel-{version}.env"
    staging = tmp_path / "archive"
    staging.mkdir()
    compose_bytes = b"services: {}\n"
    (staging / "docker-compose.yml").write_bytes(compose_bytes)
    compose_hash = hashlib.sha256(compose_bytes).hexdigest()
    (staging / "RELEASE_MANIFEST.sha256").write_text(
        f"{compose_hash}  docker-compose.yml\n",
        encoding="utf-8",
    )
    with tarfile.open(upload, "w:gz") as archive:
        archive.add(staging / "docker-compose.yml", arcname="docker-compose.yml")
        archive.add(staging / "RELEASE_MANIFEST.sha256", arcname="RELEASE_MANIFEST.sha256")
    bootstrap.write_text("APP_ENV=production\n", encoding="utf-8")
    archive_hash = hashlib.sha256(upload.read_bytes()).hexdigest()
    result = subprocess.run(
        [bash, "-s", "--", version, archive_hash, str(app_root), "/missing/nginx", str(bootstrap)],
        input=remote_install_script(),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )

    assert result.returncode != 0
    assert (app_root / "shared" / ".env").read_text(encoding="utf-8") == "APP_ENV=production\n"
    assert not upload.exists()
    assert not bootstrap.exists()


def test_ssh_connect_falls_back_to_keyboard_interactive():
    import argparse
    import scripts.deploy_release as deploy_release

    if deploy_release.paramiko is None:
        pytest.skip("Paramiko is required for SSH authentication tests")

    class FakeTransport:
        def is_active(self):
            return True

        def auth_interactive(self, username, handler):
            assert username == "deploy"
            assert handler("", "", [("Password:", False)]) == ["secret"]

    class FakeClient:
        def __init__(self):
            self.transport = FakeTransport()

        def connect(self, **kwargs):
            assert kwargs["username"] == "deploy"
            raise deploy_release.paramiko.AuthenticationException()

        def get_transport(self):
            return self.transport

    args = argparse.Namespace(
        host="example.test",
        port=22,
        user="deploy",
        key_file=None,
    )
    client = FakeClient()
    deploy_release.connect_ssh(client, args, "secret")


def test_source_archive_extracts_regular_files(tmp_path):
    archive_path = tmp_path / "source.tar"
    with tarfile.open(archive_path, "w") as archive:
        info = tarfile.TarInfo("package/module.py")
        payload = b"print('ok')\n"
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    destination = tmp_path / "release"
    extract_source_archive(archive_path, destination)
    assert (destination / "package/module.py").read_bytes() == b"print('ok')\n"


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "nested\\escape.txt"])
def test_source_archive_rejects_traversal_entries(tmp_path, name):
    archive_path = tmp_path / "unsafe.tar"
    with tarfile.open(archive_path, "w") as archive:
        info = tarfile.TarInfo(name)
        payload = b"unsafe"
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    with pytest.raises(SystemExit, match="unsafe source archive"):
        extract_source_archive(archive_path, tmp_path / "release")
