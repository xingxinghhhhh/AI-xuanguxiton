import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history import (
    build_market_aware_session_history,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_package import (
    _build_package,
    _prepare_package_inputs,
)
from tests.analysis.test_market_aware_session_package_diff import _set_current_time


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


def _package_reference(inputs: dict[str, Path], root: Path) -> dict[str, str]:
    package_dir = inputs["artifact_root"] / "package"
    return {
        "artifact_root": inputs["artifact_root"].relative_to(root).as_posix(),
        "manifest_path": (package_dir / "market_aware_session_package.json")
        .relative_to(root)
        .as_posix(),
        "report_path": (package_dir / "market_aware_session_package_report.json")
        .relative_to(root)
        .as_posix(),
    }


def _write_spec(root: Path, references: list[dict[str, str]], **extra: Any) -> Path:
    payload: dict[str, Any] = {
        "history_version": "market-aware-session-history-v1",
        "packages": references,
    }
    payload.update(extra)
    path = root / "session_history_spec.json"
    _write_json(path, payload)
    return path


def _build(inputs: dict[str, Path], spec: Path, output_name: str) -> dict[str, Any]:
    return build_market_aware_session_history(
        spec_path=spec,
        history_root=inputs["artifact_root"].parent.parent,
        output_dir=inputs["artifact_root"].parent.parent / output_name,
    )


def _refresh_package(inputs: dict[str, Path]) -> None:
    root = inputs["artifact_root"]
    package_dir = root / "package"
    session = json.loads(inputs["session"].read_text(encoding="utf-8"))
    package_path = package_dir / "market_aware_session_package.json"
    report_path = package_dir / "market_aware_session_package_report.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package_report = json.loads(report_path.read_text(encoding="utf-8"))
    package.update(
        {
            "as_of": session["as_of"],
            "evaluation_at": session["evaluation_at"],
            "freshness_status": session["freshness_status"],
            "reference_at": session["reference_at"],
            "session_ready": session["session_ready"],
            "status": session["status"],
            "symbol": session["symbol"],
        }
    )
    files = {
        "session": inputs["session"],
        "session_report": inputs["session_report"],
        "freshness_report": inputs["freshness_report"],
        "session_markdown": inputs["session_markdown"],
        "session_render_report": inputs["session_render_report"],
    }
    for entry in package["artifacts"]:
        path = files[entry["role"]]
        entry["sha256"] = sha256_bytes(path.read_bytes())
        entry["size_bytes"] = path.stat().st_size
    _write_json(package_path, package)
    package_report.update(
        {
            "as_of": package["as_of"],
            "artifacts": package["artifacts"],
            "artifact_count": len(package["artifacts"]),
            "evaluation_at": package["evaluation_at"],
            "freshness_status": package["freshness_status"],
            "output_sha256": sha256_bytes(package_path.read_bytes()),
            "reference_at": package["reference_at"],
            "session_ready": package["session_ready"],
            "status": package["status"],
            "symbol": package["symbol"],
        }
    )
    _write_json(report_path, package_report)


def test_history_is_ready_sorted_and_deterministic(tmp_path: Path) -> None:
    previous, current = _prepare_pair(tmp_path)
    spec = _write_spec(
        tmp_path,
        [_package_reference(previous, tmp_path), _package_reference(current, tmp_path)],
    )

    first = _build(previous, spec, "history-one")
    second = _build(previous, spec, "history-two")

    assert first["history_ready"] is True
    assert first["package_count"] == 2
    assert first["symbol"] == "600000.SH"
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    history_path = tmp_path / "history-one" / "market_aware_session_history.json"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["history_version"] == "market-aware-session-history-v1"
    assert history["packages"][0]["as_of"] < history["packages"][1]["as_of"]
    assert history["packages"][0]["package_sha256"]
    assert all(item["decision_ready"] is not True for item in history["packages"])


def test_history_preserves_stale_session_without_upgrade(tmp_path: Path) -> None:
    previous = _prepare_package_inputs(tmp_path / "previous")
    stale = _prepare_package_inputs(
        tmp_path / "stale",
        stale=True,
    )
    _build_package(previous, "package")
    _build_package(stale, "package")
    _set_current_time(
        stale,
        as_of="2026-08-11T00:00:00+00:00",
        evaluation_at="2026-08-11T08:00:00+00:00",
    )
    spec = _write_spec(
        tmp_path,
        [_package_reference(previous, tmp_path), _package_reference(stale, tmp_path)],
    )

    report = _build(previous, spec, "history")

    assert report["history_ready"] is True
    history = json.loads(
        (tmp_path / "history" / "market_aware_session_history.json").read_text(
            encoding="utf-8"
        )
    )
    assert history["packages"][1]["session_ready"] is False
    assert history["packages"][1]["freshness_status"] == "stale"


@pytest.mark.parametrize("failure", ["count", "unknown", "duplicate", "absolute"])
def test_history_rejects_invalid_specs(tmp_path: Path, failure: str) -> None:
    previous, current = _prepare_pair(tmp_path / failure)
    root = tmp_path / failure
    references = [_package_reference(previous, root), _package_reference(current, root)]
    if failure == "count":
        references = references[:1]
    elif failure == "unknown":
        spec = _write_spec(root, references, unexpected=True)
    elif failure == "duplicate":
        references[1] = references[0]
        spec = _write_spec(root, references)
    else:
        references[0]["manifest_path"] = str(Path(references[0]["manifest_path"]).resolve())
        spec = _write_spec(root, references)
    if failure == "count":
        spec = _write_spec(root, references)

    report = _build(previous, spec, "history")

    assert report["history_ready"] is False
    expected = {
        "count": "PACKAGE_COUNT_INVALID",
        "unknown": "SPEC_INVALID",
        "duplicate": "DUPLICATE_PACKAGE",
        "absolute": "PATH_INVALID",
    }
    assert report["issues"][0]["code"] == expected[failure]


@pytest.mark.parametrize("failure", ["symbol", "duplicate_as_of", "reverse_as_of", "audit"])
def test_history_rejects_invalid_package_chain(tmp_path: Path, failure: str) -> None:
    previous, current = _prepare_pair(tmp_path / failure)
    root = tmp_path / failure
    if failure == "duplicate_as_of":
        _set_current_time(
            current,
            as_of="2026-08-10T00:00:00+00:00",
            evaluation_at="2026-08-10T13:00:00+00:00",
        )
    elif failure == "reverse_as_of":
        _set_current_time(
            current,
            as_of="2026-08-09T00:00:00+00:00",
            evaluation_at="2026-08-09T13:00:00+00:00",
        )
    elif failure == "audit":
        current["session_markdown"].write_bytes(
            current["session_markdown"].read_bytes() + b"tampered\n"
        )
    elif failure == "symbol":
        session = json.loads(current["session"].read_text(encoding="utf-8"))
        session_report = json.loads(current["session_report"].read_text(encoding="utf-8"))
        render_report = json.loads(current["session_render_report"].read_text(encoding="utf-8"))
        for payload in (session, session_report):
            payload["symbol"] = "600001.SZ"
        _write_json(current["session"], session)
        session_report["output_sha256"] = sha256_bytes(current["session"].read_bytes())
        _write_json(current["session_report"], session_report)
        render_report["symbol"] = "600001.SZ"
        render_report["session_sha256"] = sha256_bytes(current["session"].read_bytes())
        render_report["session_report_sha256"] = sha256_bytes(
            current["session_report"].read_bytes()
        )
        _write_json(current["session_render_report"], render_report)
        _refresh_package(current)
    spec = _write_spec(
        root,
        [_package_reference(previous, root), _package_reference(current, root)],
    )

    report = _build(previous, spec, "history")

    assert report["history_ready"] is False
    expected = {
        "symbol": "SYMBOL_MISMATCH",
        "duplicate_as_of": "AS_OF_ORDER_INVALID",
        "reverse_as_of": "AS_OF_ORDER_INVALID",
        "audit": "PACKAGE_AUDIT_FAILED",
    }
    assert report["issues"][0]["code"] == expected[failure]


def test_history_cli_and_output_boundary(tmp_path: Path, capsys: Any) -> None:
    previous, current = _prepare_pair(tmp_path)
    spec = _write_spec(
        tmp_path,
        [_package_reference(previous, tmp_path), _package_reference(current, tmp_path)],
    )
    output_dir = tmp_path / "cli-history"
    exit_code = main(
        [
            "build-market-aware-session-history",
            "--spec",
            str(spec),
            "--history-root",
            str(tmp_path),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["history_ready"] is True

    outside = tmp_path.parent / "outside-history"
    boundary = build_market_aware_session_history(
        spec_path=spec,
        history_root=tmp_path,
        output_dir=outside,
    )
    assert boundary["history_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_HISTORY_ROOT"
    assert not (outside / "market_aware_session_history_report.json").exists()

    with pytest.raises(SystemExit):
        main(["build-market-aware-session-history"])
