"""Fixed-parameter, Decimal-based technical feature snapshots."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from ..market.contracts import DailyBar
from ..market.health import ValidationIssue, build_health_report

INDICATOR_VERSION = "technical-v1"
SCHEMA_VERSION = "1.0"
OUTPUT_QUANTUM = Decimal("0.00000001")

FEATURE_NAMES = (
    "return_1d",
    "return_5d",
    "return_20d",
    "sma_5",
    "sma_20",
    "sma_60",
    "ema_12",
    "ema_26",
    "macd",
    "macd_signal",
    "macd_histogram",
    "rsi_14",
    "atr_14",
    "high_20",
    "low_20",
    "avg_volume_20",
    "volume_ratio",
)


class FeatureState(StrEnum):
    READY = "ready"
    INSUFFICIENT_HISTORY = "insufficient_history"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class TechnicalFeatureSnapshot:
    symbol: str
    trade_date: str
    close: Decimal
    warmup_state: str
    values: tuple[tuple[str, Decimal | None], ...]

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "indicator_version": INDICATOR_VERSION,
            "symbol": self.symbol,
            "trade_date": self.trade_date,
            "close": _format_decimal(self.close),
            "warmup_state": self.warmup_state,
        }
        result.update(
            {
                name: _format_decimal(value) if value is not None else None
                for name, value in self.values
            }
        )
        return result


@dataclass(frozen=True, slots=True)
class TechnicalFeatureIssue:
    code: str
    message: str

    def to_mapping(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TechnicalFeatureReport:
    schema_version: str
    indicator_version: str
    input_sha256: str
    output_sha256: str
    as_of: str
    symbol: str | None
    sample_count: int
    last_trade_date: str | None
    warmup_state: str
    status: str
    decision_ready: bool
    issues: tuple[TechnicalFeatureIssue | ValidationIssue, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "indicator_version": self.indicator_version,
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "as_of": self.as_of,
            "symbol": self.symbol,
            "sample_count": self.sample_count,
            "last_trade_date": self.last_trade_date,
            "warmup_state": self.warmup_state,
            "status": self.status,
            "decision_ready": self.decision_ready,
            "issues": [issue.to_mapping() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class FeatureComputation:
    snapshots: tuple[TechnicalFeatureSnapshot, ...]
    symbol: str | None
    sample_count: int
    status: FeatureState
    issues: tuple[TechnicalFeatureIssue | ValidationIssue, ...]


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(OUTPUT_QUANTUM, rounding=ROUND_HALF_UP)


def _format_decimal(value: Decimal) -> str:
    return format(_quantize(value), "f")


def _sma(values: list[Decimal], index: int, period: int) -> Decimal | None:
    if index + 1 < period:
        return None
    return sum(values[index + 1 - period : index + 1], Decimal(0)) / Decimal(period)


def _ema_series(values: list[Decimal], period: int) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return result
    alpha = Decimal(2) / Decimal(period + 1)
    previous = sum(values[:period], Decimal(0)) / Decimal(period)
    result[period - 1] = previous
    for index in range(period, len(values)):
        previous = previous + alpha * (values[index] - previous)
        result[index] = previous
    return result


def _ema_optional_series(
    values: list[Decimal | None], period: int
) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(values)
    valid = [(index, value) for index, value in enumerate(values) if value is not None]
    if len(valid) < period:
        return result
    alpha = Decimal(2) / Decimal(period + 1)
    initial_index = valid[period - 1][0]
    previous = sum((value for _, value in valid[:period]), Decimal(0)) / Decimal(period)
    result[initial_index] = previous
    for index, value in valid[period:]:
        previous = previous + alpha * (value - previous)
        result[index] = previous
    return result


def _rsi_series(closes: list[Decimal], period: int) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(closes)
    if len(closes) <= period:
        return result
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, Decimal(0)))
        losses.append(max(-change, Decimal(0)))
    average_gain = sum(gains[:period], Decimal(0)) / Decimal(period)
    average_loss = sum(losses[:period], Decimal(0)) / Decimal(period)
    result[period] = _rsi_value(average_gain, average_loss)
    for index in range(period + 1, len(closes)):
        average_gain = ((average_gain * Decimal(period - 1)) + gains[index - 1]) / Decimal(period)
        average_loss = ((average_loss * Decimal(period - 1)) + losses[index - 1]) / Decimal(period)
        result[index] = _rsi_value(average_gain, average_loss)
    return result


def _rsi_value(average_gain: Decimal, average_loss: Decimal) -> Decimal:
    if average_loss == 0:
        return Decimal(50) if average_gain == 0 else Decimal(100)
    relative_strength = average_gain / average_loss
    return Decimal(100) - (Decimal(100) / (Decimal(1) + relative_strength))


def _atr_series(
    bars: list[DailyBar], closes: list[Decimal], period: int
) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(bars)
    if len(bars) <= period:
        return result
    true_ranges: list[Decimal] = []
    for index in range(1, len(bars)):
        true_ranges.append(
            max(
                bars[index].high - bars[index].low,
                abs(bars[index].high - closes[index - 1]),
                abs(bars[index].low - closes[index - 1]),
            )
        )
    average_true_range = sum(true_ranges[:period], Decimal(0)) / Decimal(period)
    result[period] = average_true_range
    for index in range(period + 1, len(bars)):
        average_true_range = (
            (average_true_range * Decimal(period - 1)) + true_ranges[index - 1]
        ) / Decimal(period)
        result[index] = average_true_range
    return result


def _return(closes: list[Decimal], index: int, period: int) -> Decimal | None:
    if index < period or closes[index - period] == 0:
        return None
    return (closes[index] / closes[index - period]) - Decimal(1)


def compute_feature_snapshots(
    bars: Iterable[DailyBar],
    *,
    as_of: datetime,
    input_sha256: str,
    parse_issues: Iterable[ValidationIssue] = (),
) -> FeatureComputation:
    """Validate one symbol's bars and calculate fixed-parameter snapshots."""

    bar_list = list(bars)
    symbols = {bar.symbol for bar in bar_list}
    input_issues = tuple(parse_issues)
    if input_issues:
        return FeatureComputation(
            (), next(iter(symbols), None), len(bar_list), FeatureState.INVALID, input_issues
        )
    if len(symbols) != 1:
        issue = TechnicalFeatureIssue(
            "MULTIPLE_SYMBOLS" if symbols else "NO_DATA",
            "technical snapshots require exactly one symbol",
        )
        return FeatureComputation(
            (), next(iter(symbols), None), len(bar_list), FeatureState.INVALID, (issue,)
        )
    symbol = next(iter(symbols))
    health = build_health_report(
        bar_list,
        as_of=as_of,
        input_sha256=input_sha256,
        output_sha256="",
        source="technical-input",
    )
    if not health.decision_ready:
        return FeatureComputation(
            (), symbol, len(bar_list), FeatureState.INVALID, tuple(health.issues)
        )

    closes = [bar.close for bar in bar_list]
    highs = [bar.high for bar in bar_list]
    lows = [bar.low for bar in bar_list]
    volumes = [bar.volume for bar in bar_list]
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    macd = [
        ema12[index] - ema26[index]
        if ema12[index] is not None and ema26[index] is not None
        else None
        for index in range(len(bar_list))
    ]
    signal = _ema_optional_series(macd, 9)
    rsi = _rsi_series(closes, 14)
    atr = _atr_series(bar_list, closes, 14)
    snapshots: list[TechnicalFeatureSnapshot] = []
    for index, bar in enumerate(bar_list):
        macd_value = macd[index]
        signal_value = signal[index]
        average_volume = _sma(volumes, index, 20)
        values: dict[str, Decimal | None] = {
            "return_1d": _return(closes, index, 1),
            "return_5d": _return(closes, index, 5),
            "return_20d": _return(closes, index, 20),
            "sma_5": _sma(closes, index, 5),
            "sma_20": _sma(closes, index, 20),
            "sma_60": _sma(closes, index, 60),
            "ema_12": ema12[index],
            "ema_26": ema26[index],
            "macd": macd_value,
            "macd_signal": signal_value,
            "macd_histogram": macd_value - signal_value
            if macd_value is not None and signal_value is not None
            else None,
            "rsi_14": rsi[index],
            "atr_14": atr[index],
            "high_20": max(highs[index + 1 - 20 : index + 1]) if index >= 19 else None,
            "low_20": min(lows[index + 1 - 20 : index + 1]) if index >= 19 else None,
            "avg_volume_20": average_volume,
            "volume_ratio": volumes[index] / average_volume
            if average_volume is not None and average_volume != 0
            else None,
        }
        state = (
            FeatureState.READY
            if all(values[name] is not None for name in FEATURE_NAMES)
            else FeatureState.INSUFFICIENT_HISTORY
        )
        snapshots.append(
            TechnicalFeatureSnapshot(
                symbol=symbol,
                trade_date=bar.trade_date.isoformat(),
                close=bar.close,
                warmup_state=state.value,
                values=tuple((name, values[name]) for name in FEATURE_NAMES),
            )
        )
    final_state = FeatureState(snapshots[-1].warmup_state)
    return FeatureComputation(
        tuple(snapshots),
        symbol,
        len(bar_list),
        final_state,
        (),
    )


def serialize_feature_snapshots(snapshots: Iterable[TechnicalFeatureSnapshot]) -> bytes:
    lines = [
        json.dumps(snapshot.to_mapping(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for snapshot in snapshots
    ]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def build_feature_report(
    computation: FeatureComputation,
    *,
    as_of: datetime,
    input_sha256: str,
    output_sha256: str,
) -> TechnicalFeatureReport:
    last_trade_date = computation.snapshots[-1].trade_date if computation.snapshots else None
    return TechnicalFeatureReport(
        schema_version=SCHEMA_VERSION,
        indicator_version=INDICATOR_VERSION,
        input_sha256=input_sha256,
        output_sha256=output_sha256,
        as_of=as_of.isoformat(),
        symbol=computation.symbol,
        sample_count=computation.sample_count,
        last_trade_date=last_trade_date,
        warmup_state=computation.status.value,
        status=computation.status.value,
        decision_ready=computation.status is FeatureState.READY,
        issues=computation.issues,
    )
