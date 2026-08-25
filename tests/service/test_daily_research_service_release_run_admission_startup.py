import json
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service.daily_research_service_release_run_admission import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME,
    build_daily_research_service_release_run_admission,
)
from a_share_ai.service.daily_research_service_release_run_admission_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME,
    audit_daily_research_service_release_run_admission,
)
from a_share_ai.service.daily_research_service_release_run_admission_startup import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME,
    DailyResearchServiceReleaseRunAdmissionStartupError,
)
from a_share_ai.service.daily_research_service_release_run_audit import (
    audit_daily_research_service_release_run,
)
from a_share_ai.service.daily_research_service_release_startup import (
    load_daily_research_service_release_startup,
)
from a_share_ai.service.read_only_receipt_server import ReadOnlyReceiptServiceError
from tests.service.test_daily_research_service_release_run_audit import _make_run


def _prepare_startup(tmp_path: Path, **kwargs: bool) -> dict[str, Path]:
    root, run_path = _make_run(tmp_path / "release", **kwargs)
    run = json.loads(run_path.read_text(encoding="utf-8"))
    manifest = root / run["release_manifest_path"]
    release_report = root / run["release_report_path"]
    release_audit = root / run["release_audit_report_path"]
    run_audit_dir = root / "run-audit"
    audit_daily_research_service_release_run(
        run_report_path=run_path,
        artifact_root=root,
        output_dir=run_audit_dir,
    )
    run_audit = run_audit_dir / "daily_research_service_release_run_audit_report.json"
    admission_dir = root / "admission"
    build_daily_research_service_release_run_admission(
        run_report_path=run_path,
        run_audit_report_path=run_audit,
        artifact_root=root,
        output_dir=admission_dir,
    )
    admission = admission_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME
    admission_report = admission_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME
    admission_audit_dir = root / "admission-audit"
    audit_daily_research_service_release_run_admission(
        admission_path=admission,
        report_path=admission_report,
        artifact_root=root,
        output_dir=admission_audit_dir,
    )
    return {
        "root": root,
        "manifest": manifest,
        "release_report": release_report,
        "release_audit": release_audit,
        "admission": admission,
        "admission_report": admission_report,
        "admission_audit": admission_audit_dir
        / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME,
    }


def _args(paths: dict[str, Path], output_dir: Path, *, check_only: bool = False) -> list[str]:
    args = [
        "serve-research-receipt",
        "--artifact-root",
        str(paths["root"]),
        "--daily-run-admission",
        str(paths["admission"]),
        "--daily-run-admission-report",
        str(paths["admission_report"]),
        "--daily-run-admission-audit",
        str(paths["admission_audit"]),
        "--daily-release-manifest",
        str(paths["manifest"]),
        "--daily-release-report",
        str(paths["release_report"]),
        "--daily-release-audit-report",
        str(paths["release_audit"]),
        "--output-dir",
        str(output_dir),
    ]
    if check_only:
        args.append("--check-only")
    return args


def _output(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    return json.loads(raw), raw


def _assert_compact_self_hash(payload: dict) -> None:
    canonical = dict(payload)
    declared = canonical.pop("output_sha256")
    canonical["output_sha256"] = None
    assert declared == sha256_bytes(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _write_hashed(path: Path, payload: dict) -> None:
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _port_closed(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def test_ready_check_only_writes_exact_stdout_without_binding(tmp_path: Path, capsys) -> None:
    paths = _prepare_startup(tmp_path / "ready")
    output_dir = paths["root"] / "startup-check"
    with patch(
        "a_share_ai.service.daily_research_service_release_run_admission_startup.create_read_only_receipt_server"
    ) as create_server:
        assert main(_args(paths, output_dir, check_only=True)) == 0
    stdout = capsys.readouterr().out.encode()
    report, raw = _output(
        output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    )
    assert stdout == raw
    assert report["mode"] == "check_only"
    assert report["service_started"] is False
    assert report["startup_ready"] is True
    assert report["status"] == "ready"
    assert report["decision_ready"] is False
    for key, path_key in (
        ("release_manifest_path", "manifest"),
        ("release_report_path", "release_report"),
        ("release_audit_path", "release_audit"),
    ):
        assert report[key] == paths[path_key].relative_to(paths["root"]).as_posix()
    for key, path_key in (
        ("release_manifest_sha256", "manifest"),
        ("release_report_sha256", "release_report"),
        ("release_audit_sha256", "release_audit"),
    ):
        assert report[key] == sha256_bytes(paths[path_key].read_bytes())
    assert str(paths["root"]) not in raw.decode()
    _assert_compact_self_hash(report)
    create_server.assert_not_called()


def test_ready_check_only_is_deterministic_and_preserves_inputs(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "deterministic")
    tracked = [
        paths[key]
        for key in (
            "manifest",
            "release_report",
            "release_audit",
            "admission",
            "admission_report",
            "admission_audit",
        )
    ]
    before = {path: path.read_bytes() for path in tracked}
    output_dir = paths["root"] / "startup-check"
    assert main(_args(paths, output_dir, check_only=True)) == 0
    first = (
        output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    ).read_bytes()
    assert main(_args(paths, output_dir, check_only=True)) == 0
    second = (
        output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    ).read_bytes()
    assert first == second
    assert {path: path.read_bytes() for path in tracked} == before


@pytest.mark.parametrize("kwargs", [{"blocked": True}, {"failed": True}])
def test_non_ready_admission_never_binds_and_writes_status_report(
    tmp_path: Path, kwargs: dict[str, bool]
) -> None:
    paths = _prepare_startup(
        tmp_path / ("blocked" if kwargs.get("blocked") else "failed"), **kwargs
    )
    with patch(
        "a_share_ai.service.daily_research_service_release_run_admission_startup.create_read_only_receipt_server"
    ) as create_server:
        assert main(_args(paths, paths["root"] / "startup")) == 1
    report, _ = _output(
        paths["root"] / "startup" / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    )
    assert report["status"] in {"blocked", "failed"}
    assert report["startup_ready"] is False
    assert report["service_started"] is False
    assert report["decision_ready"] is False
    create_server.assert_not_called()


@pytest.mark.parametrize("target", ["admission", "admission_report", "admission_audit"])
def test_tampered_admission_chain_fails_closed_before_bind(tmp_path: Path, target: str) -> None:
    paths = _prepare_startup(tmp_path / target)
    path = paths[target]
    payload = json.loads(path.read_text(encoding="utf-8"))
    field = "status" if target != "admission_audit" else "audit_ready"
    payload[field] = "blocked" if field == "status" else False
    if target == "admission_report":
        _write_hashed(path, payload)
    else:
        _write_hashed(path, payload)
    with patch(
        "a_share_ai.service.daily_research_service_release_run_admission_startup.create_read_only_receipt_server"
    ) as create_server:
        assert main(_args(paths, paths["root"] / "startup")) == 1
    report, _ = _output(
        paths["root"] / "startup" / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    )
    assert report["status"] == "invalid"
    assert report["startup_ready"] is False
    create_server.assert_not_called()


def test_release_chain_tamper_is_invalid_and_never_binds(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "release-tamper")
    payload = json.loads(paths["release_audit"].read_text(encoding="utf-8"))
    payload["status"] = "blocked"
    _write_hashed(paths["release_audit"], payload)
    with patch(
        "a_share_ai.service.daily_research_service_release_run_admission_startup.create_read_only_receipt_server"
    ) as create_server:
        assert main(_args(paths, paths["root"] / "startup")) == 1
    report, _ = _output(
        paths["root"] / "startup" / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    )
    assert report["status"] == "invalid"
    create_server.assert_not_called()


@pytest.mark.parametrize("field", ["status", "run_status"])
@pytest.mark.parametrize("value", [[], {}, None, 1, 1.5])
def test_non_string_admission_enum_is_invalid_without_uncaught_type_error(
    tmp_path: Path, field: str, value: object
) -> None:
    paths = _prepare_startup(tmp_path / "invalid-status")
    payload = json.loads(paths["admission"].read_text(encoding="utf-8"))
    payload[field] = value
    _write_hashed(paths["admission"], payload)
    with patch(
        "a_share_ai.service.daily_research_service_release_run_admission_startup.create_read_only_receipt_server"
    ) as create_server:
        assert main(_args(paths, paths["root"] / "startup")) == 1
    report, _ = _output(
        paths["root"] / "startup" / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    )
    assert report["status"] == "invalid"
    assert report["issues"][0]["code"] == "FIELD_MISMATCH"
    create_server.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("release_version", "other-release-v1"),
        ("symbol", "OTHER.SYMBOL"),
        ("as_of", "2026-08-11T00:00:00Z"),
        ("evaluation_at", "2026-08-11T01:00:00Z"),
        ("status", "blocked"),
        ("release_ready", False),
        ("audit_ready", False),
        ("startup_ready", False),
        ("decision_ready", True),
    ],
)
def test_admission_identity_must_match_loaded_release_startup(
    tmp_path: Path, field: str, value: object
) -> None:
    paths = _prepare_startup(tmp_path / "identity")
    real_loader = load_daily_research_service_release_startup

    def mismatched_loader(**kwargs):
        return replace(real_loader(**kwargs), **{field: value})

    with (
        patch(
            "a_share_ai.service.daily_research_service_release_run_admission_startup.load_daily_research_service_release_startup",
            side_effect=mismatched_loader,
        ),
        patch(
            "a_share_ai.service.daily_research_service_release_run_admission_startup.create_read_only_receipt_server"
        ) as create_server,
    ):
        assert main(_args(paths, paths["root"] / "startup")) == 1
    report, _ = _output(
        paths["root"] / "startup" / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    )
    assert report["status"] == "invalid"
    assert report["issues"][0]["code"] == "STATE_MISMATCH"
    create_server.assert_not_called()


def test_bind_failure_writes_failed_report(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "bind")
    with patch(
        "a_share_ai.service.daily_research_service_release_run_admission_startup.create_read_only_receipt_server",
        side_effect=ReadOnlyReceiptServiceError("BIND_FAILED", "port is occupied"),
    ) as create_server:
        assert main(_args(paths, paths["root"] / "startup")) == 1
    report, _ = _output(
        paths["root"] / "startup" / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    )
    assert report["status"] == "failed"
    assert report["startup_status"] == "failed"
    assert report["service_started"] is False
    for key, path_key in (
        ("release_manifest_path", "manifest"),
        ("release_report_path", "release_report"),
        ("release_audit_path", "release_audit"),
    ):
        assert report[key] == paths[path_key].relative_to(paths["root"]).as_posix()
    for key, path_key in (
        ("release_manifest_sha256", "manifest"),
        ("release_report_sha256", "release_report"),
        ("release_audit_sha256", "release_audit"),
    ):
        assert report[key] == sha256_bytes(paths[path_key].read_bytes())
    create_server.assert_called_once()


def test_ready_report_write_failure_closes_bound_server_and_returns_one(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "report-write")
    with (
        patch(
            "a_share_ai.service.daily_research_service_release_run_admission_startup.create_read_only_receipt_server"
        ) as create_server,
        patch(
            "a_share_ai.service.daily_research_service_release_run_admission_startup._write_report",
            side_effect=DailyResearchServiceReleaseRunAdmissionStartupError(
                "OUTPUT_UNAVAILABLE", "startup report is unavailable", configuration=True
            ),
        ),
    ):
        assert main(_args(paths, paths["root"] / "startup")) == 1
    create_server.return_value.server_close.assert_called_once()
    create_server.return_value.serve_forever.assert_not_called()


def test_configuration_errors_return_two_without_report(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "config")
    assert main(_args(paths, paths["root"] / "startup")[:-2]) == 2
    assert main(_args(paths, tmp_path / "outside" / "startup")) == 2
    assert not (tmp_path / "outside" / "startup").exists()
    partial = _args(paths, paths["root"] / "partial")
    partial.remove("--daily-run-admission-audit")
    partial.remove(str(paths["admission_audit"]))
    assert main(partial) == 2


def test_ready_startup_runs_probe_and_writes_serve_report(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "e2e")
    startup = load_daily_research_service_release_startup(
        manifest_path=paths["manifest"],
        report_path=paths["release_report"],
        audit_report_path=paths["release_audit"],
        artifact_root=paths["root"],
    )
    port = startup.gate_config.port
    process = subprocess.Popen(
        [sys.executable, "-m", "a_share_ai.cli", *_args(paths, paths["root"] / "serve")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        assert not _port_closed(port)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    report, _ = _output(
        paths["root"] / "serve" / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    )
    assert report["mode"] == "serve"
    assert report["service_started"] is True
    assert report["startup_ready"] is True
    assert report["status"] == "ready"
    assert _port_closed(port)
