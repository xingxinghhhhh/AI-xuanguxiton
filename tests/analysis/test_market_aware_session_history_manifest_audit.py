import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_manifest_audit import (
    audit_market_aware_session_history_manifest,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_manifest import (
    _build,
    _prepare_manifest_input,
)


def _prepare_audit_input(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare_manifest_input(tmp_path)
    _build(paths, "manifest")
    paths["manifest"] = paths["root"] / "manifest" / (
        "market_aware_session_history_manifest.json"
    )
    paths["manifest_report"] = paths["root"] / "manifest" / (
        "market_aware_session_history_manifest_report.json"
    )
    return paths


def _audit(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return audit_market_aware_session_history_manifest(
        manifest_path=paths["manifest"],
        manifest_report_path=paths["manifest_report"],
        artifact_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def _rewrite_self_hashed(path: Path, payload: dict[str, Any]) -> None:
    payload["output_sha256"] = None
    canonical = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    payload["output_sha256"] = sha256_bytes(canonical)
    _write_json(path, payload)


def test_manifest_audit_is_ready_and_deterministic(tmp_path: Path) -> None:
    paths = _prepare_audit_input(tmp_path)

    first = _audit(paths, "audit-one")
    second = _audit(paths, "audit-two")

    assert first["audit_ready"] is True
    assert first["decision_ready"] is False
    assert first["status"] == "ready"
    assert first["output_sha256"] == second["output_sha256"]
    assert len(first["artifacts"]) == 6
    assert all(item["actual_sha256"] == item["sha256"] for item in first["artifacts"])
    report_path = paths["root"] / "audit-one" / (
        "market_aware_session_history_manifest_audit_report.json"
    )
    assert json.loads(report_path.read_text(encoding="utf-8"))["output_sha256"] == (
        first["output_sha256"]
    )


@pytest.mark.parametrize("mutation", ["manifest", "report", "artifact"])
def test_manifest_audit_rejects_tamper(tmp_path: Path, mutation: str) -> None:
    paths = _prepare_audit_input(tmp_path / mutation)
    if mutation == "manifest":
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        manifest["symbol"] = "000001.SZ"
        _write_json(paths["manifest"], manifest)
    elif mutation == "report":
        report = json.loads(paths["manifest_report"].read_text(encoding="utf-8"))
        report["manifest_ready"] = False
        _write_json(paths["manifest_report"], report)
    else:
        paths["markdown"].write_bytes(paths["markdown"].read_bytes() + b"tampered\n")

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] in {
        "HASH_MISMATCH",
        "FIELD_MISMATCH",
        "SIZE_MISMATCH",
    }


@pytest.mark.parametrize("mutation", ["role", "path", "decision"])
def test_manifest_audit_rejects_contract_injection(tmp_path: Path, mutation: str) -> None:
    paths = _prepare_audit_input(tmp_path / mutation)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    if mutation == "role":
        manifest["artifacts"][0]["role"] = "unknown"
    elif mutation == "path":
        manifest["artifacts"][0]["path"] = "../outside.json"
    else:
        manifest["decision_ready"] = True
    _write_json(paths["manifest"], manifest)

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] in {
        "ARTIFACT_ROLE_INVALID",
        "PATH_INVALID",
        "PATH_OUTSIDE_ARTIFACT_ROOT",
        "DECISION_GATE_INVALID",
        "HASH_MISMATCH",
    }


def test_manifest_audit_cli_and_output_boundary(tmp_path: Path, capsys: Any) -> None:
    paths = _prepare_audit_input(tmp_path)
    output_dir = paths["root"] / "cli-audit"
    exit_code = main(
        [
            "audit-market-aware-session-history-manifest",
            "--manifest",
            str(paths["manifest"]),
            "--manifest-report",
            str(paths["manifest_report"]),
            "--artifact-root",
            str(paths["root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["audit_ready"] is True

    outside = tmp_path.parent / "outside-manifest-audit"
    boundary = audit_market_aware_session_history_manifest(
        manifest_path=paths["manifest"],
        manifest_report_path=paths["manifest_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["audit_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (
        outside / "market_aware_session_history_manifest_audit_report.json"
    ).exists()

    with pytest.raises(SystemExit):
        main(["audit-market-aware-session-history-manifest"])
