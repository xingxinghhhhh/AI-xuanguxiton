import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_render_audit import (
    audit_market_aware_session_history_render,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_renderer import (
    _prepare_render_input,
    _render,
)


def _prepare(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare_render_input(tmp_path)
    _render(paths, "render")
    return paths


def _audit(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    render_dir = paths["root"] / "render"
    return audit_market_aware_session_history_render(
        history_path=paths["history"],
        history_report_path=paths["history_report"],
        history_audit_report_path=paths["audit_report"],
        markdown_path=render_dir / "market_aware_session_history.md",
        render_report_path=render_dir / "market_aware_session_history_render_report.json",
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


def test_ready_render_audits_deterministically(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)

    first = _audit(paths, "audit-one")
    second = _audit(paths, "audit-two")

    assert first["audit_ready"] is True
    assert first["decision_ready"] is False
    assert first["render_ready"] is True
    assert first["history_audit_ready"] is True
    assert first["output_sha256"] == second["output_sha256"]
    output_path = paths["root"] / "audit-one" / (
        "market_aware_session_history_render_audit_report.json"
    )
    assert json.loads(output_path.read_text(encoding="utf-8"))["output_sha256"] == (
        first["output_sha256"]
    )


def test_blocked_audit_state_is_integrity_audited_without_upgrade(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    audit_report = json.loads(paths["audit_report"].read_text(encoding="utf-8"))
    audit_report["audit_ready"] = False
    audit_report["issues"] = [{"code": "BLOCKED", "message": "audit blocked"}]
    _rewrite_self_hashed(paths["audit_report"], audit_report)
    render_report_path = paths["root"] / "render" / (
        "market_aware_session_history_render_report.json"
    )
    render_report = json.loads(render_report_path.read_text(encoding="utf-8"))
    render_report["history_audit_ready"] = False
    render_report["render_ready"] = False
    render_report["issues"] = list(
        json.loads(paths["history"].read_text(encoding="utf-8"))["issues"]
    ) + audit_report["issues"]
    _write_json(render_report_path, render_report)

    report = _audit(paths, "audit")

    assert report["audit_ready"] is True
    assert report["history_audit_ready"] is False
    assert report["render_ready"] is False


@pytest.mark.parametrize("mutation", ["history", "history_report", "audit", "markdown"])
def test_render_audit_rejects_input_tamper(tmp_path: Path, mutation: str) -> None:
    paths = _prepare(tmp_path / mutation)
    if mutation == "history":
        paths["history"].write_bytes(paths["history"].read_bytes() + b"tampered\n")
    elif mutation == "history_report":
        payload = json.loads(paths["history_report"].read_text(encoding="utf-8"))
        payload["output_sha256"] = "0" * 64
        _write_json(paths["history_report"], payload)
    elif mutation == "audit":
        payload = json.loads(paths["audit_report"].read_text(encoding="utf-8"))
        payload["history_sha256"] = "0" * 64
        _write_json(paths["audit_report"], payload)
    else:
        markdown = paths["root"] / "render" / "market_aware_session_history.md"
        markdown.write_bytes(markdown.read_bytes() + b"tampered\n")

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] in {
        "HASH_MISMATCH",
        "SIZE_MISMATCH",
        "INPUT_JSON_INVALID",
    }


@pytest.mark.parametrize("mutation", ["version", "decision", "symbol", "count"])
def test_render_audit_rejects_contract_mismatch(tmp_path: Path, mutation: str) -> None:
    paths = _prepare(tmp_path / mutation)
    render_report_path = paths["root"] / "render" / (
        "market_aware_session_history_render_report.json"
    )
    render_report = json.loads(render_report_path.read_text(encoding="utf-8"))
    if mutation == "version":
        render_report["render_version"] = "other-v1"
    elif mutation == "decision":
        render_report["decision_ready"] = True
    elif mutation == "symbol":
        render_report["symbol"] = "000001.SZ"
    else:
        render_report["package_count"] = 999
    _write_json(render_report_path, render_report)

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    expected = {
        "version": "VERSION_MISMATCH",
        "decision": "DECISION_GATE_INVALID",
        "symbol": "FIELD_MISMATCH",
        "count": "FIELD_MISMATCH",
    }
    assert report["issues"][0]["code"] == expected[mutation]


def test_render_audit_cli_and_output_boundary(tmp_path: Path, capsys: Any) -> None:
    paths = _prepare(tmp_path)
    render_dir = paths["root"] / "render"
    output_dir = paths["root"] / "cli-audit"
    exit_code = main(
        [
            "audit-market-aware-session-history-render",
            "--history",
            str(paths["history"]),
            "--history-report",
            str(paths["history_report"]),
            "--history-audit-report",
            str(paths["audit_report"]),
            "--markdown",
            str(render_dir / "market_aware_session_history.md"),
            "--render-report",
            str(render_dir / "market_aware_session_history_render_report.json"),
            "--artifact-root",
            str(paths["root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["audit_ready"] is True

    outside = tmp_path.parent / "outside-history-render-audit"
    boundary = audit_market_aware_session_history_render(
        history_path=paths["history"],
        history_report_path=paths["history_report"],
        history_audit_report_path=paths["audit_report"],
        markdown_path=render_dir / "market_aware_session_history.md",
        render_report_path=render_dir / "market_aware_session_history_render_report.json",
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["audit_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (outside / "market_aware_session_history_render_audit_report.json").exists()

    with pytest.raises(SystemExit):
        main(["audit-market-aware-session-history-render"])
