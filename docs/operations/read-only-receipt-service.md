# Read-only receipt service operations

## Start with a versioned launch manifest

For deployment or process-manager use, keep the manifest and its referenced
artifacts under one `artifact-root`. The fixed fields are shown below; hashes
must be the SHA-256 values of the exact two files.

```json
{
  "launch_version": "read-only-receipt-service-launch-v1",
  "service_version": "read-only-receipt-service-v1",
  "probe_version": "read-only-receipt-service-probe-v1",
  "receipt_path": "final-receipt/market_aware_session_history_final_receipt.json",
  "receipt_report_path": "final-receipt/market_aware_session_history_final_receipt_report.json",
  "receipt_sha256": "<64 lowercase hexadecimal characters>",
  "receipt_report_sha256": "<64 lowercase hexadecimal characters>",
  "host": "127.0.0.1",
  "port": 8765,
  "decision_ready": false
}
```

Check the manifest without opening a port:

```bash
python -m a_share_ai.cli serve-research-receipt \
  --launch-manifest reports/service/read_only_receipt_service_launch.json \
  --artifact-root reports/service \
  --check-only
```

The check rejects unknown fields, path escapes, non-loopback hosts, hash
mismatches, version mismatches, invalid Node52 receipt/report contracts, and
`decision_ready=true`. It returns a path-free JSON summary and exit code `0`
only when validation passes. A stale or blocked receipt remains stale or
blocked in that summary; validation is not readiness.

Start the exact checked configuration:

```bash
python -m a_share_ai.cli serve-research-receipt \
  --launch-manifest reports/service/read_only_receipt_service_launch.json \
  --artifact-root reports/service
```

The earlier direct form remains supported:


Start Node53 with a validated Node52 final receipt. Keep it on loopback:

```bash
python -m a_share_ai.cli serve-research-receipt \
  --receipt reports/session-history/final-receipt/market_aware_session_history_final_receipt.json \
  --receipt-report reports/session-history/final-receipt/market_aware_session_history_final_receipt_report.json \
  --artifact-root reports/session-history \
  --host 127.0.0.1 \
  --port 8765
```

## Probe

Run the deployment/process-manager check from the same host:

```bash
python -m a_share_ai.cli probe-research-receipt-service \
  --base-url http://127.0.0.1:8765 \
  --timeout-seconds 3
```

Use exit code `0` only as the combined liveness/readiness signal. Exit code
`1` means the service is down, malformed, stale, blocked, or not ready. Exit
code `2` means the probe arguments are invalid. The probe does not restart the
service, retry requests, follow redirects, or make external network calls.

## Safety boundary

The service and probe are local read-only tooling. Do not bind the service to a
public interface, expose the endpoint through an unauthenticated proxy, or
interpret `receipt_ready=true` as a trading authorization. All responses and
probe results must keep `decision_ready=false`.

## Controlled daily run

For a manual one-shot operational check, wrap the audited Node65 startup with
the Node60 four-route probe:

```bash
python -m a_share_ai.cli run-daily-research-service \
  --daily-launch-gate reports/daily-service-gate/daily_research_service_launch_gate.json \
  --daily-launch-gate-root reports \
  --daily-launch-gate-audit reports/daily-service-gate-audit/daily_research_service_launch_gate_audit_report.json \
  --startup-timeout-seconds 10 \
  --probe-timeout-seconds 3 \
  --output-dir reports/daily-service-run
```

The command is bounded and manual. It starts the already-audited loopback
service, probes health/readiness/receipt/daily-admission, stops the child, and
writes a self-hashed `daily_research_service_run_report.json`. It does not
schedule, refresh data, call AI, expose a public listener, or change
`decision_ready=false`.

## One-shot audited release run

For a single manual release verification, use the Node70 release startup
admission as the input to a bounded start/probe/stop operation:

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

The command starts only a Node70-ready release, runs the Node60 four-route
loopback probe, and then performs a controlled child-process stop. It writes
`daily_research_service_release_run_report.json`. Exit `0` requires a ready
probe and controlled stop; release validation, startup, probe, early-exit,
port, or stop failures return `1`, while invalid configuration returns `2`.
The receipt contains relative paths, hashes, statuses, and sanitized issues;
it never records process logs or secrets and keeps `decision_ready=false`.
This is a one-shot manual check, not a scheduler or daemon.

## Independent release admission audit

After building the Node68 release admission, audit it independently:

```bash
python -m a_share_ai.cli audit-daily-research-service-release \
  --manifest reports/daily-service-release/daily_research_service_release_manifest.json \
  --report reports/daily-service-release/daily_research_service_release_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release-audit
```

The audit is offline and read-only. It checks the manifest/report self-hashes,
manifest binding, actual Node66/67 hashes, paths, timestamps and readiness;
it never rebuilds or starts the service and always preserves
`decision_ready=false`.

## Audited release startup admission

After Node68 and Node69 both report a ready release, validate and start the
existing Node65 loopback service as one guarded operation:

```bash
python -m a_share_ai.cli serve-research-receipt \
  --artifact-root reports \
  --daily-release-manifest reports/daily-service-release/daily_research_service_release_manifest.json \
  --daily-release-report reports/daily-service-release/daily_research_service_release_report.json \
  --daily-release-audit-report reports/daily-service-release-audit/daily_research_service_release_audit_report.json \
  --check-only
```

Remove `--check-only` only for the actual loopback startup. The three release
arguments are a required group and cannot be mixed with direct launch,
launch-manifest, daily-admission, daily-launch-gate, host, or port options.
Node70 validates the Node68/69 hashes and status chain, then delegates the
Node65 gate check. It does not rebuild or audit artifacts. Non-ready,
tampered, missing, or out-of-root artifacts return before socket binding;
check-only never binds. The Node65 host/port and receipt inputs remain the
source of runtime configuration, and the service stays loopback-only and
`decision_ready=false`.

## Read-only service release admission

Bind the completed run and independent audit before a human deployment or
rollback review:

```bash
python -m a_share_ai.cli build-daily-research-service-release \
  --run-report reports/daily-service-run/daily_research_service_run_report.json \
  --run-audit-report reports/daily-service-run-audit/daily_research_service_run_audit_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release
```

The manifest/report pair is deterministic and read-only. The command checks
the upstream self-hashes, actual file hashes, normalized artifact-root paths,
symbol/timestamp equality, and readiness state. It never deploys, starts, or
probes the service, and always keeps `decision_ready=false`.

## Independent service-run audit

After the bounded run, independently verify its report with:

```bash
python -m a_share_ai.cli audit-daily-research-service-run \
  --run-report reports/daily-service-run/daily_research_service_run_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-run-audit
```

This is a read-only, offline audit. It checks the Node66 report self-hash,
fixed fields, artifact-root boundaries, gate/audit hashes, and the Node65
startup chain. It never starts the service, calls the probe, reads API keys,
or changes the input reports. Its output is
`daily_research_service_run_audit_report.json`, and it always keeps
`decision_ready=false`.
