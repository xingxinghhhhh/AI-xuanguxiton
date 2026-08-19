# Phase 71 — Audited daily research service release run MVP

## Scope

Node71 adds one manual, bounded execution path for a Node70-ready release:
start the existing loopback-only read-only service, run the Node60 four-route
probe, stop the child process in a controlled way, and write
`daily_research_service_release_run_report.json`.

```bash
python -m a_share_ai.cli run-daily-research-service-release \
  --daily-release-manifest reports/daily-service-release/daily_research_service_release_manifest.json \
  --daily-release-report reports/daily-service-release/daily_research_service_release_report.json \
  --daily-release-audit-report reports/daily-service-release-audit/daily_research_service_release_audit_report.json \
  --artifact-root reports \
  --startup-timeout-seconds 10 \
  --probe-timeout-seconds 3 \
  --output-dir reports/daily-service-release-run
```

## Readiness contract

`run_ready=true` and exit code `0` require Node70 startup validation, a live
child while Node60 reports exit code `0`, a controlled child stop, and
`decision_ready=false`. Release-chain failure, missing or tampered inputs, path
escape, port conflict, probe failure/timeout, early natural exit, or stop
failure is fail-closed with exit code `1`. Invalid timeout, artifact-root,
output-root, or other command configuration returns `2`.

The report records only artifact-root-relative paths and SHA-256 values. It does
not record PID, command lines, process output, environment variables, secrets,
or absolute paths. Node68–70 inputs and the Node66 command remain unchanged.

## Verification

```bash
python -m pytest -q tests/service/test_daily_research_service_release_run.py
python -m pytest -q tests/service
python -m pytest -q
python -m ruff check .
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
