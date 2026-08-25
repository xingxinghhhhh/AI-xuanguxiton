import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_manifest import (
    build_market_aware_session_history_manifest,
)
from a_share_ai.analysis.market_aware_session_history_render_audit import (
    audit_market_aware_session_history_render,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_render_audit import _prepare


def _prepare_manifest_input(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare(tmp_path)
    render_dir = paths["root"] / "render"
    audit_render_dir = paths["root"] / "render-audit"
    audit_market_aware_session_history_render(
        history_path=paths["history"],
        history_report_path=paths["history_report"],
        history_audit_report_path=paths["audit_report"],
        markdown_path=render_dir / "market_aware_session_history.md",
        render_report_path=render_dir / "market_aware_session_history_render_report.json",
        artifact_root=paths["root"],
        output_dir=audit_render_dir,
    )
    paths["render_audit_report"] = (
        audit_render_dir / "market_aware_session_history_render_audit_report.json"
    )
    paths["markdown"] = render_dir / "market_aware_session_history.md"
    paths["render_report"] = render_dir / "market_aware_session_history_render_report.json"
    return paths


def _build(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return build_market_aware_session_history_manifest(
        history_path=paths["history"],
        history_report_path=paths["history_report"],
        history_audit_report_path=paths["audit_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        render_audit_report_path=paths["render_audit_report"],
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


def test_manifest_is_ready_and_deterministic(tmp_path: Path) -> None:
    paths = _prepare_manifest_input(tmp_path)

    first = _build(paths, "manifest")
    second = _build(paths, "manifest")

    assert first["manifest_ready"] is True
    assert first["status"] == "ready"
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    assert first["manifest_sha256"] == second["manifest_sha256"]
    manifest = json.loads(
        (paths["root"] / "manifest" / "market_aware_session_history_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["manifest_version"] == "market-aware-session-history-manifest-v1"
    assert {item["role"] for item in manifest["artifacts"]} == {
        "history",
        "history_report",
        "history_audit_report",
        "history_markdown",
        "history_render_report",
        "history_render_audit_report",
    }
    assert all(item["byte_count"] > 0 for item in manifest["artifacts"])
    assert manifest["decision_ready"] is False


def test_manifest_fails_closed_when_node40_is_not_ready(tmp_path: Path) -> None:
    paths = _prepare_manifest_input(tmp_path)
    render_audit = json.loads(paths["render_audit_report"].read_text(encoding="utf-8"))
    render_audit["audit_ready"] = False
    render_audit["issues"] = [{"code": "BLOCKED", "message": "audit blocked"}]
    _rewrite_self_hashed(paths["render_audit_report"], render_audit)

    report = _build(paths, "manifest")

    assert report["manifest_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] == "UPSTREAM_NOT_READY"
    assert not (
        paths["root"] / "manifest" / "market_aware_session_history_manifest.json"
    ).exists()


@pytest.mark.parametrize("mutation", ["history", "render_report", "decision"])
def test_manifest_rejects_tamper_and_gate_injection(tmp_path: Path, mutation: str) -> None:
    paths = _prepare_manifest_input(tmp_path / mutation)
    if mutation == "history":
        paths["history"].write_text("bad-json\n", encoding="utf-8")
    elif mutation == "render_report":
        render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
        render_report["symbol"] = "000001.SZ"
        _write_json(paths["render_report"], render_report)
    else:
        render_audit = json.loads(paths["render_audit_report"].read_text(encoding="utf-8"))
        render_audit["decision_ready"] = True
        _rewrite_self_hashed(paths["render_audit_report"], render_audit)

    report = _build(paths, "manifest")

    assert report["manifest_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] in {
        "INPUT_JSON_INVALID",
        "FIELD_MISMATCH",
        "DECISION_GATE_INVALID",
        "HASH_MISMATCH",
    }


def test_manifest_cli_and_output_boundary(tmp_path: Path, capsys: Any) -> None:
    paths = _prepare_manifest_input(tmp_path)
    output_dir = paths["root"] / "manifest-cli"
    exit_code = main(
        [
            "build-market-aware-session-history-manifest",
            "--history",
            str(paths["history"]),
            "--history-report",
            str(paths["history_report"]),
            "--history-audit-report",
            str(paths["audit_report"]),
            "--markdown",
            str(paths["markdown"]),
            "--render-report",
            str(paths["render_report"]),
            "--render-audit-report",
            str(paths["render_audit_report"]),
            "--artifact-root",
            str(paths["root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["manifest_ready"] is True

    outside = tmp_path.parent / "outside-history-manifest"
    boundary = build_market_aware_session_history_manifest(
        history_path=paths["history"],
        history_report_path=paths["history_report"],
        history_audit_report_path=paths["audit_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        render_audit_report_path=paths["render_audit_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["manifest_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (outside / "market_aware_session_history_manifest_report.json").exists()

    with pytest.raises(SystemExit):
        main(["build-market-aware-session-history-manifest"])
