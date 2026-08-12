import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history import build_market_aware_session_history
from a_share_ai.analysis.market_aware_session_history_audit import (
    audit_market_aware_session_history,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history import (
    _package_reference,
    _prepare_pair,
    _write_spec,
)
from tests.analysis.test_market_aware_session_package import (
    _build_package,
    _prepare_package_inputs,
)
from tests.analysis.test_market_aware_session_package_diff import _set_current_time


def _prepare_history(tmp_path: Path) -> dict[str, Path]:
    previous, current = _prepare_pair(tmp_path)
    spec = _write_spec(
        tmp_path,
        [_package_reference(previous, tmp_path), _package_reference(current, tmp_path)],
    )
    build_market_aware_session_history(
        spec_path=spec,
        history_root=tmp_path,
        output_dir=tmp_path / "history",
    )
    return {
        "history": tmp_path / "history" / "market_aware_session_history.json",
        "history_report": tmp_path
        / "history"
        / "market_aware_session_history_report.json",
        "root": tmp_path,
    }


def _audit(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return audit_market_aware_session_history(
        history_path=paths["history"],
        history_report_path=paths["history_report"],
        history_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def _refresh_history_report(paths: dict[str, Path]) -> None:
    report = json.loads(paths["history_report"].read_text(encoding="utf-8"))
    report["output_sha256"] = sha256_bytes(paths["history"].read_bytes())
    _write_json(paths["history_report"], report)


def test_ready_history_audit_is_deterministic_and_complete(tmp_path: Path) -> None:
    paths = _prepare_history(tmp_path)

    first = _audit(paths, "audit-one")
    second = _audit(paths, "audit-two")

    assert first["audit_ready"] is True
    assert first["package_count"] == 2
    assert first["symbol"] == "600000.SH"
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    assert first["history_size_bytes"] == paths["history"].stat().st_size
    assert first["history_report_size_bytes"] == paths["history_report"].stat().st_size
    assert all(item["audit_ready"] is True for item in first["packages"])


def test_stale_history_audit_preserves_blocked_package(tmp_path: Path) -> None:
    previous = _prepare_package_inputs(tmp_path / "previous")
    current = _prepare_package_inputs(tmp_path / "current", stale=True)
    _build_package(previous, "package")
    _build_package(current, "package")
    _set_current_time(
        current,
        as_of="2026-08-11T00:00:00+00:00",
        evaluation_at="2026-08-11T08:00:00+00:00",
    )
    spec = _write_spec(
        tmp_path,
        [_package_reference(previous, tmp_path), _package_reference(current, tmp_path)],
    )
    build_market_aware_session_history(
        spec_path=spec,
        history_root=tmp_path,
        output_dir=tmp_path / "history",
    )
    paths = {
        "history": tmp_path / "history" / "market_aware_session_history.json",
        "history_report": tmp_path
        / "history"
        / "market_aware_session_history_report.json",
        "root": tmp_path,
    }

    report = _audit(paths, "audit")

    assert report["audit_ready"] is True
    assert report["packages"][1]["session_ready"] is False
    assert report["packages"][1]["freshness_status"] == "stale"


@pytest.mark.parametrize("mutation", ["history", "report", "package", "sha"])
def test_history_audit_fails_closed_for_tamper(tmp_path: Path, mutation: str) -> None:
    paths = _prepare_history(tmp_path / mutation)
    if mutation == "history":
        history = json.loads(paths["history"].read_text(encoding="utf-8"))
        history["symbol"] = "600001.SZ"
        _write_json(paths["history"], history)
    elif mutation == "report":
        report = json.loads(paths["history_report"].read_text(encoding="utf-8"))
        report["output_sha256"] = "0" * 64
        _write_json(paths["history_report"], report)
    elif mutation == "package":
        history = json.loads(paths["history"].read_text(encoding="utf-8"))
        package_path = paths["root"] / history["packages"][0]["manifest_path"]
        package = json.loads(package_path.read_text(encoding="utf-8"))
        package["decision_ready"] = True
        _write_json(package_path, package)
    else:
        history = json.loads(paths["history"].read_text(encoding="utf-8"))
        history["packages"][0]["package_sha256"] = "0" * 64
        _write_json(paths["history"], history)
        _refresh_history_report(paths)

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] in {
        "HASH_MISMATCH",
        "FIELD_MISMATCH",
        "PACKAGE_AUDIT_FAILED",
    }


@pytest.mark.parametrize("mutation", ["version", "decision", "json", "path"])
def test_history_audit_rejects_invalid_contract(tmp_path: Path, mutation: str) -> None:
    paths = _prepare_history(tmp_path / mutation)
    if mutation == "version":
        history = json.loads(paths["history"].read_text(encoding="utf-8"))
        history["history_version"] = "other-v1"
        _write_json(paths["history"], history)
        _refresh_history_report(paths)
    elif mutation == "decision":
        history = json.loads(paths["history"].read_text(encoding="utf-8"))
        history["decision_ready"] = True
        _write_json(paths["history"], history)
        _refresh_history_report(paths)
    elif mutation == "json":
        paths["history"].write_text("bad-json\n", encoding="utf-8")
    else:
        history = json.loads(paths["history"].read_text(encoding="utf-8"))
        history["packages"][0]["manifest_path"] = "../outside.json"
        _write_json(paths["history"], history)
        _refresh_history_report(paths)

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    expected = {
        "version": "VERSION_MISMATCH",
        "decision": "DECISION_GATE_INVALID",
        "json": "INPUT_JSON_INVALID",
        "path": "PATH_INVALID",
    }
    assert report["issues"][0]["code"] == expected[mutation]


def test_history_audit_rejects_as_of_and_missing_or_duplicate_references(
    tmp_path: Path,
) -> None:
    paths = _prepare_history(tmp_path)
    history = json.loads(paths["history"].read_text(encoding="utf-8"))
    history["packages"][1]["as_of"] = history["packages"][0]["as_of"]
    _write_json(paths["history"], history)
    _refresh_history_report(paths)

    duplicate = _audit(paths, "duplicate-audit")
    assert duplicate["audit_ready"] is False
    assert duplicate["issues"][0]["code"] == "FIELD_MISMATCH"

    history = json.loads(paths["history"].read_text(encoding="utf-8"))
    history["packages"][0]["report_path"] = history["packages"][0]["manifest_path"]
    _write_json(paths["history"], history)
    _refresh_history_report(paths)
    duplicate_reference = _audit(paths, "reference-audit")
    assert duplicate_reference["audit_ready"] is False
    assert duplicate_reference["issues"][0]["code"] == "PACKAGE_AUDIT_FAILED"


def test_history_audit_cli_and_output_boundary(tmp_path: Path, capsys: Any) -> None:
    paths = _prepare_history(tmp_path)
    output_dir = tmp_path / "cli-audit"
    exit_code = main(
        [
            "audit-market-aware-session-history",
            "--history",
            str(paths["history"]),
            "--history-report",
            str(paths["history_report"]),
            "--history-root",
            str(paths["root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["audit_ready"] is True

    outside = tmp_path.parent / "outside-history-audit"
    boundary = audit_market_aware_session_history(
        history_path=paths["history"],
        history_report_path=paths["history_report"],
        history_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["audit_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_HISTORY_ROOT"
    assert not (outside / "market_aware_session_history_audit_report.json").exists()

    with pytest.raises(SystemExit):
        main(["audit-market-aware-session-history"])
