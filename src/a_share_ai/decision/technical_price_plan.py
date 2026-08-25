"""Fixed-parameter technical price planning with fail-closed invariants."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from ..analysis.technical_features import INDICATOR_VERSION
from ..market.replay import sha256_bytes

PRICE_PLAN_VERSION = "price-plan-v1"
OUTPUT_QUANTUM = Decimal("0.00000001")
REQUIRED_FEATURES = ("close", "sma_20", "atr_14", "low_20", "high_20")


class PricePlanState(StrEnum):
    READY = "ready"
    NO_VALID_PRICE_PLAN = "no_valid_price_plan"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True, slots=True)
class PricePlanIssue:
    code: str
    message: str

    def to_mapping(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TechnicalPricePlan:
    schema_version: str
    price_plan_version: str
    indicator_version: str
    symbol: str
    trade_date: str
    close: Decimal
    support: Decimal
    resistance: Decimal
    entry_low: Decimal
    entry_high: Decimal
    stop_loss: Decimal
    risk_per_share: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    risk_reward_ratio: Decimal
    price_plan_ready: bool
    decision_ready: bool

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "price_plan_version": self.price_plan_version,
            "indicator_version": self.indicator_version,
            "symbol": self.symbol,
            "trade_date": self.trade_date,
            "close": _format_decimal(self.close),
            "support": _format_decimal(self.support),
            "resistance": _format_decimal(self.resistance),
            "entry_low": _format_decimal(self.entry_low),
            "entry_high": _format_decimal(self.entry_high),
            "stop_loss": _format_decimal(self.stop_loss),
            "risk_per_share": _format_decimal(self.risk_per_share),
            "take_profit_1": _format_decimal(self.take_profit_1),
            "take_profit_2": _format_decimal(self.take_profit_2),
            "risk_reward_ratio": _format_decimal(self.risk_reward_ratio),
            "price_plan_ready": self.price_plan_ready,
            "decision_ready": self.decision_ready,
        }


@dataclass(frozen=True, slots=True)
class PricePlanReport:
    schema_version: str
    price_plan_version: str
    indicator_version: str | None
    input_sha256: str
    technical_report_sha256: str | None
    output_sha256: str
    as_of: str | None
    symbol: str | None
    trade_date: str | None
    status: str
    price_plan_ready: bool
    decision_ready: bool
    issues: tuple[PricePlanIssue, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "price_plan_version": self.price_plan_version,
            "indicator_version": self.indicator_version,
            "input_sha256": self.input_sha256,
            "technical_report_sha256": self.technical_report_sha256,
            "output_sha256": self.output_sha256,
            "as_of": self.as_of,
            "symbol": self.symbol,
            "trade_date": self.trade_date,
            "status": self.status,
            "price_plan_ready": self.price_plan_ready,
            "decision_ready": self.decision_ready,
            "issues": [issue.to_mapping() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class PricePlanComputation:
    plan: TechnicalPricePlan | None
    symbol: str | None
    trade_date: str | None
    status: PricePlanState
    issues: tuple[PricePlanIssue, ...]


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(OUTPUT_QUANTUM, rounding=ROUND_HALF_UP)


def _format_decimal(value: Decimal) -> str:
    return format(_quantize(value), "f")


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return parsed


def _issue(code: str, message: str) -> PricePlanIssue:
    return PricePlanIssue(code, message)


def _invalid_computation(
    *,
    status: PricePlanState,
    issues: tuple[PricePlanIssue, ...],
    snapshot: dict[str, Any] | None = None,
) -> PricePlanComputation:
    return PricePlanComputation(
        None,
        str(snapshot.get("symbol")) if snapshot and snapshot.get("symbol") else None,
        str(snapshot.get("trade_date")) if snapshot and snapshot.get("trade_date") else None,
        status,
        issues,
    )


def compute_price_plan(
    snapshot: dict[str, Any] | None,
    *,
    technical_report: dict[str, Any] | None,
    input_sha256: str,
    technical_input_sha256: str | None = None,
    input_issues: tuple[PricePlanIssue, ...] = (),
) -> PricePlanComputation:
    """Build one price plan from the latest ready technical snapshot."""

    if input_issues:
        return _invalid_computation(
            status=PricePlanState.INVALID_INPUT,
            issues=input_issues,
            snapshot=snapshot,
        )
    if snapshot is None or technical_report is None:
        return _invalid_computation(
            status=PricePlanState.INVALID_INPUT,
            issues=(_issue("INPUT_MISSING", "technical snapshot and report are required"),),
            snapshot=snapshot,
        )
    if technical_input_sha256 is not None and technical_input_sha256 != input_sha256:
        return _invalid_computation(
            status=PricePlanState.INVALID_INPUT,
            issues=(_issue("INPUT_SHA_MISMATCH", "feature input SHA does not match report"),),
            snapshot=snapshot,
        )
    if (
        technical_report.get("status") != "ready"
        or technical_report.get("decision_ready") is not True
    ):
        return _invalid_computation(
            status=PricePlanState.NO_VALID_PRICE_PLAN,
            issues=(_issue("INPUT_NOT_READY", "technical features are not decision-ready"),),
            snapshot=snapshot,
        )
    if snapshot.get("warmup_state") != "ready":
        return _invalid_computation(
            status=PricePlanState.NO_VALID_PRICE_PLAN,
            issues=(_issue("WARMUP_NOT_READY", "latest technical snapshot is not warmed up"),),
            snapshot=snapshot,
        )
    if not isinstance(snapshot.get("symbol"), str) or not snapshot["symbol"].strip():
        return _invalid_computation(
            status=PricePlanState.INVALID_INPUT,
            issues=(_issue("FEATURE_INVALID", "symbol must be a non-empty string"),),
            snapshot=snapshot,
        )
    if not isinstance(snapshot.get("trade_date"), str) or not snapshot["trade_date"].strip():
        return _invalid_computation(
            status=PricePlanState.INVALID_INPUT,
            issues=(_issue("FEATURE_INVALID", "trade_date must be an ISO date string"),),
            snapshot=snapshot,
        )
    if technical_report.get("indicator_version") != INDICATOR_VERSION:
        return _invalid_computation(
            status=PricePlanState.INVALID_INPUT,
            issues=(_issue("INDICATOR_VERSION_MISMATCH", "unsupported indicator version"),),
            snapshot=snapshot,
        )
    try:
        values = {field: _decimal(snapshot[field], field) for field in REQUIRED_FEATURES}
    except (KeyError, ValueError) as exc:
        return _invalid_computation(
            status=PricePlanState.INVALID_INPUT,
            issues=(_issue("FEATURE_INVALID", str(exc)),),
            snapshot=snapshot,
        )
    close = values["close"]
    sma20 = values["sma_20"]
    atr14 = values["atr_14"]
    low20 = values["low_20"]
    high20 = values["high_20"]
    if any(values[field] <= 0 for field in ("close", "sma_20", "low_20", "high_20")):
        return _invalid_computation(
            status=PricePlanState.NO_VALID_PRICE_PLAN,
            issues=(_issue("PRICE_NON_POSITIVE", "price inputs must be positive"),),
            snapshot=snapshot,
        )
    if atr14 <= 0:
        return _invalid_computation(
            status=PricePlanState.NO_VALID_PRICE_PLAN,
            issues=(_issue("ATR_NON_POSITIVE", "ATR must be positive"),),
            snapshot=snapshot,
        )
    support = min(low20, sma20 - atr14)
    resistance = max(high20, sma20 + atr14)
    half_atr = Decimal("0.5") * atr14
    entry_low = max(support, close - half_atr)
    entry_high = min(close, support + half_atr)
    stop_loss = support - half_atr
    risk_per_share = entry_high - stop_loss
    take_profit_1 = min(resistance, entry_high + Decimal("1.5") * risk_per_share)
    take_profit_2 = min(resistance, entry_high + Decimal("2.5") * risk_per_share)
    invariants = (
        (entry_low <= entry_high, "ENTRY_ZONE_INVALID", "entry_low must be <= entry_high"),
        (stop_loss < entry_low, "STOP_INVALID", "stop_loss must be below entry_low"),
        (
            take_profit_1 > entry_high,
            "TAKE_PROFIT_1_INVALID",
            "take_profit_1 must exceed entry_high",
        ),
        (
            take_profit_2 > take_profit_1,
            "TAKE_PROFIT_2_INVALID",
            "take_profit_2 must exceed take_profit_1",
        ),
        (risk_per_share > 0, "RISK_INVALID", "risk_per_share must be positive"),
    )
    failures = tuple(_issue(code, message) for valid, code, message in invariants if not valid)
    if failures:
        return _invalid_computation(
            status=PricePlanState.NO_VALID_PRICE_PLAN,
            issues=failures,
            snapshot=snapshot,
        )
    risk_reward_ratio = (take_profit_1 - entry_high) / risk_per_share
    plan = TechnicalPricePlan(
        schema_version="1.0",
        price_plan_version=PRICE_PLAN_VERSION,
        indicator_version=INDICATOR_VERSION,
        symbol=str(snapshot["symbol"]),
        trade_date=str(snapshot["trade_date"]),
        close=close,
        support=support,
        resistance=resistance,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        risk_per_share=risk_per_share,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        risk_reward_ratio=risk_reward_ratio,
        price_plan_ready=True,
        decision_ready=False,
    )
    return PricePlanComputation(plan, plan.symbol, plan.trade_date, PricePlanState.READY, ())


def serialize_price_plan(plan: TechnicalPricePlan | None) -> bytes:
    if plan is None:
        return b""
    return (
        json.dumps(plan.to_mapping(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def build_price_plan_report(
    computation: PricePlanComputation,
    *,
    input_sha256: str,
    technical_report_sha256: str | None,
    output_sha256: str,
    technical_report: dict[str, Any] | None,
) -> PricePlanReport:
    return PricePlanReport(
        schema_version="1.0",
        price_plan_version=PRICE_PLAN_VERSION,
        indicator_version=(
            str(technical_report.get("indicator_version")) if technical_report else None
        ),
        input_sha256=input_sha256,
        technical_report_sha256=technical_report_sha256,
        output_sha256=output_sha256,
        as_of=str(technical_report.get("as_of")) if technical_report else None,
        symbol=computation.symbol,
        trade_date=computation.trade_date,
        status=computation.status.value,
        price_plan_ready=computation.plan is not None,
        decision_ready=False,
        issues=computation.issues,
    )


def report_sha256(report_bytes: bytes) -> str:
    """Hash helper kept here so callers can audit the report itself."""

    return sha256_bytes(report_bytes)
