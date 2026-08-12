import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_package import (
    build_market_aware_session_package,
)
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


def _prepare_package_inputs(tmp_path: Path, *, stale: bool = False) -> dict[str, Path]:
    calendar_dates = (
        ["2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11"] if stale else None
    )
    inputs = _prepare_session(tmp_path, calendar_dates=calendar_dates)
    evaluation = "2026-08-11T08:00:00+00:00" if stale else "2026-08-10T13:00:00+00:00"
    _run_session(inputs, "session", evaluation)
    artifact_root = inputs["artifact_root"]
    session_dir = artifact_root / "session"
    render_dir = artifact_root / "render"
    render_market_aware_session(
        session_path=session_dir / "market_aware_session.json",
        session_report_path=session_dir / "market_aware_session_report.json",
        artifact_root=artifact_root,
        output_dir=render_dir,
    )
    return {
        "artifact_root": artifact_root,
        "session": session_dir / "market_aware_session.json",
        "session_report": session_dir / "market_aware_session_report.json",
        "freshness_report": session_dir / "research_freshness_report.json",
        "session_markdown": render_dir / "market_aware_session.md",
        "session_render_report": render_dir / "market_aware_session_render_report.json",
    }


def _build_package(inputs: dict[str, Path], output_name: str) -> dict[str, Any]:
    return build_market_aware_session_package(
        session_path=inputs["session"],
        session_report_path=inputs["session_report"],
        freshness_report_path=inputs["freshness_report"],
        session_markdown_path=inputs["session_markdown"],
        session_render_report_path=inputs["session_render_report"],
        artifact_root=inputs["artifact_root"],
        output_dir=inputs["artifact_root"] / output_name,
    )


def test_ready_package_is_deterministic_and_relative(tmp_path: Path) -> None:
    inputs = _prepare_package_inputs(tmp_path)

    first = _build_package(inputs, "package-one")
    second = _build_package(inputs, "package-two")

    assert first["package_ready"] is True
    assert first["session_ready"] is True
    assert first["decision_ready"] is False
    assert first["artifact_count"] == 5
    assert first["output_sha256"] == second["output_sha256"]
    package = json.loads(
        (inputs["artifact_root"] / "package-one" / "market_aware_session_package.json").read_text(
            encoding="utf-8"
        )
    )
    assert package["package_version"] == "market-aware-session-package-v1"
    assert {item["role"] for item in package["artifacts"]} == {
        "session",
        "session_report",
        "freshness_report",
        "session_markdown",
        "session_render_report",
    }
    assert all(not Path(item["path"]).is_absolute() for item in package["artifacts"])
    assert all(item["size_bytes"] > 0 for item in package["artifacts"])


def test_blocked_session_is_packaged_without_becoming_ready(tmp_path: Path) -> None:
    inputs = _prepare_package_inputs(tmp_path, stale=True)

    report = _build_package(inputs, "package")

    assert report["package_ready"] is True
    assert report["session_ready"] is False
    assert report["status"] == "stale"
    assert report["decision_ready"] is False


def test_package_fails_closed_for_tamper_and_session_report_mismatch(tmp_path: Path) -> None:
    inputs = _prepare_package_inputs(tmp_path / "tamper")
    inputs["session_markdown"].write_text(
        inputs["session_markdown"].read_text(encoding="utf-8") + "tampered\n",
        encoding="utf-8",
    )
    tampered = _build_package(inputs, "package")
    assert tampered["package_ready"] is False
    assert tampered["issues"][0]["code"] == "HASH_MISMATCH"
    assert not (inputs["artifact_root"] / "package" / "market_aware_session_package.json").exists()

    mismatch_inputs = _prepare_package_inputs(tmp_path / "mismatch")
    session_report = json.loads(
        mismatch_inputs["session_report"].read_text(encoding="utf-8")
    )
    session_report["symbol"] = "600001.SZ"
    _write_json(mismatch_inputs["session_report"], session_report)
    mismatch = _build_package(mismatch_inputs, "package")
    assert mismatch["package_ready"] is False
    assert mismatch["issues"][0]["code"] == "FIELD_MISMATCH"


def test_package_rejects_symlink_escape_and_output_boundary(tmp_path: Path) -> None:
    inputs = _prepare_package_inputs(tmp_path / "boundary")
    outside = tmp_path / "outside-session.json"
    outside.write_bytes(inputs["session"].read_bytes())
    linked = inputs["artifact_root"] / "linked-session.json"
    try:
        linked.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    symlink_inputs = dict(inputs)
    symlink_inputs["session"] = linked
    symlink = _build_package(symlink_inputs, "symlink-package")
    assert symlink["package_ready"] is False
    assert symlink["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"

    boundary = build_market_aware_session_package(
        session_path=inputs["session"],
        session_report_path=inputs["session_report"],
        freshness_report_path=inputs["freshness_report"],
        session_markdown_path=inputs["session_markdown"],
        session_render_report_path=inputs["session_render_report"],
        artifact_root=inputs["artifact_root"],
        output_dir=inputs["artifact_root"].parent / "outside-package",
    )
    assert boundary["package_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"


def test_package_cli_and_report_hashes(tmp_path: Path, capsys: Any) -> None:
    inputs = _prepare_package_inputs(tmp_path)
    output_dir = inputs["artifact_root"] / "cli-package"
    exit_code = main(
        [
            "build-market-aware-session-package",
            "--session",
            str(inputs["session"]),
            "--session-report",
            str(inputs["session_report"]),
            "--freshness-report",
            str(inputs["freshness_report"]),
            "--session-markdown",
            str(inputs["session_markdown"]),
            "--session-render-report",
            str(inputs["session_render_report"]),
            "--artifact-root",
            str(inputs["artifact_root"]),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    package_path = output_dir / "market_aware_session_package.json"
    assert report["package_ready"] is True
    assert report["output_sha256"] == sha256_bytes(package_path.read_bytes())
    assert (output_dir / "market_aware_session_package_report.json").exists()
