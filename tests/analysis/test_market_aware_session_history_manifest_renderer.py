import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_manifest_renderer import (
    render_market_aware_session_history_manifest,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_manifest_audit import (
    _audit,
    _prepare_audit_input,
)


def _prepare_render_input(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare_audit_input(tmp_path)
    _audit(paths, "manifest-audit")
    paths["audit_report"] = paths["root"] / "manifest-audit" / (
        "market_aware_session_history_manifest_audit_report.json"
    )
    return paths


def _render(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return render_market_aware_session_history_manifest(
        manifest_path=paths["manifest"],
        manifest_report_path=paths["manifest_report"],
        audit_report_path=paths["audit_report"],
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


def test_manifest_render_is_ready_and_deterministic(tmp_path: Path) -> None:
    paths = _prepare_render_input(tmp_path)

    first = _render(paths, "render-one")
    second = _render(paths, "render-two")

    assert first["render_ready"] is True
    assert first["decision_ready"] is False
    assert first["render_version"] == "market-aware-session-history-manifest-render-v1"
    assert first["output_sha256"] == second["output_sha256"]
    markdown_path = paths["root"] / "render-one" / (
        "market_aware_session_history_manifest.md"
    )
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Evidence artifacts" in markdown
    assert "history_render_audit_report" in markdown
    assert "does not infer market trends" in markdown
    report = json.loads(
        (paths["root"] / "render-one" / (
            "market_aware_session_history_manifest_render_report.json"
        )).read_text(encoding="utf-8")
    )
    assert report["output_sha256"] == sha256_bytes(markdown_path.read_bytes())


def test_manifest_render_escapes_audit_issue_text(tmp_path: Path) -> None:
    paths = _prepare_render_input(tmp_path)
    audit_report = json.loads(paths["audit_report"].read_text(encoding="utf-8"))
    audit_report["issues"] = [
        {"code": "<script>|#", "message": "[bad](javascript:x) `fence`"}
    ]
    _rewrite_self_hashed(paths["audit_report"], audit_report)

    report = _render(paths, "render")

    assert report["render_ready"] is True
    markdown = (paths["root"] / "render" / "market_aware_session_history_manifest.md").read_text(
        encoding="utf-8"
    )
    assert "<script>" not in markdown
    assert "&lt;script&gt;" in markdown
    assert "\\|" in markdown
    assert "\\#" in markdown


@pytest.mark.parametrize("mutation", ["manifest", "audit", "decision"])
def test_manifest_render_fails_closed_on_input_contract_tamper(
    tmp_path: Path, mutation: str
) -> None:
    paths = _prepare_render_input(tmp_path / mutation)
    if mutation == "manifest":
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        manifest["symbol"] = "000001.SZ"
        _write_json(paths["manifest"], manifest)
    elif mutation == "audit":
        audit_report = json.loads(paths["audit_report"].read_text(encoding="utf-8"))
        audit_report["manifest_sha256"] = "0" * 64
        _rewrite_self_hashed(paths["audit_report"], audit_report)
    else:
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        manifest["decision_ready"] = True
        _write_json(paths["manifest"], manifest)

    report = _render(paths, "render")

    assert report["render_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] in {
        "FIELD_MISMATCH",
        "HASH_MISMATCH",
        "DECISION_GATE_INVALID",
    }


def test_manifest_render_cli_and_output_boundary(tmp_path: Path, capsys: Any) -> None:
    paths = _prepare_render_input(tmp_path)
    output_dir = paths["root"] / "cli-render"
    exit_code = main(
        [
            "render-market-aware-session-history-manifest",
            "--manifest",
            str(paths["manifest"]),
            "--manifest-report",
            str(paths["manifest_report"]),
            "--audit-report",
            str(paths["audit_report"]),
            "--artifact-root",
            str(paths["root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["render_ready"] is True

    outside = tmp_path.parent / "outside-manifest-render"
    boundary = render_market_aware_session_history_manifest(
        manifest_path=paths["manifest"],
        manifest_report_path=paths["manifest_report"],
        audit_report_path=paths["audit_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["render_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (outside / "market_aware_session_history_manifest.md").exists()

    with pytest.raises(SystemExit):
        main(["render-market-aware-session-history-manifest"])
