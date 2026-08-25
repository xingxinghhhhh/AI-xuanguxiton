"""Build a final read-only admission report for a market-aware research session."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .research_freshness import audit_research_freshness

MARKET_AWARE_SESSION_VERSION = "market-aware-session-v1"
MARKET_AWARE_RELEASE_REPLAY_VERSION = "market-aware-release-replay-v1"
ANALYSIS_INPUT_V2 = "analysis-input-v2"
MARKET_CONTEXT_SUMMARY_VERSION = "market-context-summary-v1"
RELATIVE_STRENGTH_VERSION = "relative-strength-v1"
TIME_POLICY = "explicit-reference-v1"


class MarketAwareSessionError(ValueError):
    """A fail-closed session admission validation error."""

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
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketAwareSessionError("INPUT_JSON_INVALID", f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return payload, raw


def _require(payload: Mapping[str, Any], field: str, expected: Any, *, label: str) -> None:
    if payload.get(field) != expected:
        raise MarketAwareSessionError("FIELD_MISMATCH", f"{label}.{field} is inconsistent")


def _parse_datetime(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionError("TIME_INVALID", f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionError("TIME_INVALID", f"{label} is not ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionError("TIME_INVALID", f"{label} must include timezone")
    return parsed


def _safe_artifact(path_value: Any, *, root: Path, label: str) -> tuple[Path, bytes, str]:
    if not isinstance(path_value, str) or not path_value.strip():
        raise MarketAwareSessionError("PATH_INVALID", f"{label} path is required")
    candidate = (root / Path(path_value)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise MarketAwareSessionError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionError("INPUT_UNAVAILABLE", f"{label} is not a file")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionError("INPUT_UNAVAILABLE", f"{label} is unavailable") from exc
    return candidate, raw, sha256_bytes(raw)


def _safe_input(path: Path, *, root: Path, label: str) -> tuple[Path, bytes, str]:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    return _safe_artifact(relative, root=root, label=label)


def _check_artifact_entry(
    entry: Any, *, artifact_root: Path, label: str
) -> tuple[Path, bytes, str]:
    if not isinstance(entry, Mapping):
        raise MarketAwareSessionError("ARTIFACT_INVALID", f"{label} entry is invalid")
    path, raw, digest = _safe_artifact(entry.get("path"), root=artifact_root, label=label)
    if entry.get("sha256") != digest:
        raise MarketAwareSessionError("HASH_MISMATCH", f"{label} SHA does not match")
    return path, raw, digest


def _validate_release(
    *,
    release_manifest_path: Path,
    release_report_path: Path,
    replay_report_path: Path,
    artifact_root: Path,
) -> dict[str, Any]:
    root = artifact_root.resolve()
    if not root.is_dir():
        raise MarketAwareSessionError("ARTIFACT_ROOT_INVALID", "artifact root must be a directory")
    manifest_path, manifest_raw, manifest_sha = _safe_input(
        release_manifest_path, root=root, label="research_release_manifest"
    )
    report_path, report_raw, report_sha = _safe_input(
        release_report_path, root=root, label="research_release_report"
    )
    replay_path, _replay_raw, _replay_sha = _safe_input(
        replay_report_path, root=root, label="market_aware_release_replay_report"
    )
    manifest, _ = _read_json(manifest_path, label="research_release_manifest")
    release_report, _ = _read_json(report_path, label="research_release_report")
    replay_report, _ = _read_json(replay_path, label="market_aware_release_replay_report")

    _require(manifest, "research_release_version", "research-release-v1", label="manifest")
    _require(release_report, "research_release_version", "research-release-v1", label="report")
    _require(manifest, "research_release_ready", True, label="manifest")
    _require(release_report, "research_release_ready", True, label="report")
    _require(manifest, "status", "ready", label="manifest")
    _require(release_report, "status", "ready", label="report")
    _require(manifest, "decision_ready", False, label="manifest")
    _require(release_report, "decision_ready", False, label="report")
    if release_report.get("output_sha256") != manifest_sha:
        raise MarketAwareSessionError("HASH_MISMATCH", "release report does not match manifest")
    if release_report.get("output_sha256") != sha256_bytes(manifest_raw):
        raise MarketAwareSessionError("HASH_MISMATCH", "release manifest SHA is inconsistent")
    if not isinstance(release_report.get("output_sha256"), str):
        raise MarketAwareSessionError("HASH_INVALID", "release report output SHA is required")
    if report_sha == manifest_sha:
        raise MarketAwareSessionError("HASH_INVALID", "release manifest and report must differ")
    if not isinstance(manifest.get("symbol"), str) or not manifest["symbol"].strip():
        raise MarketAwareSessionError("SYMBOL_INVALID", "release symbol is required")
    as_of = _parse_datetime(manifest.get("as_of"), label="release_as_of")
    if (
        release_report.get("symbol") != manifest["symbol"]
        or release_report.get("as_of") != manifest["as_of"]
    ):
        raise MarketAwareSessionError("CHAIN_MISMATCH", "release symbol or as_of is inconsistent")

    artifacts = manifest.get("artifacts")
    expected_artifacts = {
        "decision_input_snapshot",
        "decision_input_report",
        "safety_report",
        "analysis_review_packet",
        "analysis_review_result",
        "analysis_review_result_report",
    }
    if not isinstance(artifacts, Mapping) or set(artifacts) != expected_artifacts:
        raise MarketAwareSessionError("ARTIFACT_INVALID", "release artifact set is inconsistent")
    resolved: dict[str, tuple[Path, bytes, str]] = {}
    for label in sorted(expected_artifacts):
        resolved[label] = _check_artifact_entry(
            artifacts[label], artifact_root=root, label=label
        )

    snapshot, _ = _read_json(
        resolved["decision_input_snapshot"][0], label="decision_input_snapshot"
    )
    snapshot_report, _ = _read_json(
        resolved["decision_input_report"][0], label="decision_input_report"
    )
    _require(snapshot, "decision_input_version", "decision-input-v1", label="snapshot")
    _require(snapshot_report, "decision_input_version", "decision-input-v1", label="report")
    _require(snapshot, "decision_input_ready", True, label="snapshot")
    _require(snapshot_report, "decision_input_ready", True, label="report")
    _require(snapshot, "decision_ready", False, label="snapshot")
    _require(snapshot_report, "decision_ready", False, label="report")
    if snapshot_report.get("output_sha256") != resolved["decision_input_snapshot"][2]:
        raise MarketAwareSessionError(
            "HASH_MISMATCH", "decision input report does not match snapshot"
        )
    bundle_ref = snapshot.get("input_bundle")
    if not isinstance(bundle_ref, Mapping):
        raise MarketAwareSessionError(
            "CHAIN_MISMATCH", "decision input bundle reference is missing"
        )
    bundle_path, bundle_raw, bundle_sha = _safe_artifact(
        bundle_ref.get("path"), root=root, label="analysis_input_bundle"
    )
    if bundle_ref.get("sha256") != bundle_sha:
        raise MarketAwareSessionError("HASH_MISMATCH", "decision input bundle SHA does not match")
    bundle, _ = _read_json(bundle_path, label="analysis_input_bundle")
    _require(bundle, "bundle_version", ANALYSIS_INPUT_V2, label="analysis_input_bundle")
    summaries = bundle.get("summaries")
    market = summaries.get("market") if isinstance(summaries, Mapping) else None
    technical = summaries.get("technical") if isinstance(summaries, Mapping) else None
    context = market.get("market_context") if isinstance(market, Mapping) else None
    relative_strength = (
        technical.get("relative_strength") if isinstance(technical, Mapping) else None
    )
    if (
        not isinstance(context, Mapping)
        or context.get("market_context_summary_version") != MARKET_CONTEXT_SUMMARY_VERSION
    ):
        raise MarketAwareSessionError(
            "VERSION_MISMATCH", "market context summary version is missing"
        )
    if (
        not isinstance(relative_strength, Mapping)
        or relative_strength.get("version") != RELATIVE_STRENGTH_VERSION
    ):
        raise MarketAwareSessionError("VERSION_MISMATCH", "relative strength version is missing")
    if bundle.get("symbol") != manifest["symbol"] or bundle.get("as_of") != manifest["as_of"]:
        raise MarketAwareSessionError("CHAIN_MISMATCH", "bundle symbol or as_of is inconsistent")

    _require(replay_report, "replay_version", MARKET_AWARE_RELEASE_REPLAY_VERSION, label="replay")
    _require(replay_report, "status", "ready", label="replay")
    _require(replay_report, "research_release_ready", True, label="replay")
    _require(replay_report, "review_complete", True, label="replay")
    _require(replay_report, "review_gate_pass", True, label="replay")
    _require(replay_report, "decision_ready", False, label="replay")
    if replay_report.get("bundle_sha256") != bundle_sha:
        raise MarketAwareSessionError("HASH_MISMATCH", "replay bundle SHA does not match bundle")
    if (
        replay_report.get("symbol") != manifest["symbol"]
        or replay_report.get("as_of") != manifest["as_of"]
    ):
        raise MarketAwareSessionError("CHAIN_MISMATCH", "replay symbol or as_of is inconsistent")
    if replay_report.get("market_context_summary_version") != MARKET_CONTEXT_SUMMARY_VERSION:
        raise MarketAwareSessionError(
            "VERSION_MISMATCH", "replay market context version is inconsistent"
        )
    if replay_report.get("relative_strength_version") != RELATIVE_STRENGTH_VERSION:
        raise MarketAwareSessionError(
            "VERSION_MISMATCH", "replay relative strength version is inconsistent"
        )
    stages = replay_report.get("stages")
    release_stage = stages.get("research_release") if isinstance(stages, Mapping) else None
    replay_root = replay_path.parent.resolve()
    try:
        expected_manifest_path = manifest_path.relative_to(replay_root).as_posix()
        expected_report_path = report_path.relative_to(replay_root).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", "release artifacts are outside replay root"
        ) from exc
    if not isinstance(release_stage, Mapping):
        raise MarketAwareSessionError("CHAIN_MISMATCH", "replay release stage is missing")
    if release_stage.get("status") != "ready" or release_stage.get("ready") is not True:
        raise MarketAwareSessionError("CHAIN_MISMATCH", "replay release stage is not ready")
    if release_stage.get("output_path") != expected_manifest_path:
        raise MarketAwareSessionError("CHAIN_MISMATCH", "replay manifest path is inconsistent")
    if release_stage.get("report_path") != expected_report_path:
        raise MarketAwareSessionError("CHAIN_MISMATCH", "replay report path is inconsistent")
    if release_stage.get("output_sha256") != manifest_sha:
        raise MarketAwareSessionError("HASH_MISMATCH", "replay manifest SHA does not match")
    if release_stage.get("report_sha256") != report_sha:
        raise MarketAwareSessionError("HASH_MISMATCH", "replay report SHA does not match")

    return {
        "as_of": manifest["as_of"],
        "bundle_sha256": bundle_sha,
        "market_context_summary_version": MARKET_CONTEXT_SUMMARY_VERSION,
        "relative_strength_version": RELATIVE_STRENGTH_VERSION,
        "release_manifest_sha256": manifest_sha,
        "review_complete": True,
        "review_gate_pass": True,
        "research_release_ready": True,
        "symbol": manifest["symbol"],
        "release_as_of_datetime": as_of,
    }


def _base_session(*, evaluation_at: datetime, reference_at: datetime) -> dict[str, Any]:
    return {
        "as_of": None,
        "bundle_sha256": None,
        "calendar_sha256": None,
        "decision_ready": False,
        "evaluation_at": evaluation_at.isoformat(),
        "freshness_report_sha256": None,
        "freshness_ready": False,
        "freshness_status": "invalid",
        "issues": [],
        "market_context_summary_version": None,
        "relative_strength_version": None,
        "release_manifest_sha256": None,
        "research_release_ready": False,
        "review_complete": False,
        "review_gate_pass": False,
        "session_ready": False,
        "session_version": MARKET_AWARE_SESSION_VERSION,
        "status": "invalid",
        "symbol": None,
        "reference_at": reference_at.isoformat(),
        "time_policy": TIME_POLICY,
    }


def _write_session(output_dir: Path, session: Mapping[str, Any]) -> dict[str, Any]:
    session_bytes = _json_bytes(session)
    write_atomic(output_dir / "market_aware_session.json", session_bytes)
    report = dict(session)
    report["output_sha256"] = sha256_bytes(session_bytes)
    write_atomic(output_dir / "market_aware_session_report.json", _json_bytes(report))
    return report


def build_market_aware_session(
    *,
    release_manifest_path: Path,
    release_report_path: Path,
    replay_report_path: Path,
    calendar_path: Path,
    calendar_report_path: Path,
    evaluation_at: datetime,
    reference_at: datetime,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a read-only session admission snapshot and report."""

    session = _base_session(evaluation_at=evaluation_at, reference_at=reference_at)
    try:
        for label, value in (("evaluation_at", evaluation_at), ("reference_at", reference_at)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise MarketAwareSessionError("TIME_INVALID", f"{label} must include timezone")
        if evaluation_at.astimezone(UTC) > reference_at.astimezone(UTC):
            raise MarketAwareSessionError("TIME_IN_FUTURE", "evaluation_at is after reference_at")
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "session output is outside artifact root"
            ) from exc
        details = _validate_release(
            release_manifest_path=release_manifest_path,
            release_report_path=release_report_path,
            replay_report_path=replay_report_path,
            artifact_root=root,
        )
        if details["release_as_of_datetime"].astimezone(UTC) > evaluation_at.astimezone(UTC):
            raise MarketAwareSessionError(
                "RELEASE_AFTER_EVALUATION", "release_as_of is after evaluation_at"
            )
        calendar_file, _calendar_raw, _calendar_sha = _safe_input(
            calendar_path, root=root, label="calendar"
        )
        calendar_report_file, _calendar_report_raw, _calendar_report_sha = _safe_input(
            calendar_report_path, root=root, label="calendar_report"
        )
        freshness = audit_research_freshness(
            release_manifest_path=release_manifest_path,
            release_report_path=release_report_path,
            calendar_path=calendar_file,
            calendar_report_path=calendar_report_file,
            evaluation_at=evaluation_at,
            output_dir=output_dir,
        )
        freshness_path = output_dir / "research_freshness_report.json"
        freshness_raw = freshness_path.read_bytes()
        session.update(
            {
                "as_of": details["as_of"],
                "bundle_sha256": details["bundle_sha256"],
                "calendar_sha256": freshness.get("calendar_sha256"),
                "freshness_report_sha256": sha256_bytes(freshness_raw),
                "freshness_ready": freshness.get("freshness_ready") is True,
                "freshness_status": freshness.get("freshness_status"),
                "market_context_summary_version": details["market_context_summary_version"],
                "relative_strength_version": details["relative_strength_version"],
                "release_manifest_sha256": details["release_manifest_sha256"],
                "research_release_ready": details["research_release_ready"],
                "review_complete": details["review_complete"],
                "review_gate_pass": details["review_gate_pass"],
                "symbol": details["symbol"],
            }
        )
        if freshness.get("freshness_status") == "fresh":
            session.update({"session_ready": True, "status": "ready"})
        else:
            session["issues"] = list(freshness.get("issues", []))
            session["status"] = freshness.get("freshness_status", "invalid")
    except MarketAwareSessionError as exc:
        session["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        session["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    return _write_session(output_dir, session)
