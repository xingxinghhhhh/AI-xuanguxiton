"""Baostock quarterly profitability snapshots with publication-date gating."""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

from ..market.replay import sha256_bytes, write_atomic

SYMBOL_PATTERN = re.compile(r"^(?P<code>\d{6})\.(?P<market>SH|SZ)$", re.IGNORECASE)
PROFITABILITY_FIELDS = (
    "code",
    "pubDate",
    "statDate",
    "roeAvg",
    "npMargin",
    "netProfit",
    "epsTTM",
)
OPTIONAL_PROFITABILITY_FIELDS = ("gpMargin",)
OUTPUT_QUANTUM = Decimal("0.00000001")


class ProfitabilityError(RuntimeError):
    """Raised when a profitability response cannot be safely captured."""


class ProfitabilityDataError(ProfitabilityError):
    """Raised when provider rows cannot be represented by the contract."""


class ProfitabilityState(StrEnum):
    READY = "ready"
    NO_VALID_REPORT = "no_valid_report"
    FUTURE_ONLY = "future_only"
    PUBLICATION_DATE_UNKNOWN = "publication_date_unknown"
    PROVIDER_ERROR = "provider_error"
    INVALID = "invalid"


class ProfitabilityResponse(Protocol):
    error_code: str
    error_msg: str
    fields: list[str]

    def next(self) -> bool:
        """Move to the next response row."""

    def get_row_data(self) -> list[str]:
        """Return the current response row."""


@dataclass(frozen=True, slots=True)
class BaostockProfitabilityConfig:
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
            raise ProfitabilityError("year must be at least 2000")
        if not 1 <= self.start_quarter <= 4 or not 1 <= self.end_quarter <= 4:
            raise ProfitabilityError("quarter must be between 1 and 4")
        if (self.start_year, self.start_quarter) > (self.end_year, self.end_quarter):
            raise ProfitabilityError("start period must be on or before end period")
        object.__setattr__(self, "symbol", normalized_symbol)

    @property
    def provider_symbol(self) -> str:
        return provider_symbol_for(self.symbol)

    @property
    def periods(self) -> tuple[tuple[int, int], ...]:
        current_year, current_quarter = self.start_year, self.start_quarter
        result: list[tuple[int, int]] = []
        while (current_year, current_quarter) <= (self.end_year, self.end_quarter):
            result.append((current_year, current_quarter))
            if current_quarter == 4:
                current_year += 1
                current_quarter = 1
            else:
                current_quarter += 1
        return tuple(result)

    def request_mapping(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "provider_symbol": self.provider_symbol,
            "as_of": self.as_of.isoformat(),
            "start_year": self.start_year,
            "start_quarter": self.start_quarter,
            "end_year": self.end_year,
            "end_quarter": self.end_quarter,
            "periods": [
                {"year": year, "quarter": quarter} for year, quarter in self.periods
            ],
            "interface": "query_profit_data",
        }


@dataclass(frozen=True, slots=True)
class ProfitabilityRecord:
    symbol: str
    published_date: date | None
    report_date: date
    roe: Decimal
    net_margin: Decimal
    net_profit: Decimal
    eps: Decimal
    gross_margin: Decimal | None


@dataclass(frozen=True, slots=True)
class ProfitabilitySnapshot:
    schema_version: str
    source: str
    symbol: str
    as_of: str
    published_date: str
    report_date: str
    roe: Decimal
    net_margin: Decimal
    net_profit: Decimal
    eps: Decimal
    gross_margin: Decimal | None
    fundamental_ready: bool
    decision_ready: bool

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "published_date": self.published_date,
            "report_date": self.report_date,
            "roe": _format_decimal(self.roe),
            "net_margin": _format_decimal(self.net_margin),
            "net_profit": _format_decimal(self.net_profit),
            "eps": _format_decimal(self.eps),
            "gross_margin": (
                _format_decimal(self.gross_margin) if self.gross_margin is not None else None
            ),
            "fundamental_ready": self.fundamental_ready,
            "decision_ready": self.decision_ready,
        }


@dataclass(frozen=True, slots=True)
class ProfitabilityIssue:
    code: str
    message: str

    def to_mapping(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProfitabilityReport:
    schema_version: str
    source: str
    symbol: str
    as_of: str
    raw_response_sha256: str | None
    snapshot_sha256: str
    selected_published_date: str | None
    selected_report_date: str | None
    status: str
    fundamental_ready: bool
    decision_ready: bool
    issues: tuple[ProfitabilityIssue, ...]

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
            "fundamental_ready": self.fundamental_ready,
            "decision_ready": self.decision_ready,
            "issues": [issue.to_mapping() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class ProfitabilityComputation:
    snapshot: ProfitabilitySnapshot | None
    status: ProfitabilityState
    issues: tuple[ProfitabilityIssue, ...]


@dataclass(frozen=True, slots=True)
class ProfitabilityCapture:
    records: tuple[ProfitabilityRecord, ...]
    request: dict[str, object]
    raw_response: dict[str, object]


def provider_symbol_for(symbol: str) -> str:
    match = SYMBOL_PATTERN.fullmatch(symbol.strip().upper())
    if not match:
        raise ProfitabilityError("symbol must match six digits followed by .SH or .SZ")
    return f"{match.group('market').lower()}.{match.group('code')}"


def _decimal(value: str, field: str) -> Decimal:
    if not value.strip():
        raise ProfitabilityDataError(f"{field} is empty")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ProfitabilityDataError(f"{field} must be a decimal") from exc
    if not parsed.is_finite():
        raise ProfitabilityDataError(f"{field} must be finite")
    return parsed


def _format_decimal(value: Decimal) -> str:
    return format(value.quantize(OUTPUT_QUANTUM, rounding=ROUND_HALF_UP), "f")


def _parse_record(
    fields: list[str], row: list[str], symbol: str
) -> ProfitabilityRecord:
    required_fields = set(PROFITABILITY_FIELDS)
    missing_fields = required_fields - set(fields)
    if missing_fields:
        raise ProfitabilityDataError(
            f"query response missing fields: {', '.join(sorted(missing_fields))}"
        )
    if len(row) != len(fields):
        raise ProfitabilityDataError("query response row length does not match fields")
    values: Mapping[str, str] = dict(zip(fields, row, strict=True))
    if values["code"].strip().lower() != provider_symbol_for(symbol):
        raise ProfitabilityDataError("query response code does not match requested symbol")
    try:
        published_date = (
            date.fromisoformat(values["pubDate"]) if values["pubDate"].strip() else None
        )
        report_date = date.fromisoformat(values["statDate"])
    except ValueError as exc:
        raise ProfitabilityDataError("pubDate and statDate must be ISO dates") from exc
    gross_margin = (
        _decimal(values["gpMargin"], "gpMargin")
        if "gpMargin" in values and values["gpMargin"].strip()
        else None
    )
    return ProfitabilityRecord(
        symbol=symbol,
        published_date=published_date,
        report_date=report_date,
        roe=_decimal(values["roeAvg"], "roeAvg"),
        net_margin=_decimal(values["npMargin"], "npMargin"),
        net_profit=_decimal(values["netProfit"], "netProfit"),
        eps=_decimal(values["epsTTM"], "epsTTM"),
        gross_margin=gross_margin,
    )


def compute_profitability_snapshot(
    records: list[ProfitabilityRecord], *, as_of: date
) -> ProfitabilityComputation:
    if not records:
        return ProfitabilityComputation(
            None,
            ProfitabilityState.NO_VALID_REPORT,
            (ProfitabilityIssue("NO_REPORT", "no profitability records were returned"),),
        )
    grouped: dict[date, ProfitabilityRecord] = {}
    for record in records:
        previous = grouped.get(record.report_date)
        if previous is not None and previous != record:
            return ProfitabilityComputation(
                None,
                ProfitabilityState.INVALID,
                (
                    ProfitabilityIssue(
                        "CONFLICTING_REPORT", "same report date has conflicting records"
                    ),
                ),
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
            return ProfitabilityComputation(
                None,
                ProfitabilityState.PUBLICATION_DATE_UNKNOWN,
                (
                    ProfitabilityIssue(
                        "PUBLICATION_DATE_UNKNOWN", "a report has no publication date"
                    ),
                ),
            )
        if known_dates and all(published_date > as_of for published_date in known_dates):
            return ProfitabilityComputation(
                None,
                ProfitabilityState.FUTURE_ONLY,
                (ProfitabilityIssue("FUTURE_REPORT", "all reports were published after as_of"),),
            )
        return ProfitabilityComputation(
            None,
            ProfitabilityState.PUBLICATION_DATE_UNKNOWN,
            (
                ProfitabilityIssue(
                    "PUBLICATION_DATE_UNKNOWN", "no report has a usable publication date"
                ),
            ),
        )
    selected = max(eligible, key=lambda record: (record.published_date, record.report_date))
    snapshot = ProfitabilitySnapshot(
        schema_version="1.0",
        source="baostock-profitability",
        symbol=selected.symbol,
        as_of=as_of.isoformat(),
        published_date=selected.published_date.isoformat(),
        report_date=selected.report_date.isoformat(),
        roe=selected.roe,
        net_margin=selected.net_margin,
        net_profit=selected.net_profit,
        eps=selected.eps,
        gross_margin=selected.gross_margin,
        fundamental_ready=True,
        decision_ready=False,
    )
    return ProfitabilityComputation(snapshot, ProfitabilityState.READY, ())


class BaostockProfitabilitySource:
    """Read-only, single-symbol quarterly profitability source."""

    def __init__(self, config: BaostockProfitabilityConfig, client: Any | None = None) -> None:
        self.config = config
        self._client = client

    @property
    def name(self) -> str:
        return "baostock-profitability"

    def _client_module(self) -> ModuleType | Any:
        if self._client is not None:
            return self._client
        try:
            return importlib.import_module("baostock")
        except ImportError as exc:
            raise ProfitabilityError(
                "Baostock is optional; install with `pip install -e .[baostock]`"
            ) from exc

    def _query(self) -> ProfitabilityCapture:
        client = self._client_module()
        logged_in = False
        queries: list[dict[str, object]] = []
        records: list[ProfitabilityRecord] = []
        try:
            login_result = client.login()
            _require_success(login_result, "login")
            logged_in = True
            for year, quarter in self.config.periods:
                response: ProfitabilityResponse = client.query_profit_data(
                    code=self.config.provider_symbol, year=year, quarter=quarter
                )
                _require_success(response, "query_profit_data")
                fields = [str(field) for field in response.fields]
                if not fields:
                    raise ProfitabilityError("query response did not include fields")
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
            return ProfitabilityCapture(
                tuple(records), self.config.request_mapping(), {"queries": queries}
            )
        except ProfitabilityError:
            raise
        except Exception as exc:
            raise ProfitabilityError(f"Baostock request failed: {exc}") from exc
        finally:
            if logged_in:
                try:
                    client.logout()
                except Exception as exc:  # pragma: no cover - provider-specific cleanup
                    raise ProfitabilityError(f"Baostock logout failed: {exc}") from exc

    def capture(self, output_dir: Path) -> dict[str, object]:
        request = self.config.request_mapping()
        raw_bytes = b""
        try:
            capture = self._query()
            raw_bytes = (
                json.dumps(
                    capture.raw_response, ensure_ascii=False, sort_keys=True, indent=2
                )
                + "\n"
            ).encode("utf-8")
            computation = compute_profitability_snapshot(
                list(capture.records), as_of=self.config.as_of
            )
            snapshot_bytes = (
                json.dumps(
                    computation.snapshot.to_mapping(),
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8") if computation.snapshot else b""
            report = ProfitabilityReport(
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
                fundamental_ready=computation.snapshot is not None,
                decision_ready=False,
                issues=computation.issues,
            )
        except ProfitabilityDataError as exc:
            snapshot_bytes = b""
            report = ProfitabilityReport(
                schema_version="1.0",
                source=self.name,
                symbol=self.config.symbol,
                as_of=self.config.as_of.isoformat(),
                raw_response_sha256=sha256_bytes(raw_bytes) if raw_bytes else None,
                snapshot_sha256=sha256_bytes(snapshot_bytes),
                selected_published_date=None,
                selected_report_date=None,
                status=ProfitabilityState.INVALID.value,
                fundamental_ready=False,
                decision_ready=False,
                issues=(ProfitabilityIssue("INVALID", str(exc)),),
            )
        except ProfitabilityError as exc:
            snapshot_bytes = b""
            report = ProfitabilityReport(
                schema_version="1.0",
                source=self.name,
                symbol=self.config.symbol,
                as_of=self.config.as_of.isoformat(),
                raw_response_sha256=sha256_bytes(raw_bytes) if raw_bytes else None,
                snapshot_sha256=sha256_bytes(snapshot_bytes),
                selected_published_date=None,
                selected_report_date=None,
                status=ProfitabilityState.PROVIDER_ERROR.value,
                fundamental_ready=False,
                decision_ready=False,
                issues=(ProfitabilityIssue("PROVIDER_ERROR", str(exc)),),
            )
        request_bytes = (
            json.dumps(request, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        report_bytes = (
            json.dumps(report.to_mapping(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        write_atomic(output_dir / "request.json", request_bytes)
        write_atomic(output_dir / "raw_response.json", raw_bytes)
        write_atomic(output_dir / "profitability_snapshot.json", snapshot_bytes)
        write_atomic(output_dir / "profitability_report.json", report_bytes)
        return report.to_mapping()


def _require_success(result: Any, operation: str) -> None:
    error_code = str(getattr(result, "error_code", ""))
    if error_code != "0":
        error_msg = str(getattr(result, "error_msg", "unknown provider error"))
        raise ProfitabilityError(f"Baostock {operation} failed ({error_code}): {error_msg}")
