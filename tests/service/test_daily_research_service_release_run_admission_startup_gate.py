import json
import socket
import subprocess
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME,
    audit_daily_research_service_release_run_admission_startup_smoke_admission,
    check_daily_research_service_release_run_admission_startup_gate,
)
from tests.service.test_daily_research_service_release_run_admission_startup import (
    _prepare_startup,
)
from tests.service.test_daily_research_service_release_run_admission_startup_smoke_admission import (  # noqa: E501
    _build,
)


def _prepare_gate(tmp_path: Path, **kwargs: bool) -> dict[str, Path]:
    paths = _prepare_startup(tmp_path, **kwargs)
    _build(paths)
    admission_dir = paths["root"] / "admission"
    audit_dir = paths["root"] / "admission-audit"
    audit_daily_research_service_release_run_admission_startup_smoke_admission(
        admission_path=admission_dir
        / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME,
        report_path=admission_dir
        / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME,
        artifact_root=paths["root"],
        output_dir=audit_dir,
    )
    paths.update(
        {
            "smoke_admission": admission_dir
            / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME,
            "smoke_admission_report": admission_dir
            / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME,
            "smoke_admission_audit": (
                audit_dir
                / (
                    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_REPORT_NAME
                )
            ),
        }
    )
    return paths


def _gate(paths: dict[str, Path]) -> tuple[dict, int, object | None]:
    return check_daily_research_service_release_run_admission_startup_gate(
        admission_path=paths["smoke_admission"],
        admission_report_path=paths["smoke_admission_report"],
        admission_audit_path=paths["smoke_admission_audit"],
        release_manifest_path=paths["manifest"],
        release_report_path=paths["release_report"],
        release_audit_report_path=paths["release_audit"],
        artifact_root=paths["root"],
    )


def _cli_args(paths: dict[str, Path], *, check_only: bool = True) -> list[str]:
    args = [
        "serve-research-receipt",
        "--daily-smoke-admission",
        str(paths["smoke_admission"]),
        "--daily-smoke-admission-report",
        str(paths["smoke_admission_report"]),
        "--daily-smoke-admission-audit-report",
        str(paths["smoke_admission_audit"]),
        "--daily-release-manifest",
        str(paths["manifest"]),
        "--daily-release-report",
        str(paths["release_report"]),
        "--daily-release-audit-report",
        str(paths["release_audit"]),
        "--artifact-root",
        str(paths["root"]),
    ]
    if check_only:
        args.append("--check-only")
    return args


def _refresh(path: Path, payload: dict) -> None:
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    path.write_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def test_ready_gate_check_only_is_offline_and_ready(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path / "ready")
    with (
        patch.object(socket, "socket") as socket_factory,
        patch.object(subprocess, "Popen") as popen,
        patch.object(urllib.request, "urlopen") as urlopen,
    ):
        summary, code, startup = _gate(paths)
    assert code == 0
    assert startup is not None
    assert summary["status"] == "ready"
    assert summary["release_startup_ready"] is True
    assert summary["bind_attempted"] is False
    assert summary["service_started"] is False
    assert summary["decision_ready"] is False
    assert summary["output_sha256"] == sha256_bytes(
        json.dumps(
            {**summary, "output_sha256": None},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    socket_factory.assert_not_called()
    popen.assert_not_called()
    urlopen.assert_not_called()


def test_ready_cli_check_only_returns_zero_and_is_path_redacted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _prepare_gate(tmp_path / "cli")
    assert main(_cli_args(paths)) == 0
    raw = capsys.readouterr().out
    parsed = json.loads(raw)
    assert parsed["status"] == "ready"
    assert str(paths["root"]) not in raw
    assert parsed["decision_ready"] is False


@pytest.mark.parametrize("kwargs", [{"blocked": True}, {"failed": True}])
def test_non_ready_gate_fails_closed_without_loading_release(
    tmp_path: Path, kwargs: dict[str, bool]
) -> None:
    paths = _prepare_gate(tmp_path / next(iter(kwargs)), **kwargs)
    with patch(
        "a_share_ai.service.daily_research_service_release_run_admission_startup_gate.load_daily_research_service_release_startup"
    ) as loader:
        summary, code, startup = _gate(paths)
    assert code == 1
    assert startup is None
    assert summary["status"] in {"blocked", "failed"}
    assert summary["bind_attempted"] is False
    loader.assert_not_called()


def test_invalid_gate_does_not_bind_or_load(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path / "invalid")
    payload = json.loads(paths["smoke_admission_report"].read_text(encoding="utf-8"))
    payload["decision_ready"] = True
    _refresh(paths["smoke_admission_report"], payload)
    with patch(
        "a_share_ai.service.daily_research_service_release_run_admission_startup_gate.load_daily_research_service_release_startup"
    ) as loader:
        summary, code, startup = _gate(paths)
    assert code == 1
    assert startup is None
    assert summary["status"] == "invalid"
    loader.assert_not_called()


@pytest.mark.parametrize(
    ("source", "field", "value"),
    [
        ("smoke_admission", "status", []),
        ("smoke_admission", "startup_ready", "true"),
        ("smoke_admission", "issues", {}),
        ("smoke_admission_audit", "status", {"status": "ready"}),
        ("smoke_admission_audit", "audit_ready", 1),
    ],
)
def test_invalid_runtime_types_fail_closed(
    tmp_path: Path, source: str, field: str, value: object
) -> None:
    paths = _prepare_gate(tmp_path)
    path = paths[source]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    _refresh(path, payload)
    with patch(
        "a_share_ai.service.daily_research_service_release_run_admission_startup_gate.load_daily_research_service_release_startup"
    ) as loader:
        summary, code, startup = _gate(paths)
    assert code == 1
    assert startup is None
    assert summary["status"] == "invalid"
    loader.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [("as_of", "not-a-time"), ("evaluation_at", "2026-01-01T00:00:00")],
)
def test_invalid_identity_time_fails_closed(tmp_path: Path, field: str, value: str) -> None:
    paths = _prepare_gate(tmp_path / field)
    path = paths["smoke_admission"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    _refresh(path, payload)
    summary, code, startup = _gate(paths)
    assert code == 1
    assert startup is None
    assert summary["status"] == "invalid"


def test_ready_runtime_state_cannot_be_weakened_after_self_hash_refresh(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path / "runtime-state")
    path = paths["smoke_admission"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["probe_status"] = "timeout"
    payload["probe_exit_code"] = None
    _refresh(path, payload)
    summary, code, startup = _gate(paths)
    assert code == 1
    assert startup is None
    assert summary["status"] == "invalid"


def test_actual_ready_mode_reuses_existing_server_entrypoint(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path / "serve")
    with patch("a_share_ai.cli.serve_read_only_receipt") as serve:
        assert main(_cli_args(paths, check_only=False)) == 0
    serve.assert_called_once()


def test_gate_cli_rejects_incomplete_group_and_output_escape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _prepare_gate(tmp_path / "config")
    incomplete = _cli_args(paths)
    incomplete.remove("--daily-smoke-admission-audit-report")
    incomplete.remove(str(paths["smoke_admission_audit"]))
    assert main(incomplete) == 2
    capsys.readouterr()
    escape = _cli_args(paths)
    escape[escape.index("--artifact-root") + 1] = str(tmp_path / "outside")
    assert main(escape) == 2
    capsys.readouterr()
