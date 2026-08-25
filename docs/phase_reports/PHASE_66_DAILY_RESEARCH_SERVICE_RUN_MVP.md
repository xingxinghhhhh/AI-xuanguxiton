# Phase 66 — Daily research service controlled run MVP

Node66 adds one bounded, operator-triggered run around the Node65 audited
startup gate and the existing Node60 four-route probe. It starts the loopback
service, waits for the probe to report ready, then terminates and verifies the
child process before writing a sanitized run report.

```bash
python -m a_share_ai.cli run-daily-research-service \
  --daily-launch-gate reports/daily-service-gate/daily_research_service_launch_gate.json \
  --daily-launch-gate-root reports \
  --daily-launch-gate-audit reports/daily-service-gate-audit/daily_research_service_launch_gate_audit_report.json \
  --startup-timeout-seconds 10 \
  --probe-timeout-seconds 3 \
  --output-dir reports/daily-service-run
```

The command writes `daily_research_service_run_report.json` only under the
controlled artifact root. Its strict, self-hashed fields contain relative
gate/audit references and SHAs, bound symbol/timestamps, startup and probe
states, the probe exit code, child-process cleanup state, `run_ready`, issues,
and `decision_ready=false`.

Exit `0` requires a ready Node65 gate and audit, a successful Node60 probe of
all four routes, and a stopped child process. Gate/audit failures, stale or
blocked inputs, probe failures, timeouts, and child-process failures return
`1`; invalid timeouts, artifact roots, or output locations return `2`.

The run is manual and bounded. It does not schedule work, refresh data, call
external APIs or AI providers, add HTTP routes, expose a public listener, or
change any Node52–65 artifact. A failed run never reports `run_ready=true`.
