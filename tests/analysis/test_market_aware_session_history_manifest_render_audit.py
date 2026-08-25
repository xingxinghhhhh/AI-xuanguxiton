import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_manifest_render_audit import (
    audit_market_aware_session_history_manifest_render,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_manifest_renderer import (
    _prepare_render_input,
    _render,
)


def _prepare_audit_input(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare_render_input(tmp_path)
    _render(paths, "render")
    paths["markdown"] = paths["root"] / "render" / (
        "market_aware_session_history_manifest.md"
    )
    paths["render_report"] = paths["root"] / "render" / (
        "market_aware_session_history_manifest_render_report.json"
    )
    return paths


def _audit(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return audit_market_aware_session_history_manifest_render(
        manifest_path=paths["manifest"],
        manifest_report_path=paths["manifest_report"],
        manifest_audit_report_path=paths["audit_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
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


def test_manifest_render_audit_is_ready_and_deterministic(tmp_path: Path) -> None:
    paths = _prepare_audit_input(tmp_path)

    first = _audit(paths, "audit-one")
    second = _audit(paths, "audit-two")

    assert first["audit_ready"] is True
    assert first["decision_ready"] is False
    assert first["status"] == "ready"
    assert first["output_sha256"] == second["output_sha256"]
    report_path = paths["root"] / "audit-one" / (
        "market_aware_session_history_manifest_render_audit_report.json"
    )
    assert json.loads(report_path.read_text(encoding="utf-8"))["output_sha256"] == (
        first["output_sha256"]
    )


@pytest.mark.parametrize("mutation", ["manifest", "audit", "markdown", "render"])
def test_manifest_render_audit_rejects_tamper(tmp_path: Path, mutation: str) -> None:
    paths = _prepare_audit_input(tmp_path / mutation)
    if mutation == "manifest":
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        manifest["symbol"] = "000001.SZ"
        _write_json(paths["manifest"], manifest)
    elif mutation == "audit":
        audit_report = json.loads(paths["audit_report"].read_text(encoding="utf-8"))
        audit_report["manifest_sha256"] = "0" * 64
        _rewrite_self_hashed(paths["audit_report"], audit_report)
    elif mutation == "markdown":
        paths["markdown"].write_bytes(paths["markdown"].read_bytes() + b"tampered\n")
    else:
        render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
        render_report["symbol"] = "000001.SZ"
        _write_json(paths["render_report"], render_report)

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] in {
        "FIELD_MISMATCH",
        "HASH_MISMATCH",
        "SIZE_MISMATCH",
    }


@pytest.mark.parametrize("mutation", ["version", "decision", "path"])
def test_manifest_render_audit_rejects_contract_injection(
    tmp_path: Path, mutation: str
) -> None:
    paths = _prepare_audit_input(tmp_path / mutation)
    render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
    if mutation == "version":
        render_report["render_version"] = "other-v1"
    elif mutation == "decision":
        render_report["decision_ready"] = True
    else:
        render_report["manifest_path"] = "../outside.json"
    _write_json(paths["render_report"], render_report)

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] in {
        "VERSION_MISMATCH",
        "DECISION_GATE_INVALID",
        "CHAIN_MISMATCH",
        "PATH_INVALID",
    }


def test_manifest_render_audit_cli_and_output_boundary(
    tmp_path: Path, capsys: Any
) -> None:
    paths = _prepare_audit_input(tmp_path)
    output_dir = paths["root"] / "cli-audit"
    exit_code = main(
        [
            "audit-market-aware-session-history-manifest-render",
            "--manifest",
            str(paths["manifest"]),
            "--manifest-report",
            str(paths["manifest_report"]),
            "--manifest-audit-report",
            str(paths["audit_report"]),
            "--markdown",
            str(paths["markdown"]),
            "--render-report",
            str(paths["render_report"]),
            "--artifact-root",
            str(paths["root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["audit_ready"] is True

    outside = tmp_path.parent / "outside-manifest-render-audit"
    boundary = audit_market_aware_session_history_manifest_render(
        manifest_path=paths["manifest"],
        manifest_report_path=paths["manifest_report"],
        manifest_audit_report_path=paths["audit_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["audit_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (
        outside / "market_aware_session_history_manifest_render_audit_report.json"
    ).exists()

    with pytest.raises(SystemExit):
        main(["audit-market-aware-session-history-manifest-render"])
