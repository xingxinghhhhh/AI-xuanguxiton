"""Read-only Baostock adapter for the fixed benchmark-index context."""

from __future__ import annotations

import importlib
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

from .adapters.baostock_daily import BaostockDependencyError
from .calendar import JsonTradingCalendarSource
from .market_context import (
    INDEX_SPECS,
    MarketContextConfig,
    MarketContextError,
    MarketIndexRecord,
    write_market_context,
)
from .replay import sha256_bytes

MARKET_CONTEXT_FIELDS = ("date", "code", "open", "high", "low", "close", "volume", "amount")
MARKET_CONTEXT_FIELDS_STRING = ",".join(MARKET_CONTEXT_FIELDS)


class MarketContextProviderError(RuntimeError):
    """Raised when Baostock cannot return a valid benchmark response."""


class MarketContextResponse(Protocol):
    error_code: str
    error_msg: str
    fields: list[str]

    def next(self) -> bool:
        """Move to the next response row."""

    def get_row_data(self) -> list[str]:
        """Return the current response row."""


class BaostockMarketContextSource:
    """Capture three fixed benchmark indexes in one login/query/logout cycle."""

    def __init__(self, config: MarketContextConfig, client: Any | None = None) -> None:
        self.config = config
        self._client = client

    def _client_module(self) -> ModuleType | Any:
        if self._client is not None:
            return self._client
        try:
            return importlib.import_module("baostock")
        except ImportError as exc:
            raise BaostockDependencyError(
                "Baostock is optional; install with `pip install -e .[baostock]`"
            ) from exc

    def _query(self) -> tuple[list[MarketIndexRecord], dict[str, Any], dict[str, Any]]:
        client = self._client_module()
        logged_in = False
        records: list[MarketIndexRecord] = []
        raw_response: dict[str, Any] = {}
        try:
            login_result = client.login()
            _require_success(login_result, "login")
            logged_in = True
            for symbol, provider_symbol, _name in INDEX_SPECS:
                response: MarketContextResponse = client.query_history_k_data_plus(
                    provider_symbol,
                    MARKET_CONTEXT_FIELDS_STRING,
                    start_date=self.config.start.isoformat(),
                    end_date=self.config.end.isoformat(),
                    frequency="d",
                    adjustflag="3",
                )
                _require_success(response, f"query {provider_symbol}")
                fields = [str(field) for field in response.fields]
                if not fields:
                    raise MarketContextProviderError(
                        f"query response did not include fields for {symbol}"
                    )
                rows: list[list[str]] = []
                while response.next():
                    row = [str(value) for value in response.get_row_data()]
                    if len(row) != len(fields):
                        raise MarketContextProviderError(f"row length mismatch for {symbol}")
                    rows.append(row)
                raw_response[symbol] = {
                    "error_code": str(response.error_code),
                    "error_msg": str(response.error_msg),
                    "fields": fields,
                    "rows": rows,
                }
                records.extend(self._records_from_rows(symbol, fields, rows))
            return records, self.config.request_mapping(), raw_response
        except (MarketContextError, MarketContextProviderError):
            raise
        except Exception as exc:
            raise MarketContextProviderError(f"Baostock request failed: {exc}") from exc
        finally:
            if logged_in:
                try:
                    client.logout()
                except Exception as exc:  # pragma: no cover - provider-specific cleanup
                    raise MarketContextProviderError(f"Baostock logout failed: {exc}") from exc

    def _records_from_rows(
        self, symbol: str, fields: list[str], rows: list[list[str]]
    ) -> list[MarketIndexRecord]:
        missing = set(MARKET_CONTEXT_FIELDS) - set(fields)
        if missing:
            raise MarketContextProviderError(
                f"query response missing fields for {symbol}: {', '.join(sorted(missing))}"
            )
        records: list[MarketIndexRecord] = []
        for row in rows:
            values = dict(zip(fields, row, strict=True))
            if any(not values[field].strip() for field in MARKET_CONTEXT_FIELDS):
                raise MarketContextProviderError(
                    f"query response contains empty field for {symbol}"
                )
            trade_date = date.fromisoformat(values["date"])
            record = MarketIndexRecord.from_mapping(
                {
                    "symbol": symbol,
                    "instrument_type": "index",
                    "trade_date": values["date"],
                    "open": values["open"],
                    "high": values["high"],
                    "low": values["low"],
                    "close": values["close"],
                    "volume": values["volume"],
                    "amount": values["amount"],
                    "source": "baostock",
                    "market_time": f"{trade_date.isoformat()}T15:00:00+08:00",
                    "received_at": self.config.received_at.isoformat(),
                    "data_status": "connected",
                }
            )
            records.append(record)
        return records

    def capture(self, *, calendar_path: Path, output_dir: Path) -> dict[str, Any]:
        """Capture, validate against a local calendar, and persist audit artifacts."""

        calendar_raw = calendar_path.read_bytes()
        calendar = JsonTradingCalendarSource(calendar_path).load_calendar()
        records, request, raw_response = self._query()
        return write_market_context(
            config=self.config,
            calendar=calendar,
            calendar_sha256=sha256_bytes(calendar_raw),
            records=records,
            request=request,
            raw_response=raw_response,
            output_dir=output_dir,
        )


def _require_success(result: Any, operation: str) -> None:
    error_code = str(getattr(result, "error_code", ""))
    if error_code != "0":
        raise MarketContextProviderError(
            f"Baostock {operation} failed ({error_code}): {getattr(result, 'error_msg', 'unknown')}"
        )
