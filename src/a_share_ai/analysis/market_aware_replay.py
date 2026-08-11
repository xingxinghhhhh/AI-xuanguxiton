"""Offline end-to-end replay for a market-aware analysis-input-v2 chain."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..decision.decision_input import build_decision_input
from ..market.replay import sha256_bytes, write_atomic
from .contracts import AnalysisReportConfig
from .quality import audit_analysis_quality
from .renderer import render_analysis
from .review import build_analysis_review
from .safety import audit_analysis_safety
from .validator import AnalysisReportSource

MARKET_AWARE_REPLAY_VERSION = "market-aware-replay-v1"
STAGE_NAMES = ("analysis", "render", "quality", "decision_input", "safety", "review")


class MarketAwareReplayError(ValueError):
    """A fail-closed input error before the downstream chain is started."""


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketAwareReplayError(f"{label} is unavailable or invalid JSON") from exc
    if not isinstance(value, dict):
        raise MarketAwareReplayError(f"{label} must be a JSON object")
    return value, raw


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareReplayError(f"path is outside its declared root: {path}") from exc


def _empty_stages() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "output_path": None,
            "output_sha256": None,
            "ready": False,
            "report_path": None,
            "report_sha256": None,
            "status": "skipped",
        }
        for name in STAGE_NAMES
    }


def _record_stage(
    *,
    name: str,
    report: Mapping[str, Any],
    report_path: Path,
    output_path: Path,
    output_root: Path,
    ready_field: str,
) -> dict[str, Any]:
    status = report.get("status")
    if not isinstance(status, str):
        status = report.get("quality_status") or report.get("review_status")
    return {
        "output_path": _relative(output_path, output_root),
        "output_sha256": sha256_bytes(output_path.read_bytes()) if output_path.exists() else None,
        "ready": report.get(ready_field) is True,
        "report_path": _relative(report_path, output_root),
        "report_sha256": sha256_bytes(report_path.read_bytes()) if report_path.exists() else None,
        "status": status,
    }


def _stage_issue(name: str, report: Mapping[str, Any]) -> dict[str, str]:
    issues = report.get("issues")
    if isinstance(issues, list) and issues and isinstance(issues[0], Mapping):
        code = issues[0].get("code")
        message = issues[0].get("message")
        if isinstance(code, str) and isinstance(message, str):
            return {"code": f"{name.upper()}_{code}", "message": message}
    return {"code": "STAGE_FAILED", "message": f"{name} stage did not become ready"}


def _write_report(output_dir: Path, report: Mapping[str, Any]) -> dict[str, Any]:
    write_atomic(output_dir / "market_aware_replay_report.json", _json_bytes(report))
    return dict(report)


def replay_market_aware_analysis(
    *,
    bundle_path: Path,
    bundle_report_path: Path,
    input_root: Path,
    response_fixture: Path | None,
    output_dir: Path,
    provider: str = "offline",
    model: str = "offline",
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Replay the v2 research chain through a pending-only review packet."""

    stages = _empty_stages()
    bundle: dict[str, Any] | None = None
    bundle_raw = b""
    bundle_report_raw = b""
    symbol: str | None = None
    as_of: str | None = None
    market_context_summary_version: str | None = None
    relative_strength_version: str | None = None
    issues: list[dict[str, str]] = []

    try:
        bundle, bundle_raw = _read_json(bundle_path, label="analysis_input_bundle")
        bundle_report, bundle_report_raw = _read_json(
            bundle_report_path, label="analysis_input_report"
        )
        symbol = bundle.get("symbol") if isinstance(bundle.get("symbol"), str) else None
        as_of = bundle.get("as_of") if isinstance(bundle.get("as_of"), str) else None
        if bundle.get("bundle_version") != "analysis-input-v2":
            raise MarketAwareReplayError("market-aware replay requires analysis-input-v2")
        if (
            bundle.get("analysis_input_ready") is not True
            or bundle.get("decision_ready") is not False
        ):
            raise MarketAwareReplayError("analysis-input-v2 readiness gates are invalid")
        if bundle_report.get("bundle_version") != "analysis-input-v2":
            raise MarketAwareReplayError("analysis input report must declare analysis-input-v2")
        summaries = bundle.get("summaries")
        market_summary = summaries.get("market") if isinstance(summaries, Mapping) else None
        technical_summary = summaries.get("technical") if isinstance(summaries, Mapping) else None
        context = (
            market_summary.get("market_context")
            if isinstance(market_summary, Mapping)
            else None
        )
        relative_strength = (
            technical_summary.get("relative_strength")
            if isinstance(technical_summary, Mapping)
            else None
        )
        if not isinstance(context, Mapping):
            raise MarketAwareReplayError("market-context summary is missing")
        if context.get("market_context_summary_version") != "market-context-summary-v1":
            raise MarketAwareReplayError("market-context-summary-v1 is required")
        if not isinstance(relative_strength, Mapping):
            raise MarketAwareReplayError("relative-strength summary is missing")
        if relative_strength.get("version") != "relative-strength-v1":
            raise MarketAwareReplayError("relative-strength-v1 is required")
        market_context_summary_version = str(context["market_context_summary_version"])
        relative_strength_version = str(relative_strength["version"])
    except MarketAwareReplayError as exc:
        issues.append({"code": "INPUT_INVALID", "message": str(exc)})

    base = {
        "as_of": as_of,
        "analysis_ready": False,
        "bundle_report_sha256": sha256_bytes(bundle_report_raw) if bundle_report_raw else None,
        "bundle_sha256": sha256_bytes(bundle_raw) if bundle_raw else None,
        "market_context_summary_version": market_context_summary_version,
        "decision_ready": False,
        "issues": issues,
        "replay_version": MARKET_AWARE_REPLAY_VERSION,
        "relative_strength_version": relative_strength_version,
        "review_complete": False,
        "review_packet_ready": False,
        "safety_ready": False,
        "quality_ready": False,
        "review_status": None,
        "stages": stages,
        "status": "invalid",
        "symbol": symbol,
    }
    if issues or bundle is None:
        return _write_report(output_dir, base)

    analysis_dir = output_dir / "analysis"
    render_dir = output_dir / "rendered"
    quality_dir = output_dir / "quality"
    decision_dir = output_dir / "decision-input"
    safety_dir = output_dir / "safety"
    review_dir = output_dir / "review"
    if provider == "offline" and response_fixture is None:
        base["issues"].append(
            {"code": "INPUT_INVALID", "message": "offline replay requires a response fixture"}
        )
        return _write_report(output_dir, base)
    analysis_config = AnalysisReportConfig(
        bundle_path=bundle_path,
        bundle_report=bundle_report_path,
        input_root=input_root,
        response_fixture=response_fixture,
        output_dir=analysis_dir,
        provider=provider,
        model=model,
        env_file=env_file,
    )

    def finish(
        report: Mapping[str, Any],
        *,
        name: str,
        ready_field: str,
        report_path: Path,
        output_path: Path,
    ) -> bool:
        stages[name] = _record_stage(
            name=name,
            report=report,
            report_path=report_path,
            output_path=output_path,
            output_root=output_dir,
            ready_field=ready_field,
        )
        if report.get(ready_field) is not True:
            base["issues"].append(_stage_issue(name, report))
            return False
        return True

    analysis_report = AnalysisReportSource(analysis_config).capture()
    if not finish(
        analysis_report,
        name="analysis",
        ready_field="analysis_ready",
        report_path=analysis_dir / "research_analysis_report.json",
        output_path=analysis_dir / "research_analysis.json",
    ):
        return _write_report(output_dir, base)

    analysis_path = analysis_dir / "research_analysis.json"
    analysis_report_path = analysis_dir / "research_analysis_report.json"
    render_report = render_analysis(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        input_root=input_root,
        output_dir=render_dir,
    )
    if not finish(
        render_report,
        name="render",
        ready_field="analysis_ready",
        report_path=render_dir / "analysis_render_report.json",
        output_path=render_dir / "research_analysis.md",
    ):
        return _write_report(output_dir, base)

    render_report_path = render_dir / "analysis_render_report.json"
    quality_report = audit_analysis_quality(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        render_report_path=render_report_path,
        input_root=input_root,
        output_dir=quality_dir,
    )
    if not finish(
        quality_report,
        name="quality",
        ready_field="quality_ready",
        report_path=quality_dir / "analysis_quality_report.json",
        output_path=quality_dir / "analysis_quality_report.json",
    ):
        return _write_report(output_dir, base)

    quality_report_path = quality_dir / "analysis_quality_report.json"
    decision_report = build_decision_input(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        render_report_path=render_report_path,
        quality_report_path=quality_report_path,
        bundle_path=bundle_path,
        bundle_report_path=bundle_report_path,
        input_root=input_root,
        artifact_root=output_dir.parent,
        output_dir=decision_dir,
    )
    if not finish(
        decision_report,
        name="decision_input",
        ready_field="decision_input_ready",
        report_path=decision_dir / "decision_input_report.json",
        output_path=decision_dir / "decision_input_snapshot.json",
    ):
        return _write_report(output_dir, base)

    decision_input_path = decision_dir / "decision_input_snapshot.json"
    decision_report_path = decision_dir / "decision_input_report.json"
    safety_report = audit_analysis_safety(
        decision_input_path=decision_input_path,
        decision_input_report_path=decision_report_path,
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        render_report_path=render_report_path,
        artifact_root=output_dir.parent,
        output_dir=safety_dir,
    )
    if not finish(
        safety_report,
        name="safety",
        ready_field="safety_ready",
        report_path=safety_dir / "analysis_safety_report.json",
        output_path=safety_dir / "analysis_safety_report.json",
    ):
        return _write_report(output_dir, base)

    safety_report_path = safety_dir / "analysis_safety_report.json"
    review_report = build_analysis_review(
        decision_input_path=decision_input_path,
        decision_input_report_path=decision_report_path,
        safety_report_path=safety_report_path,
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        artifact_root=output_dir.parent,
        output_dir=review_dir,
    )
    if not finish(
        review_report,
        name="review",
        ready_field="review_packet_ready",
        report_path=review_dir / "analysis_review_report.json",
        output_path=review_dir / "analysis_review_packet.json",
    ):
        return _write_report(output_dir, base)

    base.update(
        {
            "analysis_ready": True,
            "quality_ready": True,
            "review_packet_ready": True,
            "review_status": "pending",
            "safety_ready": True,
            "status": "ready",
        }
    )
    return _write_report(output_dir, base)
