import json
import socket
from pathlib import Path

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service import (
    daily_research_service_release_run_admission_startup_gate_smoke_audit as audit_module,
)
from a_share_ai.service.daily_research_service_release_run_admission_startup_gate_smoke import (
    _REPORT_FIELDS as SMOKE_REPORT_FIELDS,
)
from a_share_ai.service.daily_research_service_release_run_admission_startup_gate_smoke import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_VERSION,
)
from a_share_ai.service.read_only_receipt_deployment_readiness import (
    READ_ONLY_RECEIPT_DEPLOYMENT_VERSION,
    check_read_only_receipt_deployment,
)


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _with_hash(payload: dict) -> dict:
    payload = dict(payload)
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(_json_bytes(payload))
    return payload


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _write_fixture(
    root: Path, *, status: str = "ready", port: int | None = None
) -> dict[str, Path]:
    smoke_dir = root / "node81"
    audit_dir = root / "node82"
    smoke_dir.mkdir(parents=True)
    audit_dir.mkdir(parents=True)
    smoke_path = (
        smoke_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME
    )
    audit_report_name = getattr(
        audit_module,
        "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_"
        "AUDIT_REPORT_NAME",
    )
    audit_path = audit_dir / audit_report_name
    smoke = {
        "smoke_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_VERSION,
        "admission_path": "inputs/admission.json",
        "admission_sha256": "a" * 64,
        "admission_report_path": "inputs/admission_report.json",
        "admission_report_sha256": "b" * 64,
        "admission_audit_path": "inputs/admission_audit.json",
        "admission_audit_sha256": "c" * 64,
        "release_manifest_path": "inputs/release_manifest.json",
        "release_manifest_sha256": "d" * 64,
        "release_report_path": "inputs/release_report.json",
        "release_report_sha256": "e" * 64,
        "release_audit_report_path": "inputs/release_audit.json",
        "release_audit_report_sha256": "f" * 64,
        "symbol": "sh.600000",
        "as_of": "2026-08-20",
        "evaluation_at": "2026-08-20T09:30:00+08:00",
        "gate_status": "ready" if status == "ready" else status,
        "gate_ready": status == "ready",
        "startup_status": "ready" if status == "ready" else "not_started",
        "service_started": status == "ready",
        "probe_status": "ready" if status == "ready" else "not_started",
        "probe_exit_code": 0 if status == "ready" else None,
        "stop_status": "controlled" if status == "ready" else "not_attempted",
        "service_stopped": status == "ready",
        "port_released": status == "ready",
        "smoke_status": status,
        "smoke_ready": status == "ready",
        "issues": [] if status == "ready" else [{"code": "BLOCKED", "message": "fixture blocked"}],
        "decision_ready": False,
        "output_sha256": None,
    }
    assert set(smoke) == SMOKE_REPORT_FIELDS
    smoke = _with_hash(smoke)
    smoke_raw = _json_bytes(smoke)
    smoke_path.write_bytes(smoke_raw)
    smoke_relative = smoke_path.relative_to(root).as_posix()
    smoke_sha = sha256_bytes(smoke_raw)
    audit = {
        "audit_version": (
            audit_module.DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_VERSION
        ),
        "smoke_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_VERSION,
        "smoke_report_path": smoke_relative,
        "smoke_report_sha256": smoke_sha,
        "admission_path": None,
        "admission_sha256": None,
        "admission_report_path": None,
        "admission_report_sha256": None,
        "admission_audit_path": None,
        "admission_audit_sha256": None,
        "release_manifest_path": None,
        "release_manifest_sha256": None,
        "release_report_path": None,
        "release_report_sha256": None,
        "release_audit_report_path": None,
        "release_audit_report_sha256": None,
        "symbol": smoke["symbol"],
        "as_of": smoke["as_of"],
        "evaluation_at": smoke["evaluation_at"],
        "gate_status": smoke["gate_status"],
        "gate_ready": smoke["gate_ready"],
        "startup_status": smoke["startup_status"],
        "service_started": smoke["service_started"],
        "probe_status": smoke["probe_status"],
        "probe_exit_code": smoke["probe_exit_code"],
        "stop_status": smoke["stop_status"],
        "service_stopped": smoke["service_stopped"],
        "port_released": smoke["port_released"],
        "smoke_status": smoke["smoke_status"],
        "smoke_ready": smoke["smoke_ready"],
        "status": status,
        "audit_ready": True,
        "issues": smoke["issues"],
        "decision_ready": False,
        "output_sha256": None,
    }
    assert set(audit) == audit_module._AUDIT_FIELDS
    audit_path.write_bytes(_json_bytes(_with_hash(audit)))
    spec = {
        "deployment_version": READ_ONLY_RECEIPT_DEPLOYMENT_VERSION,
        "mode": "node80-gated-loopback-v1",
        "smoke_report_path": smoke_relative,
        "smoke_report_sha256": smoke_sha,
        "smoke_audit_report_path": audit_path.relative_to(root).as_posix(),
        "smoke_audit_report_sha256": sha256_bytes(audit_path.read_bytes()),
        "configured_host": "127.0.0.1",
        "configured_port": port or _free_port(),
        "decision_ready": False,
    }
    spec_path = root / "deployment-spec.json"
    spec_path.write_bytes(_json_bytes(spec))
    return {"spec": spec_path, "smoke": smoke_path, "audit": audit_path}


def test_ready_deployment_report_is_deterministic_and_cli_writes_output(
    tmp_path: Path, capsys
) -> None:
    paths = _write_fixture(tmp_path, port=_free_port())
    before = {key: value.read_bytes() for key, value in paths.items()}
    output_dir = tmp_path / "deployment-check"
    assert (
        main(
            [
                "check-read-only-receipt-deployment",
                "--spec",
                str(paths["spec"]),
                "--artifact-root",
                str(tmp_path),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )
    first = json.loads(capsys.readouterr().out)
    _, code = check_read_only_receipt_deployment(
        spec_path=paths["spec"], artifact_root=tmp_path, output_dir=output_dir
    )
    assert code == 0
    second = json.loads(
        (output_dir / "read_only_receipt_service_deployment_readiness_report.json").read_text()
    )
    assert first == second
    assert first["deployment_status"] == "ready"
    assert first["deployment_ready"] is True
    assert first["decision_ready"] is False
    assert first["host_port_source"] == "deployment-spec-only"
    assert first["port_check_scope"] == "loopback-bind-close-at-check-time"
    assert all(value.read_bytes() == before[key] for key, value in paths.items())
    assert "127.0.0.1" not in json.dumps(first["issues"])


@pytest.mark.parametrize("status", ["blocked", "failed"])
def test_legal_non_ready_upstream_is_blocked(tmp_path: Path, status: str) -> None:
    paths = _write_fixture(tmp_path, status=status)
    report, code = check_read_only_receipt_deployment(
        spec_path=paths["spec"], artifact_root=tmp_path, output_dir=tmp_path / "out"
    )
    assert code == 1
    assert report["deployment_status"] == "blocked"
    assert report["deployment_ready"] is False
    assert report["audit_ready"] is True


def test_tampered_upstream_hash_is_invalid(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    paths["smoke"].write_bytes(
        paths["smoke"].read_bytes().replace(b'"smoke_ready":true', b'"smoke_ready":false')
    )
    report, code = check_read_only_receipt_deployment(
        spec_path=paths["spec"], artifact_root=tmp_path, output_dir=tmp_path / "out"
    )
    assert code == 1
    assert report["deployment_status"] == "invalid"
    assert report["deployment_ready"] is False
    assert report["issues"][0]["code"] == "HASH_MISMATCH"


def test_occupied_port_is_blocked_without_starting_service(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        paths = _write_fixture(tmp_path, port=occupied.getsockname()[1])
        report, code = check_read_only_receipt_deployment(
            spec_path=paths["spec"], artifact_root=tmp_path, output_dir=tmp_path / "out"
        )
    assert code == 1
    assert report["deployment_status"] == "blocked"
    assert report["port_available_at_check"] is False


def test_non_loopback_host_is_a_reported_invalid_input(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    spec = json.loads(paths["spec"].read_text())
    spec["configured_host"] = "0.0.0.0"
    paths["spec"].write_bytes(_json_bytes(spec))
    report, code = check_read_only_receipt_deployment(
        spec_path=paths["spec"], artifact_root=tmp_path, output_dir=tmp_path / "out"
    )
    assert code == 1
    assert report["deployment_status"] == "invalid"
    assert report["issues"][0]["code"] == "HOST_INVALID"


def test_invalid_utf8_is_a_reported_invalid_input(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    paths["spec"].write_bytes(b"{\xff")
    report, code = check_read_only_receipt_deployment(
        spec_path=paths["spec"], artifact_root=tmp_path, output_dir=tmp_path / "out"
    )
    assert code == 1
    assert report["deployment_status"] == "invalid"
    assert report["issues"][0]["code"] == "INPUT_INVALID"


def test_configuration_error_for_output_outside_root(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    with pytest.raises(Exception) as exc_info:
        check_read_only_receipt_deployment(
            spec_path=paths["spec"], artifact_root=tmp_path, output_dir=tmp_path.parent / "outside"
        )
    assert getattr(exc_info.value, "configuration") is True
