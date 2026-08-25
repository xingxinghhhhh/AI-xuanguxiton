# Phase 4: Daily Technical Features MVP

## Scope completed

This phase adds a deterministic, offline technical-feature layer for one symbol
and closed daily bars. It consumes the normalized JSONL contract and applies the
existing health gate before calculating any feature.

Fixed indicators:

- 1-day, 5-day, and 20-day returns
- SMA 5/20/60
- EMA 12/26
- MACD, Signal, and Histogram (Signal EMA 9)
- Wilder RSI 14
- Wilder ATR 14
- 20-day high/low
- 20-day average volume and volume ratio

All calculations use `Decimal`, fixed parameters, deterministic rounding, and
no current-time or future-data dependency. Warm-up rows are retained with null
values and `insufficient_history`; they never become decision-ready. Invalid,
stale, disconnected, or malformed input fails closed.

## CLI

```bash
python -m a_share_ai.cli compute-technical-features \
  --input fixtures/analysis/technical/valid_80_bars.jsonl \
  --as-of 2026-04-01T00:00:00Z \
  --output-dir reports/technical-valid-001
```

The command writes:

- `technical_features.jsonl`: one immutable feature snapshot per input bar
- `technical_report.json`: input/output hashes, version, warm-up state, and
  decision gate result

`--as-of` is optional for convenience; when omitted, it is derived from the
latest input `received_at`, not from wall-clock time.

## Verification

- `ruff check .`: passed
- `python -m pytest`: 47 passed
- `python -m compileall -q src tests`: passed
- Editable package installation: passed
- 80-bar fixture: `ready`, `decision_ready=true`
- Four-bar Baostock capture: `insufficient_history`, `decision_ready=false`
- Repeated 80-bar run: feature and report hashes matched exactly

## Explicit non-goals

No buy/hold/sell decision, entry or exit levels, AI model, multi-symbol scan,
database, scheduling, alerting, position management, or order capability is
included in this phase.
