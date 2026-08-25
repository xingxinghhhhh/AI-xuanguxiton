# Phase 72 — Audited release-run independent audit MVP

## Scope

Node72 adds a read-only independent auditor for the Node71
`daily_research_service_release_run_report.json`. It rechecks the complete
Node71 → Node68/69 → Node66/67 → Node65 artifact chain, recomputes the runtime
state, and writes `daily_research_service_release_run_audit_report.json`.

```bash
python -m a_share_ai.cli audit-daily-research-service-release-run \
  --run-report reports/daily-service-release-run/daily_research_service_release_run_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release-run-audit
```

## Audit contract

The auditor never starts a process, invokes Node60, sends HTTP, reads API keys,
or changes any input artifact. It verifies fixed filenames, normalized
artifact-root-relative paths, actual SHA-256 values, self-hashes, versions,
path bindings, symbol/timestamp equality, `decision_ready=false`, and the
independently derived startup/probe/stop/run state.

`audit_ready=true` means the result is structurally and cryptographically
auditable. A valid blocked or failed run can therefore have `audit_ready=true`,
but the CLI returns `1` unless the independently derived audit `status` is
`ready`. Invalid, missing, tampered, out-of-root, or contradictory inputs have
`audit_ready=false` and also return `1`. Invalid command configuration returns
`2`.

The audit report contains only relative paths, hashes, statuses, sanitized
issues, and `decision_ready=false`; it does not contain process identifiers,
commands, logs, secrets, or absolute paths.

## Verification

```bash
python -m pytest -q tests/service/test_daily_research_service_release_run_audit.py
python -m pytest -q tests/service
python -m pytest -q
python -m ruff check .
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
