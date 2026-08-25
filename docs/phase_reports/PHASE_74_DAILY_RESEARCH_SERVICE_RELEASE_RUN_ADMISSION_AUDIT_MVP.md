# Phase 74 — Release-run admission pair audit MVP

## Scope

Node74 independently audits the two Node73 admission artifacts. It reads only
the admission JSON and admission report; it does not rerun Node73, read Node71
or Node72 inputs, start a service, send HTTP, use the network, read keys, or
refresh data.

```bash
python -m a_share_ai.cli audit-daily-research-service-release-run-admission \
  --admission reports/daily-service-release-run-admission/daily_research_service_release_run_admission.json \
  --report reports/daily-service-release-run-admission/daily_research_service_release_run_admission_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release-run-admission-audit
```

## Contract

Version: `daily-research-service-release-run-admission-audit-v1`.

The output is `daily_research_service_release_run_admission_audit_report.json`.
It contains deterministic relative paths, actual SHA-256 values for the two
Node73 files, the declared upstream paths and hashes copied from Node73, the
derived state, sanitized issues, and `output_sha256`. Every result keeps
`decision_ready=false`.

Node74 verifies fixed fields, canonical self-hashes, fixed filenames,
artifact-root boundaries, actual admission/report hashes, report-to-admission
path and hash bindings, version values, and equality of the report state to
the admission state. It derives readiness from the startup, probe, stop, run,
audit, admission, and issue fields. A consistent ready pair returns exit `0`;
consistent blocked or failed pairs are auditable but return `1`; invalid or
tampered pairs return `1`; invalid command configuration returns `2`.

This is an internal consistency audit only. Matching hashes and copied fields
prove the completeness and consistency of the two supplied Node73 files, but
cannot prove that an external actor did not rewrite both files together. It is
not a replacement for the independent Node71 run receipt or Node72 audit.

## Verification

```bash
python -m pytest -q tests/service/test_daily_research_service_release_run_admission_audit.py
python -m pytest -q tests/service
python -m pytest -q
python -m ruff check .
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
