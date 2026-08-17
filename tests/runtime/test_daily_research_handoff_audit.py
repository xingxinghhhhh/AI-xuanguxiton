import json
import os
from pathlib import Path

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.runtime.daily_research_handoff_audit import (
    DAILY_RESEARCH_HANDOFF_AUDIT_VERSION,
    audit_daily_research_handoff,
)
from tests.runtime.test_daily_research_handoff import _build_handoff, _prepare_handoff


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _sign(payload: dict[str, object]) -> dict[str, object]:
    payload = dict(payload)
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    return payload


def _build_audit(root: Path, *, as_of: str = "2026-08-10T08:00:00+00:00") -> dict[str, object]:
    inputs = _prepare_handoff(root, as_of=as_of)
    handoff_dir = root / "handoff"
    _build_handoff(inputs, handoff_dir)
    return audit_daily_research_handoff(
        handoff_path=handoff_dir / "daily_research_handoff.json",
        handoff_report_path=handoff_dir / "daily_research_handoff_report.json",
        artifact_root=root,
        output_dir=root / "audit",
    )


def test_ready_handoff_audit_is_independent_and_self_hashed(tmp_path: Path) -> None:
    report = _build_audit(tmp_path / "ready")

    assert report["audit_version"] == DAILY_RESEARCH_HANDOFF_AUDIT_VERSION
    assert report["status"] == "ready"
    assert report["audit_ready"] is True
    assert report["handoff_ready"] is True
    assert report["decision_ready"] is False
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


def test_stale_and_blocked_handoffs_are_auditable_but_not_promoted(tmp_path: Path) -> None:
    stale = _build_audit(
        tmp_path / "stale", as_of="2026-08-07T12:00:00+00:00"
    )
    assert stale["audit_ready"] is True
    assert stale["status"] == "blocked"
    assert stale["handoff_ready"] is False

    blocked_root = tmp_path / "blocked"
    inputs = _prepare_handoff(blocked_root, blocked=True)
    _build_handoff(inputs, blocked_root / "handoff")
    blocked = audit_daily_research_handoff(
        handoff_path=blocked_root / "handoff/daily_research_handoff.json",
        handoff_report_path=blocked_root / "handoff/daily_research_handoff_report.json",
        artifact_root=blocked_root,
        output_dir=blocked_root / "audit",
    )
    assert blocked["audit_ready"] is True
    assert blocked["status"] == "blocked"
    assert blocked["handoff_ready"] is False


def test_audit_rejects_tamper_missing_and_unknown_fields(tmp_path: Path) -> None:
    root = tmp_path / "tamper"
    inputs = _prepare_handoff(root)
    _build_handoff(inputs, root / "handoff")

    run = json.loads(inputs["run_report"].read_text(encoding="utf-8"))
    run["symbol"] = "000001.SZ"
    _write_json(inputs["run_report"], run)
    tampered = audit_daily_research_handoff(
        handoff_path=root / "handoff/daily_research_handoff.json",
        handoff_report_path=root / "handoff/daily_research_handoff_report.json",
        artifact_root=root,
        output_dir=root / "audit-tampered",
    )
    assert tampered["audit_ready"] is False
    assert tampered["issues"][0]["code"] == "HASH_MISMATCH"

    missing = audit_daily_research_handoff(
        handoff_path=root / "handoff/daily_research_handoff.json",
        handoff_report_path=root / "handoff/missing.json",
        artifact_root=root,
        output_dir=root / "audit-missing",
    )
    assert missing["audit_ready"] is False
    assert missing["issues"][0]["code"] == "INPUT_UNAVAILABLE"

    clean_root = tmp_path / "unknown"
    clean_inputs = _prepare_handoff(clean_root)
    _build_handoff(clean_inputs, clean_root / "handoff")
    handoff_path = clean_root / "handoff/daily_research_handoff.json"
    payload = json.loads(handoff_path.read_text(encoding="utf-8"))
    payload["unknown"] = True
    _write_json(handoff_path, payload)
    unknown = audit_daily_research_handoff(
        handoff_path=handoff_path,
        handoff_report_path=clean_root / "handoff/daily_research_handoff_report.json",
        artifact_root=clean_root,
        output_dir=clean_root / "audit",
    )
    assert unknown["audit_ready"] is False
    assert unknown["issues"][0]["code"] == "HANDOFF_FIELDS_INVALID"


def test_audit_rejects_invalid_or_rewritten_handoff_state(tmp_path: Path) -> None:
    root = tmp_path / "state"
    inputs = _prepare_handoff(root)
    _build_handoff(inputs, root / "handoff")
    handoff_paths = [
        root / "handoff/daily_research_handoff.json",
        root / "handoff/daily_research_handoff_report.json",
    ]
    for path in handoff_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "invalid"
        _write_json(path, _sign(payload))
    invalid = audit_daily_research_handoff(
        handoff_path=handoff_paths[0],
        handoff_report_path=handoff_paths[1],
        artifact_root=root,
        output_dir=root / "audit-invalid",
    )
    assert invalid["audit_ready"] is False
    assert invalid["issues"][0]["code"] == "STATUS_INVALID"

    root = tmp_path / "rewritten"
    inputs = _prepare_handoff(root)
    _build_handoff(inputs, root / "handoff")
    handoff_paths = [
        root / "handoff/daily_research_handoff.json",
        root / "handoff/daily_research_handoff_report.json",
    ]
    for path in handoff_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "blocked"
        payload["handoff_ready"] = False
        _write_json(path, _sign(payload))
    rewritten = audit_daily_research_handoff(
        handoff_path=handoff_paths[0],
        handoff_report_path=handoff_paths[1],
        artifact_root=root,
        output_dir=root / "audit-rewritten",
    )
    assert rewritten["audit_ready"] is False
    assert rewritten["issues"][0]["code"] == "CHAIN_MISMATCH"


def test_ready_admission_must_have_all_internal_references(tmp_path: Path) -> None:
    root = tmp_path / "admission-refs"
    inputs = _prepare_handoff(root)
    _build_handoff(inputs, root / "handoff")
    for path in (inputs["admission"], inputs["admission_report"]):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for field in (
            "run_report_path",
            "run_audit_report_path",
            "calendar_path",
            "calendar_report_path",
            "run_report_sha256",
            "run_audit_report_sha256",
            "calendar_sha256",
            "calendar_report_sha256",
        ):
            payload[field] = None
        _write_json(path, _sign(payload))
    handoff_paths = [
        root / "handoff/daily_research_handoff.json",
        root / "handoff/daily_research_handoff_report.json",
    ]
    for path in handoff_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["admission_sha256"] = sha256_bytes(inputs["admission"].read_bytes())
        payload["admission_report_sha256"] = sha256_bytes(
            inputs["admission_report"].read_bytes()
        )
        _write_json(path, _sign(payload))
    result = audit_daily_research_handoff(
        handoff_path=handoff_paths[0],
        handoff_report_path=handoff_paths[1],
        artifact_root=root,
        output_dir=root / "audit",
    )
    assert result["audit_ready"] is False
    assert result["issues"][0]["code"] == "ADMISSION_REFERENCES_MISSING"


def test_audit_rejects_invalid_admission_and_contradictory_ready_chain(
    tmp_path: Path,
) -> None:
    root = tmp_path / "invalid-admission"
    inputs = _prepare_handoff(root)
    _build_handoff(inputs, root / "handoff")
    for path in (inputs["admission"], inputs["admission_report"]):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "invalid"
        payload["freshness_status"] = "invalid"
        payload["audit_ready"] = False
        payload["admission_ready"] = False
        _write_json(path, _sign(payload))
    handoff_paths = [
        root / "handoff/daily_research_handoff.json",
        root / "handoff/daily_research_handoff_report.json",
    ]
    for path in handoff_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["admission_status"] = "invalid"
        payload["admission_ready"] = False
        payload["admission_sha256"] = sha256_bytes(inputs["admission"].read_bytes())
        payload["admission_report_sha256"] = sha256_bytes(
            inputs["admission_report"].read_bytes()
        )
        _write_json(path, _sign(payload))
    invalid_admission = audit_daily_research_handoff(
        handoff_path=handoff_paths[0],
        handoff_report_path=handoff_paths[1],
        artifact_root=root,
        output_dir=root / "audit",
    )
    assert invalid_admission["audit_ready"] is False
    assert invalid_admission["issues"][0]["code"] == "UPSTREAM_INVALID"

    root = tmp_path / "contradictory"
    inputs = _prepare_handoff(root, blocked=True)
    _build_handoff(inputs, root / "handoff")
    for path in (inputs["admission"], inputs["admission_report"]):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "ready"
        payload["freshness_status"] = "fresh"
        payload["audit_ready"] = True
        payload["analysis_input_ready"] = True
        payload["admission_ready"] = True
        payload["run_report_path"] = inputs["run_report"].relative_to(root).as_posix()
        payload["run_audit_report_path"] = inputs["run_audit_report"].relative_to(
            root
        ).as_posix()
        payload["calendar_path"] = inputs["calendar"].relative_to(root).as_posix()
        payload["calendar_report_path"] = inputs["calendar_report"].relative_to(
            root
        ).as_posix()
        payload["run_report_sha256"] = sha256_bytes(inputs["run_report"].read_bytes())
        payload["run_audit_report_sha256"] = sha256_bytes(
            inputs["run_audit_report"].read_bytes()
        )
        payload["calendar_sha256"] = sha256_bytes(inputs["calendar"].read_bytes())
        payload["calendar_report_sha256"] = sha256_bytes(
            inputs["calendar_report"].read_bytes()
        )
        _write_json(path, _sign(payload))
    handoff_paths = [
        root / "handoff/daily_research_handoff.json",
        root / "handoff/daily_research_handoff_report.json",
    ]
    for path in handoff_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["admission_status"] = "ready"
        payload["admission_ready"] = True
        payload["admission_sha256"] = sha256_bytes(inputs["admission"].read_bytes())
        payload["admission_report_sha256"] = sha256_bytes(
            inputs["admission_report"].read_bytes()
        )
        _write_json(path, _sign(payload))
    contradictory = audit_daily_research_handoff(
        handoff_path=handoff_paths[0],
        handoff_report_path=handoff_paths[1],
        artifact_root=root,
        output_dir=root / "audit",
    )
    assert contradictory["audit_ready"] is False
    assert contradictory["issues"][0]["code"] == "CHAIN_MISMATCH"


def test_audit_rejects_path_escape_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "paths"
    inputs = _prepare_handoff(root)
    _build_handoff(inputs, root / "handoff")
    handoff_paths = [
        root / "handoff/daily_research_handoff.json",
        root / "handoff/daily_research_handoff_report.json",
    ]
    for path in handoff_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["run_report_path"] = "../outside.json"
        _write_json(path, _sign(payload))
    outside = root.parent / "outside.json"
    outside.write_bytes(inputs["run_report"].read_bytes())
    escaped = audit_daily_research_handoff(
        handoff_path=handoff_paths[0],
        handoff_report_path=handoff_paths[1],
        artifact_root=root,
        output_dir=root / "audit-escape",
    )
    assert escaped["audit_ready"] is False
    assert escaped["issues"][0]["code"] == "PATH_INVALID"

    symlink_root = tmp_path / "symlink"
    symlink_inputs = _prepare_handoff(symlink_root)
    _build_handoff(symlink_inputs, symlink_root / "handoff")
    link = symlink_root / "linked-run.json"
    symlink_outside = tmp_path / "symlink-outside.json"
    symlink_outside.write_bytes(symlink_inputs["run_report"].read_bytes())
    try:
        os.symlink(symlink_outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this Windows host")
    for path in (
        symlink_root / "handoff/daily_research_handoff.json",
        symlink_root / "handoff/daily_research_handoff_report.json",
    ):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["run_report_path"] = "linked-run.json"
        payload["run_report_sha256"] = sha256_bytes(link.read_bytes())
        _write_json(path, _sign(payload))
    symlink_result = audit_daily_research_handoff(
        handoff_path=symlink_root / "handoff/daily_research_handoff.json",
        handoff_report_path=symlink_root / "handoff/daily_research_handoff_report.json",
        artifact_root=symlink_root,
        output_dir=symlink_root / "audit",
    )
    assert symlink_result["audit_ready"] is False
    assert symlink_result["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"


def test_audit_is_deterministic_and_does_not_modify_inputs(tmp_path: Path) -> None:
    root = tmp_path / "stable"
    inputs = _prepare_handoff(root)
    _build_handoff(inputs, root / "handoff")
    tracked = [
        inputs["run_report"],
        inputs["run_audit_report"],
        inputs["admission"],
        inputs["admission_report"],
        root / "handoff/daily_research_handoff.json",
        root / "handoff/daily_research_handoff_report.json",
    ]
    before = {path: path.read_bytes() for path in tracked}
    first = audit_daily_research_handoff(
        handoff_path=tracked[-2],
        handoff_report_path=tracked[-1],
        artifact_root=root,
        output_dir=root / "audit",
    )
    second = audit_daily_research_handoff(
        handoff_path=tracked[-2],
        handoff_report_path=tracked[-1],
        artifact_root=root,
        output_dir=root / "audit",
    )
    assert first == second
    assert before == {path: path.read_bytes() for path in tracked}


def test_cli_audit_returns_zero_one_and_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "cli-ready"
    inputs = _prepare_handoff(root)
    _build_handoff(inputs, root / "handoff")
    code = main(
        [
            "audit-daily-research-handoff",
            "--handoff",
            str(root / "handoff/daily_research_handoff.json"),
            "--handoff-report",
            str(root / "handoff/daily_research_handoff_report.json"),
            "--artifact-root",
            str(root),
            "--output-dir",
            str(root / "audit"),
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["audit_ready"] is True

    stale_root = tmp_path / "cli-stale"
    stale_inputs = _prepare_handoff(stale_root, as_of="2026-08-07T12:00:00+00:00")
    _build_handoff(stale_inputs, stale_root / "handoff")
    stale_code = main(
        [
            "audit-daily-research-handoff",
            "--handoff",
            str(stale_root / "handoff/daily_research_handoff.json"),
            "--handoff-report",
            str(stale_root / "handoff/daily_research_handoff_report.json"),
            "--artifact-root",
            str(stale_root),
            "--output-dir",
            str(stale_root / "audit"),
        ]
    )
    assert stale_code == 0
    assert json.loads(capsys.readouterr().out)["handoff_ready"] is False

    with pytest.raises(SystemExit) as exc_info:
        main(["audit-daily-research-handoff", "--handoff", str(root / "missing.json")])
    assert exc_info.value.code == 2
