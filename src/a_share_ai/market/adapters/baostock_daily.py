"""Read-only Baostock daily-bar adapter with raw-response capture."""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ..contracts import ContractError, DailyBar, MarketDataSource
from ..replay import canonical_jsonl, sha256_bytes, write_atomic

SHANGHAI = ZoneInfo("Asia/Shanghai")
SYMBOL_PATTERN = re.compile(r"^(?P<code>\d{6})\.(?P<market>SH|SZ)$", re.IGNORECASE)
BAOSTOCK_FIELDS = ("date", "code", "open", "high", "low", "close", "volume", "amount")
BAOSTOCK_FIELDS_STRING = ",".join(BAOSTOCK_FIELDS)


class BaostockError(RuntimeError):
    """Raised when Baostock cannot return a valid read-only response."""


class BaostockDependencyError(BaostockError):
    """Raised when the optional Baostock package is not installed."""


class BaostockResponse(Protocol):
    error_code: str
    error_msg: str
    fields: list[str]

    def next(self) -> bool:
        """Move to the next response row."""

    def get_row_data(self) -> list[str]:
        """Return the current response row."""


def provider_symbol_for(symbol: str) -> str:
    """Map the internal ``600000.SH``/``000001.SZ`` form to Baostock form."""

    match = SYMBOL_PATTERN.fullmatch(symbol.strip().upper())
    if not match:
        raise BaostockError("symbol must match six digits followed by .SH or .SZ")
    return f"{match.group('market').lower()}.{match.group('code')}"


@dataclass(frozen=True, slots=True)
class BaostockDailyConfig:
    symbol: str
    start_date: date
    end_date: date
    received_at: datetime
    adjustflag: str = "3"

    def __post_init__(self) -> None:
        normalized_symbol = self.symbol.strip().upper()
        provider_symbol_for(normalized_symbol)
        if self.start_date > self.end_date:
            raise BaostockError("start_date must be on or before end_date")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise BaostockError("received_at must include a timezone")
        if self.adjustflag != "3":
            raise BaostockError("this MVP fixes adjustflag to 3 (no adjustment)")
        object.__setattr__(self, "symbol", normalized_symbol)

    @property
    def provider_symbol(self) -> str:
        return provider_symbol_for(self.symbol)

    def request_mapping(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "provider_symbol": self.provider_symbol,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "frequency": "d",
            "adjustflag": self.adjustflag,
            "fields": BAOSTOCK_FIELDS_STRING,
            "received_at": self.received_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class BaostockCapture:
    bars: tuple[DailyBar, ...]
    request: dict[str, str]
    raw_response: dict[str, object]


class BaostockDailySource(MarketDataSource):
    """A single-symbol, historical, no-adjustment Baostock source."""

    def __init__(self, config: BaostockDailyConfig, client: Any | None = None) -> None:
        self.config = config
        self._client = client

    @property
    def name(self) -> str:
        return "baostock-daily"

    def _client_module(self) -> ModuleType | Any:
        if self._client is not None:
            return self._client
        try:
            return importlib.import_module("baostock")
        except ImportError as exc:
            raise BaostockDependencyError(
                "Baostock is optional; install with `pip install -e .[baostock]`"
            ) from exc

    def _query(self) -> BaostockCapture:
        client = self._client_module()
        logged_in = False
        try:
            login_result = client.login()
            _require_success(login_result, "login")
            logged_in = True
            response: BaostockResponse = client.query_history_k_data_plus(
                self.config.provider_symbol,
                BAOSTOCK_FIELDS_STRING,
                start_date=self.config.start_date.isoformat(),
                end_date=self.config.end_date.isoformat(),
                frequency="d",
                adjustflag=self.config.adjustflag,
            )
            _require_success(response, "query_history_k_data_plus")
            fields = [str(field) for field in response.fields]
            if not fields:
                raise BaostockError("query response did not include fields")
            rows: list[list[str]] = []
            while response.next():
                row = [str(value) for value in response.get_row_data()]
                if len(row) != len(fields):
                    raise BaostockError("query response row length does not match fields")
                rows.append(row)
            raw_response: dict[str, object] = {
                "error_code": str(response.error_code),
                "error_msg": str(response.error_msg),
                "fields": fields,
                "rows": rows,
            }
            bars = tuple(self._bars_from_rows(fields, rows))
            return BaostockCapture(
                bars=bars,
                request=self.config.request_mapping(),
                raw_response=raw_response,
            )
        except BaostockError:
            raise
        except Exception as exc:
            raise BaostockError(f"Baostock request failed: {exc}") from exc
        finally:
            if logged_in:
                try:
                    client.logout()
                except Exception as exc:  # pragma: no cover - provider-specific cleanup
                    raise BaostockError(f"Baostock logout failed: {exc}") from exc

    def iter_daily_bars(self):
        """Yield normalized bars after one login/query/logout cycle."""

        yield from self._query().bars

    def capture(self, output_dir: Path) -> dict[str, object]:
        """Persist request, raw response, normalized bars and deterministic hashes."""

        capture = self._query()
        normalized_bytes = canonical_jsonl(capture.bars)
        raw_bytes = (
            json.dumps(capture.raw_response, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        request_bytes = (
            json.dumps(capture.request, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        report: dict[str, object] = {
            "schema_version": "1.0",
            "source": self.name,
            "request": capture.request,
            "bar_count": len(capture.bars),
            "raw_response_sha256": sha256_bytes(raw_bytes),
            "normalized_output_sha256": sha256_bytes(normalized_bytes),
            "decision_ready": False,
            "error": None,
        }
        write_atomic(output_dir / "request.json", request_bytes)
        write_atomic(output_dir / "raw_response.json", raw_bytes)
        write_atomic(output_dir / "normalized_daily.jsonl", normalized_bytes)
        write_atomic(
            output_dir / "capture_report.json",
            (
                json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8"),
        )
        return report

    def _bars_from_rows(self, fields: list[str], rows: list[list[str]]) -> list[DailyBar]:
        required = set(BAOSTOCK_FIELDS)
        missing_fields = required - set(fields)
        if missing_fields:
            raise BaostockError(
                f"query response missing fields: {', '.join(sorted(missing_fields))}"
            )
        bars: list[DailyBar] = []
        for row in rows:
            values: Mapping[str, str] = dict(zip(fields, row, strict=True))
            if any(not values[field].strip() for field in BAOSTOCK_FIELDS):
                raise BaostockError("query response contains an empty required field")
            try:
                trade_date = date.fromisoformat(values["date"])
                market_time = datetime.combine(
                    trade_date, time(15, 0), tzinfo=SHANGHAI
                ).isoformat()
                bars.append(
                    DailyBar.from_mapping(
                        {
                            "schema_version": "1.0",
                            "symbol": self.config.symbol,
                            "trade_date": values["date"],
                            "open": values["open"],
                            "high": values["high"],
                            "low": values["low"],
                            "close": values["close"],
                            "volume": values["volume"],
                            "amount": values["amount"],
                            "source": "baostock",
                            "market_time": market_time,
                            "received_at": self.config.received_at.isoformat(),
                            "data_status": "connected",
                        }
                    )
                )
            except (ContractError, ValueError) as exc:
                raise BaostockError(f"invalid Baostock row: {exc}") from exc
        return bars


def _require_success(result: Any, operation: str) -> None:
    error_code = str(getattr(result, "error_code", ""))
    if error_code != "0":
        error_msg = str(getattr(result, "error_msg", "unknown provider error"))
        raise BaostockError(f"Baostock {operation} failed ({error_code}): {error_msg}")
