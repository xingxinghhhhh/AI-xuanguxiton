import json
from pathlib import Path

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service.daily_research_service_release_run_admission_startup import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME,
)
from a_share_ai.service.daily_research_service_release_run_admission_startup_smoke import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION,
    run_daily_research_service_release_run_admission_startup_smoke,
)
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
