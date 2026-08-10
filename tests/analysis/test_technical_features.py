from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from a_share_ai.analysis.technical_features import (
    FeatureState,
    build_feature_report,
    compute_feature_snapshots,
)
from a_share_ai.market.contracts import DailyBar

AS_OF = datetime(2026, 4, 1, tzinfo=UTC)


def make_bars(count: int = 80) -> list[DailyBar]:
    bars = []
    for index in range(count):
        close = Decimal("10") + Decimal(index) / Decimal("10")
        bars.append(
            DailyBar(
                symbol="600000.SH",
                trade_date=date(2026, 1, 1) + timedelta(days=index),
                open=close - Decimal("0.05"),
                high=close + Decimal("0.25"),
                low=close - Decimal("0.25"),
                close=close,
                volume=Decimal(1000 + index * 10),
                amount=close * Decimal(1000 + index * 10),
                source="test",
                market_time=datetime(2026, 1, 1, 15, tzinfo=timezone(timedelta(hours=8)))
                + timedelta(days=index),
                received_at=AS_OF,
            )
        )
    return bars


def test_fixed_indicators_become_ready_after_warmup() -> None:
    computation = compute_feature_snapshots(make_bars(), as_of=AS_OF, input_sha256="input")

    assert computation.status is FeatureState.READY
    latest = computation.snapshots[-1].to_mapping()
    assert latest["warmup_state"] == "ready"
    assert latest["sma_5"] == "17.70000000"
    assert latest["ema_12"] is not None
    assert latest["ema_26"] is not None
    assert latest["macd"] is not None
    assert latest["macd_signal"] is not None
    assert latest["macd_histogram"] is not None
    assert latest["rsi_14"] == "100.00000000"
    assert latest["atr_14"] is not None
    assert latest["high_20"] == "18.15000000"
    assert latest["low_20"] == "15.75000000"
    assert latest["volume_ratio"] is not None


def test_four_bars_are_insufficient_and_not_decision_ready() -> None:
    computation = compute_feature_snapshots(make_bars(4), as_of=AS_OF, input_sha256="input")
    report = build_feature_report(
        computation,
        as_of=AS_OF,
        input_sha256="input",
        output_sha256="output",
    )

    assert computation.status is FeatureState.INSUFFICIENT_HISTORY
    assert computation.snapshots[-1].warmup_state == "insufficient_history"
    assert report.decision_ready is False
    assert report.status == "insufficient_history"


def test_feature_at_same_date_has_no_future_leakage() -> None:
    bars = make_bars()
    first = compute_feature_snapshots(bars[:40], as_of=AS_OF, input_sha256="input")
    full = compute_feature_snapshots(bars, as_of=AS_OF, input_sha256="input")

    assert first.snapshots[-1].to_mapping() == full.snapshots[39].to_mapping()


def test_health_gate_blocks_invalid_input() -> None:
    bars = make_bars()
    bars[20] = replace(bars[20], high=Decimal("1"))

    computation = compute_feature_snapshots(bars, as_of=AS_OF, input_sha256="input")

    assert computation.status is FeatureState.INVALID
    assert not computation.snapshots
    assert any(issue.code == "OHLC_HIGH_INVALID" for issue in computation.issues)


def test_fixture_is_available() -> None:
    fixture = (
        Path(__file__).parents[2]
        / "fixtures"
        / "analysis"
        / "technical"
        / "valid_80_bars.jsonl"
    )
    assert len(fixture.read_text(encoding="utf-8").splitlines()) == 80
