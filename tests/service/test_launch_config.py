import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service.launch_config import (
    READ_ONLY_RECEIPT_SERVICE_LAUNCH_VERSION,
    ReadOnlyReceiptLaunchError,
    check_read_only_receipt_launch,
    load_read_only_receipt_launch_config,
)
from a_share_ai.service.read_only_receipt_probe import (
    READ_ONLY_RECEIPT_SERVICE_PROBE_VERSION,
    probe_exit_code,
    probe_read_only_receipt_service,
)
from a_share_ai.service.read_only_receipt_server import READ_ONLY_RECEIPT_SERVICE_VERSION
from tests.service.test_read_only_receipt_server import _free_port, _write_input_pair


def _write_manifest(
    root: Path,
    receipt_path: Path,
    report_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> Path:
    manifest_path = root / "service" / "read_only_receipt_service_launch.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "launch_version": READ_ONLY_RECEIPT_SERVICE_LAUNCH_VERSION,
        "service_version": READ_ONLY_RECEIPT_SERVICE_VERSION,
        "probe_version": READ_ONLY_RECEIPT_SERVICE_PROBE_VERSION,
        "receipt_path": receipt_path.relative_to(root).as_posix(),
        "receipt_report_path": report_path.relative_to(root).as_posix(),
        "receipt_sha256": sha256_bytes(receipt_path.read_bytes()),
        "receipt_report_sha256": sha256_bytes(report_path.read_bytes()),
        "host": host,
        "port": _free_port() if port is None else port,
        "decision_ready": False,
    }
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def test_valid_launch_manifest_reuses_node53_validation(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    manifest = _write_manifest(tmp_path / "artifacts", receipt_path, report_path)

    config = load_read_only_receipt_launch_config(
        manifest_path=manifest,
        artifact_root=tmp_path / "artifacts",
    )

    assert config.receipt_path == receipt_path.resolve()
    assert config.receipt_report_path == report_path.resolve()
    assert config.receipt_status == "ready"
    assert config.receipt_ready is True
    assert config.decision_ready is False


def test_check_only_is_side_effect_free_and_reports_receipt_state(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    port = _free_port()
    manifest = _write_manifest(tmp_path / "artifacts", receipt_path, report_path, port=port)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "a_share_ai.cli",
            "serve-research-receipt",
            "--launch-manifest",
            str(manifest),
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--check-only",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["validation"] == "passed"
    assert report["receipt_status"] == "ready"
    assert report["receipt_ready"] is True
    assert report["decision_ready"] is False
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", port)) != 0


def test_manifest_startup_works_with_node54_probe(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    port = _free_port()
    manifest = _write_manifest(tmp_path / "artifacts", receipt_path, report_path, port=port)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "a_share_ai.cli",
            "serve-research-receipt",
            "--launch-manifest",
            str(manifest),
            "--artifact-root",
            str(tmp_path / "artifacts"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(30):
            try:
                report = probe_read_only_receipt_service(
                    base_url=f"http://127.0.0.1:{port}", timeout_seconds=0.2
                )
                if report["status"] == "ready":
                    assert probe_exit_code(report) == 0
                    break
            except Exception:  # noqa: BLE001 - startup polling is bounded
                pass
        else:
            stderr = process.stderr.read() if process.stderr else ""
            pytest.fail(f"manifest service did not start: {stderr}")
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_stale_manifest_preserves_receipt_state(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path, status="stale")
    manifest = _write_manifest(tmp_path / "artifacts", receipt_path, report_path)
    config = check_read_only_receipt_launch(
        manifest_path=manifest,
        artifact_root=tmp_path / "artifacts",
    )
    assert config.receipt_status == "stale"
    assert config.receipt_ready is False


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("receipt_sha256", "0" * 64, "HASH_MISMATCH"),
        ("receipt_report_sha256", "0" * 64, "HASH_MISMATCH"),
        ("receipt_path", "../outside.json", "PATH_OUTSIDE_ARTIFACT_ROOT"),
        ("host", "0.0.0.0", "HOST_INVALID"),
        ("port", 0, "PORT_INVALID"),
        ("decision_ready", True, "DECISION_GATE_INVALID"),
        ("service_version", "wrong", "VERSION_MISMATCH"),
    ],
)
def test_manifest_validation_fails_closed(
    tmp_path: Path, field: str, value: Any, code: str
) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    root = tmp_path / "artifacts"
    manifest = _write_manifest(root, receipt_path, report_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload[field] = value
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ReadOnlyReceiptLaunchError) as exc_info:
        load_read_only_receipt_launch_config(manifest_path=manifest, artifact_root=root)
    assert exc_info.value.code == code
    assert str(tmp_path) not in str(exc_info.value)


def test_unknown_manifest_fields_and_direct_option_mix_are_rejected(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    root = tmp_path / "artifacts"
    manifest = _write_manifest(root, receipt_path, report_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["unknown"] = True
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ReadOnlyReceiptLaunchError, match="fields are invalid"):
        load_read_only_receipt_launch_config(manifest_path=manifest, artifact_root=root)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "a_share_ai.cli",
            "serve-research-receipt",
            "--launch-manifest",
            str(manifest),
            "--receipt",
            str(receipt_path),
            "--artifact-root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "cannot be combined" in result.stderr
    assert str(tmp_path) not in result.stderr


def test_old_manifest_can_validate_a_previous_receipt(tmp_path: Path) -> None:
    (tmp_path / "old").mkdir()
    (tmp_path / "current").mkdir()
    old_receipt, old_report = _write_input_pair(tmp_path / "old", status="stale")
    current_receipt, current_report = _write_input_pair(tmp_path / "current")

    old_config = load_read_only_receipt_launch_config(
        manifest_path=_write_manifest(
            tmp_path / "old" / "artifacts", old_receipt, old_report
        ),
        artifact_root=tmp_path / "old" / "artifacts",
    )
    current_config = load_read_only_receipt_launch_config(
        manifest_path=_write_manifest(
            tmp_path / "current" / "artifacts", current_receipt, current_report
        ),
        artifact_root=tmp_path / "current" / "artifacts",
    )

    assert old_config.receipt_status == "stale"
    assert current_config.receipt_status == "ready"
    assert old_config.receipt_path != current_config.receipt_path
