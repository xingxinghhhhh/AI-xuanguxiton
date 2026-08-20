import json
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service import (
    daily_research_service_release_run_admission_startup_smoke as smoke_module,
)
from a_share_ai.service.daily_research_service_release_run_admission_startup import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME,
    run_daily_research_service_release_run_admission_startup,
)
from a_share_ai.service.daily_research_service_release_run_admission_startup_smoke import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION,
    _validate_node75_report,
    run_daily_research_service_release_run_admission_startup_smoke,
)
from tests.service.test_daily_research_service_release_run import _ready_probe
from tests.service.test_daily_research_service_release_run_admission_startup import (
    _prepare_startup,
)


def _smoke_args(paths: dict[str, Path], output_dir: Path) -> list[str]:
    return [
        "run-daily-research-service-release-run-admission-startup-smoke",
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
        "--artifact-root",
        str(paths["root"]),
        "--startup-timeout-seconds",
        "10",
        "--probe-timeout-seconds",
        "1",
        "--output-dir",
        str(output_dir),
    ]


def _read_report(output_dir: Path) -> tuple[dict, bytes]:
    report_path = (
        output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_REPORT_NAME
    )
    raw = report_path.read_bytes()
    return json.loads(raw), raw


def _assert_self_hash(report: dict) -> None:
    canonical = dict(report)
    declared = canonical.pop("output_sha256")
    canonical["output_sha256"] = None
    assert declared == sha256_bytes(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def test_ready_smoke_runs_four_probes_and_releases_port(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "ready")
    output_dir = paths["root"] / "smoke"

    assert main(_smoke_args(paths, output_dir)) == 0

    report, raw = _read_report(output_dir)
    assert (
        report["run_version"]
        == DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION
    )
    assert report["startup_status"] == "ready"
    assert report["startup_ready"] is True
    assert report["service_started"] is True
    assert report["probe_status"] == "ready"
    assert report["probe_exit_code"] == 0
    assert report["stop_status"] == "controlled"
    assert report["service_stopped"] is True
    assert report["status"] == "ready"
    assert report["run_ready"] is True
    assert report["decision_ready"] is False
    for key, path_key in (
        ("admission_path", "admission"),
        ("admission_report_path", "admission_report"),
        ("admission_audit_path", "admission_audit"),
        ("release_manifest_path", "manifest"),
        ("release_report_path", "release_report"),
        ("release_audit_path", "release_audit"),
    ):
        assert report[key] == paths[path_key].relative_to(paths["root"]).as_posix()
    assert report["preflight_report_path"] == (
        output_dir / "preflight" / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    ).relative_to(paths["root"]).as_posix()
    assert report["startup_report_path"] == (
        output_dir / "startup" / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    ).relative_to(paths["root"]).as_posix()
    assert str(paths["root"]) not in raw.decode()
    _assert_self_hash(report)


@pytest.mark.parametrize("kwargs", [{"blocked": True}, {"failed": True}])
def test_non_ready_preflight_never_starts_service(
    tmp_path: Path, kwargs: dict[str, bool]
) -> None:
    paths = _prepare_startup(tmp_path / next(iter(kwargs)), **kwargs)
    report, exit_code = run_daily_research_service_release_run_admission_startup_smoke(
        admission_path=paths["admission"],
        admission_report_path=paths["admission_report"],
        admission_audit_path=paths["admission_audit"],
        release_manifest_path=paths["manifest"],
        release_report_path=paths["release_report"],
        release_audit_path=paths["release_audit"],
        artifact_root=paths["root"],
        startup_timeout_seconds=1,
        probe_timeout_seconds=0.2,
        output_dir=paths["root"] / "smoke",
    )
    assert exit_code == 1
    assert report["status"] in {"blocked", "failed"}
    assert report["service_started"] is False
    assert report["probe_status"] == "not_started"
    assert report["decision_ready"] is False
    assert not (paths["root"] / "smoke" / "startup").exists()


def test_smoke_rejects_timeout_and_output_escape(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "config")
    args = _smoke_args(paths, paths["root"] / "smoke")
    args[args.index("--startup-timeout-seconds") + 1] = "0"
    assert main(args) == 2
    assert main(_smoke_args(paths, tmp_path / "outside")) == 2


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout: float) -> int:
        self.returncode = 0
        return 0


def _write_fake_startup_report(output_dir: Path, preflight_dir: Path) -> None:
    preflight_path = (
        preflight_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    )
    payload = json.loads(preflight_path.read_text(encoding="utf-8"))
    payload["mode"] = "serve"
    payload["service_started"] = True
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    startup_path = (
        output_dir / "startup" / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    )
    startup_path.parent.mkdir(parents=True, exist_ok=True)
    startup_path.write_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def test_startup_service_unavailable_report_retries_with_remaining_timeout(
    tmp_path: Path,
) -> None:
    paths = _prepare_startup(tmp_path / "race")
    output_dir = paths["root"] / "smoke"
    process = _FakeProcess()
    timeouts: list[float] = []
    unavailable = {
        "status": "invalid",
        "issues": [{"code": "SERVICE_UNAVAILABLE", "message": "not listening yet"}],
    }

    def start_process(*args, **kwargs):
        _write_fake_startup_report(output_dir, output_dir / "preflight")
        return process

    def probe(*, base_url: str, timeout_seconds: float) -> dict:
        timeouts.append(timeout_seconds)
        return unavailable if len(timeouts) == 1 else _ready_probe()

    with (
        patch.object(smoke_module.subprocess, "Popen", side_effect=start_process),
        patch.object(smoke_module, "probe_daily_research_service", side_effect=probe),
    ):
        report, exit_code = run_daily_research_service_release_run_admission_startup_smoke(
            admission_path=paths["admission"],
            admission_report_path=paths["admission_report"],
            admission_audit_path=paths["admission_audit"],
            release_manifest_path=paths["manifest"],
            release_report_path=paths["release_report"],
            release_audit_path=paths["release_audit"],
            artifact_root=paths["root"],
            startup_timeout_seconds=0.2,
            probe_timeout_seconds=10,
            output_dir=output_dir,
        )
    assert exit_code == 0
    assert report["status"] == "ready"
    assert len(timeouts) == 2
    assert all(timeout <= 0.2 for timeout in timeouts)


def test_stale_startup_report_is_removed_before_service_start(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "stale")
    output_dir = paths["root"] / "smoke"
    preflight_dir = output_dir / "preflight"
    preflight_dir.mkdir(parents=True, exist_ok=True)
    from a_share_ai.service.daily_research_service_release_run_admission_startup import (
        run_daily_research_service_release_run_admission_startup,
    )

    assert (
        run_daily_research_service_release_run_admission_startup(
            admission_path=paths["admission"],
            report_path=paths["admission_report"],
            audit_path=paths["admission_audit"],
            release_manifest_path=paths["manifest"],
            release_report_path=paths["release_report"],
            release_audit_path=paths["release_audit"],
            artifact_root=paths["root"],
            output_dir=preflight_dir,
            check_only=True,
        )[0]
        == 0
    )
    _write_fake_startup_report(output_dir, preflight_dir)
    process = _FakeProcess()
    with patch.object(smoke_module.subprocess, "Popen", return_value=process):
        report, exit_code = run_daily_research_service_release_run_admission_startup_smoke(
            admission_path=paths["admission"],
            admission_report_path=paths["admission_report"],
            admission_audit_path=paths["admission_audit"],
            release_manifest_path=paths["manifest"],
            release_report_path=paths["release_report"],
            release_audit_path=paths["release_audit"],
            artifact_root=paths["root"],
            startup_timeout_seconds=0.1,
            probe_timeout_seconds=0.1,
            output_dir=output_dir,
        )
    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["probe_status"] == "not_started"


@pytest.mark.parametrize(
    "field",
    [
        "admission_version",
        "audit_version",
        "release_version",
        "admission_path",
        "admission_sha256",
        "admission_report_path",
        "admission_report_sha256",
        "admission_audit_path",
        "admission_audit_sha256",
        "release_manifest_path",
        "release_manifest_sha256",
        "release_report_path",
        "release_report_sha256",
        "release_audit_path",
        "release_audit_sha256",
    ],
)
def test_ready_node75_report_rejects_null_bound_field(tmp_path: Path, field: str) -> None:
    paths = _prepare_startup(tmp_path / field)
    preflight_dir = paths["root"] / "preflight"
    assert (
        run_daily_research_service_release_run_admission_startup(
            admission_path=paths["admission"],
            report_path=paths["admission_report"],
            audit_path=paths["admission_audit"],
            release_manifest_path=paths["manifest"],
            release_report_path=paths["release_report"],
            release_audit_path=paths["release_audit"],
            artifact_root=paths["root"],
            output_dir=preflight_dir,
            check_only=True,
        )[0]
        == 0
    )
    report_path = preflight_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload[field] = None
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    report_path.write_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    inputs = {
        "admission": paths["admission"],
        "admission_report": paths["admission_report"],
        "admission_audit": paths["admission_audit"],
        "release_manifest": paths["manifest"],
        "release_report": paths["release_report"],
        "release_audit": paths["release_audit"],
    }
    assert (
        _validate_node75_report(
            report_path,
            expected_mode="check_only",
            expected_inputs=inputs,
            root=paths["root"],
        )
        is None
    )
