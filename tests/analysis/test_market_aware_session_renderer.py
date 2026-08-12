import json
from pathlib import Path
from typing import Any

from a_share_ai.analysis.market_aware_session_renderer import render_market_aware_session
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _prepare_session, _run_session


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _prepare_render_inputs(
    tmp_path: Path, *, calendar_dates: list[str] | None = None
) -> dict[str, Path]:
    inputs = _prepare_session(tmp_path, calendar_dates=calendar_dates)
    _run_session(inputs, "session", "2026-08-10T13:00:00+00:00")
    session_dir = inputs["artifact_root"] / "session"
    return {
        "artifact_root": inputs["artifact_root"],
        "session": session_dir / "market_aware_session.json",
        "session_report": session_dir / "market_aware_session_report.json",
        "freshness": session_dir / "research_freshness_report.json",
    }


def _render(inputs: dict[str, Path], output_name: str) -> dict[str, Any]:
    return render_market_aware_session(
        session_path=inputs["session"],
        session_report_path=inputs["session_report"],
        artifact_root=inputs["artifact_root"],
        output_dir=inputs["artifact_root"] / output_name,
    )


def test_ready_session_renders_deterministically(tmp_path: Path) -> None:
    inputs = _prepare_render_inputs(tmp_path)

    first = _render(inputs, "render-one")
    second = _render(inputs, "render-two")

    assert first["session_render_ready"] is True
    assert first["status"] == "ready"
    assert first["decision_ready"] is False
    assert first["freshness_report_sha256"] == sha256_bytes(inputs["freshness"].read_bytes())
    assert first["session_sha256"] == sha256_bytes(inputs["session"].read_bytes())
    assert first["session_report_sha256"] == sha256_bytes(inputs["session_report"].read_bytes())
    assert first["output_sha256"] == second["output_sha256"]

    markdown = (inputs["artifact_root"] / "render-one" / "market_aware_session.md").read_text(
        encoding="utf-8"
    )
    assert "600000.SH" in markdown
    assert "2026-08-10T13:00:00+00:00" in markdown
    assert "declared summaries only" in markdown
    assert "research_analysis.md" not in markdown


def test_stale_session_renders_blocked_markdown(tmp_path: Path) -> None:
    source_inputs = _prepare_session(
        tmp_path / "stale-source",
        calendar_dates=["2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11"],
    )
    _run_session(source_inputs, "session", "2026-08-11T08:00:00+00:00")
    stale_inputs = {
        "artifact_root": source_inputs["artifact_root"],
        "session": source_inputs["artifact_root"] / "session" / "market_aware_session.json",
        "session_report": (
            source_inputs["artifact_root"]
            / "session"
            / "market_aware_session_report.json"
        ),
        "freshness": source_inputs["artifact_root"] / "session" / "research_freshness_report.json",
    }

    report = _render(stale_inputs, "render")
    assert report["session_render_ready"] is False
    assert report["status"] == "stale"
    assert report["freshness_report_sha256"] is not None
    markdown = (stale_inputs["artifact_root"] / "render" / "market_aware_session.md").read_text(
        encoding="utf-8"
    )
    assert "session_render_status: `blocked`" in markdown
    assert "freshness_status: `stale`" in markdown
    assert "decision_ready: `false`" in markdown


def test_renderer_fails_closed_for_freshness_tamper_and_output_boundary(
    tmp_path: Path,
) -> None:
    inputs = _prepare_render_inputs(tmp_path / "tamper")
    freshness = json.loads(inputs["freshness"].read_text(encoding="utf-8"))
    freshness["freshness_status"] = "stale"
    _write_json(inputs["freshness"], freshness)

    tampered = _render(inputs, "tampered-render")
    assert tampered["session_render_ready"] is False
    assert tampered["issues"][0]["code"] == "HASH_MISMATCH"
    assert not (inputs["artifact_root"] / "tampered-render" / "market_aware_session.md").exists()

    boundary = render_market_aware_session(
        session_path=inputs["session"],
        session_report_path=inputs["session_report"],
        artifact_root=inputs["artifact_root"],
        output_dir=inputs["artifact_root"].parent / "outside-render",
    )
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"


def test_renderer_escapes_metadata_without_changing_structure(tmp_path: Path) -> None:
    inputs = _prepare_render_inputs(tmp_path)
    session = json.loads(inputs["session"].read_text(encoding="utf-8"))
    report = json.loads(inputs["session_report"].read_text(encoding="utf-8"))
    session["symbol"] = "<script>alert(1)</script>\n## injected"
    report["symbol"] = session["symbol"]
    _write_json(inputs["session"], session)
    report["output_sha256"] = sha256_bytes(inputs["session"].read_bytes())
    _write_json(inputs["session_report"], report)

    rendered = _render(inputs, "escaped")
    assert rendered["session_render_ready"] is True
    markdown = (inputs["artifact_root"] / "escaped" / "market_aware_session.md").read_text(
        encoding="utf-8"
    )
    assert "<script>" not in markdown
    assert "## injected" not in markdown
    assert "&lt;script&gt;" in markdown


def test_render_market_aware_session_cli(tmp_path: Path, capsys: Any) -> None:
    inputs = _prepare_render_inputs(tmp_path)
    output_dir = inputs["artifact_root"] / "cli-render"
    exit_code = main(
        [
            "render-market-aware-session",
            "--session",
            str(inputs["session"]),
            "--session-report",
            str(inputs["session_report"]),
            "--artifact-root",
            str(inputs["artifact_root"]),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["session_render_ready"] is True
    assert (output_dir / "market_aware_session.md").exists()
    assert (output_dir / "market_aware_session_render_report.json").exists()
