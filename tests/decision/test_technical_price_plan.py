import json
from pathlib import Path

from a_share_ai.analysis.technical_features import INDICATOR_VERSION
from a_share_ai.decision.technical_price_plan import (
    PRICE_PLAN_VERSION,
    PricePlanState,
    compute_price_plan,
)

FIXTURE_ROOT = (
    Path(__file__).parents[2] / "fixtures" / "decision" / "technical_price_plan"
)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def ready_report() -> dict:
    return {
        "schema_version": "1.0",
        "indicator_version": INDICATOR_VERSION,
        "status": "ready",
        "decision_ready": True,
        "as_of": "2026-04-01T00:00:00+00:00",
    }


def test_valid_snapshot_matches_manual_decimal_plan() -> None:
    computation = compute_price_plan(
        load_fixture("valid_features.jsonl"),
        technical_report=ready_report(),
        input_sha256="input",
        technical_input_sha256="input",
    )

    assert computation.status is PricePlanState.READY
    assert computation.plan is not None
    mapping = computation.plan.to_mapping()
    assert mapping["price_plan_version"] == PRICE_PLAN_VERSION
    assert mapping["support"] == "9.00000000"
    assert mapping["resistance"] == "12.00000000"
    assert mapping["entry_low"] == "9.50000000"
    assert mapping["entry_high"] == "9.50000000"
    assert mapping["stop_loss"] == "8.50000000"
    assert mapping["take_profit_1"] == "11.00000000"
    assert mapping["take_profit_2"] == "12.00000000"
    assert mapping["risk_reward_ratio"] == "1.50000000"
    assert mapping["decision_ready"] is False


def test_no_valid_zone_fails_closed() -> None:
    computation = compute_price_plan(
        load_fixture("no_valid_zone.jsonl"),
        technical_report=ready_report(),
        input_sha256="input",
        technical_input_sha256="input",
    )

    assert computation.status is PricePlanState.NO_VALID_PRICE_PLAN
    assert computation.plan is None
    assert any(issue.code == "ENTRY_ZONE_INVALID" for issue in computation.issues)


def test_not_ready_technical_input_cannot_create_plan() -> None:
    report = ready_report()
    report["status"] = "insufficient_history"
    report["decision_ready"] = False

    computation = compute_price_plan(
        load_fixture("valid_features.jsonl"),
        technical_report=report,
        input_sha256="input",
        technical_input_sha256="input",
    )

    assert computation.status is PricePlanState.NO_VALID_PRICE_PLAN
    assert computation.plan is None
    assert computation.issues[0].code == "INPUT_NOT_READY"


def test_input_hash_mismatch_is_invalid() -> None:
    computation = compute_price_plan(
        load_fixture("valid_features.jsonl"),
        technical_report=ready_report(),
        input_sha256="actual",
        technical_input_sha256="expected",
    )

    assert computation.status is PricePlanState.INVALID_INPUT
    assert computation.issues[0].code == "INPUT_SHA_MISMATCH"
