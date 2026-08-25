# Phase 5: Technical Price Plan and Risk Baseline MVP

## Scope completed

This phase adds a deterministic price-calculation layer on top of a ready
`technical_features.jsonl` and its `technical_report.json`. It does not make a
buy, hold, or sell decision and always keeps `decision_ready=false`.

The fixed formula version is `price-plan-v1`:

```text
support = min(low_20, sma_20 - atr_14)
resistance = max(high_20, sma_20 + atr_14)
entry_low = max(support, close - 0.5 * atr_14)
entry_high = min(close, support + 0.5 * atr_14)
stop_loss = support - 0.5 * atr_14
risk_per_share = entry_high - stop_loss
take_profit_1 = min(resistance, entry_high + 1.5 * risk_per_share)
take_profit_2 = min(resistance, entry_high + 2.5 * risk_per_share)
```

The layer uses `Decimal`, validates the technical input status/version/SHA, and
fails closed when the input is not ready or when price invariants do not hold.

## CLI

```bash
python -m a_share_ai.cli compute-price-plan \
  --input fixtures/decision/technical_price_plan/valid_features.jsonl \
  --technical-report fixtures/decision/technical_price_plan/valid_technical_report.json \
  --output-dir reports/price-plan-valid-001
```

The command writes:

- `technical_price_plan.json`: one deterministic plan, only when all invariants
  pass
- `price_plan_report.json`: input/report/output hashes, versions, status,
  `price_plan_ready`, and the permanently false `decision_ready`

## Verification

- `python -m pytest`: 54 passed
- `ruff check .`: passed
- `python -m compileall -q src tests`: passed
- Editable package installation: passed
- Valid price fixture: exit code 0, `price_plan_ready=true`,
  `decision_ready=false`
- Invalid price zone fixture: exit code 1, `no_valid_price_plan`, no plan file
  content
- Four-bar insufficient-history input: rejected without a price plan
- Repeated valid runs use identical feature/report inputs and deterministic
  output hashes

## Explicit non-goals

No buy/hold/sell label, AI call, fundamental/news input, position sizing,
backtest claim, schedule, alert, database, order, or user-configurable formula
parameter is included.
