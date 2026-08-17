"""Run one bounded, read-only daily research refresh through existing stages."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path, PureWindowsPath
from typing import Any
from zoneinfo import ZoneInfo

from ..analysis.technical_features import (
    build_feature_report,
    compute_feature_snapshots,
    serialize_feature_snapshots,
)
from ..decision.technical_price_plan import (
    PricePlanIssue,
    build_price_plan_report,
    compute_price_plan,
    serialize_price_plan,
)
from ..events.cninfo_announcements import CninfoAnnouncementConfig, CninfoAnnouncementSource
from ..evidence.analysis_input_bundle import AnalysisInputConfig, AnalysisInputSource
from ..evidence.contracts import (
    BUNDLE_VERSION_V2,
    MARKET_CONTEXT_SUMMARY_VERSION,
    RELATIVE_STRENGTH_VERSION,
)
from ..fundamentals.baostock_growth import BaostockGrowthConfig, BaostockGrowthSource
from ..fundamentals.baostock_profitability import (
    BaostockProfitabilityConfig,
    BaostockProfitabilitySource,
)
from ..market.adapter import JsonlReplaySource, ReplayInputError
from ..market.adapters.baostock_daily import BaostockDailyConfig, BaostockDailySource
from ..market.baostock_market_context import BaostockMarketContextSource
from ..market.calendar import CalendarError, JsonTradingCalendarSource
from ..market.coverage import audit_daily_coverage
from ..market.health import ValidationIssue, build_health_report
from ..market.market_context import MarketContextConfig
from ..market.replay import canonical_jsonl, sha256_bytes, write_atomic

DAILY_RESEARCH_RUN_VERSION = "daily-research-run-v1"
PUBLIC_READ_ONLY_SOURCE_MODE = "public-read-only"
_SYMBOL_PATTERN = re.compile(r"^\d{6}\.(SH|SZ)$")
_SPEC_FIELDS = {
    "run_version",
    "symbol",
    "start_date",
    "end_date",
    "as_of",
    "received_at",
    "calendar_path",
    "fundamentals_start",
    "fundamentals_end",
    "announcement_start",
    "announcement_end",
}
_QUARTER_FIELDS = {"year", "quarter"}
SHANGHAI = ZoneInfo("Asia/Shanghai")


class DailyResearchRunError(ValueError):
    """A sanitized runtime specification or controlled-path failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DailyResearchSpec:
    run_version: str
    symbol: str
    start_date: date
    end_date: date
    as_of: datetime
    received_at: datetime
    calendar_path: Path
    fundamentals_start: tuple[int, int]
    fundamentals_end: tuple[int, int]
    announcement_start: date
    announcement_end: date


@dataclass(frozen=True, slots=True)
class _StageDefinition:
    name: str
    relative_artifacts: tuple[str, ...]


_STAGES = (
    _StageDefinition(
        "daily_bars",
        (
            "market/request.json",
            "market/raw_response.json",
            "market/normalized_daily.jsonl",
            "market/capture_report.json",
        ),
    ),
    _StageDefinition(
        "market_context",
        (
            "market-context/request.json",
            "market-context/raw_response.json",
            "market-context/market_context_snapshot.json",
            "market-context/market_context_report.json",
        ),
    ),
    _StageDefinition(
        "data_quality",
        ("quality/health_report.json", "quality/coverage_report.json"),
    ),
    _StageDefinition(
        "technical_features",
        ("technical/technical_features.jsonl", "technical/technical_report.json"),
    ),
    _StageDefinition(
        "price_plan",
        ("price-plan/technical_price_plan.json", "price-plan/price_plan_report.json"),
    ),
    _StageDefinition(
        "profitability",
        (
            "fundamentals/profitability/request.json",
            "fundamentals/profitability/raw_response.json",
            "fundamentals/profitability/profitability_snapshot.json",
            "fundamentals/profitability/profitability_report.json",
        ),
    ),
    _StageDefinition(
        "growth",
        (
            "fundamentals/growth/request.json",
            "fundamentals/growth/raw_response.json",
            "fundamentals/growth/growth_snapshot.json",
            "fundamentals/growth/growth_report.json",
        ),
    ),
    _StageDefinition(
        "announcements",
        (
            "announcements/request.json",
            "announcements/raw_response.json",
            "announcements/announcements_snapshot.json",
            "announcements/announcements_report.json",
        ),
    ),
    _StageDefinition(
        "analysis_input_v2",
        (
            "analysis-input/analysis_input_bundle.json",
            "analysis-input/analysis_input_report.json",
        ),
    ),
)


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    write_atomic(path, _json_bytes(value))


def _safe_message(value: Any) -> str:
    message = str(value)
    message = re.sub(r"(?i)\b[A-Z]:[\\/][^\s\"']*", "<redacted-path>", message)
    return re.sub(r"(?<![A-Za-z0-9])/(?:[^\s\"']+/?)+", "<redacted-path>", message)


def _parse_date(value: Any, *, field: str) -> date:
    if not isinstance(value, str):
        raise DailyResearchRunError("SPEC_INVALID", f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DailyResearchRunError("SPEC_INVALID", f"{field} must be an ISO date") from exc


def _parse_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise DailyResearchRunError("SPEC_INVALID", f"{field} must be an aware ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchRunError(
            "SPEC_INVALID", f"{field} must be an aware ISO datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchRunError("SPEC_INVALID", f"{field} must include a timezone")
    return parsed


def _parse_quarter(value: Any, *, field: str) -> tuple[int, int]:
    if not isinstance(value, dict) or set(value) != _QUARTER_FIELDS:
        raise DailyResearchRunError("SPEC_INVALID", f"{field} is invalid")
    year = value["year"]
    quarter = value["quarter"]
    if isinstance(year, bool) or not isinstance(year, int) or year < 1900 or year > 2200:
        raise DailyResearchRunError("SPEC_INVALID", f"{field}.year is invalid")
    if isinstance(quarter, bool) or not isinstance(quarter, int) or quarter not in {1, 2, 3, 4}:
        raise DailyResearchRunError("SPEC_INVALID", f"{field}.quarter is invalid")
    return year, quarter


def _relative_file(value: Any, *, root: Path, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchRunError("PATH_INVALID", f"{field} is invalid")
    try:
        windows_path = PureWindowsPath(value)
        if Path(value).is_absolute() or windows_path.is_absolute() or windows_path.drive:
            raise DailyResearchRunError("PATH_INVALID", f"{field} must be relative")
        candidate = (root / Path(value)).resolve()
        candidate.relative_to(root.resolve())
    except DailyResearchRunError:
        raise
    except (OSError, ValueError) as exc:
        raise DailyResearchRunError("PATH_INVALID", f"{field} is invalid") from exc
    if not candidate.is_file():
        raise DailyResearchRunError("INPUT_UNAVAILABLE", f"{field} is unavailable")
    return candidate


def _controlled_directory(value: Path, *, root: Path, field: str) -> Path:
    try:
        candidate = value.resolve()
        candidate.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise DailyResearchRunError("PATH_INVALID", f"{field} must be inside input-root") from exc
    if candidate == root:
        raise DailyResearchRunError("PATH_INVALID", f"{field} must not equal input-root")
    return candidate


def load_daily_research_spec(*, spec_path: Path, input_root: Path) -> DailyResearchSpec:
    """Load and validate the fixed, path-bounded runtime specification."""

    try:
        root = input_root.resolve()
    except (OSError, ValueError) as exc:
        raise DailyResearchRunError("PATH_INVALID", "input-root is invalid") from exc
    if not root.is_dir():
        raise DailyResearchRunError("INPUT_UNAVAILABLE", "input-root is unavailable")
    try:
        spec_file = spec_path.resolve()
        spec_file.relative_to(root)
        payload = json.loads(spec_file.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise DailyResearchRunError("SPEC_INVALID", "runtime spec is invalid") from exc
    if not spec_file.is_file():
        raise DailyResearchRunError("INPUT_UNAVAILABLE", "runtime spec is unavailable")
    if not isinstance(payload, dict) or set(payload) != _SPEC_FIELDS:
        raise DailyResearchRunError("SPEC_INVALID", "runtime spec fields are invalid")
    if payload["run_version"] != DAILY_RESEARCH_RUN_VERSION:
        raise DailyResearchRunError("VERSION_MISMATCH", "runtime spec version is invalid")
    symbol = payload["symbol"]
    if not isinstance(symbol, str) or not _SYMBOL_PATTERN.fullmatch(symbol.strip().upper()):
        raise DailyResearchRunError("SPEC_INVALID", "symbol is invalid")
    symbol = symbol.strip().upper()
    start_date = _parse_date(payload["start_date"], field="start_date")
    end_date = _parse_date(payload["end_date"], field="end_date")
    announcement_start = _parse_date(payload["announcement_start"], field="announcement_start")
    announcement_end = _parse_date(payload["announcement_end"], field="announcement_end")
    as_of = _parse_datetime(payload["as_of"], field="as_of")
    received_at = _parse_datetime(payload["received_at"], field="received_at")
    if start_date > end_date or announcement_start > announcement_end:
        raise DailyResearchRunError("SPEC_INVALID", "date range is invalid")
    if received_at != as_of:
        raise DailyResearchRunError(
            "SPEC_INVALID", "received_at must equal as_of for a consistent point-in-time run"
        )
    fundamentals_start = _parse_quarter(payload["fundamentals_start"], field="fundamentals_start")
    fundamentals_end = _parse_quarter(payload["fundamentals_end"], field="fundamentals_end")
    if fundamentals_start > fundamentals_end:
        raise DailyResearchRunError("SPEC_INVALID", "fundamental quarter range is invalid")
    calendar_path = _relative_file(payload["calendar_path"], root=root, field="calendar_path")
    return DailyResearchSpec(
        run_version=DAILY_RESEARCH_RUN_VERSION,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        as_of=as_of,
        received_at=received_at,
        calendar_path=calendar_path,
        fundamentals_start=fundamentals_start,
        fundamentals_end=fundamentals_end,
        announcement_start=announcement_start,
        announcement_end=announcement_end,
    )


def _load_bars(path: Path) -> tuple[list[Any], bytes, tuple[ValidationIssue, ...]]:
    try:
        input_bytes = path.read_bytes()
    except OSError as exc:
        return [], b"", (ValidationIssue("INPUT_UNAVAILABLE", _safe_message(exc)),)
    bars: list[Any] = []
    issues: list[ValidationIssue] = []
    try:
        for bar in JsonlReplaySource(path).iter_daily_bars():
            bars.append(bar)
    except ReplayInputError as exc:
        issues.append(
            ValidationIssue(
                "INPUT_UNAVAILABLE" if exc.line_number == 0 else "INPUT_INVALID",
                _safe_message(exc),
                line_number=exc.line_number or None,
            )
        )
    return bars, input_bytes, tuple(issues)


def _artifact_entries(paths: tuple[Path, ...], *, input_root: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in paths:
        try:
            relative = path.resolve().relative_to(input_root.resolve()).as_posix()
            digest = sha256_bytes(path.read_bytes())
        except (OSError, ValueError):
            continue
        entries.append({"path": relative, "sha256": digest})
    return entries


def _issues_from_result(result: Any) -> list[dict[str, str]]:
    if not isinstance(result, Mapping):
        return []
    raw = result.get("issues")
    if not isinstance(raw, list):
        return []
    issues: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, Mapping):
            code = item.get("code", "STAGE_ERROR")
            message = item.get("message", "stage did not become ready")
            issues.append({"code": str(code), "message": _safe_message(message)})
    return issues


def _stage_record(
    definition: _StageDefinition,
    *,
    status: str,
    input_root: Path,
    output_dir: Path,
    error_code: str | None = None,
    issues: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    paths = tuple(
        output_dir / relative
        for relative in definition.relative_artifacts
        if not relative.endswith("/")
    )
    return {
        "name": definition.name,
        "status": status,
        "skipped": status == "skipped",
        "error_code": error_code,
        "artifacts": _artifact_entries(paths, input_root=input_root),
        "issues": issues or [],
    }


def _skip_records(
    definitions: tuple[_StageDefinition, ...], *, input_root: Path, output_dir: Path
) -> list[dict[str, Any]]:
    return [
        _stage_record(
            definition,
            status="skipped",
            input_root=input_root,
            output_dir=output_dir,
            error_code="UPSTREAM_FAILED",
            issues=[{"code": "UPSTREAM_FAILED", "message": "upstream stage failed"}],
        )
        for definition in definitions
    ]


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _latest_feature(path: Path) -> tuple[dict[str, Any] | None, tuple[PricePlanIssue, ...]]:
    try:
        values = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, (PricePlanIssue("INPUT_INVALID", _safe_message(exc)),)
    if not values or any(not isinstance(value, dict) for value in values):
        return None, (PricePlanIssue("INPUT_EMPTY", "technical features are empty"),)
    return values[-1], ()


def run_daily_research(
    *, spec_path: Path, input_root: Path, output_dir: Path, source_mode: str
) -> dict[str, Any]:
    """Execute exactly one public, read-only daily refresh without retries."""

    if source_mode != PUBLIC_READ_ONLY_SOURCE_MODE:
        raise DailyResearchRunError("SOURCE_MODE_INVALID", "source-mode must be public-read-only")
    spec = load_daily_research_spec(spec_path=spec_path, input_root=input_root)
    root = input_root.resolve()
    output = _controlled_directory(output_dir, root=root, field="output-dir")
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DailyResearchRunError("OUTPUT_UNAVAILABLE", "output-dir is unavailable") from exc

    stage_records: list[dict[str, Any]] = []
    state: dict[str, Any] = {}
    top_issues: list[dict[str, str]] = []
    failed_at: int | None = None

    def execute(
        index: int,
        action: Callable[[], Any],
        ready: Callable[[Any], bool],
    ) -> bool:
        nonlocal failed_at
        definition = _STAGES[index]
        try:
            result = action()
            is_ready = ready(result)
        except Exception as exc:  # provider and filesystem failures are one bounded stage failure
            is_ready = False
            error_code = "PROVIDER_ERROR" if "Source" in type(exc).__name__ else "STAGE_ERROR"
            issues = [{"code": error_code, "message": _safe_message(exc)}]
            stage_records.append(
                _stage_record(
                    definition,
                    status="failed",
                    input_root=root,
                    output_dir=output,
                    error_code=error_code,
                    issues=issues,
                )
            )
            top_issues.extend(issues)
            failed_at = index
            return False
        issues = _issues_from_result(result)
        if not is_ready and not issues:
            issues = [{"code": "STAGE_NOT_READY", "message": "stage did not become ready"}]
        status = "ready" if is_ready else "failed"
        error_code = None if is_ready else (issues[0]["code"] if issues else "STAGE_NOT_READY")
        stage_records.append(
            _stage_record(
                definition,
                status=status,
                input_root=root,
                output_dir=output,
                error_code=error_code,
                issues=issues,
            )
        )
        if not is_ready:
            top_issues.extend(issues)
            failed_at = index
        return is_ready

    daily_dir = output / "market"
    daily_ready = execute(
        0,
        lambda: BaostockDailySource(
            BaostockDailyConfig(
                symbol=spec.symbol,
                start_date=spec.start_date,
                end_date=spec.end_date,
                received_at=spec.received_at,
            )
        ).capture(daily_dir),
        lambda report: isinstance(report, Mapping)
        and report.get("error") is None
        and isinstance(report.get("bar_count"), int)
        and report["bar_count"] > 0
        and report.get("decision_ready") is False,
    )
    if daily_ready:
        state["daily_path"] = daily_dir / "normalized_daily.jsonl"

    if failed_at is None:
        market_dir = output / "market-context"
        market_ready = execute(
            1,
            lambda: BaostockMarketContextSource(
                MarketContextConfig(
                    start=spec.start_date,
                    end=spec.end_date,
                    as_of=spec.as_of,
                    received_at=spec.received_at,
                )
            ).capture(calendar_path=spec.calendar_path, output_dir=market_dir),
            lambda report: isinstance(report, Mapping)
            and report.get("market_context_ready") is True
            and report.get("decision_ready") is False,
        )
        if market_ready:
            state["market_snapshot"] = market_dir / "market_context_snapshot.json"
            state["market_report"] = market_dir / "market_context_report.json"

    if failed_at is None:
        quality_dir = output / "quality"

        def build_quality() -> dict[str, Any]:
            bars, bars_bytes, parse_issues = _load_bars(state["daily_path"])
            state["bars"] = bars
            state["bars_bytes"] = bars_bytes
            try:
                calendar_bytes = spec.calendar_path.read_bytes()
                calendar = JsonTradingCalendarSource(spec.calendar_path).load_calendar()
                calendar_error = None
            except (OSError, CalendarError) as exc:
                calendar_bytes = b""
                calendar = None
                calendar_error = _safe_message(exc)
            output_bytes = canonical_jsonl(bars)
            health = build_health_report(
                bars,
                as_of=spec.as_of,
                input_sha256=sha256_bytes(bars_bytes),
                output_sha256=sha256_bytes(output_bytes),
                source="jsonl-replay",
                parse_issues=parse_issues,
                stale_after=timedelta(hours=24),
            )
            bars_error = "; ".join(issue.message for issue in parse_issues) or None
            coverage = audit_daily_coverage(
                bars,
                calendar=calendar,
                start=spec.start_date,
                end=spec.end_date,
                as_of=spec.as_of,
                bars_sha256=sha256_bytes(bars_bytes),
                calendar_sha256=sha256_bytes(calendar_bytes),
                calendar_source="json-calendar",
                calendar_error=calendar_error,
                bars_error=bars_error,
            )
            health_mapping = health.to_mapping()
            coverage_mapping = coverage.to_mapping()
            _write_json(quality_dir / "health_report.json", health_mapping)
            _write_json(quality_dir / "coverage_report.json", coverage_mapping)
            return {"health": health_mapping, "coverage": coverage_mapping}

        execute(
            2,
            build_quality,
            lambda result: isinstance(result, Mapping)
            and result["health"].get("decision_ready") is True
            and result["coverage"].get("decision_ready") is True,
        )

    if failed_at is None:
        technical_dir = output / "technical"

        def build_technical() -> dict[str, Any]:
            input_bytes = state["bars_bytes"]
            computation = compute_feature_snapshots(
                state["bars"],
                as_of=spec.as_of,
                input_sha256=sha256_bytes(input_bytes),
            )
            output_bytes = serialize_feature_snapshots(computation.snapshots)
            report = build_feature_report(
                computation,
                as_of=spec.as_of,
                input_sha256=sha256_bytes(input_bytes),
                output_sha256=sha256_bytes(output_bytes),
            ).to_mapping()
            write_atomic(technical_dir / "technical_features.jsonl", output_bytes)
            _write_json(technical_dir / "technical_report.json", report)
            return report

        technical_ready = execute(
            3,
            build_technical,
            lambda report: isinstance(report, Mapping) and report.get("decision_ready") is True,
        )
        if technical_ready:
            state["technical_features"] = technical_dir / "technical_features.jsonl"
            state["technical_report"] = technical_dir / "technical_report.json"

    if failed_at is None:
        price_dir = output / "price-plan"

        def build_price() -> dict[str, Any]:
            technical_features = state["technical_features"]
            technical_report_path = state["technical_report"]
            snapshot, input_issues = _latest_feature(technical_features)
            technical_report = _load_json_object(technical_report_path)
            input_bytes = technical_features.read_bytes()
            report_bytes = technical_report_path.read_bytes()
            computation = compute_price_plan(
                snapshot,
                technical_report=technical_report,
                input_sha256=sha256_bytes(input_bytes),
                technical_input_sha256=str(technical_report.get("output_sha256")),
                input_issues=input_issues,
            )
            output_bytes = serialize_price_plan(computation.plan)
            report = build_price_plan_report(
                computation,
                input_sha256=sha256_bytes(input_bytes),
                technical_report_sha256=sha256_bytes(report_bytes),
                output_sha256=sha256_bytes(output_bytes),
                technical_report=technical_report,
            ).to_mapping()
            write_atomic(price_dir / "technical_price_plan.json", output_bytes)
            _write_json(price_dir / "price_plan_report.json", report)
            return report

        price_ready = execute(
            4,
            build_price,
            lambda report: isinstance(report, Mapping) and report.get("price_plan_ready") is True,
        )
        if price_ready:
            state["price_plan"] = price_dir / "technical_price_plan.json"
            state["price_report"] = price_dir / "price_plan_report.json"

    if failed_at is None:
        profitability_dir = output / "fundamentals" / "profitability"
        profitability_ready = execute(
            5,
            lambda: BaostockProfitabilitySource(
                BaostockProfitabilityConfig(
                    symbol=spec.symbol,
                    as_of=spec.as_of.astimezone(SHANGHAI).date(),
                    start_year=spec.fundamentals_start[0],
                    start_quarter=spec.fundamentals_start[1],
                    end_year=spec.fundamentals_end[0],
                    end_quarter=spec.fundamentals_end[1],
                )
            ).capture(profitability_dir),
            lambda report: isinstance(report, Mapping)
            and report.get("fundamental_ready") is True
            and report.get("decision_ready") is False,
        )
        if profitability_ready:
            state["profitability_snapshot"] = profitability_dir / "profitability_snapshot.json"
            state["profitability_report"] = profitability_dir / "profitability_report.json"

    if failed_at is None:
        growth_dir = output / "fundamentals" / "growth"
        growth_ready = execute(
            6,
            lambda: BaostockGrowthSource(
                BaostockGrowthConfig(
                    symbol=spec.symbol,
                    as_of=spec.as_of.astimezone(SHANGHAI).date(),
                    start_year=spec.fundamentals_start[0],
                    start_quarter=spec.fundamentals_start[1],
                    end_year=spec.fundamentals_end[0],
                    end_quarter=spec.fundamentals_end[1],
                )
            ).capture(growth_dir),
            lambda report: isinstance(report, Mapping)
            and report.get("growth_ready") is True
            and report.get("decision_ready") is False,
        )
        if growth_ready:
            state["growth_snapshot"] = growth_dir / "growth_snapshot.json"
            state["growth_report"] = growth_dir / "growth_report.json"

    if failed_at is None:
        announcements_dir = output / "announcements"
        announcements_ready = execute(
            7,
            lambda: CninfoAnnouncementSource(
                CninfoAnnouncementConfig(
                    symbol=spec.symbol,
                    start_date=spec.announcement_start,
                    end_date=spec.announcement_end,
                    as_of=spec.as_of.astimezone(SHANGHAI).date(),
                    received_at=spec.received_at,
                )
            ).capture(announcements_dir),
            lambda report: isinstance(report, Mapping)
            and report.get("announcement_ready") is True
            and report.get("decision_ready") is False,
        )
        if announcements_ready:
            state["announcements_snapshot"] = announcements_dir / "announcements_snapshot.json"
            state["announcements_report"] = announcements_dir / "announcements_report.json"

    if failed_at is None:
        analysis_dir = output / "analysis-input"

        def build_analysis_input() -> dict[str, Any]:
            config = AnalysisInputConfig(
                symbol=spec.symbol,
                as_of=spec.as_of,
                input_root=root,
                market_bars=state["daily_path"],
                market_health_report=output / "quality" / "health_report.json",
                coverage_report=output / "quality" / "coverage_report.json",
                calendar=spec.calendar_path,
                technical_input=state["daily_path"],
                technical_features=state["technical_features"],
                technical_report=state["technical_report"],
                price_plan_input=state["technical_features"],
                price_plan=state["price_plan"],
                price_plan_report=state["price_report"],
                profitability_snapshot=state["profitability_snapshot"],
                profitability_report=state["profitability_report"],
                growth_snapshot=state["growth_snapshot"],
                growth_report=state["growth_report"],
                announcements_snapshot=state["announcements_snapshot"],
                announcements_report=state["announcements_report"],
                market_context_snapshot=state["market_snapshot"],
                market_context_report=state["market_report"],
            )
            return AnalysisInputSource(config).capture(analysis_dir)

        def analysis_ready(report: Any) -> bool:
            if not isinstance(report, Mapping) or report.get("analysis_input_ready") is not True:
                return False
            try:
                bundle = _load_json_object(analysis_dir / "analysis_input_bundle.json")
                summaries = bundle.get("summaries")
                market = summaries["market"]
                technical = summaries["technical"]
                context = market["market_context"]
                relative = technical["relative_strength"]
                return (
                    bundle.get("bundle_version") == BUNDLE_VERSION_V2
                    and context.get("market_context_summary_version")
                    == MARKET_CONTEXT_SUMMARY_VERSION
                    and relative.get("version") == RELATIVE_STRENGTH_VERSION
                    and bundle.get("decision_ready") is False
                )
            except (KeyError, TypeError, OSError, UnicodeError, json.JSONDecodeError):
                return False

        analysis_input_ready = execute(8, build_analysis_input, analysis_ready)
        if analysis_input_ready:
            state["analysis_input"] = analysis_dir / "analysis_input_bundle.json"

    if failed_at is not None:
        stage_records.extend(
            _skip_records(_STAGES[failed_at + 1 :], input_root=root, output_dir=output)
        )

    analysis_path = state.get("analysis_input")
    analysis_sha = None
    if isinstance(analysis_path, Path) and analysis_path.is_file():
        analysis_sha = sha256_bytes(analysis_path.read_bytes())
    market_summary_version = None
    relative_strength_version = None
    if analysis_path is not None and analysis_path.is_file():
        try:
            bundle = _load_json_object(analysis_path)
            market_summary_version = bundle["summaries"]["market"]["market_context"][
                "market_context_summary_version"
            ]
            relative_strength_version = bundle["summaries"]["technical"]["relative_strength"][
                "version"
            ]
        except (KeyError, TypeError, OSError, UnicodeError, json.JSONDecodeError):
            pass

    report = {
        "run_version": DAILY_RESEARCH_RUN_VERSION,
        "source_mode": source_mode,
        "symbol": spec.symbol,
        "start_date": spec.start_date.isoformat(),
        "end_date": spec.end_date.isoformat(),
        "as_of": spec.as_of.isoformat(),
        "received_at": spec.received_at.isoformat(),
        "status": "ready" if failed_at is None else "blocked",
        "stages": stage_records,
        "analysis_input_path": (
            analysis_path.resolve().relative_to(root.resolve()).as_posix()
            if isinstance(analysis_path, Path)
            else None
        ),
        "analysis_input_sha256": analysis_sha,
        "analysis_input_ready": failed_at is None,
        "market_context_summary_version": market_summary_version,
        "relative_strength_version": relative_strength_version,
        "issues": top_issues,
        "decision_ready": False,
    }
    _write_json(output / "daily_research_run_report.json", report)
    return report
