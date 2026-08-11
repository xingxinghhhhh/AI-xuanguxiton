"""Build a verified, non-trading decision input snapshot."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..analysis.contracts import ANALYSIS_REPORT_VERSION, EVIDENCE_IDS
from ..analysis.quality import ANALYSIS_QUALITY_VERSION
from ..evidence.contracts import SUPPORTED_BUNDLE_VERSIONS
from ..market.replay import sha256_bytes, write_atomic

DECISION_INPUT_VERSION = "decision-input-v1"
RENDER_SCHEMA_VERSION = "1.0"


class DecisionInputError(ValueError):
    """A fail-closed decision-input validation error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DecisionInputError("INPUT_JSON_INVALID", f"{label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DecisionInputError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return payload, raw


def _safe_relative(root: Path, value: Any, *, label: str) -> tuple[str, Path]:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise DecisionInputError("PATH_INVALID", f"{label} must be relative")
    root_resolved = root.resolve()
    candidate = (root / value).resolve()
    try:
        relative = candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise DecisionInputError(
            "PATH_OUTSIDE_ROOT", f"{label} escapes its allowed root"
        ) from exc
    return relative.as_posix(), candidate


def _path_reference(path: Path, *, root: Path, label: str) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise DecisionInputError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    return relative.as_posix()


def _file_sha(path: Path, *, label: str) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise DecisionInputError("FILE_UNAVAILABLE", f"{label}: {exc}") from exc


def _require_sha(payload: Mapping[str, Any], field: str, actual: str, *, label: str) -> None:
    expected = payload.get(field)
    if not isinstance(expected, str) or len(expected) != 64:
        raise DecisionInputError("HASH_INVALID", f"{label}.{field} must be a SHA-256 string")
    if expected != actual:
        raise DecisionInputError("HASH_MISMATCH", f"{label}.{field} does not match the file")


def _require_common(
    payload: Mapping[str, Any],
    *,
    label: str,
    symbol: str | None = None,
    as_of: str | None = None,
) -> tuple[str, str]:
    payload_symbol = payload.get("symbol")
    payload_as_of = payload.get("as_of")
    if not isinstance(payload_symbol, str) or not payload_symbol.strip():
        raise DecisionInputError("FIELD_INVALID", f"{label}.symbol must be non-empty")
    if not isinstance(payload_as_of, str) or not payload_as_of.strip():
        raise DecisionInputError("FIELD_INVALID", f"{label}.as_of must be non-empty")
    try:
        parsed = datetime.fromisoformat(payload_as_of.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionInputError("AS_OF_INVALID", f"{label}.as_of is not ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DecisionInputError("AS_OF_INVALID", f"{label}.as_of must include timezone")
    if parsed > datetime.now(UTC):
        raise DecisionInputError("AS_OF_IN_FUTURE", f"{label}.as_of is in the future")
    if symbol is not None and payload_symbol != symbol:
        raise DecisionInputError("SYMBOL_MISMATCH", f"{label}.symbol does not match")
    if as_of is not None and payload_as_of != as_of:
        raise DecisionInputError("AS_OF_MISMATCH", f"{label}.as_of does not match")
    return payload_symbol, payload_as_of


def _require_false(payload: Mapping[str, Any], field: str, *, label: str) -> None:
    if payload.get(field) is not False:
        raise DecisionInputError("DECISION_GATE_INVALID", f"{label}.{field} must be false")


def _require_ready(payload: Mapping[str, Any], field: str, *, label: str) -> None:
    if payload.get(field) is not True:
        raise DecisionInputError("READY_GATE_INVALID", f"{label}.{field} must be true")


def _require_display_path(
    payload: Mapping[str, Any], field: str, expected: Path, *, label: str
) -> None:
    value = payload.get(field)
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise DecisionInputError("PATH_INVALID", f"{label}.{field} must be relative")
    if ".." in Path(value).parts:
        raise DecisionInputError("PATH_INVALID", f"{label}.{field} must not escape")
    if Path(value).name != expected.name:
        raise DecisionInputError("PATH_MISMATCH", f"{label}.{field} names another file")


def _artifact_entry(
    *,
    path: Path,
    artifact_root: Path,
    label: str,
    version: Any,
    status: Any,
    symbol: Any,
    as_of: Any,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "as_of": as_of,
        "path": _path_reference(path, root=artifact_root, label=label),
        "sha256": _file_sha(path, label=label),
        "status": status,
        "symbol": symbol,
        "version": version,
    }
    if extra:
        entry.update(extra)
    return entry


def _validate_inputs(
    *,
    analysis_path: Path,
    analysis_report_path: Path,
    render_report_path: Path,
    quality_report_path: Path,
    bundle_path: Path,
    bundle_report_path: Path,
    input_root: Path,
    artifact_root: Path,
) -> dict[str, Any]:
    _path_reference(input_root, root=artifact_root, label="input_root")

    analysis, _analysis_raw = _read_json(analysis_path, label="analysis")
    analysis_report, _analysis_report_raw = _read_json(
        analysis_report_path, label="analysis_report"
    )
    render_report, _render_report_raw = _read_json(
        render_report_path, label="render_report"
    )
    quality_report, _quality_report_raw = _read_json(
        quality_report_path, label="quality_report"
    )
    bundle, _bundle_raw = _read_json(bundle_path, label="input_bundle")
    bundle_report, _bundle_report_raw = _read_json(
        bundle_report_path, label="input_bundle_report"
    )

    symbol, as_of = _require_common(analysis, label="analysis")
    _require_common(analysis_report, label="analysis_report", symbol=symbol, as_of=as_of)
    _require_common(bundle, label="input_bundle", symbol=symbol, as_of=as_of)
    _require_common(bundle_report, label="input_bundle_report", symbol=symbol, as_of=as_of)
    _require_common(quality_report, label="quality_report", symbol=symbol, as_of=as_of)
    if render_report.get("symbol") != symbol:
        raise DecisionInputError("SYMBOL_MISMATCH", "render_report.symbol does not match")

    _require_ready(analysis, "analysis_ready", label="analysis")
    _require_ready(analysis_report, "analysis_ready", label="analysis_report")
    _require_ready(bundle, "analysis_input_ready", label="input_bundle")
    _require_ready(bundle_report, "analysis_input_ready", label="input_bundle_report")
    _require_ready(quality_report, "quality_ready", label="quality_report")
    if analysis.get("status") != "ready" or analysis_report.get("status") != "ready":
        raise DecisionInputError("UPSTREAM_NOT_READY", "analysis artifacts must be ready")
    if bundle_report.get("status") != "ready":
        raise DecisionInputError("UPSTREAM_NOT_READY", "input bundle report must be ready")
    if render_report.get("status") != "ready":
        raise DecisionInputError("UPSTREAM_NOT_READY", "render report must be ready")
    if quality_report.get("quality_status") != "pass":
        raise DecisionInputError("UPSTREAM_NOT_READY", "quality report must pass")
    _require_ready(render_report, "analysis_ready", label="render_report")

    for payload, label in (
        (analysis, "analysis"),
        (analysis_report, "analysis_report"),
        (bundle, "input_bundle"),
        (bundle_report, "input_bundle_report"),
        (render_report, "render_report"),
        (quality_report, "quality_report"),
    ):
        _require_false(payload, "decision_ready", label=label)

    if analysis.get("analysis_version") != ANALYSIS_REPORT_VERSION:
        raise DecisionInputError("VERSION_INVALID", "analysis version is unsupported")
    if analysis_report.get("analysis_version") != ANALYSIS_REPORT_VERSION:
        raise DecisionInputError("VERSION_INVALID", "analysis report version is unsupported")
    bundle_version = bundle.get("bundle_version")
    if bundle_version not in SUPPORTED_BUNDLE_VERSIONS:
        raise DecisionInputError("VERSION_INVALID", "input bundle version is unsupported")
    if bundle_report.get("bundle_version") != bundle_version:
        raise DecisionInputError("VERSION_INVALID", "input bundle report version is unsupported")
    if quality_report.get("analysis_quality_version") != ANALYSIS_QUALITY_VERSION:
        raise DecisionInputError("VERSION_INVALID", "quality report version is unsupported")
    if render_report.get("schema_version") != RENDER_SCHEMA_VERSION:
        raise DecisionInputError("VERSION_INVALID", "render report schema is unsupported")

    analysis_sha = _file_sha(analysis_path, label="analysis")
    analysis_report_sha = _file_sha(analysis_report_path, label="analysis_report")
    bundle_sha = _file_sha(bundle_path, label="input_bundle")

    _require_sha(analysis_report, "output_sha256", analysis_sha, label="analysis_report")
    _require_sha(bundle_report, "bundle_sha256", bundle_sha, label="input_bundle_report")
    _require_sha(quality_report, "output_sha256", analysis_sha, label="quality_report")
    if quality_report.get("input_bundle_sha256") != bundle_sha:
        raise DecisionInputError("HASH_MISMATCH", "quality report input bundle SHA does not match")

    bundle_rel, resolved_bundle = _safe_relative(
        input_root,
        analysis_report.get("input_bundle_path"),
        label="analysis_report.input_bundle_path",
    )
    if resolved_bundle != bundle_path.resolve():
        raise DecisionInputError("BUNDLE_PATH_MISMATCH", "analysis report points to another bundle")
    if analysis.get("input_bundle_path") != bundle_rel:
        raise DecisionInputError("BUNDLE_PATH_MISMATCH", "analysis bundle paths differ")
    if analysis.get("input_bundle_sha256") != bundle_sha:
        raise DecisionInputError("HASH_MISMATCH", "analysis input bundle SHA does not match")
    if bundle_report.get("bundle_sha256") != bundle_sha:
        raise DecisionInputError("HASH_MISMATCH", "bundle report SHA does not match")

    if render_report.get("analysis_sha256") != analysis_sha:
        raise DecisionInputError("HASH_MISMATCH", "render report analysis SHA does not match")
    if render_report.get("analysis_report_sha256") != analysis_report_sha:
        raise DecisionInputError(
            "HASH_MISMATCH", "render report analysis-report SHA does not match"
        )
    _require_display_path(
        render_report,
        "input_analysis_path",
        analysis_path,
        label="render_report",
    )
    _require_display_path(
        render_report,
        "input_analysis_report_path",
        analysis_report_path,
        label="render_report",
    )

    rendered_rel, rendered_path = _safe_relative(
        render_report_path.parent,
        render_report.get("output_path"),
        label="render_report.output_path",
    )
    rendered_sha = _file_sha(rendered_path, label="rendered report")
    if render_report.get("output_sha256") != rendered_sha:
        raise DecisionInputError("HASH_MISMATCH", "rendered report SHA does not match")
    if quality_report.get("rendered_report_sha256") != rendered_sha:
        raise DecisionInputError("HASH_MISMATCH", "quality rendered-report SHA does not match")

    evidence = bundle.get("evidence")
    evidence_ids = {
        item.get("name") if isinstance(item, dict) else None for item in evidence
    } if isinstance(evidence, list) else set()
    if evidence_ids != set(EVIDENCE_IDS):
        raise DecisionInputError("EVIDENCE_COVERAGE", "input bundle evidence IDs are incomplete")

    artifact_root_ref = artifact_root.resolve()
    input_bundle_entry = _artifact_entry(
        path=bundle_path,
        artifact_root=artifact_root_ref,
        label="input_bundle",
        version=bundle.get("bundle_version"),
        status=bundle_report.get("status"),
        symbol=symbol,
        as_of=as_of,
        extra={"analysis_input_ready": True, "decision_ready": False},
    )
    bundle_report_entry = _artifact_entry(
        path=bundle_report_path,
        artifact_root=artifact_root_ref,
        label="input_bundle_report",
        version=bundle_report.get("bundle_version"),
        status=bundle_report.get("status"),
        symbol=symbol,
        as_of=as_of,
        extra={"bundle_sha256": bundle_sha, "decision_ready": False},
    )
    analysis_entry = _artifact_entry(
        path=analysis_path,
        artifact_root=artifact_root_ref,
        label="analysis",
        version=analysis.get("analysis_version"),
        status=analysis.get("status"),
        symbol=symbol,
        as_of=as_of,
        extra={"analysis_ready": True, "decision_ready": False},
    )
    analysis_report_entry = _artifact_entry(
        path=analysis_report_path,
        artifact_root=artifact_root_ref,
        label="analysis_report",
        version=analysis_report.get("analysis_version"),
        status=analysis_report.get("status"),
        symbol=symbol,
        as_of=as_of,
        extra={
            "analysis_sha256": analysis_sha,
            "decision_ready": False,
            "model": analysis_report.get("model"),
            "provider": analysis_report.get("provider"),
        },
    )
    render_entry = _artifact_entry(
        path=render_report_path,
        artifact_root=artifact_root_ref,
        label="render_report",
        version=render_report.get("schema_version"),
        status=render_report.get("status"),
        symbol=symbol,
        as_of=as_of,
        extra={
            "analysis_report_sha256": analysis_report_sha,
            "analysis_sha256": analysis_sha,
            "decision_ready": False,
            "output_path": _path_reference(
                rendered_path, root=artifact_root_ref, label="rendered report"
            ),
            "output_sha256": rendered_sha,
        },
    )
    quality_entry = _artifact_entry(
        path=quality_report_path,
        artifact_root=artifact_root_ref,
        label="quality_report",
        version=quality_report.get("analysis_quality_version"),
        status=quality_report.get("quality_status"),
        symbol=symbol,
        as_of=as_of,
        extra={
            "analysis_ready": True,
            "analysis_sha256": analysis_sha,
            "citation_coverage_percent": quality_report.get("citation_coverage_percent"),
            "decision_ready": False,
            "quality_ready": True,
            "rendered_report_sha256": rendered_sha,
        },
    )
    return {
        "as_of": as_of,
        "analysis": analysis_entry,
        "analysis_report": analysis_report_entry,
        "decision_input_ready": True,
        "decision_input_version": DECISION_INPUT_VERSION,
        "decision_ready": False,
        "input_bundle": input_bundle_entry,
        "input_bundle_report": bundle_report_entry,
        "input_root": _path_reference(input_root, root=artifact_root_ref, label="input_root"),
        "issues": [],
        "quality": quality_entry,
        "render": render_entry,
        "status": "ready",
        "symbol": symbol,
    }


def build_decision_input(
    *,
    analysis_path: Path,
    analysis_report_path: Path,
    render_report_path: Path,
    quality_report_path: Path,
    bundle_path: Path,
    bundle_report_path: Path,
    input_root: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build the decision-input snapshot and always write its audit report."""

    try:
        snapshot = _validate_inputs(
            analysis_path=analysis_path,
            analysis_report_path=analysis_report_path,
            render_report_path=render_report_path,
            quality_report_path=quality_report_path,
            bundle_path=bundle_path,
            bundle_report_path=bundle_report_path,
            input_root=input_root,
            artifact_root=artifact_root,
        )
    except DecisionInputError as exc:
        snapshot = {
            "as_of": None,
            "decision_input_ready": False,
            "decision_input_version": DECISION_INPUT_VERSION,
            "decision_ready": False,
            "issues": [{"code": exc.code, "message": str(exc)}],
            "status": "invalid",
            "symbol": None,
        }
    snapshot_bytes = _json_bytes(snapshot)
    snapshot_path = output_dir / "decision_input_snapshot.json"
    write_atomic(snapshot_path, snapshot_bytes)
    report = {
        "as_of": snapshot.get("as_of"),
        "decision_input_ready": snapshot.get("decision_input_ready") is True,
        "decision_input_version": DECISION_INPUT_VERSION,
        "decision_ready": False,
        "issues": snapshot.get("issues", []),
        "output_sha256": sha256_bytes(snapshot_bytes),
        "status": snapshot.get("status"),
        "symbol": snapshot.get("symbol"),
    }
    write_atomic(output_dir / "decision_input_report.json", _json_bytes(report))
    return report
