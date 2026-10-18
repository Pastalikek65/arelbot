from __future__ import annotations

from scripts.security_replay import run_replay


def test_security_replay_covers_release_boundary_without_network_calls():
    report = run_replay()

    assert report.ok is True
    assert {
        "media_url_policy",
        "private_host_policy",
        "document_archive_policy",
        "prompt_boundary",
        "secret_redaction",
    } <= set(report.checks)
    assert report.failures == ()
