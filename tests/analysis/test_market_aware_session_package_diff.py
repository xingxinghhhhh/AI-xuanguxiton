import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_package_diff import (
    compare_market_aware_session_packages,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session_package import (
    _build_package,
    _prepare_package_inputs,
    _write_json,
)


def _package_paths(inputs: dict[str, Path]) -> dict[str, Path]:
    package_dir = inputs["artifact_root"] / "package"
    return {
        "package": package_dir / "market_aware_session_package.json",
        "package_report": package_dir / "market_aware_session_package_report.json",
    }


def _update_package_manifest(inputs: dict[str, Path]) -> None:
    paths = _package_paths(inputs)
    package = json.loads(paths["package"].read_text(encoding="utf-8"))
    package_report = json.loads(paths["package_report"].read_text(encoding="utf-8"))
    package["artifacts"] = [
        {
            **entry,
            "sha256": sha256_bytes(
                (inputs["artifact_root"] / entry["path"]).read_bytes()
            ),
            "size_bytes": (inputs["artifact_root"] / entry["path"]).stat().st_size,
        }
        for entry in package["artifacts"]
    ]
    _write_json(paths["package"], package)
    package_report["as_of"] = package["as_of"]
    package_report["evaluation_at"] = package["evaluation_at"]
    package_report["reference_at"] = package["reference_at"]
    package_report["status"] = package["status"]
    package_report["session_ready"] = package["session_ready"]
    package_report["freshness_status"] = package["freshness_status"]
    package_report["artifacts"] = package["artifacts"]
    package_report["artifact_count"] = len(package["artifacts"])
    package_report["output_sha256"] = sha256_bytes(paths["package"].read_bytes())
    _write_json(paths["package_report"], package_report)


def _set_current_time(inputs: dict[str, Path], *, as_of: str, evaluation_at: str) -> None:
    session = json.loads(inputs["session"].read_text(encoding="utf-8"))
    freshness_path = inputs["freshness_report"]
    freshness = json.loads(freshness_path.read_text(encoding="utf-8"))
    session_report_path = inputs["session_report"]
    render_report_path = inputs["session_render_report"]
    session_report = json.loads(session_report_path.read_text(encoding="utf-8"))
    render_report = json.loads(render_report_path.read_text(encoding="utf-8"))

    freshness["evaluation_at"] = evaluation_at
    _write_json(freshness_path, freshness)
    session["as_of"] = as_of
    session["evaluation_at"] = evaluation_at
    session["freshness_report_sha256"] = sha256_bytes(freshness_path.read_bytes())
    _write_json(inputs["session"], session)
    session_report = dict(session)
    session_report["output_sha256"] = sha256_bytes(inputs["session"].read_bytes())
    _write_json(session_report_path, session_report)
    render_report["as_of"] = as_of
    render_report["evaluation_at"] = evaluation_at
    render_report["session_sha256"] = sha256_bytes(inputs["session"].read_bytes())
    render_report["session_report_sha256"] = sha256_bytes(session_report_path.read_bytes())
    render_report["freshness_report_sha256"] = sha256_bytes(freshness_path.read_bytes())
    _write_json(render_report_path, render_report)
    package = json.loads(
        (inputs["artifact_root"] / "package" / "market_aware_session_package.json").read_text(
            encoding="utf-8"
        )
    )
    package["as_of"] = as_of
    package["evaluation_at"] = evaluation_at
    package["reference_at"] = session["reference_at"]
    _write_json(inputs["artifact_root"] / "package" / "market_aware_session_package.json", package)
    _update_package_manifest(inputs)


def _prepare_pair(tmp_path: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    previous = _prepare_package_inputs(tmp_path / "previous")
    current = _prepare_package_inputs(tmp_path / "current")
    _build_package(previous, "package")
    _build_package(current, "package")
    _set_current_time(
        current,
        as_of="2026-08-11T00:00:00+00:00",
        evaluation_at="2026-08-11T13:00:00+00:00",
    )
    return previous, current


def _compare(
    previous: dict[str, Path], current: dict[str, Path], output_name: str
) -> dict[str, Any]:
    previous_paths = _package_paths(previous)
    current_paths = _package_paths(current)
    return compare_market_aware_session_packages(
        previous_package_path=previous_paths["package"],
        previous_package_report_path=previous_paths["package_report"],
        previous_artifact_root=previous["artifact_root"],
        current_package_path=current_paths["package"],
        current_package_report_path=current_paths["package_report"],
        current_artifact_root=current["artifact_root"],
        output_dir=current["artifact_root"] / output_name,
    )


def test_ready_package_diff_is_deterministic_and_tracks_artifacts(tmp_path: Path) -> None:
    previous, current = _prepare_pair(tmp_path)

    first = _compare(previous, current, "diff-one")
    second = _compare(previous, current, "diff-two")

    assert first["comparison_ready"] is True
    assert first["decision_ready"] is False
    assert first["symbol"] == "600000.SH"
    assert first["output_sha256"] == second["output_sha256"]
    diff_path = current["artifact_root"] / "diff-one" / "market_aware_session_package_diff.json"
    diff = json.loads(diff_path.read_text(encoding="utf-8"))
    assert diff["diff_version"] == "market-aware-session-package-diff-v1"
    assert "as_of" in diff["changed_fields"]
    assert set(diff["artifact_changes"]) == {
        "session",
        "session_report",
        "freshness_report",
        "session_markdown",
        "session_render_report",
    }
    assert diff["output_sha256"]
    assert first["output_sha256"] == sha256_bytes(diff_path.read_bytes())


def test_diff_reports_session_and_version_changes(tmp_path: Path) -> None:
    previous, current = _prepare_pair(tmp_path)
    session = json.loads(current["session"].read_text(encoding="utf-8"))
    session_report = json.loads(current["session_report"].read_text(encoding="utf-8"))
    for payload in (session, session_report):
        payload.update(
            {
                "market_context_summary_version": "market-context-summary-v2",
                "relative_strength_version": "relative-strength-v2",
                "review_complete": False,
                "review_gate_pass": False,
                "research_release_ready": False,
            }
        )
    _write_json(current["session"], session)
    session_report["output_sha256"] = sha256_bytes(current["session"].read_bytes())
    _write_json(current["session_report"], session_report)
    render_report = json.loads(current["session_render_report"].read_text(encoding="utf-8"))
    render_report["session_sha256"] = sha256_bytes(current["session"].read_bytes())
    render_report["session_report_sha256"] = sha256_bytes(current["session_report"].read_bytes())
    _write_json(current["session_render_report"], render_report)
    _update_package_manifest(current)

    report = _compare(previous, current, "diff")

    assert report["comparison_ready"] is True
    diff = json.loads(
        (current["artifact_root"] / "diff" / "market_aware_session_package_diff.json").read_text(
            encoding="utf-8"
        )
    )
    assert diff["field_changes"]["market_context_summary_version"]["status"] == "changed"
    assert diff["field_changes"]["relative_strength_version"]["status"] == "changed"
    assert diff["field_changes"]["review_gate_pass"]["status"] == "changed"
    assert diff["field_changes"]["research_release_ready"]["status"] == "changed"


@pytest.mark.parametrize("failure", ["symbol", "as_of", "tamper", "decision"])
def test_diff_fails_closed_for_invalid_comparisons(tmp_path: Path, failure: str) -> None:
    previous, current = _prepare_pair(tmp_path / failure)
    paths = _package_paths(current)
    if failure == "symbol":
        package = json.loads(paths["package"].read_text(encoding="utf-8"))
        package["symbol"] = "600001.SZ"
        _write_json(paths["package"], package)
        _update_package_manifest(current)
    elif failure == "as_of":
        _set_current_time(
            current,
            as_of="2026-08-10T00:00:00+00:00",
            evaluation_at="2026-08-10T13:00:00+00:00",
        )
    elif failure == "tamper":
        current["session_markdown"].write_bytes(
            bytearray(current["session_markdown"].read_bytes()).replace(b"#", b"X", 1)
        )
    else:
        package = json.loads(paths["package"].read_text(encoding="utf-8"))
        package["decision_ready"] = True
        _write_json(paths["package"], package)
        _update_package_manifest(current)

    report = _compare(previous, current, "diff")

    assert report["comparison_ready"] is False
    expected = {
        "symbol": "PACKAGE_AUDIT_FAILED",
        "as_of": "AS_OF_ORDER_INVALID",
        "tamper": "PACKAGE_AUDIT_FAILED",
        "decision": "PACKAGE_AUDIT_FAILED",
    }
    assert report["issues"][0]["code"] == expected[failure]


def test_diff_cli_and_invalid_arguments(tmp_path: Path, capsys: Any) -> None:
    previous, current = _prepare_pair(tmp_path)
    previous_paths = _package_paths(previous)
    current_paths = _package_paths(current)
    exit_code = main(
        [
            "compare-market-aware-session-packages",
            "--previous-package",
            str(previous_paths["package"]),
            "--previous-package-report",
            str(previous_paths["package_report"]),
            "--previous-artifact-root",
            str(previous["artifact_root"]),
            "--current-package",
            str(current_paths["package"]),
            "--current-package-report",
            str(current_paths["package_report"]),
            "--current-artifact-root",
            str(current["artifact_root"]),
            "--output-dir",
            str(current["artifact_root"] / "cli-diff"),
        ]
    )
    assert exit_code == 0
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["comparison_ready"] is True

    with pytest.raises(SystemExit):
        main(["compare-market-aware-session-packages"])
