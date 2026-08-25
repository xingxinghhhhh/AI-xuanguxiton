# Phase 28: DeepSeek claim-kind contract fix

The controlled Phase 27 smoke reached DeepSeek successfully, but the model
returned `kind="fact"`. The local `analysis-report-v1` validator correctly
rejected that response because the allowed claim kinds are only
`observation`, `risk`, and `unknown`.

## Change

The DeepSeek-specific prompt now explicitly states:

- `kind` must be exactly one of `observation`, `risk`, or `unknown`;
- `observation` is for a directly supplied evidence statement;
- `risk` is for a supported risk statement;
- `unknown` is required when evidence is insufficient;
- `fact`, `finding`, `statement`, `conclusion`, `recommendation`, `trend`, and
  other labels are forbidden.

The local validator remains authoritative. The allowed enum is not widened and
`fact` is not aliased to `observation`.

## Fixtures and verification

`market_aware_valid_observation.json` is an explicitly labeled offline test
fixture using the existing claim contract. `market_aware_invalid_fact.json`
preserves the negative case and must be rejected at analysis validation, with
all later replay stages skipped. The Phase 27 real response and its audit
hashes remain unchanged and are not replayed or sent again.

No real API call is made in this node. All ordinary tests remain offline and
the decision gate stays `decision_ready=false`.
