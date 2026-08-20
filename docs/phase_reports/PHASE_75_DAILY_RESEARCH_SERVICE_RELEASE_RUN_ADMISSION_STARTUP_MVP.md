# Phase 75 — Audited release-run admission startup MVP

## Scope

Node75 binds the Node73 release-run admission and Node74 independent pair audit
to the existing Node70 release startup. Only a complete ready chain may bind
the loopback receipt service. Older direct, launch-manifest, Node63, and Node65
startup modes remain unchanged.

```bash
python -m a_share_ai.cli serve-research-receipt \
  --artifact-root reports \
  --daily-run-admission reports/daily-service-release-run-admission/daily_research_service_release_run_admission.json \
  --daily-run-admission-report reports/daily-service-release-run-admission/daily_research_service_release_run_admission_report.json \
  --daily-run-admission-audit reports/daily-service-release-run-admission-audit/daily_research_service_release_run_admission_audit_report.json \
  --daily-release-manifest reports/daily-service-release/daily_research_service_release_manifest.json \
  --daily-release-report reports/daily-service-release/daily_research_service_release_report.json \
  --daily-release-audit-report reports/daily-service-release-audit/daily_research_service_release_audit_report.json \
  --output-dir reports/daily-service-release-run-admission-startup \
  --check-only
```

## Contract

Version: `daily-research-service-release-run-admission-startup-v2`.

The new mode requires all three Node73/74 admission paths, all three Node70
release paths, `--artifact-root`, and an `--output-dir` inside the artifact
root. It rejects mixing these options with legacy launch options. The fixed
output is `daily_research_service_release_run_admission_startup_report.json`.

The report records the startup version, Node73/74 and release versions,
relative input paths and actual SHA-256 values for all three admission inputs
and all three Node70 release inputs, symbol/timestamps, admission and audit
status/readiness, mode, service-started state, startup state, sanitized issues,
`decision_ready=false`, and `output_sha256`. Its self-hash
uses sorted keys and compact UTF-8 JSON; it contains no absolute paths, PID,
command line, process logs, credentials, or secrets.

`--check-only` validates the complete chain, writes a `mode=check_only` report,
prints the exact report bytes, never binds a socket, and returns `0` only for
ready. A blocked or failed admission writes a corresponding non-ready report
and returns `1`. Tampered, invalid, missing, out-of-root, inconsistent, or
release-startup-invalid inputs write an invalid report and return `1`.
Configuration or output-directory errors return `2` without writing a report.

Without `--check-only`, Node75 first completes all validations and creates the
existing loopback server (binding the port). It then writes a ready
`mode=serve`, `service_started=true` report and serves the existing receipt
routes. Bind/startup failures write a failed report and return `1`; if the
ready report cannot be written, the already-created server is closed and the
command fails closed. Node75 does not monitor long-term process health or
update the later run receipt.

## Verification

```bash
python -m pytest -q tests/service/test_daily_research_service_release_run_admission_startup.py
python -m pytest -q tests/service
python -m pytest -q
python -m ruff check .
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
