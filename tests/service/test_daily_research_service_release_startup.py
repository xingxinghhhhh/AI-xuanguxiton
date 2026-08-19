import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service.daily_research_service_launch_gate_startup import (
    load_daily_research_service_launch_gate_startup,
)
from a_share_ai.service.daily_research_service_probe import (
    daily_research_service_probe_exit_code,
    probe_daily_research_service,
)
from a_share_ai.service.daily_research_service_release import (
    build_daily_research_service_release,
)
from a_share_ai.service.daily_research_service_release_audit import (
    audit_daily_research_service_release,
)
from a_share_ai.service.daily_research_service_release_startup import (
    DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION,
    daily_research_service_release_startup_check_report,
    load_daily_research_service_release_startup,
)
from a_share_ai.service.read_only_receipt_server import ReadOnlyReceiptServiceError
from tests.service.test_daily_research_service_release import _prepare_chain


def _prepare_release(tmp_path: Path, **kwargs: bool):
    root, run_path, run_audit_path = _prepare_chain(tmp_path / "chain", **kwargs)
    release_dir = root / "release"
    build_daily_research_service_release(
        run_report_path=run_path,
        run_audit_report_path=run_audit_path,
        artifact_root=root,
        output_dir=release_dir,
    )
    manifest_path = release_dir / "daily_research_service_release_manifest.json"
    report_path = release_dir / "daily_research_service_release_report.json"
    audit_dir = root / "release-audit"
    audit_daily_research_service_release(
        manifest_path=manifest_path,
        report_path=report_path,
        artifact_root=root,
        output_dir=audit_dir,
    )
    return (
        root,
        manifest_path,
        report_path,
        audit_dir / "daily_research_service_release_audit_report.json",
        run_path,
        run_audit_path,
    )


def _release_args(
    root: Path,
    manifest_path: Path,
    report_path: Path,
    audit_path: Path,
    *,
    check_only: bool = False,
) -> list[str]:
    args = [
        "serve-research-receipt",
        "--artifact-root",
        str(root),
        "--daily-release-manifest",
        str(manifest_path),
        "--daily-release-report",
        str(report_path),
        "--daily-release-audit-report",
        str(audit_path),
    ]
    if check_only:
        args.append("--check-only")
    return args


def _port_closed(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def test_ready_release_startup_loader_is_bound_and_check_only_is_side_effect_free(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, manifest_path, report_path, audit_path, run_path, run_audit_path = _prepare_release(
        tmp_path / "ready"
    )
    before = {
        path: path.read_bytes()
        for path in (manifest_path, report_path, audit_path, run_path, run_audit_path)
    }
    config = load_daily_research_service_release_startup(
        manifest_path=manifest_path,
        report_path=report_path,
        audit_report_path=audit_path,
        artifact_root=root,
    )
    summary = daily_research_service_release_startup_check_report(config)
    assert config.startup_ready is True
    assert config.release_ready is True
    assert config.audit_ready is True
    assert config.decision_ready is False
    assert summary["startup_version"] == DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION
    assert summary["startup_ready"] is True
    assert str(root) not in json.dumps(summary, ensure_ascii=False, sort_keys=True)
    with patch("a_share_ai.cli.serve_read_only_receipt") as serve:
        assert (
            main(_release_args(root, manifest_path, report_path, audit_path, check_only=True)) == 0
        )
    serve.assert_not_called()
    assert capsys.readouterr().out
    assert {path: path.read_bytes() for path in before} == before


def test_ready_release_startup_runs_node60_probe_e2e(tmp_path: Path) -> None:
    root, manifest_path, report_path, audit_path, run_path, _ = _prepare_release(tmp_path / "e2e")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    gate_startup = load_daily_research_service_launch_gate_startup(
        gate_path=root / run["gate_path"],
        audit_path=root / run["audit_path"],
        artifact_root=root,
    )
    port = gate_startup.gate_config.port
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "a_share_ai.cli",
            *_release_args(root, manifest_path, report_path, audit_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    probe = None
    try:
        for _ in range(100):
            probe = probe_daily_research_service(
                base_url=f"http://127.0.0.1:{port}", timeout_seconds=0.2
            )
            if daily_research_service_probe_exit_code(probe) == 0:
                break
            time.sleep(0.05)
        assert probe is not None
        assert daily_research_service_probe_exit_code(probe) == 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    assert _port_closed(port)


@pytest.mark.parametrize("kwargs", [{"blocked": True}, {"failed": True}])
def test_blocked_or_failed_release_never_calls_node65_or_binds(
    tmp_path: Path, kwargs: dict[str, bool]
) -> None:
    root, manifest_path, report_path, audit_path, _, _ = _prepare_release(
        tmp_path / ("blocked" if kwargs.get("blocked") else "failed"), **kwargs
    )
    with (
        patch(
            "a_share_ai.service.daily_research_service_release_startup.load_daily_research_service_launch_gate_startup"
        ) as node65,
        patch("a_share_ai.cli.serve_read_only_receipt") as serve,
    ):
        code = main(_release_args(root, manifest_path, report_path, audit_path))
    assert code == 1
    node65.assert_not_called()
    serve.assert_not_called()


@pytest.mark.parametrize("field", ["release_ready", "audit_ready", "status"])
def test_release_audit_tamper_fails_before_startup(tmp_path: Path, field: str) -> None:
    root, manifest_path, report_path, audit_path, _, _ = _prepare_release(tmp_path / field)
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    payload[field] = False if field != "status" else "blocked"
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    audit_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    with patch("a_share_ai.cli.serve_read_only_receipt") as serve:
        code = main(_release_args(root, manifest_path, report_path, audit_path))
    assert code == 1
    serve.assert_not_called()


def test_missing_and_outside_release_inputs_fail_without_bind(tmp_path: Path) -> None:
    root, manifest_path, report_path, audit_path, _, _ = _prepare_release(tmp_path / "paths")
    missing = root / "missing" / audit_path.name
    with patch("a_share_ai.cli.serve_read_only_receipt") as serve:
        assert main(_release_args(root, manifest_path, report_path, missing)) == 1
    serve.assert_not_called()

    outside = tmp_path / "outside" / audit_path.name
    with patch("a_share_ai.cli.serve_read_only_receipt") as serve:
        assert main(_release_args(root, manifest_path, report_path, outside)) == 1
    serve.assert_not_called()


def test_release_parameter_combinations_return_two(tmp_path: Path) -> None:
    root, manifest_path, report_path, audit_path, _, _ = _prepare_release(tmp_path / "config")
    assert (
        main(
            [
                "serve-research-receipt",
                "--artifact-root",
                str(root),
                "--daily-release-manifest",
                str(manifest_path),
            ]
        )
        == 2
    )
    mixed = _release_args(root, manifest_path, report_path, audit_path) + [
        "--host",
        "127.0.0.1",
    ]
    assert main(mixed) == 2


def test_release_service_bind_failure_returns_one(tmp_path: Path) -> None:
    root, manifest_path, report_path, audit_path, _, _ = _prepare_release(tmp_path / "bind")
    with patch(
        "a_share_ai.cli.serve_read_only_receipt",
        side_effect=ReadOnlyReceiptServiceError("BIND_FAILED", "port is occupied"),
    ):
        assert main(_release_args(root, manifest_path, report_path, audit_path)) == 1
