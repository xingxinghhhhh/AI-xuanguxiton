import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_closure_renderer import (
    render_market_aware_session_history_closure,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_closure import (
    _build,
    _prepare_closure_input,
)


def _rewrite_self_hashed(path: Path, payload: dict[str, Any]) -> None:
    payload["output_sha256"] = None
    canonical = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    payload["output_sha256"] = sha256_bytes(canonical)
    _write_json(path, payload)


def _prepare_render_input(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare_closure_input(tmp_path)
    _build(paths, "closure")
    paths["closure"] = paths["root"] / "closure" / (
        "market_aware_session_history_closure.json"
    )
    paths["closure_report"] = paths["root"] / "closure" / (
        "market_aware_session_history_closure_report.json"
    )
    return paths


def _render(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return render_market_aware_session_history_closure(
        closure_path=paths["closure"],
        closure_report_path=paths["closure_report"],
        artifact_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def _rewrite_closure_state(
    paths: dict[str, Path], *, status: str, closure_ready: bool
) -> None:
    closure = json.loads(paths["closure"].read_text(encoding="utf-8"))
    closure["status"] = status
    closure["closure_ready"] = closure_ready
    closure["issues"] = [{"code": "STALE_INPUT", "message": "history is stale | <safe>"}]
    _rewrite_self_hashed(paths["closure"], closure)

    report = json.loads(paths["closure_report"].read_text(encoding="utf-8"))
    report["status"] = status
    report["closure_ready"] = closure_ready
    report["issues"] = closure["issues"]
    report["closure_sha256"] = sha256_bytes(paths["closure"].read_bytes())
    _rewrite_self_hashed(paths["closure_report"], report)


def test_closure_render_is_ready_and_deterministic(tmp_path: Path) -> None:
    paths = _prepare_render_input(tmp_path)

    first = _render(paths, "render-one")
    second = _render(paths, "render-two")

    assert first["render_ready"] is True
    assert first["closure_ready"] is True
    assert first["decision_ready"] is False
    assert first["status"] == "ready"
    assert first["output_sha256"] == second["output_sha256"]
    first_markdown = (
        paths["root"] / "render-one" / "market_aware_session_history_closure.md"
    ).read_bytes()
    second_markdown = (
        paths["root"] / "render-two" / "market_aware_session_history_closure.md"
    ).read_bytes()
    assert first_markdown == second_markdown
    report_path = paths["root"] / "render-one" / (
        "market_aware_session_history_closure_render_report.json"
    )
    assert json.loads(report_path.read_text(encoding="utf-8"))["output_sha256"] == (
        first["output_sha256"]
    )
    assert b"read-only evidence-chain view" in first_markdown


def test_blocked_closure_renders_without_upgrade(tmp_path: Path) -> None:
    paths = _prepare_render_input(tmp_path)
    _rewrite_closure_state(paths, status="stale", closure_ready=False)

    report = _render(paths, "render")
    markdown = (paths["root"] / "render" / "market_aware_session_history_closure.md").read_text(
        encoding="utf-8"
    )

    assert report["render_ready"] is False
    assert report["closure_ready"] is False
    assert report["status"] == "stale"
    assert report["decision_ready"] is False
    assert "stale" in markdown
    assert "STALE_INPUT" in markdown


@pytest.mark.parametrize("mutation", ["closure", "report", "decision"])
def test_closure_render_fails_closed_on_tamper(
    tmp_path: Path, mutation: str
) -> None:
    paths = _prepare_render_input(tmp_path / mutation)
    if mutation == "closure":
        paths["closure"].write_text("bad-json\n", encoding="utf-8")
    elif mutation == "report":
        report = json.loads(paths["closure_report"].read_text(encoding="utf-8"))
        report["closure_sha256"] = "0" * 64
        _write_json(paths["closure_report"], report)
    else:
        closure = json.loads(paths["closure"].read_text(encoding="utf-8"))
        closure["decision_ready"] = True
        _write_json(paths["closure"], closure)

    report = _render(paths, "render")

    assert report["render_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] in {
        "DECISION_GATE_INVALID",
        "HASH_MISMATCH",
        "INPUT_JSON_INVALID",
    }
    assert not (
        paths["root"] / "render" / "market_aware_session_history_closure.md"
    ).exists()


def test_closure_render_escapes_literal_fields(tmp_path: Path) -> None:
    paths = _prepare_render_input(tmp_path)
    closure = json.loads(paths["closure"].read_text(encoding="utf-8"))
    closure["symbol"] = "X|`<script>\n#[]()"
    _rewrite_self_hashed(paths["closure"], closure)
    report = json.loads(paths["closure_report"].read_text(encoding="utf-8"))
    report["closure_sha256"] = sha256_bytes(paths["closure"].read_bytes())
    _rewrite_self_hashed(paths["closure_report"], report)

    result = _render(paths, "render")
    markdown = (paths["root"] / "render" / "market_aware_session_history_closure.md").read_text(
        encoding="utf-8"
    )

    assert result["render_ready"] is True
    assert "<script>" not in markdown
    assert "&lt;script&gt;" in markdown
    assert "\\|" in markdown


def test_closure_render_cli_and_output_boundary(
    tmp_path: Path, capsys: Any
) -> None:
    paths = _prepare_render_input(tmp_path)
    output_dir = paths["root"] / "cli-render"
    exit_code = main(
        [
            "render-market-aware-session-history-closure",
            "--closure",
            str(paths["closure"]),
            "--closure-report",
            str(paths["closure_report"]),
            "--artifact-root",
            str(paths["root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["render_ready"] is True

    outside = tmp_path.parent / "outside-closure-render"
    boundary = render_market_aware_session_history_closure(
        closure_path=paths["closure"],
        closure_report_path=paths["closure_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["render_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (
        outside / "market_aware_session_history_closure_render_report.json"
    ).exists()

    with pytest.raises(SystemExit):
        main(["render-market-aware-session-history-closure"])
