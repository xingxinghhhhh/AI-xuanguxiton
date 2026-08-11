"""Command line entry points for the offline A-share data MVP."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .analysis.contracts import AnalysisReportConfig
from .analysis.technical_features import (
    TechnicalFeatureReport,
    build_feature_report,
    compute_feature_snapshots,
    serialize_feature_snapshots,
)
from .analysis.validator import AnalysisReportSource
from .decision.technical_price_plan import (
    PricePlanIssue,
    PricePlanReport,
    build_price_plan_report,
    compute_price_plan,
    serialize_price_plan,
)
from .events.cninfo_announcements import CninfoAnnouncementConfig, CninfoAnnouncementSource
from .evidence.analysis_input_bundle import AnalysisInputConfig, AnalysisInputSource
from .fundamentals.baostock_growth import BaostockGrowthConfig, BaostockGrowthSource
from .fundamentals.baostock_profitability import (
    BaostockProfitabilityConfig,
    BaostockProfitabilitySource,
)
from .market.adapter import JsonlReplaySource, ReplayInputError
from .market.adapters.baostock_daily import BaostockDailyConfig, BaostockDailySource
from .market.calendar import CalendarError, JsonTradingCalendarSource
from .market.coverage import CoverageReport, audit_daily_coverage
from .market.health import HealthReport, ValidationIssue, build_health_report
from .market.replay import canonical_jsonl, sha256_bytes, write_atomic


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
        )
        report = AnalysisInputSource(config).capture(args.output_dir)
    except Exception as exc:
        report = {
            "schema_version": "1.0",
            "bundle_version": "analysis-input-v1",
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
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
