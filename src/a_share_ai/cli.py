"""Command line entry points for the offline A-share data MVP."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .analysis.contracts import AnalysisReportConfig
from .analysis.market_aware_release_replay import replay_market_aware_release
from .analysis.market_aware_replay import replay_market_aware_analysis
from .analysis.market_aware_session import build_market_aware_session
from .analysis.market_aware_session_history import build_market_aware_session_history
from .analysis.market_aware_session_history_audit import audit_market_aware_session_history
from .analysis.market_aware_session_history_closure import (
    build_market_aware_session_history_closure,
)
from .analysis.market_aware_session_history_closure_admission import (
    build_market_aware_session_history_closure_admission,
)
from .analysis.market_aware_session_history_closure_admission_audit import (
    audit_market_aware_session_history_closure_admission,
)
from .analysis.market_aware_session_history_closure_admission_render_audit import (
    audit_market_aware_session_history_closure_admission_render,
)
from .analysis.market_aware_session_history_closure_admission_renderer import (
    render_market_aware_session_history_closure_admission,
)
from .analysis.market_aware_session_history_closure_render_audit import (
    audit_market_aware_session_history_closure_render,
)
from .analysis.market_aware_session_history_closure_renderer import (
    render_market_aware_session_history_closure,
)
from .analysis.market_aware_session_history_final_receipt import (
    build_market_aware_session_history_final_receipt,
)
from .analysis.market_aware_session_history_manifest import (
    build_market_aware_session_history_manifest,
)
from .analysis.market_aware_session_history_manifest_audit import (
    audit_market_aware_session_history_manifest,
)
from .analysis.market_aware_session_history_manifest_render_audit import (
    audit_market_aware_session_history_manifest_render,
)
from .analysis.market_aware_session_history_manifest_renderer import (
    render_market_aware_session_history_manifest,
)
from .analysis.market_aware_session_history_render_audit import (
    audit_market_aware_session_history_render,
)
from .analysis.market_aware_session_history_renderer import render_market_aware_session_history
from .analysis.market_aware_session_package import build_market_aware_session_package
from .analysis.market_aware_session_package_audit import audit_market_aware_session_package
from .analysis.market_aware_session_package_diff import compare_market_aware_session_packages
from .analysis.market_aware_session_package_diff_render_audit import (
    audit_market_aware_session_package_diff_render,
)
from .analysis.market_aware_session_package_diff_renderer import (
    render_market_aware_session_package_diff,
)
from .analysis.market_aware_session_renderer import render_market_aware_session
from .analysis.market_aware_smoke import smoke_market_aware_analysis
from .analysis.quality import audit_analysis_quality
from .analysis.release_diff import compare_research_releases
from .analysis.renderer import render_analysis
from .analysis.research_freshness import audit_research_freshness
from .analysis.research_release import build_research_release
from .analysis.review import build_analysis_review
from .analysis.review_record import apply_analysis_review
from .analysis.safety import audit_analysis_safety
from .analysis.technical_features import (
    TechnicalFeatureReport,
    build_feature_report,
    compute_feature_snapshots,
    serialize_feature_snapshots,
)
from .analysis.validator import AnalysisReportSource
from .decision.decision_input import build_decision_input
from .decision.technical_price_plan import (
    PricePlanIssue,
    PricePlanReport,
    build_price_plan_report,
    compute_price_plan,
    serialize_price_plan,
)
from .events.cninfo_announcements import CninfoAnnouncementConfig, CninfoAnnouncementSource
from .evidence.analysis_input_bundle import AnalysisInputConfig, AnalysisInputSource
from .evidence.contracts import BUNDLE_VERSION_V1, BUNDLE_VERSION_V2
from .fundamentals.baostock_growth import BaostockGrowthConfig, BaostockGrowthSource
from .fundamentals.baostock_profitability import (
    BaostockProfitabilityConfig,
    BaostockProfitabilitySource,
)
from .market.adapter import JsonlReplaySource, ReplayInputError
from .market.adapters.baostock_daily import BaostockDailyConfig, BaostockDailySource
from .market.baostock_market_context import BaostockMarketContextSource
from .market.calendar import CalendarError, JsonTradingCalendarSource
from .market.coverage import CoverageReport, audit_daily_coverage
from .market.health import HealthReport, ValidationIssue, build_health_report
from .market.market_context import INDEX_SYMBOLS, MARKET_CONTEXT_VERSION, MarketContextConfig
from .market.replay import canonical_jsonl, sha256_bytes, write_atomic
from .service.read_only_receipt_probe import (
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    ReadOnlyReceiptProbeError,
    probe_exit_code,
    probe_read_only_receipt_service,
)
from .service.read_only_receipt_server import (
    DEFAULT_READ_ONLY_RECEIPT_HOST,
    DEFAULT_READ_ONLY_RECEIPT_PORT,
    ReadOnlyReceiptServiceError,
    serve_read_only_receipt,
)


def _parse_as_of(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("as-of must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("as-of must include a timezone")
    return parsed


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be an ISO date") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A-share read-only data replay and audit tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay = subparsers.add_parser("replay-health", help="replay JSONL and create a health report")
    replay.add_argument("--input", type=Path, required=True)
    replay.add_argument("--as-of", type=_parse_as_of, required=True)
    replay.add_argument("--output-dir", type=Path, required=True)
    replay.add_argument("--stale-after-seconds", type=int, default=86_400)

    coverage = subparsers.add_parser(
        "audit-coverage", help="audit daily-bar dates against a calendar"
    )
    coverage.add_argument("--bars", type=Path, required=True)
    coverage.add_argument("--calendar", type=Path, required=True)
    coverage.add_argument("--start", type=_parse_date, required=True)
    coverage.add_argument("--end", type=_parse_date, required=True)
    coverage.add_argument("--as-of", type=_parse_as_of, required=True)
    coverage.add_argument("--output-dir", type=Path, required=True)

    capture = subparsers.add_parser(
        "capture-baostock-daily", help="capture one Baostock historical daily series"
    )
    capture.add_argument("--symbol", required=True)
    capture.add_argument("--start", type=_parse_date, required=True)
    capture.add_argument("--end", type=_parse_date, required=True)
    capture.add_argument("--received-at", type=_parse_as_of, required=True)
    capture.add_argument("--output-dir", type=Path, required=True)

    market_context = subparsers.add_parser(
        "capture-market-context",
        help="capture fixed benchmark-index context from Baostock",
    )
    market_context.add_argument("--start", type=_parse_date, required=True)
    market_context.add_argument("--end", type=_parse_date, required=True)
    market_context.add_argument("--as-of", type=_parse_as_of, required=True)
    market_context.add_argument("--received-at", type=_parse_as_of, required=True)
    market_context.add_argument("--calendar", type=Path, required=True)
    market_context.add_argument("--output-dir", type=Path, required=True)

    features = subparsers.add_parser(
        "compute-technical-features", help="compute fixed-parameter daily technical features"
    )
    features.add_argument("--input", type=Path, required=True)
    features.add_argument("--output-dir", type=Path, required=True)
    features.add_argument(
        "--as-of",
        type=_parse_as_of,
        help="fixed evaluation time; defaults to the latest received_at in the input",
    )

    price_plan = subparsers.add_parser(
        "compute-price-plan", help="compute a fixed technical price plan without a trade decision"
    )
    price_plan.add_argument("--input", type=Path, required=True)
    price_plan.add_argument("--technical-report", type=Path)
    price_plan.add_argument("--output-dir", type=Path, required=True)

    profitability = subparsers.add_parser(
        "capture-profitability", help="capture one stock's quarterly profitability evidence"
    )
    profitability.add_argument("--symbol", required=True)
    profitability.add_argument("--as-of", type=_parse_date, required=True)
    profitability.add_argument("--start-year", type=int, required=True)
    profitability.add_argument("--start-quarter", type=int, required=True)
    profitability.add_argument("--end-year", type=int, required=True)
    profitability.add_argument("--end-quarter", type=int, required=True)
    profitability.add_argument("--output-dir", type=Path, required=True)

    growth = subparsers.add_parser(
        "capture-growth", help="capture one stock's quarterly growth evidence"
    )
    growth.add_argument("--symbol", required=True)
    growth.add_argument("--as-of", type=_parse_date, required=True)
    growth.add_argument("--start-year", type=int, required=True)
    growth.add_argument("--start-quarter", type=int, required=True)
    growth.add_argument("--end-year", type=int, required=True)
    growth.add_argument("--end-quarter", type=int, required=True)
    growth.add_argument("--output-dir", type=Path, required=True)

    announcements = subparsers.add_parser(
        "capture-announcements", help="capture one stock's official CNINFO announcements"
    )
    announcements.add_argument("--symbol", required=True)
    announcements.add_argument("--start", type=_parse_date, required=True)
    announcements.add_argument("--end", type=_parse_date, required=True)
    announcements.add_argument("--as-of", type=_parse_date, required=True)
    announcements.add_argument("--received-at", type=_parse_as_of, required=True)
    announcements.add_argument("--output-dir", type=Path, required=True)

    evidence = subparsers.add_parser(
        "build-analysis-input", help="build one stock's point-in-time evidence bundle"
    )
    evidence.add_argument("--symbol", required=True)
    evidence.add_argument("--as-of", type=_parse_as_of, required=True)
    evidence.add_argument("--input-root", type=Path, required=True)
    evidence.add_argument("--market-bars", type=Path, required=True)
    evidence.add_argument("--market-health-report", type=Path, required=True)
    evidence.add_argument("--coverage-report", type=Path, required=True)
    evidence.add_argument("--calendar", type=Path, required=True)
    evidence.add_argument("--technical-input", type=Path, required=True)
    evidence.add_argument("--technical-features", type=Path, required=True)
    evidence.add_argument("--technical-report", type=Path, required=True)
    evidence.add_argument("--price-plan-input", type=Path, required=True)
    evidence.add_argument("--price-plan", type=Path, required=True)
    evidence.add_argument("--price-plan-report", type=Path, required=True)
    evidence.add_argument("--profitability-snapshot", type=Path, required=True)
    evidence.add_argument("--profitability-report", type=Path, required=True)
    evidence.add_argument("--growth-snapshot", type=Path, required=True)
    evidence.add_argument("--growth-report", type=Path, required=True)
    evidence.add_argument("--announcements-snapshot", type=Path, required=True)
    evidence.add_argument("--announcements-report", type=Path, required=True)
    evidence.add_argument("--market-context-snapshot", type=Path)
    evidence.add_argument("--market-context-report", type=Path)
    evidence.add_argument("--output-dir", type=Path, required=True)

    analysis = subparsers.add_parser(
        "analyze-input", help="build an evidence-backed research analysis report"
    )
    analysis.add_argument("--bundle", type=Path, required=True)
    analysis.add_argument("--bundle-report", type=Path)
    analysis.add_argument("--input-root", type=Path, required=True)
    analysis.add_argument(
        "--provider", choices=("offline", "openai", "deepseek"), default="offline"
    )
    analysis.add_argument("--response-fixture", type=Path)
    analysis.add_argument("--model")
    analysis.add_argument("--timeout-seconds", type=float, default=60.0)
    analysis.add_argument("--env-file", type=Path)
    analysis.add_argument("--output-dir", type=Path, required=True)

    market_aware_replay = subparsers.add_parser(
        "replay-market-aware-analysis",
        help="replay an analysis-input-v2 chain through a pending review packet",
    )
    market_aware_replay.add_argument("--bundle", type=Path, required=True)
    market_aware_replay.add_argument("--bundle-report", type=Path, required=True)
    market_aware_replay.add_argument("--input-root", type=Path, required=True)
    market_aware_replay.add_argument("--response-fixture", type=Path, required=True)
    market_aware_replay.add_argument("--output-dir", type=Path, required=True)

    market_aware_smoke = subparsers.add_parser(
        "market-aware-smoke", help="run one DeepSeek market-aware smoke test"
    )
    market_aware_smoke.add_argument("--bundle", type=Path, required=True)
    market_aware_smoke.add_argument("--bundle-report", type=Path, required=True)
    market_aware_smoke.add_argument("--input-root", type=Path, required=True)
    market_aware_smoke.add_argument("--env-file", type=Path, required=True)
    market_aware_smoke.add_argument("--model", default="deepseek-v4-flash")
    market_aware_smoke.add_argument("--timeout-seconds", type=float, default=60.0)
    market_aware_smoke.add_argument("--output-dir", type=Path, required=True)

    market_aware_release = subparsers.add_parser(
        "replay-market-aware-release",
        help="replay an explicit market-aware human review submission to release",
    )
    market_aware_release.add_argument("--packet", type=Path, required=True)
    market_aware_release.add_argument("--packet-report", type=Path, required=True)
    market_aware_release.add_argument("--submission", type=Path, required=True)
    market_aware_release.add_argument("--artifact-root", type=Path, required=True)
    market_aware_release.add_argument("--output-dir", type=Path, required=True)

    market_aware_session = subparsers.add_parser(
        "build-market-aware-session",
        help="build a fresh, fully reviewed market-aware research session",
    )
    market_aware_session.add_argument("--release-manifest", type=Path, required=True)
    market_aware_session.add_argument("--release-report", type=Path, required=True)
    market_aware_session.add_argument("--replay-report", type=Path, required=True)
    market_aware_session.add_argument("--calendar", type=Path, required=True)
    market_aware_session.add_argument("--calendar-report", type=Path, required=True)
    market_aware_session.add_argument("--evaluation-at", type=_parse_as_of, required=True)
    market_aware_session.add_argument("--reference-at", type=_parse_as_of, required=True)
    market_aware_session.add_argument("--artifact-root", type=Path, required=True)
    market_aware_session.add_argument("--output-dir", type=Path, required=True)

    market_aware_session_renderer = subparsers.add_parser(
        "render-market-aware-session",
        help="render a market-aware session admission report as Markdown",
    )
    market_aware_session_renderer.add_argument("--session", type=Path, required=True)
    market_aware_session_renderer.add_argument("--session-report", type=Path, required=True)
    market_aware_session_renderer.add_argument("--artifact-root", type=Path, required=True)
    market_aware_session_renderer.add_argument("--output-dir", type=Path, required=True)

    market_aware_session_package = subparsers.add_parser(
        "build-market-aware-session-package",
        help="build a relative-path audit package for a market-aware session",
    )
    market_aware_session_package.add_argument("--session", type=Path, required=True)
    market_aware_session_package.add_argument("--session-report", type=Path, required=True)
    market_aware_session_package.add_argument("--freshness-report", type=Path, required=True)
    market_aware_session_package.add_argument("--session-markdown", type=Path, required=True)
    market_aware_session_package.add_argument(
        "--session-render-report", type=Path, required=True
    )
    market_aware_session_package.add_argument("--artifact-root", type=Path, required=True)
    market_aware_session_package.add_argument("--output-dir", type=Path, required=True)

    market_aware_session_package_audit = subparsers.add_parser(
        "audit-market-aware-session-package",
        help="independently audit a market-aware session package",
    )
    market_aware_session_package_audit.add_argument("--package", type=Path, required=True)
    market_aware_session_package_audit.add_argument(
        "--package-report", type=Path, required=True
    )
    market_aware_session_package_audit.add_argument("--artifact-root", type=Path, required=True)
    market_aware_session_package_audit.add_argument("--output-dir", type=Path, required=True)

    market_aware_session_package_diff = subparsers.add_parser(
        "compare-market-aware-session-packages",
        help="compare two independently audited market-aware session packages",
    )
    market_aware_session_package_diff.add_argument(
        "--previous-package", type=Path, required=True
    )
    market_aware_session_package_diff.add_argument(
        "--previous-package-report", type=Path, required=True
    )
    market_aware_session_package_diff.add_argument(
        "--previous-artifact-root", type=Path, required=True
    )
    market_aware_session_package_diff.add_argument("--current-package", type=Path, required=True)
    market_aware_session_package_diff.add_argument(
        "--current-package-report", type=Path, required=True
    )
    market_aware_session_package_diff.add_argument(
        "--current-artifact-root", type=Path, required=True
    )
    market_aware_session_package_diff.add_argument("--output-dir", type=Path, required=True)

    market_aware_session_package_diff_renderer = subparsers.add_parser(
        "render-market-aware-session-package-diff",
        help="render a market-aware session package diff as Markdown",
    )
    market_aware_session_package_diff_renderer.add_argument("--diff", type=Path, required=True)
    market_aware_session_package_diff_renderer.add_argument(
        "--diff-report", type=Path, required=True
    )
    market_aware_session_package_diff_renderer.add_argument(
        "--input-root", type=Path, required=True
    )
    market_aware_session_package_diff_renderer.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_package_diff_render_audit = subparsers.add_parser(
        "audit-market-aware-session-package-diff-render",
        help="audit market-aware session package diff render artifacts",
    )
    market_aware_session_package_diff_render_audit.add_argument(
        "--diff", type=Path, required=True
    )
    market_aware_session_package_diff_render_audit.add_argument(
        "--diff-report", type=Path, required=True
    )
    market_aware_session_package_diff_render_audit.add_argument(
        "--markdown", type=Path, required=True
    )
    market_aware_session_package_diff_render_audit.add_argument(
        "--render-report", type=Path, required=True
    )
    market_aware_session_package_diff_render_audit.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_package_diff_render_audit.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history = subparsers.add_parser(
        "build-market-aware-session-history",
        help="build a deterministic history manifest from session packages",
    )
    market_aware_session_history.add_argument("--spec", type=Path, required=True)
    market_aware_session_history.add_argument("--history-root", type=Path, required=True)
    market_aware_session_history.add_argument("--output-dir", type=Path, required=True)

    market_aware_session_history_audit = subparsers.add_parser(
        "audit-market-aware-session-history",
        help="audit a market-aware session history manifest",
    )
    market_aware_session_history_audit.add_argument("--history", type=Path, required=True)
    market_aware_session_history_audit.add_argument(
        "--history-report", type=Path, required=True
    )
    market_aware_session_history_audit.add_argument(
        "--history-root", type=Path, required=True
    )
    market_aware_session_history_audit.add_argument("--output-dir", type=Path, required=True)

    market_aware_session_history_renderer = subparsers.add_parser(
        "render-market-aware-session-history",
        help="render a market-aware session history as Markdown",
    )
    market_aware_session_history_renderer.add_argument("--history", type=Path, required=True)
    market_aware_session_history_renderer.add_argument(
        "--history-report", type=Path, required=True
    )
    market_aware_session_history_renderer.add_argument(
        "--history-audit-report", type=Path, required=True
    )
    market_aware_session_history_renderer.add_argument(
        "--history-root", type=Path, required=True
    )
    market_aware_session_history_renderer.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_render_audit = subparsers.add_parser(
        "audit-market-aware-session-history-render",
        help="audit a rendered market-aware session history",
    )
    market_aware_session_history_render_audit.add_argument(
        "--history", type=Path, required=True
    )
    market_aware_session_history_render_audit.add_argument(
        "--history-report", type=Path, required=True
    )
    market_aware_session_history_render_audit.add_argument(
        "--history-audit-report", type=Path, required=True
    )
    market_aware_session_history_render_audit.add_argument(
        "--markdown", type=Path, required=True
    )
    market_aware_session_history_render_audit.add_argument(
        "--render-report", type=Path, required=True
    )
    market_aware_session_history_render_audit.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_render_audit.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_manifest = subparsers.add_parser(
        "build-market-aware-session-history-manifest",
        help="build an immutable market-aware session history manifest",
    )
    market_aware_session_history_manifest.add_argument(
        "--history", type=Path, required=True
    )
    market_aware_session_history_manifest.add_argument(
        "--history-report", type=Path, required=True
    )
    market_aware_session_history_manifest.add_argument(
        "--history-audit-report", type=Path, required=True
    )
    market_aware_session_history_manifest.add_argument(
        "--markdown", type=Path, required=True
    )
    market_aware_session_history_manifest.add_argument(
        "--render-report", type=Path, required=True
    )
    market_aware_session_history_manifest.add_argument(
        "--render-audit-report", type=Path, required=True
    )
    market_aware_session_history_manifest.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_manifest.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_manifest_audit = subparsers.add_parser(
        "audit-market-aware-session-history-manifest",
        help="audit a market-aware session history manifest",
    )
    market_aware_session_history_manifest_audit.add_argument(
        "--manifest", type=Path, required=True
    )
    market_aware_session_history_manifest_audit.add_argument(
        "--manifest-report", type=Path, required=True
    )
    market_aware_session_history_manifest_audit.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_manifest_audit.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_manifest_renderer = subparsers.add_parser(
        "render-market-aware-session-history-manifest",
        help="render a market-aware session history manifest",
    )
    market_aware_session_history_manifest_renderer.add_argument(
        "--manifest", type=Path, required=True
    )
    market_aware_session_history_manifest_renderer.add_argument(
        "--manifest-report", type=Path, required=True
    )
    market_aware_session_history_manifest_renderer.add_argument(
        "--audit-report", type=Path, required=True
    )
    market_aware_session_history_manifest_renderer.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_manifest_renderer.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_manifest_render_audit = subparsers.add_parser(
        "audit-market-aware-session-history-manifest-render",
        help="audit a rendered market-aware session history manifest",
    )
    market_aware_session_history_manifest_render_audit.add_argument(
        "--manifest", type=Path, required=True
    )
    market_aware_session_history_manifest_render_audit.add_argument(
        "--manifest-report", type=Path, required=True
    )
    market_aware_session_history_manifest_render_audit.add_argument(
        "--manifest-audit-report", type=Path, required=True
    )
    market_aware_session_history_manifest_render_audit.add_argument(
        "--markdown", type=Path, required=True
    )
    market_aware_session_history_manifest_render_audit.add_argument(
        "--render-report", type=Path, required=True
    )
    market_aware_session_history_manifest_render_audit.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_manifest_render_audit.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_closure = subparsers.add_parser(
        "build-market-aware-session-history-closure",
        help="build a market-aware session history closure report",
    )
    market_aware_session_history_closure.add_argument(
        "--manifest", type=Path, required=True
    )
    market_aware_session_history_closure.add_argument(
        "--manifest-report", type=Path, required=True
    )
    market_aware_session_history_closure.add_argument(
        "--manifest-audit-report", type=Path, required=True
    )
    market_aware_session_history_closure.add_argument(
        "--render-report", type=Path, required=True
    )
    market_aware_session_history_closure.add_argument(
        "--render-audit-report", type=Path, required=True
    )
    market_aware_session_history_closure.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_closure.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_closure_renderer = subparsers.add_parser(
        "render-market-aware-session-history-closure",
        help="render a market-aware session history closure",
    )
    market_aware_session_history_closure_renderer.add_argument(
        "--closure", type=Path, required=True
    )
    market_aware_session_history_closure_renderer.add_argument(
        "--closure-report", type=Path, required=True
    )
    market_aware_session_history_closure_renderer.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_closure_renderer.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_closure_render_audit = subparsers.add_parser(
        "audit-market-aware-session-history-closure-render",
        help="audit a market-aware session history closure render",
    )
    market_aware_session_history_closure_render_audit.add_argument(
        "--closure", type=Path, required=True
    )
    market_aware_session_history_closure_render_audit.add_argument(
        "--closure-report", type=Path, required=True
    )
    market_aware_session_history_closure_render_audit.add_argument(
        "--markdown", type=Path, required=True
    )
    market_aware_session_history_closure_render_audit.add_argument(
        "--render-report", type=Path, required=True
    )
    market_aware_session_history_closure_render_audit.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_closure_render_audit.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_closure_admission = subparsers.add_parser(
        "build-market-aware-session-history-closure-admission",
        help="build a market-aware session history closure admission report",
    )
    market_aware_session_history_closure_admission.add_argument(
        "--closure", type=Path, required=True
    )
    market_aware_session_history_closure_admission.add_argument(
        "--closure-report", type=Path, required=True
    )
    market_aware_session_history_closure_admission.add_argument(
        "--markdown", type=Path, required=True
    )
    market_aware_session_history_closure_admission.add_argument(
        "--render-report", type=Path, required=True
    )
    market_aware_session_history_closure_admission.add_argument(
        "--render-audit-report", type=Path, required=True
    )
    market_aware_session_history_closure_admission.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_closure_admission.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_closure_admission_audit = subparsers.add_parser(
        "audit-market-aware-session-history-closure-admission",
        help="audit a market-aware session history closure admission",
    )
    market_aware_session_history_closure_admission_audit.add_argument(
        "--admission", type=Path, required=True
    )
    market_aware_session_history_closure_admission_audit.add_argument(
        "--admission-report", type=Path, required=True
    )
    market_aware_session_history_closure_admission_audit.add_argument(
        "--closure", type=Path, required=True
    )
    market_aware_session_history_closure_admission_audit.add_argument(
        "--closure-report", type=Path, required=True
    )
    market_aware_session_history_closure_admission_audit.add_argument(
        "--markdown", type=Path, required=True
    )
    market_aware_session_history_closure_admission_audit.add_argument(
        "--render-report", type=Path, required=True
    )
    market_aware_session_history_closure_admission_audit.add_argument(
        "--render-audit-report", type=Path, required=True
    )
    market_aware_session_history_closure_admission_audit.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_closure_admission_audit.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_closure_admission_renderer = subparsers.add_parser(
        "render-market-aware-session-history-closure-admission",
        help="render a market-aware session history closure admission",
    )
    market_aware_session_history_closure_admission_renderer.add_argument(
        "--admission", type=Path, required=True
    )
    market_aware_session_history_closure_admission_renderer.add_argument(
        "--admission-report", type=Path, required=True
    )
    market_aware_session_history_closure_admission_renderer.add_argument(
        "--admission-audit-report", type=Path, required=True
    )
    market_aware_session_history_closure_admission_renderer.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_closure_admission_renderer.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_closure_admission_render_audit = subparsers.add_parser(
        "audit-market-aware-session-history-closure-admission-render",
        help="audit a market-aware session history closure admission render",
    )
    market_aware_session_history_closure_admission_render_audit.add_argument(
        "--admission", type=Path, required=True
    )
    market_aware_session_history_closure_admission_render_audit.add_argument(
        "--admission-report", type=Path, required=True
    )
    market_aware_session_history_closure_admission_render_audit.add_argument(
        "--admission-audit-report", type=Path, required=True
    )
    market_aware_session_history_closure_admission_render_audit.add_argument(
        "--markdown", type=Path, required=True
    )
    market_aware_session_history_closure_admission_render_audit.add_argument(
        "--render-report", type=Path, required=True
    )
    market_aware_session_history_closure_admission_render_audit.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_closure_admission_render_audit.add_argument(
        "--output-dir", type=Path, required=True
    )

    market_aware_session_history_final_receipt = subparsers.add_parser(
        "build-market-aware-session-history-final-receipt",
        help="build the final market-aware session history receipt",
    )
    market_aware_session_history_final_receipt.add_argument(
        "--admission", type=Path, required=True
    )
    market_aware_session_history_final_receipt.add_argument(
        "--admission-report", type=Path, required=True
    )
    market_aware_session_history_final_receipt.add_argument(
        "--render-report", type=Path, required=True
    )
    market_aware_session_history_final_receipt.add_argument(
        "--render-audit-report", type=Path, required=True
    )
    market_aware_session_history_final_receipt.add_argument(
        "--artifact-root", type=Path, required=True
    )
    market_aware_session_history_final_receipt.add_argument(
        "--output-dir", type=Path, required=True
    )

    receipt_service = subparsers.add_parser(
        "serve-research-receipt",
        help="serve a validated research receipt over loopback HTTP",
    )
    receipt_service.add_argument("--receipt", type=Path, required=True)
    receipt_service.add_argument("--receipt-report", type=Path, required=True)
    receipt_service.add_argument("--artifact-root", type=Path, required=True)
    receipt_service.add_argument(
        "--host",
        choices=("127.0.0.1", "::1"),
        default=DEFAULT_READ_ONLY_RECEIPT_HOST,
    )
    receipt_service.add_argument(
        "--port", type=int, default=DEFAULT_READ_ONLY_RECEIPT_PORT
    )

    receipt_probe = subparsers.add_parser(
        "probe-research-receipt-service",
        help="probe a loopback research receipt service",
    )
    receipt_probe.add_argument("--base-url", required=True)
    receipt_probe.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_PROBE_TIMEOUT_SECONDS
    )

    renderer = subparsers.add_parser(
        "render-analysis", help="render a validated analysis report as Markdown"
    )
    renderer.add_argument("--analysis", type=Path, required=True)
    renderer.add_argument("--analysis-report", type=Path, required=True)
    renderer.add_argument("--input-root", type=Path, required=True)
    renderer.add_argument("--output-dir", type=Path, required=True)

    quality = subparsers.add_parser(
        "audit-analysis-quality", help="audit analysis evidence coverage offline"
    )
    quality.add_argument("--analysis", type=Path, required=True)
    quality.add_argument("--analysis-report", type=Path, required=True)
    quality.add_argument("--render-report", type=Path, required=True)
    quality.add_argument("--input-root", type=Path, required=True)
    quality.add_argument("--output-dir", type=Path, required=True)

    decision_input = subparsers.add_parser(
        "build-decision-input",
        help="build a verified non-trading decision input snapshot",
    )
    decision_input.add_argument("--analysis", type=Path, required=True)
    decision_input.add_argument("--analysis-report", type=Path, required=True)
    decision_input.add_argument("--render-report", type=Path, required=True)
    decision_input.add_argument("--quality-report", type=Path, required=True)
    decision_input.add_argument("--bundle", type=Path, required=True)
    decision_input.add_argument("--bundle-report", type=Path, required=True)
    decision_input.add_argument("--input-root", type=Path, required=True)
    decision_input.add_argument("--artifact-root", type=Path, required=True)
    decision_input.add_argument("--output-dir", type=Path, required=True)

    safety = subparsers.add_parser(
        "audit-analysis-safety", help="audit analysis output for non-trading safety"
    )
    safety.add_argument("--decision-input", type=Path, required=True)
    safety.add_argument("--decision-input-report", type=Path, required=True)
    safety.add_argument("--analysis", type=Path, required=True)
    safety.add_argument("--analysis-report", type=Path, required=True)
    safety.add_argument("--render-report", type=Path, required=True)
    safety.add_argument("--artifact-root", type=Path, required=True)
    safety.add_argument("--output-dir", type=Path, required=True)

    review = subparsers.add_parser(
        "build-analysis-review", help="build a pending human review packet"
    )
    review.add_argument("--decision-input", type=Path, required=True)
    review.add_argument("--decision-input-report", type=Path, required=True)
    review.add_argument("--safety-report", type=Path, required=True)
    review.add_argument("--analysis", type=Path, required=True)
    review.add_argument("--analysis-report", type=Path, required=True)
    review.add_argument("--artifact-root", type=Path, required=True)
    review.add_argument("--output-dir", type=Path, required=True)

    review_record = subparsers.add_parser(
        "apply-analysis-review", help="apply a complete human review submission"
    )
    review_record.add_argument("--packet", type=Path, required=True)
    review_record.add_argument("--packet-report", type=Path, required=True)
    review_record.add_argument("--submission", type=Path, required=True)
    review_record.add_argument("--output-dir", type=Path, required=True)

    release = subparsers.add_parser(
        "build-research-release", help="build a reviewed research release manifest"
    )
    release.add_argument("--decision-input", type=Path, required=True)
    release.add_argument("--decision-input-report", type=Path, required=True)
    release.add_argument("--safety-report", type=Path, required=True)
    release.add_argument("--analysis-review-packet", type=Path, required=True)
    release.add_argument("--analysis-review-result", type=Path, required=True)
    release.add_argument("--analysis-review-result-report", type=Path, required=True)
    release.add_argument("--artifact-root", type=Path, required=True)
    release.add_argument("--output-dir", type=Path, required=True)

    release_diff = subparsers.add_parser(
        "compare-research-releases", help="compare two reviewed research releases"
    )
    release_diff.add_argument("--previous-manifest", type=Path, required=True)
    release_diff.add_argument("--previous-report", type=Path, required=True)
    release_diff.add_argument("--current-manifest", type=Path, required=True)
    release_diff.add_argument("--current-report", type=Path, required=True)
    release_diff.add_argument("--previous-artifact-root", type=Path, required=True)
    release_diff.add_argument("--current-artifact-root", type=Path, required=True)
    release_diff.add_argument("--output-dir", type=Path, required=True)

    freshness = subparsers.add_parser(
        "audit-research-freshness", help="audit research release freshness"
    )
    freshness.add_argument("--release-manifest", type=Path, required=True)
    freshness.add_argument("--release-report", type=Path, required=True)
    freshness.add_argument("--calendar", type=Path, required=True)
    freshness.add_argument("--calendar-report", type=Path, required=True)
    freshness.add_argument("--evaluation-at", type=_parse_as_of, required=True)
    freshness.add_argument("--output-dir", type=Path, required=True)
    return parser


def _report_json(
    report: HealthReport | CoverageReport | TechnicalFeatureReport | PricePlanReport,
) -> bytes:
    return (
        json.dumps(report.to_mapping(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _load_bars(path: Path) -> tuple[list, bytes, list[ValidationIssue]]:
    try:
        input_bytes = path.read_bytes()
    except OSError as exc:
        return [], b"", [ValidationIssue("INPUT_UNAVAILABLE", str(exc))]
    bars = []
    issues: list[ValidationIssue] = []
    source = JsonlReplaySource(path)
    try:
        bars = list(source.iter_daily_bars())
    except ReplayInputError as exc:
        code = "INPUT_UNAVAILABLE" if exc.line_number == 0 else "INPUT_INVALID"
        issues.append(ValidationIssue(code, str(exc), line_number=exc.line_number or None))
    return bars, input_bytes, issues


def run_replay_health(args: argparse.Namespace) -> int:
    bars, input_bytes, parse_issues = _load_bars(args.input)
    output_bytes = canonical_jsonl(bars)
    report = build_health_report(
        bars,
        as_of=args.as_of,
        input_sha256=sha256_bytes(input_bytes),
        output_sha256=sha256_bytes(output_bytes),
        source="jsonl-replay",
        parse_issues=parse_issues,
        stale_after=timedelta(seconds=args.stale_after_seconds),
    )
    write_atomic(args.output_dir / "replay_output.jsonl", output_bytes)
    write_atomic(args.output_dir / "health_report.json", _report_json(report))
    print(json.dumps(report.to_mapping(), ensure_ascii=False, sort_keys=True))
    return 0 if report.decision_ready else 1


def run_coverage_audit(args: argparse.Namespace) -> int:
    bars, bars_bytes, bar_issues = _load_bars(args.bars)
    try:
        calendar_bytes = args.calendar.read_bytes()
    except OSError as exc:
        calendar_bytes = b""
        calendar = None
        calendar_error = str(exc)
    else:
        try:
            calendar = JsonTradingCalendarSource(args.calendar).load_calendar()
            calendar_error = None
        except CalendarError as exc:
            calendar = None
            calendar_error = str(exc)
    bars_error = "; ".join(issue.message for issue in bar_issues) if bar_issues else None
    report = audit_daily_coverage(
        bars,
        calendar=calendar,
        start=args.start,
        end=args.end,
        as_of=args.as_of,
        bars_sha256=sha256_bytes(bars_bytes),
        calendar_sha256=sha256_bytes(calendar_bytes),
        calendar_source="json-calendar",
        calendar_error=calendar_error,
        bars_error=bars_error,
    )
    write_atomic(args.output_dir / "coverage_report.json", _report_json(report))
    write_atomic(args.output_dir / "normalized_bars.jsonl", canonical_jsonl(bars))
    print(json.dumps(report.to_mapping(), ensure_ascii=False, sort_keys=True))
    return 0 if report.decision_ready else 1


def run_baostock_capture(args: argparse.Namespace) -> int:
    try:
        config = BaostockDailyConfig(
            symbol=args.symbol,
            start_date=args.start,
            end_date=args.end,
            received_at=args.received_at,
        )
        source = BaostockDailySource(config)
        report = source.capture(args.output_dir)
    except Exception as exc:
        request = {
            "symbol": args.symbol,
            "start_date": args.start.isoformat(),
            "end_date": args.end.isoformat(),
            "received_at": args.received_at.isoformat(),
            "frequency": "d",
            "adjustflag": "3",
            "fields": "date,code,open,high,low,close,volume,amount",
        }
        report = {
            "schema_version": "1.0",
            "source": "baostock-daily",
            "request": request,
            "bar_count": 0,
            "raw_response_sha256": None,
            "normalized_output_sha256": None,
            "decision_ready": False,
            "error": str(exc),
        }
        write_atomic(
            args.output_dir / "capture_report.json",
            (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
                "utf-8"
            ),
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


def run_market_context_capture(args: argparse.Namespace) -> int:
    config = None
    try:
        config = MarketContextConfig(
            start=args.start,
            end=args.end,
            as_of=args.as_of,
            received_at=args.received_at,
        )
        report = BaostockMarketContextSource(config).capture(
            calendar_path=args.calendar,
            output_dir=args.output_dir,
        )
    except Exception as exc:
        request = (
            config.request_mapping()
            if config is not None
            else {
                "as_of": args.as_of.isoformat(),
                "end": args.end.isoformat(),
                "indexes": list(INDEX_SYMBOLS),
                "received_at": args.received_at.isoformat(),
                "start": args.start.isoformat(),
            }
        )
        snapshot = {
            "as_of": args.as_of.isoformat(),
            "indexes": [],
            "market_context_version": MARKET_CONTEXT_VERSION,
            "received_at": args.received_at.isoformat(),
            "request": request,
        }
        request_bytes = (
            json.dumps(request, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        raw_bytes = b"{}\n"
        snapshot_bytes = (
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        report = {
            "as_of": args.as_of.isoformat(),
            "calendar_sha256": None,
            "calendar_version": None,
            "decision_ready": False,
            "end": args.end.isoformat(),
            "index_reports": {},
            "index_symbols": list(INDEX_SYMBOLS),
            "market_context_ready": False,
            "market_context_version": MARKET_CONTEXT_VERSION,
            "raw_response_sha256": sha256_bytes(raw_bytes),
            "received_at": args.received_at.isoformat(),
            "request": request,
            "snapshot_sha256": sha256_bytes(snapshot_bytes),
            "start": args.start.isoformat(),
            "status": "invalid",
            "issues": [{"code": "PROVIDER_ERROR", "message": str(exc)}],
        }
        write_atomic(args.output_dir / "request.json", request_bytes)
        write_atomic(args.output_dir / "raw_response.json", raw_bytes)
        write_atomic(args.output_dir / "market_context_snapshot.json", snapshot_bytes)
        write_atomic(
            args.output_dir / "market_context_report.json",
            (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
                "utf-8"
            ),
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("market_context_ready") is True else 1


def run_technical_features(args: argparse.Namespace) -> int:
    bars, input_bytes, parse_issues = _load_bars(args.input)
    as_of = args.as_of or max(
        (bar.received_at for bar in bars),
        default=datetime.fromisoformat("1970-01-01T00:00:00+00:00"),
    )
    input_sha256 = sha256_bytes(input_bytes)
    computation = compute_feature_snapshots(
        bars,
        as_of=as_of,
        input_sha256=input_sha256,
        parse_issues=parse_issues,
    )
    output_bytes = serialize_feature_snapshots(computation.snapshots)
    report = build_feature_report(
        computation,
        as_of=as_of,
        input_sha256=input_sha256,
        output_sha256=sha256_bytes(output_bytes),
    )
    write_atomic(args.output_dir / "technical_features.jsonl", output_bytes)
    write_atomic(args.output_dir / "technical_report.json", _report_json(report))
    print(json.dumps(report.to_mapping(), ensure_ascii=False, sort_keys=True))
    return 0 if report.decision_ready else 1


def _load_feature_snapshot(path: Path) -> tuple[dict | None, bytes, tuple[PricePlanIssue, ...]]:
    try:
        input_bytes = path.read_bytes()
    except OSError as exc:
        return None, b"", (PricePlanIssue("INPUT_UNAVAILABLE", str(exc)),)
    try:
        input_text = input_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, input_bytes, (PricePlanIssue("INPUT_INVALID", str(exc)),)
    snapshots: list[dict] = []
    issues: list[PricePlanIssue] = []
    for line_number, line in enumerate(input_text.splitlines(), start=1):
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            issues.append(PricePlanIssue("INPUT_INVALID", f"line {line_number}: {exc}"))
            continue
        if not isinstance(value, dict):
            issues.append(PricePlanIssue("INPUT_INVALID", f"line {line_number}: object required"))
            continue
        snapshots.append(value)
    if issues or not snapshots:
        if not snapshots and not issues:
            issues.append(PricePlanIssue("INPUT_EMPTY", "technical feature input is empty"))
        return None, input_bytes, tuple(issues)
    symbols = {snapshot.get("symbol") for snapshot in snapshots}
    dates = [snapshot.get("trade_date") for snapshot in snapshots]
    if (
        len(symbols) != 1
        or not isinstance(next(iter(symbols)), str)
        or not next(iter(symbols)).strip()
        or any(not isinstance(value, str) for value in dates)
    ):
        issues.append(
            PricePlanIssue("INPUT_SHAPE_INVALID", "one symbol and ISO trade dates required")
        )
    if all(isinstance(value, str) for value in dates) and (
        dates != sorted(dates) or len(set(dates)) != len(dates)
    ):
        issues.append(
            PricePlanIssue("INPUT_ORDER_INVALID", "trade dates must be unique and ascending")
        )
    return (None if issues else snapshots[-1]), input_bytes, tuple(issues)


def _load_json_report(path: Path) -> tuple[dict | None, bytes, tuple[PricePlanIssue, ...]]:
    try:
        report_bytes = path.read_bytes()
        report = json.loads(report_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, b"", (PricePlanIssue("TECHNICAL_REPORT_INVALID", str(exc)),)
    if not isinstance(report, dict):
        return None, report_bytes, (PricePlanIssue("TECHNICAL_REPORT_INVALID", "object required"),)
    return report, report_bytes, ()


def run_price_plan(args: argparse.Namespace) -> int:
    snapshot, input_bytes, feature_issues = _load_feature_snapshot(args.input)
    report_path = args.technical_report or args.input.with_name("technical_report.json")
    technical_report, report_bytes, report_issues = _load_json_report(report_path)
    issues = feature_issues + report_issues
    input_sha256 = sha256_bytes(input_bytes)
    technical_input_sha256 = (
        str(technical_report.get("output_sha256")) if technical_report else None
    )
    computation = compute_price_plan(
        snapshot,
        technical_report=technical_report,
        input_sha256=input_sha256,
        technical_input_sha256=technical_input_sha256,
        input_issues=issues,
    )
    output_bytes = serialize_price_plan(computation.plan)
    report = build_price_plan_report(
        computation,
        input_sha256=input_sha256,
        technical_report_sha256=sha256_bytes(report_bytes) if report_bytes else None,
        output_sha256=sha256_bytes(output_bytes),
        technical_report=technical_report,
    )
    write_atomic(args.output_dir / "technical_price_plan.json", output_bytes)
    write_atomic(args.output_dir / "price_plan_report.json", _report_json(report))
    print(json.dumps(report.to_mapping(), ensure_ascii=False, sort_keys=True))
    return 0 if report.price_plan_ready else 1


def run_profitability_capture(args: argparse.Namespace) -> int:
    try:
        config = BaostockProfitabilityConfig(
            symbol=args.symbol,
            as_of=args.as_of,
            start_year=args.start_year,
            start_quarter=args.start_quarter,
            end_year=args.end_year,
            end_quarter=args.end_quarter,
        )
        report = BaostockProfitabilitySource(config).capture(args.output_dir)
    except Exception as exc:
        report = {
            "schema_version": "1.0",
            "source": "baostock-profitability",
            "symbol": args.symbol.strip().upper(),
            "as_of": args.as_of.isoformat(),
            "raw_response_sha256": None,
            "snapshot_sha256": sha256_bytes(b""),
            "selected_published_date": None,
            "selected_report_date": None,
            "status": "invalid",
            "fundamental_ready": False,
            "decision_ready": False,
            "issues": [{"code": "INVALID_REQUEST", "message": str(exc)}],
        }
        write_atomic(
            args.output_dir / "profitability_report.json",
            (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
                "utf-8"
            ),
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("fundamental_ready") is True else 1


def run_growth_capture(args: argparse.Namespace) -> int:
    try:
        config = BaostockGrowthConfig(
            symbol=args.symbol,
            as_of=args.as_of,
            start_year=args.start_year,
            start_quarter=args.start_quarter,
            end_year=args.end_year,
            end_quarter=args.end_quarter,
        )
        report = BaostockGrowthSource(config).capture(args.output_dir)
    except Exception as exc:
        report = {
            "schema_version": "1.0",
            "source": "baostock-growth",
            "symbol": args.symbol.strip().upper(),
            "as_of": args.as_of.isoformat(),
            "raw_response_sha256": None,
            "snapshot_sha256": sha256_bytes(b""),
            "selected_published_date": None,
            "selected_report_date": None,
            "status": "invalid",
            "growth_ready": False,
            "decision_ready": False,
            "issues": [{"code": "INVALID_REQUEST", "message": str(exc)}],
        }
        write_atomic(
            args.output_dir / "growth_report.json",
            (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
                "utf-8"
            ),
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("growth_ready") is True else 1


def run_announcements_capture(args: argparse.Namespace) -> int:
    try:
        config = CninfoAnnouncementConfig(
            symbol=args.symbol,
            start_date=args.start,
            end_date=args.end,
            as_of=args.as_of,
            received_at=args.received_at,
        )
        report = CninfoAnnouncementSource(config).capture(args.output_dir)
    except Exception as exc:
        report = {
            "schema_version": "1.0",
            "source": "cninfo-announcements",
            "symbol": args.symbol.strip().upper(),
            "status": "invalid",
            "announcement_ready": False,
            "decision_ready": False,
            "issues": [{"code": "INVALID_REQUEST", "message": str(exc)}],
        }
        write_atomic(
            args.output_dir / "announcements_report.json",
            (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
                "utf-8"
            ),
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("announcement_ready") is True else 1


def run_analysis_input(args: argparse.Namespace) -> int:
    try:
        config = AnalysisInputConfig(
            symbol=args.symbol,
            as_of=args.as_of,
            input_root=args.input_root,
            market_bars=args.market_bars,
            market_health_report=args.market_health_report,
            coverage_report=args.coverage_report,
            calendar=args.calendar,
            technical_input=args.technical_input,
            technical_features=args.technical_features,
            technical_report=args.technical_report,
            price_plan_input=args.price_plan_input,
            price_plan=args.price_plan,
            price_plan_report=args.price_plan_report,
            profitability_snapshot=args.profitability_snapshot,
            profitability_report=args.profitability_report,
            growth_snapshot=args.growth_snapshot,
            growth_report=args.growth_report,
            announcements_snapshot=args.announcements_snapshot,
            announcements_report=args.announcements_report,
            market_context_snapshot=args.market_context_snapshot,
            market_context_report=args.market_context_report,
        )
        report = AnalysisInputSource(config).capture(args.output_dir)
    except Exception as exc:
        report = {
            "schema_version": "1.0",
            "bundle_version": (
                BUNDLE_VERSION_V2
                if args.market_context_snapshot is not None
                or args.market_context_report is not None
                else BUNDLE_VERSION_V1
            ),
            "source": "analysis-input-bundle",
            "symbol": args.symbol.strip().upper(),
            "as_of": args.as_of.isoformat(),
            "bundle_sha256": None,
            "status": "invalid",
            "analysis_input_ready": False,
            "decision_ready": False,
            "issues": [{"code": "INVALID_REQUEST", "message": str(exc)}],
        }
        write_atomic(
            args.output_dir / "analysis_input_report.json",
            (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
                "utf-8"
            ),
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("analysis_input_ready") is True else 1


def run_analysis_report(args: argparse.Namespace) -> int:
    if args.provider == "offline" and args.response_fixture is None:
        raise SystemExit("analyze-input --provider offline requires --response-fixture")
    if args.provider == "openai" and args.response_fixture is not None:
        raise SystemExit("analyze-input --provider openai does not accept --response-fixture")
    if args.provider == "deepseek" and args.response_fixture is not None:
        raise SystemExit("analyze-input --provider deepseek does not accept --response-fixture")
    if args.timeout_seconds <= 0:
        raise SystemExit("analyze-input --timeout-seconds must be positive")
    config = AnalysisReportConfig(
        bundle_path=args.bundle,
        bundle_report=args.bundle_report,
        input_root=args.input_root,
        response_fixture=args.response_fixture,
        output_dir=args.output_dir,
        provider=args.provider,
        model=args.model
        or ("deepseek-v4-flash" if args.provider == "deepseek" else "gpt-5.6-luna"),
        timeout_seconds=args.timeout_seconds,
        env_file=args.env_file,
    )
    report = AnalysisReportSource(config).capture()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("analysis_ready") is True else 1


def run_market_aware_replay(args: argparse.Namespace) -> int:
    report = replay_market_aware_analysis(
        bundle_path=args.bundle,
        bundle_report_path=args.bundle_report,
        input_root=args.input_root,
        response_fixture=args.response_fixture,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("review_packet_ready") is True else 1


def run_market_aware_smoke(args: argparse.Namespace) -> int:
    report = smoke_market_aware_analysis(
        bundle_path=args.bundle,
        bundle_report_path=args.bundle_report,
        input_root=args.input_root,
        env_file=args.env_file,
        output_dir=args.output_dir,
        model=args.model,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("review_packet_ready") is True else 1


def run_market_aware_release(args: argparse.Namespace) -> int:
    report = replay_market_aware_release(
        packet_path=args.packet,
        packet_report_path=args.packet_report,
        submission_path=args.submission,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("research_release_ready") is True else 1


def run_market_aware_session(args: argparse.Namespace) -> int:
    report = build_market_aware_session(
        release_manifest_path=args.release_manifest,
        release_report_path=args.release_report,
        replay_report_path=args.replay_report,
        calendar_path=args.calendar,
        calendar_report_path=args.calendar_report,
        evaluation_at=args.evaluation_at,
        reference_at=args.reference_at,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("session_ready") is True else 1


def run_market_aware_session_renderer(args: argparse.Namespace) -> int:
    report = render_market_aware_session(
        session_path=args.session,
        session_report_path=args.session_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("session_render_ready") is True else 1


def run_market_aware_session_package(args: argparse.Namespace) -> int:
    report = build_market_aware_session_package(
        session_path=args.session,
        session_report_path=args.session_report,
        freshness_report_path=args.freshness_report,
        session_markdown_path=args.session_markdown,
        session_render_report_path=args.session_render_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("package_ready") is True else 1


def run_market_aware_session_package_audit(args: argparse.Namespace) -> int:
    report = audit_market_aware_session_package(
        package_path=args.package,
        package_report_path=args.package_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("audit_ready") is True else 1


def run_market_aware_session_package_diff(args: argparse.Namespace) -> int:
    report = compare_market_aware_session_packages(
        previous_package_path=args.previous_package,
        previous_package_report_path=args.previous_package_report,
        previous_artifact_root=args.previous_artifact_root,
        current_package_path=args.current_package,
        current_package_report_path=args.current_package_report,
        current_artifact_root=args.current_artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("comparison_ready") is True else 1


def run_market_aware_session_package_diff_renderer(args: argparse.Namespace) -> int:
    report = render_market_aware_session_package_diff(
        diff_path=args.diff,
        diff_report_path=args.diff_report,
        input_root=args.input_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("render_ready") is True else 1


def run_market_aware_session_package_diff_render_audit(args: argparse.Namespace) -> int:
    report = audit_market_aware_session_package_diff_render(
        diff_path=args.diff,
        diff_report_path=args.diff_report,
        markdown_path=args.markdown,
        render_report_path=args.render_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("audit_ready") is True else 1


def run_market_aware_session_history(args: argparse.Namespace) -> int:
    report = build_market_aware_session_history(
        spec_path=args.spec,
        history_root=args.history_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("history_ready") is True else 1


def run_market_aware_session_history_audit(args: argparse.Namespace) -> int:
    report = audit_market_aware_session_history(
        history_path=args.history,
        history_report_path=args.history_report,
        history_root=args.history_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("audit_ready") is True else 1


def run_market_aware_session_history_renderer(args: argparse.Namespace) -> int:
    report = render_market_aware_session_history(
        history_path=args.history,
        history_report_path=args.history_report,
        history_audit_report_path=args.history_audit_report,
        history_root=args.history_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("render_ready") is True else 1


def run_market_aware_session_history_render_audit(args: argparse.Namespace) -> int:
    report = audit_market_aware_session_history_render(
        history_path=args.history,
        history_report_path=args.history_report,
        history_audit_report_path=args.history_audit_report,
        markdown_path=args.markdown,
        render_report_path=args.render_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("audit_ready") is True else 1


def run_market_aware_session_history_manifest(args: argparse.Namespace) -> int:
    report = build_market_aware_session_history_manifest(
        history_path=args.history,
        history_report_path=args.history_report,
        history_audit_report_path=args.history_audit_report,
        markdown_path=args.markdown,
        render_report_path=args.render_report,
        render_audit_report_path=args.render_audit_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("manifest_ready") is True else 1


def run_market_aware_session_history_manifest_audit(args: argparse.Namespace) -> int:
    report = audit_market_aware_session_history_manifest(
        manifest_path=args.manifest,
        manifest_report_path=args.manifest_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("audit_ready") is True else 1


def run_market_aware_session_history_manifest_renderer(args: argparse.Namespace) -> int:
    report = render_market_aware_session_history_manifest(
        manifest_path=args.manifest,
        manifest_report_path=args.manifest_report,
        audit_report_path=args.audit_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("render_ready") is True else 1


def run_market_aware_session_history_manifest_render_audit(
    args: argparse.Namespace,
) -> int:
    report = audit_market_aware_session_history_manifest_render(
        manifest_path=args.manifest,
        manifest_report_path=args.manifest_report,
        manifest_audit_report_path=args.manifest_audit_report,
        markdown_path=args.markdown,
        render_report_path=args.render_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("audit_ready") is True else 1


def run_market_aware_session_history_closure(args: argparse.Namespace) -> int:
    report = build_market_aware_session_history_closure(
        manifest_path=args.manifest,
        manifest_report_path=args.manifest_report,
        manifest_audit_report_path=args.manifest_audit_report,
        render_report_path=args.render_report,
        render_audit_report_path=args.render_audit_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("closure_ready") is True else 1


def run_market_aware_session_history_closure_renderer(
    args: argparse.Namespace,
) -> int:
    report = render_market_aware_session_history_closure(
        closure_path=args.closure,
        closure_report_path=args.closure_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("render_ready") is True else 1


def run_market_aware_session_history_closure_render_audit(
    args: argparse.Namespace,
) -> int:
    report = audit_market_aware_session_history_closure_render(
        closure_path=args.closure,
        closure_report_path=args.closure_report,
        markdown_path=args.markdown,
        render_report_path=args.render_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("audit_ready") is True else 1


def run_market_aware_session_history_closure_admission(
    args: argparse.Namespace,
) -> int:
    report = build_market_aware_session_history_closure_admission(
        closure_path=args.closure,
        closure_report_path=args.closure_report,
        markdown_path=args.markdown,
        render_report_path=args.render_report,
        render_audit_report_path=args.render_audit_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("admission_ready") is True else 1


def run_market_aware_session_history_closure_admission_audit(
    args: argparse.Namespace,
) -> int:
    report = audit_market_aware_session_history_closure_admission(
        admission_path=args.admission,
        admission_report_path=args.admission_report,
        closure_path=args.closure,
        closure_report_path=args.closure_report,
        markdown_path=args.markdown,
        render_report_path=args.render_report,
        render_audit_report_path=args.render_audit_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("audit_ready") is True else 1


def run_market_aware_session_history_closure_admission_renderer(
    args: argparse.Namespace,
) -> int:
    report = render_market_aware_session_history_closure_admission(
        admission_path=args.admission,
        admission_report_path=args.admission_report,
        admission_audit_report_path=args.admission_audit_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("render_ready") is True else 1


def run_market_aware_session_history_closure_admission_render_audit(
    args: argparse.Namespace,
) -> int:
    report = audit_market_aware_session_history_closure_admission_render(
        admission_path=args.admission,
        admission_report_path=args.admission_report,
        admission_audit_report_path=args.admission_audit_report,
        markdown_path=args.markdown,
        render_report_path=args.render_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("render_audit_ready") is True else 1


def run_market_aware_session_history_final_receipt(
    args: argparse.Namespace,
) -> int:
    report = build_market_aware_session_history_final_receipt(
        admission_path=args.admission,
        admission_report_path=args.admission_report,
        render_report_path=args.render_report,
        render_audit_report_path=args.render_audit_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("receipt_ready") is True else 1


def run_read_only_receipt_server(args: argparse.Namespace) -> int:
    try:
        serve_read_only_receipt(
            receipt_path=args.receipt,
            receipt_report_path=args.receipt_report,
            artifact_root=args.artifact_root,
            host=args.host,
            port=args.port,
        )
    except ReadOnlyReceiptServiceError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


def run_read_only_receipt_probe(args: argparse.Namespace) -> int:
    try:
        report = probe_read_only_receipt_service(
            base_url=args.base_url,
            timeout_seconds=args.timeout_seconds,
        )
        exit_code = probe_exit_code(report)
    except ReadOnlyReceiptProbeError as exc:
        report = {
            "probe_version": "read-only-receipt-service-probe-v1",
            "base_url": "<redacted>",
            "status": "invalid",
            "health_http_status": None,
            "ready_http_status": None,
            "receipt_http_status": None,
            "receipt_ready": None,
            "decision_ready": None,
            "checks": [],
            "issues": [{"code": exc.code, "message": str(exc)}],
        }
        exit_code = 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return exit_code


def run_render_analysis(args: argparse.Namespace) -> int:
    report = render_analysis(
        analysis_path=args.analysis,
        analysis_report_path=args.analysis_report,
        input_root=args.input_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "ready" else 1


def run_analysis_quality(args: argparse.Namespace) -> int:
    report = audit_analysis_quality(
        analysis_path=args.analysis,
        analysis_report_path=args.analysis_report,
        render_report_path=args.render_report,
        input_root=args.input_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("quality_ready") is True else 1


def run_decision_input(args: argparse.Namespace) -> int:
    report = build_decision_input(
        analysis_path=args.analysis,
        analysis_report_path=args.analysis_report,
        render_report_path=args.render_report,
        quality_report_path=args.quality_report,
        bundle_path=args.bundle,
        bundle_report_path=args.bundle_report,
        input_root=args.input_root,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("decision_input_ready") is True else 1


def run_analysis_safety(args: argparse.Namespace) -> int:
    report = audit_analysis_safety(
        decision_input_path=args.decision_input,
        decision_input_report_path=args.decision_input_report,
        analysis_path=args.analysis,
        analysis_report_path=args.analysis_report,
        render_report_path=args.render_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("safety_ready") is True else 1


def run_analysis_review(args: argparse.Namespace) -> int:
    report = build_analysis_review(
        decision_input_path=args.decision_input,
        decision_input_report_path=args.decision_input_report,
        safety_report_path=args.safety_report,
        analysis_path=args.analysis,
        analysis_report_path=args.analysis_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("review_packet_ready") is True else 1


def run_apply_analysis_review(args: argparse.Namespace) -> int:
    report = apply_analysis_review(
        packet_path=args.packet,
        packet_report_path=args.packet_report,
        submission_path=args.submission,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "ready" else 1


def run_research_release(args: argparse.Namespace) -> int:
    report = build_research_release(
        decision_input_path=args.decision_input,
        decision_input_report_path=args.decision_input_report,
        safety_report_path=args.safety_report,
        review_packet_path=args.analysis_review_packet,
        review_result_path=args.analysis_review_result,
        review_result_report_path=args.analysis_review_result_report,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("research_release_ready") is True else 1


def run_release_diff(args: argparse.Namespace) -> int:
    report = compare_research_releases(
        previous_manifest_path=args.previous_manifest,
        previous_report_path=args.previous_report,
        current_manifest_path=args.current_manifest,
        current_report_path=args.current_report,
        previous_artifact_root=args.previous_artifact_root,
        current_artifact_root=args.current_artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("comparison_ready") is True else 1


def run_research_freshness(args: argparse.Namespace) -> int:
    report = audit_research_freshness(
        release_manifest_path=args.release_manifest,
        release_report_path=args.release_report,
        calendar_path=args.calendar,
        calendar_report_path=args.calendar_report,
        evaluation_at=args.evaluation_at,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("freshness_ready") is True else 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "replay-health":
        if args.stale_after_seconds < 0:
            parser.error("--stale-after-seconds must be non-negative")
        return run_replay_health(args)
    if args.command == "audit-coverage":
        return run_coverage_audit(args)
    if args.command == "capture-baostock-daily":
        return run_baostock_capture(args)
    if args.command == "capture-market-context":
        return run_market_context_capture(args)
    if args.command == "compute-technical-features":
        return run_technical_features(args)
    if args.command == "compute-price-plan":
        return run_price_plan(args)
    if args.command == "capture-profitability":
        return run_profitability_capture(args)
    if args.command == "capture-growth":
        return run_growth_capture(args)
    if args.command == "capture-announcements":
        return run_announcements_capture(args)
    if args.command == "build-analysis-input":
        return run_analysis_input(args)
    if args.command == "analyze-input":
        return run_analysis_report(args)
    if args.command == "replay-market-aware-analysis":
        return run_market_aware_replay(args)
    if args.command == "market-aware-smoke":
        return run_market_aware_smoke(args)
    if args.command == "replay-market-aware-release":
        return run_market_aware_release(args)
    if args.command == "build-market-aware-session":
        return run_market_aware_session(args)
    if args.command == "render-market-aware-session":
        return run_market_aware_session_renderer(args)
    if args.command == "build-market-aware-session-package":
        return run_market_aware_session_package(args)
    if args.command == "audit-market-aware-session-package":
        return run_market_aware_session_package_audit(args)
    if args.command == "compare-market-aware-session-packages":
        return run_market_aware_session_package_diff(args)
    if args.command == "render-market-aware-session-package-diff":
        return run_market_aware_session_package_diff_renderer(args)
    if args.command == "audit-market-aware-session-package-diff-render":
        return run_market_aware_session_package_diff_render_audit(args)
    if args.command == "build-market-aware-session-history":
        return run_market_aware_session_history(args)
    if args.command == "audit-market-aware-session-history":
        return run_market_aware_session_history_audit(args)
    if args.command == "render-market-aware-session-history":
        return run_market_aware_session_history_renderer(args)
    if args.command == "audit-market-aware-session-history-render":
        return run_market_aware_session_history_render_audit(args)
    if args.command == "build-market-aware-session-history-manifest":
        return run_market_aware_session_history_manifest(args)
    if args.command == "audit-market-aware-session-history-manifest":
        return run_market_aware_session_history_manifest_audit(args)
    if args.command == "render-market-aware-session-history-manifest":
        return run_market_aware_session_history_manifest_renderer(args)
    if args.command == "audit-market-aware-session-history-manifest-render":
        return run_market_aware_session_history_manifest_render_audit(args)
    if args.command == "build-market-aware-session-history-closure":
        return run_market_aware_session_history_closure(args)
    if args.command == "render-market-aware-session-history-closure":
        return run_market_aware_session_history_closure_renderer(args)
    if args.command == "audit-market-aware-session-history-closure-render":
        return run_market_aware_session_history_closure_render_audit(args)
    if args.command == "build-market-aware-session-history-closure-admission":
        return run_market_aware_session_history_closure_admission(args)
    if args.command == "audit-market-aware-session-history-closure-admission":
        return run_market_aware_session_history_closure_admission_audit(args)
    if args.command == "render-market-aware-session-history-closure-admission":
        return run_market_aware_session_history_closure_admission_renderer(args)
    if args.command == "audit-market-aware-session-history-closure-admission-render":
        return run_market_aware_session_history_closure_admission_render_audit(args)
    if args.command == "build-market-aware-session-history-final-receipt":
        return run_market_aware_session_history_final_receipt(args)
    if args.command == "serve-research-receipt":
        return run_read_only_receipt_server(args)
    if args.command == "probe-research-receipt-service":
        return run_read_only_receipt_probe(args)
    if args.command == "render-analysis":
        return run_render_analysis(args)
    if args.command == "audit-analysis-quality":
        return run_analysis_quality(args)
    if args.command == "build-decision-input":
        return run_decision_input(args)
    if args.command == "audit-analysis-safety":
        return run_analysis_safety(args)
    if args.command == "build-analysis-review":
        return run_analysis_review(args)
    if args.command == "apply-analysis-review":
        return run_apply_analysis_review(args)
    if args.command == "build-research-release":
        return run_research_release(args)
    if args.command == "compare-research-releases":
        return run_release_diff(args)
    if args.command == "audit-research-freshness":
        return run_research_freshness(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
