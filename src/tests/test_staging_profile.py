from __future__ import annotations

import json

import pytest

from scripts.staging_profile import StagingProfileError, build_profile, main, write_report
from src.chat_engine import resolve_feature_runtime_policy
from src.features.staging_profile import load_profile


def _template(*rows):
    return {
        "schema_version": 1,
        "kind": "provider-approval-review-template",
        "features": list(rows),
    }


def _row(key: str, *, review_required: bool):
    return {"feature_key": key, "review_required": review_required}


def test_build_profile_disables_only_pending_features_deterministically():
    result = build_profile(
        _template(
            _row("weather", review_required=True),
            _row("currency", review_required=False),
        )
    )

    assert result["disabled_features"] == ["weather"]
    assert result["disabled_features_csv"] == "weather"
    assert result["pending_count"] == 1
    assert result["reviewed_count"] == 1
    assert result["require_provider_approval"] is True
    assert result["deployment_evidence"] is False
    assert result["secret_free"] is True


def test_build_profile_rejects_duplicate_or_malformed_keys():
    with pytest.raises(StagingProfileError, match="review_feature_duplicate"):
        build_profile(_template(_row("weather", review_required=True), _row("weather", review_required=False)))
    with pytest.raises(StagingProfileError, match="review_feature_key_invalid"):
        build_profile(_template(_row("../weather", review_required=True)))


def test_write_report_is_json_and_does_not_contain_source_urls(tmp_path):
    target = tmp_path / "profile.json"
    payload = build_profile(_template(_row("weather", review_required=True)))

    write_report(target, payload)

    stored = json.loads(target.read_text(encoding="utf-8"))
    assert stored["scope"] == "staging-core-profile"
    assert "https://" not in target.read_text(encoding="utf-8")


def test_cli_reads_worksheet_and_writes_profile(tmp_path, capsys):
    template = tmp_path / "template.json"
    report = tmp_path / "profile.json"
    template.write_text(
        json.dumps(_template(_row("weather", review_required=True)), ensure_ascii=False),
        encoding="utf-8",
    )

    assert main(["--template", str(template), "--report", str(report)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["disabled_features"] == ["weather"]
    assert json.loads(report.read_text(encoding="utf-8"))["ok"] is True


def test_runtime_profile_is_staging_only_and_validates_the_contract(tmp_path):
    template = tmp_path / "template.json"
    profile = tmp_path / "profile.json"
    template.write_text(
        json.dumps(_template(_row("weather", review_required=True)), ensure_ascii=False),
        encoding="utf-8",
    )
    write_report(profile, build_profile(json.loads(template.read_text(encoding="utf-8"))))

    assert load_profile(profile, app_env="staging") == frozenset({"weather"})
    with pytest.raises(StagingProfileError, match="staging_profile_requires_staging_env"):
        load_profile(profile, app_env="production")


def test_runtime_profile_rejects_csv_or_count_tampering(tmp_path):
    profile = tmp_path / "profile.json"
    payload = build_profile(_template(_row("weather", review_required=True)))
    payload["disabled_features_csv"] = "other"
    profile.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StagingProfileError, match="staging_profile_csv_mismatch"):
        load_profile(profile, app_env="staging")


def test_chat_runtime_policy_merges_profile_and_forces_governance(tmp_path):
    profile = tmp_path / "profile.json"
    write_report(profile, build_profile(_template(_row("weather", review_required=True))))

    disabled, requires_approval = resolve_feature_runtime_policy(
        configured_disabled={"breaking_news"},
        staging_profile_path=str(profile),
        app_env="staging",
        require_provider_approval=False,
    )

    assert disabled == frozenset({"weather", "breaking_news"})
    assert requires_approval is True
