import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_closure_render_audit import (
    audit_market_aware_session_history_closure_render,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_closure_renderer import (
    _prepare_render_input,
    _render,
    _rewrite_closure_state,
)


def _rewrite_self_hashed(path: Path, payload: dict[str, Any]) -> None:
    payload["output_sha256"] = None
    canonical = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    payload["output_sha256"] = sha256_bytes(canonical)
    _write_json(path, payload)


def _prepare_audit_input(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare_render_input(tmp_path)
    _render(paths, "render")
    paths["markdown"] = paths["root"] / "render" / (
        "market_aware_session_history_closure.md"
    )
    paths["render_report"] = paths["root"] / "render" / (
        "market_aware_session_history_closure_render_report.json"
    )
    return paths


def _audit(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return audit_market_aware_session_history_closure_render(
        closure_path=paths["closure"],
        closure_report_path=paths["closure_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        artifact_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def test_closure_render_audit_is_ready_and_deterministic(tmp_path: Path) -> None:
    paths = _prepare_audit_input(tmp_path)

    first = _audit(paths, "audit-one")
    second = _audit(paths, "audit-two")

    assert first["audit_ready"] is True
    assert first["status"] == "ready"
    assert first["closure_status"] == "ready"
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    assert first["markdown_sha256"] == sha256_bytes(paths["markdown"].read_bytes())
    report_path = paths["root"] / "audit-one" / (
        "market_aware_session_history_closure_render_audit_report.json"
    )
    assert json.loads(report_path.read_text(encoding="utf-8"))["output_sha256"] == (
        first["output_sha256"]
    )


def test_blocked_closure_render_audit_preserves_state(tmp_path: Path) -> None:
    paths = _prepare_audit_input(tmp_path)
    _rewrite_closure_state(paths, status="stale", closure_ready=False)
    _render(paths, "stale-render")
    paths["markdown"] = paths["root"] / "stale-render" / (
        "market_aware_session_history_closure.md"
    )
    paths["render_report"] = paths["root"] / "stale-render" / (
        "market_aware_session_history_closure_render_report.json"
    )

    report = _audit(paths, "audit")

    assert report["audit_ready"] is True
    assert report["status"] == "stale"
    assert report["closure_status"] == "stale"
    assert report["closure_ready"] is False
    assert report["render_ready"] is False
    assert report["decision_ready"] is False


@pytest.mark.parametrize(
    "mutation",
    ["closure", "closure_report", "markdown", "render_report", "declared_markdown", "missing"],
)
def test_closure_render_audit_fails_closed_on_tamper(
    tmp_path: Path, mutation: str
) -> None:
    paths = _prepare_audit_input(tmp_path / mutation)
    if mutation == "closure":
        closure = json.loads(paths["closure"].read_text(encoding="utf-8"))
        closure["symbol"] = "tampered"
        _write_json(paths["closure"], closure)
    elif mutation == "closure_report":
        closure_report = json.loads(paths["closure_report"].read_text(encoding="utf-8"))
        closure_report["closure_ready"] = False
        _write_json(paths["closure_report"], closure_report)
    elif mutation == "markdown":
        paths["markdown"].write_bytes(paths["markdown"].read_bytes() + b"tampered\n")
    elif mutation == "render_report":
        render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
        render_report["render_ready"] = False
        _write_json(paths["render_report"], render_report)
    elif mutation == "declared_markdown":
        render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
        render_report["markdown_sha256"] = "0" * 64
        _rewrite_self_hashed(paths["render_report"], render_report)
    else:
        paths["markdown"] = paths["root"] / "missing.md"

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] in {
        "FIELD_MISMATCH",
        "HASH_MISMATCH",
        "INPUT_UNAVAILABLE",
    }


def test_closure_render_audit_rejects_non_utf8_markdown(tmp_path: Path) -> None:
    paths = _prepare_audit_input(tmp_path)
    paths["markdown"].write_bytes(b"\xff\xfe\x00")

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] == "HASH_MISMATCH"


def test_closure_render_audit_cli_and_output_boundary(
    tmp_path: Path, capsys: Any
) -> None:
    paths = _prepare_audit_input(tmp_path)
    output_dir = paths["root"] / "cli-audit"
    exit_code = main(
        [
            "audit-market-aware-session-history-closure-render",
            "--closure",
            str(paths["closure"]),
            "--closure-report",
            str(paths["closure_report"]),
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

    outside = tmp_path.parent / "outside-closure-render-audit"
    boundary = audit_market_aware_session_history_closure_render(
        closure_path=paths["closure"],
        closure_report_path=paths["closure_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["audit_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (
        outside / "market_aware_session_history_closure_render_audit_report.json"
    ).exists()

    with pytest.raises(SystemExit):
        main(["audit-market-aware-session-history-closure-render"])
