"""Offline replay from an explicit human review submission to a release."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .research_release import build_research_release
from .review_record import apply_analysis_review

MARKET_AWARE_RELEASE_REPLAY_VERSION = "market-aware-release-replay-v1"


class MarketAwareReleaseReplayError(ValueError):
    """A fail-closed input error before review application starts."""


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketAwareReleaseReplayError(f"{label} is unavailable or invalid JSON") from exc
    if not isinstance(value, dict):
        raise MarketAwareReleaseReplayError(f"{label} must be a JSON object")
    return value, raw


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareReleaseReplayError(f"path is outside artifact root: {path}") from exc


def _stage(
    *,
    report: Mapping[str, Any],
    report_path: Path,
    output_path: Path,
    output_root: Path,
    ready: bool,
) -> dict[str, Any]:
    return {
        "output_path": _relative(output_path, output_root) if output_path.exists() else None,
        "output_sha256": sha256_bytes(output_path.read_bytes()) if output_path.exists() else None,
        "ready": ready,
        "report_path": _relative(report_path, output_root) if report_path.exists() else None,
        "report_sha256": sha256_bytes(report_path.read_bytes()) if report_path.exists() else None,
        "status": report.get("status"),
    }


def _write_report(output_dir: Path, report: Mapping[str, Any]) -> dict[str, Any]:
    write_atomic(output_dir / "market_aware_release_replay_report.json", _json_bytes(report))
    return dict(report)


def _market_context_metadata(
    *, packet_path: Path, artifact_root: Path
) -> tuple[dict[str, Any], str | None, str | None, str | None]:
    replay_root = packet_path.parent.parent
    decision_snapshot_path = replay_root / "decision-input" / "decision_input_snapshot.json"
    snapshot, _ = _read_json(decision_snapshot_path, label="decision_input_snapshot")
    bundle_reference = snapshot.get("input_bundle")
    if not isinstance(bundle_reference, Mapping) or not isinstance(
        bundle_reference.get("path"), str
    ):
        raise MarketAwareReleaseReplayError("decision input does not reference an input bundle")
    bundle_path = artifact_root / bundle_reference["path"]
    bundle, bundle_raw = _read_json(bundle_path, label="analysis_input_bundle")
    if bundle.get("bundle_version") != "analysis-input-v2":
        raise MarketAwareReleaseReplayError("release replay requires analysis-input-v2")
    summaries = bundle.get("summaries")
    market = summaries.get("market") if isinstance(summaries, Mapping) else None
    technical = summaries.get("technical") if isinstance(summaries, Mapping) else None
    context = market.get("market_context") if isinstance(market, Mapping) else None
    relative_strength = (
        technical.get("relative_strength") if isinstance(technical, Mapping) else None
    )
    if (
        not isinstance(context, Mapping)
        or context.get("market_context_summary_version") != "market-context-summary-v1"
    ):
        raise MarketAwareReleaseReplayError("market-context-summary-v1 is missing")
    if (
        not isinstance(relative_strength, Mapping)
        or relative_strength.get("version") != "relative-strength-v1"
    ):
        raise MarketAwareReleaseReplayError("relative-strength-v1 is missing")
    return (
        bundle,
        sha256_bytes(bundle_raw),
        str(context["market_context_summary_version"]),
        str(relative_strength["version"]),
    )


def replay_market_aware_release(
    *,
    packet_path: Path,
    packet_report_path: Path,
    submission_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Apply only an explicit submission, then build a guarded release."""

    base: dict[str, Any] = {
        "as_of": None,
        "bundle_sha256": None,
        "decision_ready": False,
        "issues": [],
        "market_context_summary_version": None,
        "relative_strength_version": None,
        "research_release_ready": False,
        "review_complete": False,
        "review_gate_pass": False,
        "replay_version": MARKET_AWARE_RELEASE_REPLAY_VERSION,
        "stages": {
            "review_record": {
                "output_path": None,
                "output_sha256": None,
                "ready": False,
                "report_path": None,
                "report_sha256": None,
                "status": "skipped",
            },
            "research_release": {
                "output_path": None,
                "output_sha256": None,
                "ready": False,
                "report_path": None,
                "report_sha256": None,
                "status": "skipped",
            },
        },
        "status": "invalid",
        "symbol": None,
    }
    try:
        bundle, bundle_sha, context_version, relative_version = _market_context_metadata(
            packet_path=packet_path, artifact_root=artifact_root
        )
        base.update(
            {
                "as_of": bundle.get("as_of"),
                "bundle_sha256": bundle_sha,
                "market_context_summary_version": context_version,
                "relative_strength_version": relative_version,
                "symbol": bundle.get("symbol"),
            }
        )
    except MarketAwareReleaseReplayError as exc:
        base["issues"].append({"code": "INPUT_INVALID", "message": str(exc)})
        return _write_report(output_dir, base)

    review_result_dir = output_dir / "review-result"
    review_result = apply_analysis_review(
        packet_path=packet_path,
        packet_report_path=packet_report_path,
        submission_path=submission_path,
        output_dir=review_result_dir,
    )
    review_result_path = review_result_dir / "analysis_review_result.json"
    review_result_report_path = review_result_dir / "analysis_review_result_report.json"
    base["stages"]["review_record"] = _stage(
        report=review_result,
        report_path=review_result_report_path,
        output_path=review_result_path,
        output_root=output_dir,
        ready=review_result.get("status") == "ready",
    )
    if review_result.get("status") != "ready":
        base["issues"].extend(review_result.get("issues", []))
        return _write_report(output_dir, base)

    base.update(
        {
            "review_complete": review_result.get("review_complete") is True,
            "review_gate_pass": review_result.get("review_gate_pass") is True,
        }
    )
    if not base["review_complete"] or not base["review_gate_pass"]:
        base["issues"].append(
            {
                "code": "REVIEW_GATE_FAILED",
                "message": "review submission is complete but not fully confirmed",
            }
        )
        base["status"] = "review_gate_failed"
        return _write_report(output_dir, base)

    replay_root = packet_path.parent.parent
    decision_dir = replay_root / "decision-input"
    safety_report_path = replay_root / "safety" / "analysis_safety_report.json"
    decision_input_path = decision_dir / "decision_input_snapshot.json"
    decision_input_report_path = decision_dir / "decision_input_report.json"
    release_dir = output_dir / "release"
    release_report = build_research_release(
        decision_input_path=decision_input_path,
        decision_input_report_path=decision_input_report_path,
        safety_report_path=safety_report_path,
        review_packet_path=packet_path,
        review_result_path=review_result_path,
        review_result_report_path=review_result_report_path,
        artifact_root=output_dir.parent,
        output_dir=release_dir,
    )
    release_manifest_path = release_dir / "research_release_manifest.json"
    release_report_path = release_dir / "research_release_report.json"
    base["stages"]["research_release"] = _stage(
        report=release_report,
        report_path=release_report_path,
        output_path=release_manifest_path,
        output_root=output_dir,
        ready=release_report.get("research_release_ready") is True,
    )
    if release_report.get("research_release_ready") is not True:
        base["issues"].extend(release_report.get("issues", []))
        return _write_report(output_dir, base)
    base.update(
        {
            "research_release_ready": True,
            "status": "ready",
        }
    )
    return _write_report(output_dir, base)
