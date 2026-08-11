"""Build a point-in-time, hash-checked bundle from existing evidence artifacts."""

# The validation calls intentionally keep all evidence-field names visible on one line.
# ruff: noqa: E501

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..market.calendar import CalendarError, JsonTradingCalendarSource
from ..market.market_context import (
    INDEX_SYMBOLS,
    MARKET_CONTEXT_VERSION,
    MarketContextError,
    MarketIndexRecord,
)
from ..market.replay import sha256_bytes, write_atomic
from .contracts import (
    BUNDLE_VERSION_V1,
    BUNDLE_VERSION_V2,
    MARKET_CONTEXT_SUMMARY_VERSION,
    SCHEMA_VERSION,
    AnalysisInputBundle,
    AnalysisInputReport,
    EvidenceEntry,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SYMBOL_PATTERN = re.compile(r"^\d{6}\.(SH|SZ)$")


class BundleError(RuntimeError):
    """Raised when an input cannot be safely included in the bundle."""


class BundleState(StrEnum):
    READY = "ready"
    INVALID = "invalid"
    INPUT_UNAVAILABLE = "input_unavailable"
    UPSTREAM_NOT_READY = "upstream_not_ready"


@dataclass(frozen=True, slots=True)
class AnalysisInputConfig:
    symbol: str
    as_of: datetime
    input_root: Path
    market_bars: Path
    market_health_report: Path
    coverage_report: Path
    calendar: Path
    technical_input: Path
    technical_features: Path
    technical_report: Path
    price_plan_input: Path
    price_plan: Path
    price_plan_report: Path
    profitability_snapshot: Path
    profitability_report: Path
    growth_snapshot: Path
    growth_report: Path
    announcements_snapshot: Path
    announcements_report: Path
    market_context_snapshot: Path | None = None
    market_context_report: Path | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise BundleError("symbol must match six digits followed by .SH or .SZ")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise BundleError("as_of must include a timezone")
        object.__setattr__(self, "symbol", symbol)
        if (self.market_context_snapshot is None) != (self.market_context_report is None):
            raise BundleError(
                "market context snapshot and report must be provided together"
            )

    @property
    def as_of_date(self) -> date:
        return self.as_of.astimezone(SHANGHAI).date()

    @property
    def paths(self) -> dict[str, Path]:
        paths = {
            "market_bars": self.market_bars,
            "market_health_report": self.market_health_report,
            "coverage_report": self.coverage_report,
            "calendar": self.calendar,
            "technical_input": self.technical_input,
            "technical_features": self.technical_features,
            "technical_report": self.technical_report,
            "price_plan_input": self.price_plan_input,
            "price_plan": self.price_plan,
            "price_plan_report": self.price_plan_report,
            "profitability_snapshot": self.profitability_snapshot,
            "profitability_report": self.profitability_report,
            "growth_snapshot": self.growth_snapshot,
            "growth_report": self.growth_report,
            "announcements_snapshot": self.announcements_snapshot,
            "announcements_report": self.announcements_report,
        }
        if self.market_context_snapshot is not None:
            paths["market_context_snapshot"] = self.market_context_snapshot
            paths["market_context_report"] = self.market_context_report
        return paths

    @property
    def bundle_version(self) -> str:
        return BUNDLE_VERSION_V2 if self.market_context_snapshot is not None else BUNDLE_VERSION_V1


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _parse_report_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise BundleError(f"{field} must be an ISO date or aware datetime")
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleError(f"{field} must be an ISO date or aware datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BundleError(f"{field} datetime must include a timezone")
    return parsed.astimezone(SHANGHAI).date()


def _parse_iso_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise BundleError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise BundleError(f"{field} must be an ISO date") from exc


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleError(f"{name} must be a JSON object")
    return value


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BundleError(f"input unavailable: {path}") from exc
    try:
        return _require_mapping(json.loads(raw.decode("utf-8")), str(path)), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"invalid JSON: {path}") from exc


def _safe_relative(path: Path, root: Path) -> tuple[Path, str]:
    try:
        resolved_root = root.resolve()
        resolved_path = path.resolve()
        relative = resolved_path.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise BundleError(f"path is outside input root: {path}") from exc
    return resolved_path, relative.as_posix()


def _hash_path(path: Path, root: Path) -> tuple[str, str]:
    resolved, relative = _safe_relative(path, root)
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise BundleError(f"input unavailable: {path}") from exc
    return relative, sha256_bytes(raw)


def _check_hash(actual: str, expected: Any, name: str) -> None:
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise BundleError(f"{name} does not contain a valid SHA-256")
    if actual != expected:
        raise BundleError(f"{name} SHA-256 does not match the referenced file")


def _check_common(
    report: Mapping[str, Any], *, name: str, source: str | None, symbol: str | None, as_of: date
) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise BundleError(f"{name} has unsupported schema_version")
    if source is not None and report.get("source") != source:
        raise BundleError(f"{name} source mismatch")
    if symbol is not None and report.get("symbol") != symbol:
        raise BundleError(f"{name} symbol mismatch")
    if "as_of" in report and _parse_report_date(report["as_of"], f"{name}.as_of") != as_of:
        raise BundleError(f"{name}.as_of does not match the bundle cutoff")


def _check_report_and_artifact(
    report: Mapping[str, Any],
    report_path: Path,
    artifact_path: Path,
    *,
    root: Path,
    hash_field: str,
    name: str,
) -> tuple[str, str, str, str]:
    report_relative, report_sha = _hash_path(report_path, root)
    artifact_relative, artifact_sha = _hash_path(artifact_path, root)
    _check_hash(artifact_sha, report.get(hash_field), f"{name}.{hash_field}")
    return report_relative, report_sha, artifact_relative, artifact_sha


def _load_jsonl(path: Path, *, name: str) -> tuple[list[dict[str, Any]], bytes]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BundleError(f"{name} is unavailable or invalid UTF-8") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BundleError(f"{name} line {line_number} is invalid JSON") from exc
        records.append(_require_mapping(record, f"{name} line {line_number}"))
    if not records:
        raise BundleError(f"{name} is empty")
    return records, raw


def _check_jsonl_point_in_time(
    records: list[dict[str, Any]], *, name: str, symbol: str, as_of: date
) -> None:
    for index, record in enumerate(records, start=1):
        if record.get("symbol") != symbol:
            raise BundleError(f"{name} record {index} symbol mismatch")
        trade_date = _parse_iso_date(record.get("trade_date"), f"{name}.trade_date")
        if trade_date > as_of:
            raise BundleError(f"{name} contains future trade data")


def _check_snapshot_point_in_time(
    snapshot: Mapping[str, Any], *, name: str, symbol: str, as_of: date
) -> None:
    if snapshot.get("symbol") != symbol:
        raise BundleError(f"{name} symbol mismatch")
    if "as_of" in snapshot and _parse_report_date(snapshot["as_of"], f"{name}.as_of") != as_of:
        raise BundleError(f"{name}.as_of does not match the bundle cutoff")
    for field in ("published_date", "report_date", "trade_date"):
        if field in snapshot and snapshot[field] is not None:
            if _parse_iso_date(snapshot[field], f"{name}.{field}") > as_of:
                raise BundleError(f"{name} contains future {field}")
    records = snapshot.get("records")
    if records is not None:
        if not isinstance(records, list):
            raise BundleError(f"{name}.records must be a list")
        for index, record in enumerate(records, start=1):
            item = _require_mapping(record, f"{name}.records[{index}]")
            if item.get("symbol") != symbol:
                raise BundleError(f"{name}.records[{index}] symbol mismatch")
            if (
                _parse_iso_date(
                    item.get("published_date"), f"{name}.records[{index}].published_date"
                )
                > as_of
            ):
                raise BundleError(f"{name} contains a future announcement")


@dataclass(frozen=True, slots=True)
class _Inspection:
    evidence: EvidenceEntry
    summary: dict[str, Any]


class AnalysisInputSource:
    """Validate and reference existing reports without copying their payloads."""

    def __init__(self, config: AnalysisInputConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return "analysis-input-bundle"

    def _inspect_market_context(
        self, *, calendar_sha: str
    ) -> tuple[str, str, str, str, dict[str, Any]]:
        cfg = self.config
        if cfg.market_context_snapshot is None or cfg.market_context_report is None:
            raise BundleError("market context paths are required for analysis-input-v2")
        report, _ = _read_json(cfg.market_context_report)
        snapshot, _ = _read_json(cfg.market_context_snapshot)
        if report.get("market_context_version") != MARKET_CONTEXT_VERSION:
            raise BundleError("market context version is unsupported")
        if report.get("status") != "ready" or report.get("market_context_ready") is not True:
            raise BundleError("market context is not ready")
        if report.get("decision_ready") is not False:
            raise BundleError("market context decision_ready must be false")
        expected_as_of = cfg.as_of.isoformat()
        if report.get("as_of") != expected_as_of or snapshot.get("as_of") != expected_as_of:
            raise BundleError("market context as_of does not match the bundle cutoff")
        if report.get("calendar_sha256") != calendar_sha:
            raise BundleError("market context calendar SHA-256 does not match the bundle calendar")
        try:
            calendar = JsonTradingCalendarSource(cfg.calendar).load_calendar()
        except CalendarError as exc:
            raise BundleError(f"market context calendar is invalid: {exc}") from exc
        if report.get("calendar_version") != calendar.calendar_version:
            raise BundleError("market context calendar version does not match the bundle calendar")
        snapshot_raw = cfg.market_context_snapshot.read_bytes()
        snapshot_sha = sha256_bytes(snapshot_raw)
        _check_hash(snapshot_sha, report.get("snapshot_sha256"), "market_context.snapshot_sha256")
        if snapshot.get("market_context_version") != MARKET_CONTEXT_VERSION:
            raise BundleError("market context snapshot version is unsupported")
        if snapshot.get("calendar_version") != calendar.calendar_version:
            raise BundleError("market context snapshot calendar version does not match")
        request = snapshot.get("request")
        if not isinstance(request, dict) or request.get("as_of") != expected_as_of:
            raise BundleError("market context request as_of does not match the bundle cutoff")
        raw_indexes = snapshot.get("indexes")
        if not isinstance(raw_indexes, list) or not raw_indexes:
            raise BundleError("market context indexes must be a non-empty list")
        records: list[MarketIndexRecord] = []
        try:
            records = [MarketIndexRecord.from_mapping(item) for item in raw_indexes]
        except (MarketContextError, TypeError) as exc:
            raise BundleError(f"market context record is invalid: {exc}") from exc
        by_symbol: dict[str, list[MarketIndexRecord]] = {symbol: [] for symbol in INDEX_SYMBOLS}
        for record in records:
            if record.symbol not in by_symbol:
                raise BundleError("market context contains an unsupported index")
            if record.trade_date > cfg.as_of_date:
                raise BundleError("market context contains future trade data")
            by_symbol[record.symbol].append(record)
        report_index_symbols = report.get("index_symbols")
        if report_index_symbols != list(INDEX_SYMBOLS):
            raise BundleError("market context fixed index list is invalid")
        index_reports = report.get("index_reports")
        if not isinstance(index_reports, dict):
            raise BundleError("market context index reports are invalid")
        for symbol in INDEX_SYMBOLS:
            rows = by_symbol[symbol]
            details = index_reports.get(symbol)
            if not rows or not isinstance(details, dict) or details.get("status") != "ready":
                raise BundleError(f"market context index is incomplete: {symbol}")
            if details.get("record_count") != len(rows):
                raise BundleError(f"market context record count mismatch: {symbol}")
            if len({record.trade_date for record in rows}) != len(rows):
                raise BundleError(f"market context contains duplicate dates: {symbol}")
            dates = [record.trade_date for record in rows]
            if dates != sorted(dates):
                raise BundleError(f"market context dates are not ascending: {symbol}")
            if any(record.close <= 0 for record in rows):
                raise BundleError(f"market context close must be positive: {symbol}")

        def decimal_text(value: Decimal) -> str:
            return format(value, "f")

        index_features: dict[str, dict[str, Any]] = {}
        with localcontext() as context:
            context.prec = 28
            for symbol in INDEX_SYMBOLS:
                rows = by_symbol[symbol]
                latest = rows[-1]

                def return_for(periods: int) -> str | None:
                    if len(rows) <= periods:
                        return None
                    return decimal_text(latest.close / rows[-periods - 1].close - Decimal("1"))

                index_features[symbol] = {
                    "latest_trade_date": latest.trade_date.isoformat(),
                    "latest_close": decimal_text(latest.close),
                    "return_1d": return_for(1),
                    "return_5d": return_for(5),
                    "return_20d": return_for(20),
                    "record_count": len(rows),
                }
        report_rel, report_sha = _hash_path(cfg.market_context_report, cfg.input_root)
        snapshot_rel, snapshot_sha = _hash_path(cfg.market_context_snapshot, cfg.input_root)
        return report_rel, report_sha, snapshot_rel, snapshot_sha, {
            "status": report.get("status"),
            "version": MARKET_CONTEXT_VERSION,
            "market_context_summary_version": MARKET_CONTEXT_SUMMARY_VERSION,
            "index_symbols": list(INDEX_SYMBOLS),
            "record_count": len(records),
            "start": report.get("start"),
            "end": report.get("end"),
            "index_features": index_features,
        }

    def _inspect_market(self) -> _Inspection:
        cfg = self.config
        health, health_raw = _read_json(cfg.market_health_report)
        coverage, coverage_raw = _read_json(cfg.coverage_report)
        _check_common(
            health, name="market_health", source="jsonl-replay", symbol=None, as_of=cfg.as_of_date
        )
        symbols = health.get("symbols")
        if symbols != [cfg.symbol]:
            raise BundleError("market_health symbols do not match the bundle symbol")
        _check_common(coverage, name="coverage", source=None, symbol=None, as_of=cfg.as_of_date)
        if coverage.get("calendar_source") != "json-calendar":
            raise BundleError("coverage calendar source mismatch")
        bars, bars_raw = _load_jsonl(cfg.market_bars, name="market_bars")
        _check_jsonl_point_in_time(
            bars, name="market_bars", symbol=cfg.symbol, as_of=cfg.as_of_date
        )
        bar_sha = sha256_bytes(bars_raw)
        _check_hash(bar_sha, health.get("output_sha256"), "market_health.output_sha256")
        _check_hash(bar_sha, health.get("input_sha256"), "market_health.input_sha256")
        _check_hash(bar_sha, coverage.get("bars_sha256"), "coverage.bars_sha256")
        calendar_relative, calendar_sha = _hash_path(cfg.calendar, cfg.input_root)
        _check_hash(calendar_sha, coverage.get("calendar_sha256"), "coverage.calendar_sha256")
        if health.get("decision_ready") is not True or health.get("final_state") != "connected":
            raise BundleError("market health is not connected and decision-ready")
        if coverage.get("decision_ready") is not True or coverage.get("status") != "complete":
            raise BundleError("market coverage is not complete and decision-ready")
        latest_received_at = health.get("latest_received_at")
        if latest_received_at is not None:
            latest = datetime.fromisoformat(str(latest_received_at).replace("Z", "+00:00"))
            if latest.tzinfo is None or latest.utcoffset() is None or latest > cfg.as_of:
                raise BundleError("market health contains data received after as_of")
        report_rel, report_sha = _hash_path(cfg.market_health_report, cfg.input_root)
        coverage_rel, coverage_sha = _hash_path(cfg.coverage_report, cfg.input_root)
        bars_rel, bars_sha = _hash_path(cfg.market_bars, cfg.input_root)
        artifact_paths = [coverage_rel, bars_rel, calendar_relative]
        artifact_sha256 = [coverage_sha, bars_sha, calendar_sha]
        summary: dict[str, Any] = {
            "status": "connected/complete",
            "first_trade_date": health.get("first_trade_date"),
            "last_trade_date": health.get("last_trade_date"),
            "bar_count": health.get("bar_count"),
            "coverage_status": coverage.get("status"),
        }
        source = "jsonl-replay+json-calendar"
        if cfg.market_context_snapshot is not None:
            (
                context_report_rel,
                context_report_sha,
                context_snapshot_rel,
                context_snapshot_sha,
                context_summary,
            ) = self._inspect_market_context(calendar_sha=calendar_sha)
            artifact_paths.extend((context_snapshot_rel, context_report_rel))
            artifact_sha256.extend((context_snapshot_sha, context_report_sha))
            summary["market_context"] = context_summary
            source += "+market-context-v1"
        entry = EvidenceEntry(
            name="market",
            report_path=report_rel,
            report_sha256=report_sha,
            artifact_paths=tuple(artifact_paths),
            artifact_sha256=tuple(artifact_sha256),
            source=source,
            symbol=cfg.symbol,
            as_of=str(health["as_of"]),
            status="connected/complete",
            ready=True,
        )
        return _Inspection(entry, summary)

    def _inspect_technical(self) -> _Inspection:
        cfg = self.config
        report, _ = _read_json(cfg.technical_report)
        _check_common(
            report,
            name="technical",
            source=None,
            symbol=cfg.symbol,
            as_of=cfg.as_of_date,
        )
        _, input_sha = _hash_path(cfg.technical_input, cfg.input_root)
        _check_hash(input_sha, report.get("input_sha256"), "technical.input_sha256")
        feature_rel, feature_sha = _hash_path(cfg.technical_features, cfg.input_root)
        _check_hash(feature_sha, report.get("output_sha256"), "technical.output_sha256")
        features, _ = _load_jsonl(cfg.technical_features, name="technical_features")
        _check_jsonl_point_in_time(
            features, name="technical_features", symbol=cfg.symbol, as_of=cfg.as_of_date
        )
        if report.get("status") != "ready" or report.get("decision_ready") is not True:
            raise BundleError("technical features are not ready")
        report_rel, report_sha, _, _ = _check_report_and_artifact(
            report,
            cfg.technical_report,
            cfg.technical_features,
            root=cfg.input_root,
            hash_field="output_sha256",
            name="technical",
        )
        return _Inspection(
            EvidenceEntry(
                name="technical",
                report_path=report_rel,
                report_sha256=report_sha,
                artifact_paths=(feature_rel,),
                artifact_sha256=(feature_sha,),
                source=str(report.get("source", "technical-input")),
                symbol=cfg.symbol,
                as_of=str(report["as_of"]),
                status=str(report["status"]),
                ready=True,
            ),
            {
                "status": report.get("status"),
                "indicator_version": report.get("indicator_version"),
                "last_trade_date": report.get("last_trade_date"),
                "sample_count": report.get("sample_count"),
            },
        )

    def _inspect_price_plan(self, technical_report_sha: str) -> _Inspection:
        cfg = self.config
        report, _ = _read_json(cfg.price_plan_report)
        _check_common(
            report, name="price_plan", source=None, symbol=cfg.symbol, as_of=cfg.as_of_date
        )
        _, input_sha = _hash_path(cfg.price_plan_input, cfg.input_root)
        _check_hash(input_sha, report.get("input_sha256"), "price_plan.input_sha256")
        plan_rel, plan_sha = _hash_path(cfg.price_plan, cfg.input_root)
        _check_hash(plan_sha, report.get("output_sha256"), "price_plan.output_sha256")
        actual_technical_report_sha = sha256_bytes(cfg.technical_report.read_bytes())
        _check_hash(
            actual_technical_report_sha,
            report.get("technical_report_sha256"),
            "price_plan.technical_report_sha256",
        )
        if actual_technical_report_sha != technical_report_sha:
            raise BundleError("price plan references a different technical report")
        plan, _ = _read_json(cfg.price_plan)
        _check_snapshot_point_in_time(
            plan, name="price_plan", symbol=cfg.symbol, as_of=cfg.as_of_date
        )
        if report.get("status") != "ready" or report.get("price_plan_ready") is not True:
            raise BundleError("price plan is not ready")
        report_rel, report_sha = _hash_path(cfg.price_plan_report, cfg.input_root)
        return _Inspection(
            EvidenceEntry(
                name="price_plan",
                report_path=report_rel,
                report_sha256=report_sha,
                artifact_paths=(plan_rel,),
                artifact_sha256=(plan_sha,),
                source=str(report.get("source", "technical-price-plan")),
                symbol=cfg.symbol,
                as_of=str(report["as_of"]),
                status=str(report["status"]),
                ready=True,
            ),
            {
                "status": report.get("status"),
                "price_plan_version": report.get("price_plan_version"),
                "trade_date": report.get("trade_date"),
            },
        )

    def _inspect_fundamental(
        self, *, kind: str, snapshot_path: Path, report_path: Path, ready_field: str
    ) -> _Inspection:
        cfg = self.config
        report, _ = _read_json(report_path)
        _check_common(
            report, name=kind, source=f"baostock-{kind}", symbol=cfg.symbol, as_of=cfg.as_of_date
        )
        snapshot_rel, snapshot_sha = _hash_path(snapshot_path, cfg.input_root)
        _check_hash(snapshot_sha, report.get("snapshot_sha256"), f"{kind}.snapshot_sha256")
        raw_path = report_path.with_name("raw_response.json")
        raw_rel, raw_sha = _hash_path(raw_path, cfg.input_root)
        _check_hash(raw_sha, report.get("raw_response_sha256"), f"{kind}.raw_response_sha256")
        snapshot, _ = _read_json(snapshot_path)
        _check_snapshot_point_in_time(snapshot, name=kind, symbol=cfg.symbol, as_of=cfg.as_of_date)
        if report.get("status") != "ready" or report.get(ready_field) is not True:
            raise BundleError(f"{kind} snapshot is not ready")
        report_rel, report_sha = _hash_path(report_path, cfg.input_root)
        return _Inspection(
            EvidenceEntry(
                name=kind,
                report_path=report_rel,
                report_sha256=report_sha,
                artifact_paths=(snapshot_rel, raw_rel),
                artifact_sha256=(snapshot_sha, raw_sha),
                source=str(report["source"]),
                symbol=cfg.symbol,
                as_of=str(report["as_of"]),
                status=str(report["status"]),
                ready=True,
            ),
            {
                "status": report.get("status"),
                "selected_published_date": report.get("selected_published_date"),
                "selected_report_date": report.get("selected_report_date"),
                ready_field: report.get(ready_field),
            },
        )

    def _inspect_announcements(self) -> _Inspection:
        cfg = self.config
        report, _ = _read_json(cfg.announcements_report)
        _check_common(
            report,
            name="announcements",
            source="cninfo-announcements",
            symbol=cfg.symbol,
            as_of=cfg.as_of_date,
        )
        request_rel, request_sha = _hash_path(
            cfg.announcements_report.with_name("request.json"), cfg.input_root
        )
        _check_hash(request_sha, report.get("request_sha256"), "announcements.request_sha256")
        raw_rel, raw_sha = _hash_path(
            cfg.announcements_report.with_name("raw_response.json"), cfg.input_root
        )
        _check_hash(raw_sha, report.get("raw_response_sha256"), "announcements.raw_response_sha256")
        snapshot_rel, snapshot_sha = _hash_path(cfg.announcements_snapshot, cfg.input_root)
        _check_hash(snapshot_sha, report.get("snapshot_sha256"), "announcements.snapshot_sha256")
        snapshot, _ = _read_json(cfg.announcements_snapshot)
        _check_snapshot_point_in_time(
            snapshot, name="announcements", symbol=cfg.symbol, as_of=cfg.as_of_date
        )
        status = str(report.get("status"))
        if status == "ready":
            ready = report.get("announcement_ready") is True
        elif status == "empty":
            ready = True
        else:
            raise BundleError(f"announcements status is not usable: {status}")
        if report.get("decision_ready") is not False:
            raise BundleError("announcements decision_ready must remain false")
        report_rel, report_sha = _hash_path(cfg.announcements_report, cfg.input_root)
        return _Inspection(
            EvidenceEntry(
                name="announcements",
                report_path=report_rel,
                report_sha256=report_sha,
                artifact_paths=(snapshot_rel, request_rel, raw_rel),
                artifact_sha256=(snapshot_sha, request_sha, raw_sha),
                source=str(report["source"]),
                symbol=cfg.symbol,
                as_of=str(report["as_of"]),
                status=status,
                ready=ready,
            ),
            {
                "status": status,
                "record_count": report.get("record_count"),
                "selected_announcement_ids": report.get("selected_announcement_ids", []),
            },
        )

    def build(self) -> tuple[AnalysisInputBundle, AnalysisInputReport]:
        cfg = self.config
        inspections: list[_Inspection] = []
        try:
            for name, path in cfg.paths.items():
                _safe_relative(path, cfg.input_root)
                if not path.exists():
                    raise BundleError(f"missing input artifact: {name}")
            market = self._inspect_market()
            technical = self._inspect_technical()
            price_plan = self._inspect_price_plan(sha256_bytes(cfg.technical_report.read_bytes()))
            profitability = self._inspect_fundamental(
                kind="profitability",
                snapshot_path=cfg.profitability_snapshot,
                report_path=cfg.profitability_report,
                ready_field="fundamental_ready",
            )
            growth = self._inspect_fundamental(
                kind="growth",
                snapshot_path=cfg.growth_snapshot,
                report_path=cfg.growth_report,
                ready_field="growth_ready",
            )
            announcements = self._inspect_announcements()
            inspections = [market, technical, price_plan, profitability, growth, announcements]
            if not all(item.evidence.ready for item in inspections):
                raise BundleError("one or more upstream evidence entries are not ready")
            bundle = AnalysisInputBundle(
                schema_version=SCHEMA_VERSION,
                bundle_version=cfg.bundle_version,
                source=self.name,
                symbol=cfg.symbol,
                as_of=cfg.as_of.isoformat(),
                as_of_date=cfg.as_of_date.isoformat(),
                evidence=tuple(item.evidence for item in inspections),
                summaries=tuple((item.evidence.name, item.summary) for item in inspections),
                analysis_input_ready=True,
                decision_ready=False,
            )
            return bundle, AnalysisInputReport(
                schema_version=SCHEMA_VERSION,
                bundle_version=cfg.bundle_version,
                source=self.name,
                symbol=cfg.symbol,
                as_of=cfg.as_of.isoformat(),
                bundle_sha256="",
                status=BundleState.READY.value,
                analysis_input_ready=True,
                decision_ready=False,
                issues=(),
            )
        except BundleError as exc:
            bundle = AnalysisInputBundle(
                schema_version=SCHEMA_VERSION,
                bundle_version=cfg.bundle_version,
                source=self.name,
                symbol=cfg.symbol,
                as_of=cfg.as_of.isoformat(),
                as_of_date=cfg.as_of_date.isoformat(),
                evidence=tuple(item.evidence for item in inspections),
                summaries=tuple((item.evidence.name, item.summary) for item in inspections),
                analysis_input_ready=False,
                decision_ready=False,
            )
            return bundle, AnalysisInputReport(
                schema_version=SCHEMA_VERSION,
                bundle_version=cfg.bundle_version,
                source=self.name,
                symbol=cfg.symbol,
                as_of=cfg.as_of.isoformat(),
                bundle_sha256="",
                status=BundleState.INVALID.value,
                analysis_input_ready=False,
                decision_ready=False,
                issues=(("INVALID", str(exc)),),
            )

    def capture(self, output_dir: Path) -> dict[str, object]:
        bundle, report = self.build()
        bundle_bytes = _json_bytes(bundle.to_mapping())
        report = AnalysisInputReport(
            schema_version=report.schema_version,
            bundle_version=report.bundle_version,
            source=report.source,
            symbol=report.symbol,
            as_of=report.as_of,
            bundle_sha256=sha256_bytes(bundle_bytes),
            status=report.status,
            analysis_input_ready=report.analysis_input_ready,
            decision_ready=False,
            issues=report.issues,
        )
        report_bytes = _json_bytes(report.to_mapping())
        write_atomic(output_dir / "analysis_input_bundle.json", bundle_bytes)
        write_atomic(output_dir / "analysis_input_report.json", report_bytes)
        return report.to_mapping()
