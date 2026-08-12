import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_closure import (
    build_market_aware_session_history_closure,
)
from a_share_ai.cli import main
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_manifest_render_audit import (
    _audit,
    _prepare_audit_input,
)
from tests.analysis.test_market_aware_session_history_manifest_renderer import (
    _render,
)


def _prepare_closure_input(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare_audit_input(tmp_path)
    _render(paths, "render")
    paths["markdown"] = paths["root"] / "render" / (
        "market_aware_session_history_manifest.md"
    )
    paths["render_report"] = paths["root"] / "render" / (
        "market_aware_session_history_manifest_render_report.json"
    )
    _audit(paths, "render-audit")
    paths["render_audit_report"] = paths["root"] / "render-audit" / (
        "market_aware_session_history_manifest_render_audit_report.json"
    )
    return paths


def _build(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return build_market_aware_session_history_closure(
        manifest_path=paths["manifest"],
        manifest_report_path=paths["manifest_report"],
        manifest_audit_report_path=paths["audit_report"],
        render_report_path=paths["render_report"],
        render_audit_report_path=paths["render_audit_report"],
        artifact_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def test_closure_is_ready_and_deterministic(tmp_path: Path) -> None:
    paths = _prepare_closure_input(tmp_path)

    first = _build(paths, "closure-one")
    second = _build(paths, "closure-two")

    assert first["closure_ready"] is True
    assert first["decision_ready"] is False
    assert first["status"] == "ready"
    assert first["output_sha256"] == second["output_sha256"]
    closure = json.loads(
        (paths["root"] / "closure-one" / "market_aware_session_history_closure.json").read_text(
            encoding="utf-8"
        )
    )
    assert closure["closure_version"] == "market-aware-session-history-closure-v1"
    assert closure["decision_ready"] is False
    assert closure["audit_ready"] is True


@pytest.mark.parametrize("mutation", ["manifest", "audit", "render_audit"])
def test_closure_fails_closed_on_input_tamper(tmp_path: Path, mutation: str) -> None:
    paths = _prepare_closure_input(tmp_path / mutation)
    if mutation == "manifest":
        paths["manifest"].write_text("bad-json\n", encoding="utf-8")
    elif mutation == "audit":
        audit = json.loads(paths["audit_report"].read_text(encoding="utf-8"))
        audit["manifest_sha256"] = "0" * 64
        _write_json(paths["audit_report"], audit)
    else:
        render_audit = json.loads(paths["render_audit_report"].read_text(encoding="utf-8"))
        render_audit["render_ready"] = False
        _write_json(paths["render_audit_report"], render_audit)

    report = _build(paths, "closure")

    assert report["closure_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] in {
        "INPUT_JSON_INVALID",
        "HASH_MISMATCH",
        "FIELD_MISMATCH",
        "HASH_INVALID",
    }


def test_closure_cli_and_output_boundary(tmp_path: Path, capsys: Any) -> None:
    paths = _prepare_closure_input(tmp_path)
    output_dir = paths["root"] / "cli-closure"
    exit_code = main(
        [
            "build-market-aware-session-history-closure",
            "--manifest",
            str(paths["manifest"]),
            "--manifest-report",
            str(paths["manifest_report"]),
            "--manifest-audit-report",
            str(paths["audit_report"]),
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
    assert json.loads(capsys.readouterr().out)["closure_ready"] is True

    outside = tmp_path.parent / "outside-closure"
    boundary = build_market_aware_session_history_closure(
        manifest_path=paths["manifest"],
        manifest_report_path=paths["manifest_report"],
        manifest_audit_report_path=paths["audit_report"],
        render_report_path=paths["render_report"],
        render_audit_report_path=paths["render_audit_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["closure_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (outside / "market_aware_session_history_closure_report.json").exists()

    with pytest.raises(SystemExit):
        main(["build-market-aware-session-history-closure"])
