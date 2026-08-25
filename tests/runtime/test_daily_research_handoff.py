import json
from pathlib import Path

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.runtime.daily_research_handoff import (
    DAILY_RESEARCH_HANDOFF_VERSION,
    build_daily_research_handoff,
)
from a_share_ai.service.read_only_receipt_server import load_daily_research_admission_summary
from tests.runtime.test_daily_research_admission import _build, _prepare


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _prepare_handoff(
    root: Path, *, as_of: str = "2026-08-10T08:00:00+00:00", blocked: bool = False
) -> dict[str, Path]:
    inputs = _prepare(root, as_of=as_of, blocked=blocked)
    admission_dir = root / "admission"
    _build(inputs, "2026-08-10T16:00:00+08:00", admission_dir)
    return {
        **inputs,
        "admission": admission_dir / "daily_research_admission.json",
        "admission_report": admission_dir / "daily_research_admission_report.json",
    }


def _build_handoff(inputs: dict[str, Path], output_dir: Path) -> dict[str, object]:
    return build_daily_research_handoff(
        run_report_path=inputs["run_report"],
        run_audit_report_path=inputs["run_audit_report"],
        admission_path=inputs["admission"],
        admission_report_path=inputs["admission_report"],
        artifact_root=inputs["run_report"].parent,
        output_dir=output_dir,
    )


def test_ready_handoff_is_self_hashed_and_bounded(tmp_path: Path) -> None:
    inputs = _prepare_handoff(tmp_path / "ready")
    report = _build_handoff(inputs, tmp_path / "ready/handoff")

    assert report["handoff_version"] == DAILY_RESEARCH_HANDOFF_VERSION
    assert report["status"] == "ready"
    assert report["handoff_ready"] is True
    assert report["run_status"] == "ready"
    assert report["audit_ready"] is True
    assert report["admission_status"] == "ready"
    assert report["admission_ready"] is True
    assert report["decision_ready"] is False
    service_summary = load_daily_research_admission_summary(
        admission_path=inputs["admission"],
        admission_report_path=inputs["admission_report"],
        artifact_root=inputs["run_report"].parent,
    )
    assert service_summary["admission_ready"] is True
    assert all(isinstance(report[field], str) for field in (
        "run_report_path",
        "run_audit_report_path",
        "admission_path",
        "admission_report_path",
    ))
    assert all("\\" not in report[field] for field in (
        "run_report_path",
        "run_audit_report_path",
        "admission_path",
        "admission_report_path",
    ))
    assert report["output_sha256"] == sha256_bytes(
        (
            json.dumps(
                {**report, "output_sha256": None},
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode()
    )
    assert json.loads(
        (tmp_path / "ready/handoff/daily_research_handoff.json").read_text(encoding="utf-8")
    ) == json.loads(
        (tmp_path / "ready/handoff/daily_research_handoff_report.json").read_text(
            encoding="utf-8"
        )
    )


def test_stale_and_blocked_inputs_are_visible_but_not_ready(tmp_path: Path) -> None:
    stale = _prepare_handoff(
        tmp_path / "stale", as_of="2026-08-07T12:00:00+00:00"
    )
    stale_report = _build_handoff(stale, tmp_path / "stale/handoff")
    assert stale_report["status"] == "blocked"
    assert stale_report["admission_status"] == "stale"
    assert stale_report["handoff_ready"] is False
    assert stale_report["issues"][0]["code"] == "RESEARCH_STALE"

    blocked = _prepare_handoff(tmp_path / "blocked", blocked=True)
    blocked_report = _build_handoff(blocked, tmp_path / "blocked/handoff")
    assert blocked_report["status"] == "blocked"
    assert blocked_report["run_status"] == "blocked"
    assert blocked_report["audit_ready"] is False
    assert blocked_report["admission_status"] == "blocked"
    assert blocked_report["handoff_ready"] is False


def test_handoff_rejects_symbol_mismatch_and_tampered_run(tmp_path: Path) -> None:
    inputs = _prepare_handoff(tmp_path / "mismatch")
    for path in (inputs["admission"], inputs["admission_report"]):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["symbol"] = "000001.SZ"
        payload["output_sha256"] = None
        payload["output_sha256"] = sha256_bytes(
            (
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode()
        )
        _write_json(path, payload)
    mismatch = _build_handoff(inputs, tmp_path / "mismatch/handoff")
    assert mismatch["status"] == "invalid"
    assert mismatch["issues"][0]["code"] == "CHAIN_MISMATCH"

    tampered = _prepare_handoff(tmp_path / "tampered")
    payload = json.loads(tampered["run_report"].read_text(encoding="utf-8"))
    payload["symbol"] = "000001.SZ"
    _write_json(tampered["run_report"], payload)
    tampered_report = _build_handoff(tampered, tmp_path / "tampered/handoff")
    assert tampered_report["status"] == "invalid"
    assert tampered_report["issues"][0]["code"] == "HASH_MISMATCH"


def test_handoff_rejects_path_outside_root_and_is_deterministic(tmp_path: Path) -> None:
    inputs = _prepare_handoff(tmp_path / "paths")
    outside = tmp_path / "outside.json"
    outside.write_bytes(inputs["run_report"].read_bytes())
    inputs["run_report"] = outside
    report = build_daily_research_handoff(
        run_report_path=inputs["run_report"],
        run_audit_report_path=inputs["run_audit_report"],
        admission_path=inputs["admission"],
        admission_report_path=inputs["admission_report"],
        artifact_root=tmp_path / "paths",
        output_dir=tmp_path / "paths/handoff",
    )
    assert report["status"] == "invalid"
    assert report["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"

    stable = _prepare_handoff(tmp_path / "stable")
    first = _build_handoff(stable, tmp_path / "stable/handoff")
    second = _build_handoff(stable, tmp_path / "stable/handoff")
    assert first == second


def test_cli_handoff_returns_zero_one_and_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _prepare_handoff(tmp_path / "cli-ready")
    ready_code = main(
        [
            "build-daily-research-handoff",
            "--run-report",
            str(inputs["run_report"]),
            "--run-audit-report",
            str(inputs["run_audit_report"]),
            "--admission",
            str(inputs["admission"]),
            "--admission-report",
            str(inputs["admission_report"]),
            "--artifact-root",
            str(tmp_path / "cli-ready"),
            "--output-dir",
            str(tmp_path / "cli-ready/handoff"),
        ]
    )
    assert ready_code == 0
    assert json.loads(capsys.readouterr().out)["handoff_ready"] is True

    stale = _prepare_handoff(
        tmp_path / "cli-stale", as_of="2026-08-07T12:00:00+00:00"
    )
    stale_code = main(
        [
            "build-daily-research-handoff",
            "--run-report",
            str(stale["run_report"]),
            "--run-audit-report",
            str(stale["run_audit_report"]),
            "--admission",
            str(stale["admission"]),
            "--admission-report",
            str(stale["admission_report"]),
            "--artifact-root",
            str(tmp_path / "cli-stale"),
            "--output-dir",
            str(tmp_path / "cli-stale/handoff"),
        ]
    )
    assert stale_code == 1
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        main(["build-daily-research-handoff", "--run-report", str(tmp_path / "missing.json")])
    assert exc_info.value.code == 2
