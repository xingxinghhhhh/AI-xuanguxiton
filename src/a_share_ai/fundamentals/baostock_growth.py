"""Baostock quarterly growth snapshots with publication-date gating."""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

from ..market.replay import sha256_bytes, write_atomic

SYMBOL_PATTERN = re.compile(r"^(?P<code>\d{6})\.(?P<market>SH|SZ)$", re.IGNORECASE)
GROWTH_FIELDS = (
    "code",
    "pubDate",
    "statDate",
    "YOYEquity",
    "YOYAsset",
    "YOYNI",
    "YOYPNI",
    "YOYEPSBasic",
    "YOYOR",
    "YOYGR",
)
GROWTH_VALUE_NAMES = (
    "yoy_equity",
    "yoy_asset",
    "yoy_net_income",
    "yoy_parent_net_income",
    "yoy_eps_basic",
    "yoy_operating_revenue",
    "yoy_gross_revenue",
)
OUTPUT_QUANTUM = Decimal("0.00000001")


class GrowthError(RuntimeError):
    """Raised when a growth response cannot be safely captured."""


class GrowthDataError(GrowthError):
    """Raised when provider rows cannot be represented by the contract."""


class GrowthState(StrEnum):
    READY = "ready"
    NO_VALID_REPORT = "no_valid_report"
    FUTURE_ONLY = "future_only"
    PUBLICATION_DATE_UNKNOWN = "publication_date_unknown"
    PROVIDER_ERROR = "provider_error"
    INVALID = "invalid"


class GrowthResponse(Protocol):
    error_code: str
    error_msg: str
    fields: list[str]

    def next(self) -> bool:
        """Move to the next response row."""

    def get_row_data(self) -> list[str]:
        """Return the current response row."""


@dataclass(frozen=True, slots=True)
class BaostockGrowthConfig:
    symbol: str
    as_of: date
    start_year: int
    start_quarter: int
    end_year: int
    end_quarter: int

    def __post_init__(self) -> None:
        normalized_symbol = self.symbol.strip().upper()
        provider_symbol_for(normalized_symbol)
        if self.start_year < 2000 or self.end_year < 2000:
            raise GrowthError("year must be at least 2000")
        if not 1 <= self.start_quarter <= 4 or not 1 <= self.end_quarter <= 4:
            raise GrowthError("quarter must be between 1 and 4")
        if (self.start_year, self.start_quarter) > (self.end_year, self.end_quarter):
            raise GrowthError("start period must be on or before end period")
        object.__setattr__(self, "symbol", normalized_symbol)

    @property
    def provider_symbol(self) -> str:
        return provider_symbol_for(self.symbol)

    @property
    def periods(self) -> tuple[tuple[int, int], ...]:
        year, quarter = self.start_year, self.start_quarter
        periods: list[tuple[int, int]] = []
        while (year, quarter) <= (self.end_year, self.end_quarter):
            periods.append((year, quarter))
            if quarter == 4:
                year, quarter = year + 1, 1
            else:
                quarter += 1
        return tuple(periods)

    def request_mapping(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "provider_symbol": self.provider_symbol,
            "as_of": self.as_of.isoformat(),
            "start_year": self.start_year,
            "start_quarter": self.start_quarter,
            "end_year": self.end_year,
            "end_quarter": self.end_quarter,
            "periods": [{"year": y, "quarter": q} for y, q in self.periods],
            "interface": "query_growth_data",
        }


@dataclass(frozen=True, slots=True)
class GrowthRecord:
    symbol: str
    published_date: date | None
    report_date: date
    values: tuple[tuple[str, Decimal | None], ...]


@dataclass(frozen=True, slots=True)
class GrowthSnapshot:
    schema_version: str
    source: str
    symbol: str
    as_of: str
    published_date: str
    report_date: str
    values: tuple[tuple[str, Decimal | None], ...]
    growth_ready: bool
    decision_ready: bool

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": self.schema_version,
            "source": self.source,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "published_date": self.published_date,
            "report_date": self.report_date,
            "growth_ready": self.growth_ready,
            "decision_ready": self.decision_ready,
        }
        result.update(
            {
                name: _format_decimal(value) if value is not None else None
                for name, value in self.values
            }
        )
        return result


@dataclass(frozen=True, slots=True)
class GrowthIssue:
    code: str
    message: str

    def to_mapping(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GrowthReport:
    schema_version: str
    source: str
    symbol: str
    as_of: str
    raw_response_sha256: str | None
    snapshot_sha256: str
    selected_published_date: str | None
    selected_report_date: str | None
    status: str
    growth_ready: bool
    decision_ready: bool
    issues: tuple[GrowthIssue, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "raw_response_sha256": self.raw_response_sha256,
            "snapshot_sha256": self.snapshot_sha256,
            "selected_published_date": self.selected_published_date,
            "selected_report_date": self.selected_report_date,
            "status": self.status,
            "growth_ready": self.growth_ready,
            "decision_ready": self.decision_ready,
            "issues": [issue.to_mapping() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class GrowthComputation:
    snapshot: GrowthSnapshot | None
    status: GrowthState
    issues: tuple[GrowthIssue, ...]


@dataclass(frozen=True, slots=True)
class GrowthCapture:
    records: tuple[GrowthRecord, ...]
    request: dict[str, object]
    raw_response: dict[str, object]


def provider_symbol_for(symbol: str) -> str:
    match = SYMBOL_PATTERN.fullmatch(symbol.strip().upper())
    if not match:
        raise GrowthError("symbol must match six digits followed by .SH or .SZ")
    return f"{match.group('market').lower()}.{match.group('code')}"


def _decimal(value: str, field: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise GrowthDataError(f"{field} must be a decimal") from exc
    if not parsed.is_finite():
        raise GrowthDataError(f"{field} must be finite")
    return parsed


def _format_decimal(value: Decimal) -> str:
    return format(value.quantize(OUTPUT_QUANTUM, rounding=ROUND_HALF_UP), "f")


def _parse_record(fields: list[str], row: list[str], symbol: str) -> GrowthRecord:
    required = {"code", "pubDate", "statDate"}
    missing = required - set(fields)
    if missing:
        raise GrowthDataError(f"query response missing fields: {', '.join(sorted(missing))}")
    if len(row) != len(fields):
        raise GrowthDataError("query response row length does not match fields")
    values = dict(zip(fields, row, strict=True))
    if values["code"].strip().lower() != provider_symbol_for(symbol):
        raise GrowthDataError("query response code does not match requested symbol")
    try:
        published_date = (
            date.fromisoformat(values["pubDate"]) if values["pubDate"].strip() else None
        )
        report_date = date.fromisoformat(values["statDate"])
    except ValueError as exc:
        raise GrowthDataError("pubDate and statDate must be ISO dates") from exc
    source_fields = (
        ("yoy_equity", "YOYEquity"),
        ("yoy_asset", "YOYAsset"),
        ("yoy_net_income", "YOYNI"),
        ("yoy_parent_net_income", "YOYPNI"),
        ("yoy_eps_basic", "YOYEPSBasic"),
        ("yoy_operating_revenue", "YOYOR"),
        ("yoy_gross_revenue", "YOYGR"),
    )
    normalized = tuple(
        (
            internal_name,
            _decimal(values[source_name], source_name)
            if source_name in values and values[source_name].strip()
            else None,
        )
        for internal_name, source_name in source_fields
    )
    return GrowthRecord(symbol, published_date, report_date, normalized)


def compute_growth_snapshot(records: list[GrowthRecord], *, as_of: date) -> GrowthComputation:
    if not records:
        return GrowthComputation(
            None,
            GrowthState.NO_VALID_REPORT,
            (GrowthIssue("NO_REPORT", "no growth records were returned"),),
        )
    grouped: dict[date, GrowthRecord] = {}
    for record in records:
        previous = grouped.get(record.report_date)
        if previous is not None and previous != record:
            return GrowthComputation(
                None,
                GrowthState.INVALID,
                (GrowthIssue("CONFLICTING_REPORT", "same report date has conflicting records"),),
            )
        grouped[record.report_date] = record
    unknown_publication = any(record.published_date is None for record in records)
    known_dates = [record.published_date for record in records if record.published_date is not None]
    eligible = [
        record
        for record in records
        if record.published_date is not None and record.published_date <= as_of
    ]
    if not eligible:
        if unknown_publication:
            return GrowthComputation(
                None,
                GrowthState.PUBLICATION_DATE_UNKNOWN,
                (GrowthIssue("PUBLICATION_DATE_UNKNOWN", "a report has no publication date"),),
            )
        if known_dates and all(published_date > as_of for published_date in known_dates):
            return GrowthComputation(
                None,
                GrowthState.FUTURE_ONLY,
                (GrowthIssue("FUTURE_REPORT", "all reports were published after as_of"),),
            )
        return GrowthComputation(
            None,
            GrowthState.NO_VALID_REPORT,
            (GrowthIssue("NO_REPORT", "no growth report is usable at as_of"),),
        )
    selected = max(eligible, key=lambda record: (record.published_date, record.report_date))
    snapshot = GrowthSnapshot(
        schema_version="1.0",
        source="baostock-growth",
        symbol=selected.symbol,
        as_of=as_of.isoformat(),
        published_date=selected.published_date.isoformat(),  # type: ignore[union-attr]
        report_date=selected.report_date.isoformat(),
        values=selected.values,
        growth_ready=True,
        decision_ready=False,
    )
    return GrowthComputation(snapshot, GrowthState.READY, ())


class BaostockGrowthSource:
    """Read-only, single-symbol quarterly growth source."""

    def __init__(self, config: BaostockGrowthConfig, client: Any | None = None) -> None:
        self.config = config
        self._client = client

    @property
    def name(self) -> str:
        return "baostock-growth"

    def _client_module(self) -> ModuleType | Any:
        if self._client is not None:
            return self._client
        try:
            return importlib.import_module("baostock")
        except ImportError as exc:
            raise GrowthError(
                "Baostock is optional; install with `pip install -e .[baostock]`"
            ) from exc

    def _query(self) -> GrowthCapture:
        client = self._client_module()
        logged_in = False
        queries: list[dict[str, object]] = []
        records: list[GrowthRecord] = []
        try:
            login_result = client.login()
            _require_success(login_result, "login")
            logged_in = True
            for year, quarter in self.config.periods:
                response: GrowthResponse = client.query_growth_data(
                    code=self.config.provider_symbol, year=year, quarter=quarter
                )
                _require_success(response, "query_growth_data")
                fields = [str(field) for field in response.fields]
                if not fields:
                    raise GrowthError("query response did not include fields")
                rows: list[list[str]] = []
                while response.next():
                    row = [str(value) for value in response.get_row_data()]
                    rows.append(row)
                    records.append(_parse_record(fields, row, self.config.symbol))
                queries.append(
                    {
                        "year": year,
                        "quarter": quarter,
                        "error_code": str(response.error_code),
                        "error_msg": str(response.error_msg),
                        "fields": fields,
                        "rows": rows,
                    }
                )
            return GrowthCapture(
                tuple(records), self.config.request_mapping(), {"queries": queries}
            )
        except GrowthError:
            raise
        except Exception as exc:
            raise GrowthError(f"Baostock request failed: {exc}") from exc
        finally:
            if logged_in:
                try:
                    client.logout()
                except Exception as exc:  # pragma: no cover
                    raise GrowthError(f"Baostock logout failed: {exc}") from exc

    def capture(self, output_dir: Path) -> dict[str, object]:
        request = self.config.request_mapping()
        raw_bytes = b""
        try:
            capture = self._query()
            raw_bytes = (
                json.dumps(capture.raw_response, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n"
            ).encode("utf-8")
            computation = compute_growth_snapshot(list(capture.records), as_of=self.config.as_of)
            snapshot_bytes = (
                json.dumps(
                    computation.snapshot.to_mapping(),
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8") if computation.snapshot else b""
            report = GrowthReport(
                schema_version="1.0",
                source=self.name,
                symbol=self.config.symbol,
                as_of=self.config.as_of.isoformat(),
                raw_response_sha256=sha256_bytes(raw_bytes),
                snapshot_sha256=sha256_bytes(snapshot_bytes),
                selected_published_date=(
                    computation.snapshot.published_date if computation.snapshot else None
                ),
                selected_report_date=(
                    computation.snapshot.report_date if computation.snapshot else None
                ),
                status=computation.status.value,
                growth_ready=computation.snapshot is not None,
                decision_ready=False,
                issues=computation.issues,
            )
        except GrowthDataError as exc:
            snapshot_bytes = b""
            report = _error_report(self, GrowthState.INVALID, "INVALID", exc, raw_bytes)
        except GrowthError as exc:
            snapshot_bytes = b""
            report = _error_report(
                self, GrowthState.PROVIDER_ERROR, "PROVIDER_ERROR", exc, raw_bytes
            )
        request_bytes = (
            json.dumps(request, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        report_bytes = (
            json.dumps(report.to_mapping(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        write_atomic(output_dir / "request.json", request_bytes)
        write_atomic(output_dir / "raw_response.json", raw_bytes)
        write_atomic(output_dir / "growth_snapshot.json", snapshot_bytes)
        write_atomic(output_dir / "growth_report.json", report_bytes)
        return report.to_mapping()


def _error_report(
    source: BaostockGrowthSource,
    state: GrowthState,
    code: str,
    error: Exception,
    raw_bytes: bytes,
) -> GrowthReport:
    return GrowthReport(
        schema_version="1.0",
        source=source.name,
        symbol=source.config.symbol,
        as_of=source.config.as_of.isoformat(),
        raw_response_sha256=sha256_bytes(raw_bytes) if raw_bytes else None,
        snapshot_sha256=sha256_bytes(b""),
        selected_published_date=None,
        selected_report_date=None,
        status=state.value,
        growth_ready=False,
        decision_ready=False,
        issues=(GrowthIssue(code, str(error)),),
    )


def _require_success(result: Any, operation: str) -> None:
    error_code = str(getattr(result, "error_code", ""))
    if error_code != "0":
        error_msg = str(getattr(result, "error_msg", "unknown provider error"))
        raise GrowthError(f"Baostock {operation} failed ({error_code}): {error_msg}")
