import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_package_audit import (
    audit_market_aware_session_package,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session_package import (
    _build_package,
    _prepare_package_inputs,
    _write_json,
)


def _audit(inputs: dict[str, Path], output_name: str) -> dict[str, Any]:
    package_dir = inputs["artifact_root"] / "package"
    return audit_market_aware_session_package(
        package_path=package_dir / "market_aware_session_package.json",
        package_report_path=package_dir / "market_aware_session_package_report.json",
        artifact_root=inputs["artifact_root"],
        output_dir=inputs["artifact_root"] / output_name,
    )


def _refresh_package_report(inputs: dict[str, Path], package: dict[str, Any]) -> None:
    package_dir = inputs["artifact_root"] / "package"
    _write_json(package_dir / "market_aware_session_package.json", package)
    report_path = package_dir / "market_aware_session_package_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["output_sha256"] = sha256_bytes(
        (package_dir / "market_aware_session_package.json").read_bytes()
    )
    _write_json(report_path, report)


def test_ready_package_audit_is_deterministic_and_complete(tmp_path: Path) -> None:
    inputs = _prepare_package_inputs(tmp_path)
    _build_package(inputs, "package")

    first = _audit(inputs, "audit-one")
    second = _audit(inputs, "audit-two")

    assert first["audit_ready"] is True
    assert first["package_ready"] is True
    assert first["session_ready"] is True
    assert first["decision_ready"] is False
    assert first["artifact_count"] == 5
    assert first["output_sha256"] == second["output_sha256"]
    assert first["package_sha256"] == sha256_bytes(
        (inputs["artifact_root"] / "package" / "market_aware_session_package.json").read_bytes()
    )
    audit_report_path = (
        inputs["artifact_root"]
        / "audit-one"
        / "market_aware_session_package_audit_report.json"
    )
    audit_report = json.loads(audit_report_path.read_text(encoding="utf-8"))
    assert audit_report["audit_version"] == "market-aware-session-package-audit-v1"
    assert audit_report["output_sha256"] == first["output_sha256"]
    assert all(not Path(item["path"]).is_absolute() for item in first["artifacts"])


def test_blocked_stale_package_audit_preserves_session_gate(tmp_path: Path) -> None:
    inputs = _prepare_package_inputs(tmp_path, stale=True)
    _build_package(inputs, "package")

    report = _audit(inputs, "audit")

    assert report["audit_ready"] is True
    assert report["package_ready"] is True
    assert report["session_ready"] is False
    assert report["status"] == "stale"
    assert report["decision_ready"] is False


def test_audit_fails_closed_for_tamper_and_manifest_report_mismatch(tmp_path: Path) -> None:
    tamper_inputs = _prepare_package_inputs(tmp_path / "tamper")
    _build_package(tamper_inputs, "package")
    markdown = bytearray(tamper_inputs["session_markdown"].read_bytes())
    markdown[0] = ord("X") if markdown[0] != ord("X") else ord("Y")
    tamper_inputs["session_markdown"].write_bytes(markdown)

    tampered = _audit(tamper_inputs, "audit")
    assert tampered["audit_ready"] is False
    assert tampered["issues"][0]["code"] == "HASH_MISMATCH"

    mismatch_inputs = _prepare_package_inputs(tmp_path / "mismatch")
    _build_package(mismatch_inputs, "package")
    package_dir = mismatch_inputs["artifact_root"] / "package"
    package_report = json.loads(
        (package_dir / "market_aware_session_package_report.json").read_text(encoding="utf-8")
    )
    package_report["symbol"] = "600001.SZ"
    _write_json(package_dir / "market_aware_session_package_report.json", package_report)
    mismatch = _audit(mismatch_inputs, "audit")
    assert mismatch["audit_ready"] is False
    assert mismatch["issues"][0]["code"] == "FIELD_MISMATCH"


@pytest.mark.parametrize("mutation", ["unknown_role", "absolute_path", "outside_path"])
def test_audit_rejects_invalid_manifest_roles_and_paths(
    tmp_path: Path, mutation: str
) -> None:
    inputs = _prepare_package_inputs(tmp_path / mutation)
    _build_package(inputs, "package")
    package_path = inputs["artifact_root"] / "package" / "market_aware_session_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    if mutation == "unknown_role":
        package["artifacts"][0]["role"] = "unknown"
    elif mutation == "absolute_path":
        package["artifacts"][0]["path"] = str(inputs["session"].resolve())
    else:
        package["artifacts"][0]["path"] = "../outside-session.json"
    _refresh_package_report(inputs, package)

    report = _audit(inputs, "audit")

    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] in {"ARTIFACT_INVALID", "PATH_INVALID"}


def test_audit_rejects_evaluation_after_reference(tmp_path: Path) -> None:
    inputs = _prepare_package_inputs(tmp_path)
    _build_package(inputs, "package")
    package_path = inputs["artifact_root"] / "package" / "market_aware_session_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["evaluation_at"] = "2026-08-13T13:00:00+00:00"
    _refresh_package_report(inputs, package)
    package_report_path = (
        inputs["artifact_root"] / "package" / "market_aware_session_package_report.json"
    )
    package_report = json.loads(package_report_path.read_text(encoding="utf-8"))
    package_report["evaluation_at"] = package["evaluation_at"]
    _write_json(package_report_path, package_report)

    report = _audit(inputs, "audit")

    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] == "TIME_IN_FUTURE"


def test_audit_cli_and_output_boundary(tmp_path: Path, capsys: Any) -> None:
    inputs = _prepare_package_inputs(tmp_path)
    _build_package(inputs, "package")
    package_dir = inputs["artifact_root"] / "package"
    output_dir = inputs["artifact_root"] / "cli-audit"
    exit_code = main(
        [
            "audit-market-aware-session-package",
            "--package",
            str(package_dir / "market_aware_session_package.json"),
            "--package-report",
            str(package_dir / "market_aware_session_package_report.json"),
            "--artifact-root",
            str(inputs["artifact_root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["audit_ready"] is True
    assert (output_dir / "market_aware_session_package_audit_report.json").exists()

    outside_dir = inputs["artifact_root"].parent / "outside-audit"
    boundary = audit_market_aware_session_package(
        package_path=package_dir / "market_aware_session_package.json",
        package_report_path=package_dir / "market_aware_session_package_report.json",
        artifact_root=inputs["artifact_root"],
        output_dir=outside_dir,
    )
    assert boundary["audit_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (outside_dir / "market_aware_session_package_audit_report.json").exists()


def test_audit_rejects_symlink_escape(tmp_path: Path) -> None:
    inputs = _prepare_package_inputs(tmp_path)
    _build_package(inputs, "package")
    outside = tmp_path / "outside-session.json"
    outside.write_bytes(inputs["session"].read_bytes())
    linked = inputs["artifact_root"] / "linked-session.json"
    try:
        linked.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    package_path = inputs["artifact_root"] / "package" / "market_aware_session_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["artifacts"][0]["path"] = "linked-session.json"
    _refresh_package_report(inputs, package)

    report = _audit(inputs, "audit")

    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
