# Phase 73 — Release-run read-only admission MVP

## Scope

Node73 consumes only the Node71 release-run receipt and the Node72 independent
audit report. It writes a deterministic admission JSON and a self-hashed
admission report without re-running Node72 or reading the older release chain.

```bash
python -m a_share_ai.cli build-daily-research-service-release-run-admission \
  --run-report reports/daily-service-release-run/daily_research_service_release_run_report.json \
  --run-audit-report reports/daily-service-release-run-audit/daily_research_service_release_run_audit_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release-run-admission
```

## Contract

Version: `daily-research-service-release-run-admission-v1`.

Outputs:

- `daily_research_service_release_run_admission.json`
- `daily_research_service_release_run_admission_report.json`

The admission checks fixed filenames, canonical self-hashes, actual SHA-256
values, root-relative paths, versions, and the Node71/Node72 state chain.
Only a complete ready pair yields `audit_ready=true`,
`admission_ready=true`, `status=ready`, and exit `0`. Valid blocked or failed
pairs remain auditable but are not admitted and return `1`. Invalid or
tampered inputs are invalid and return `1`; invalid command configuration
returns `2`.

The implementation is read-only with respect to both inputs. It does not
start a service, call HTTP or network APIs, read credentials, refresh data,
schedule work, or authorize trading. All outputs keep `decision_ready=false`
and contain only relative paths, hashes, status metadata, and sanitized
issues.

## Verification

```bash
python -m pytest -q tests/service/test_daily_research_service_release_run_admission.py
python -m pytest -q tests/service
python -m pytest -q
python -m ruff check .
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
