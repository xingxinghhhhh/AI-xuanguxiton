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

## Independent release-run audit

After a Node71 one-shot run, independently audit its receipt without starting
anything:

```bash
python -m a_share_ai.cli audit-daily-research-service-release-run \
  --run-report reports/daily-service-release-run/daily_research_service_release_run_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release-run-audit
```

The audit rechecks the Node71 receipt and the Node68/69, Node66/67, and Node65
artifact chain, including actual hashes, self-hashes, path bindings, versions,
timestamps, and independent readiness state. It never starts a service, calls
Node60, sends HTTP, reads API keys, or changes inputs. A structurally valid
blocked/failed run is auditable but returns CLI exit `1`; only an independently
verified ready run returns `0`. Invalid configuration returns `2`. The output
is `daily_research_service_release_run_audit_report.json` and always keeps
`decision_ready=false`.

## Read-only release-run admission

Build the controlled admission summary only after Node71 and Node72 have both
produced their reports:

```bash
python -m a_share_ai.cli build-daily-research-service-release-run-admission \
  --run-report reports/daily-service-release-run/daily_research_service_release_run_report.json \
  --run-audit-report reports/daily-service-release-run-audit/daily_research_service_release_run_audit_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release-run-admission
```

Node73 verifies the two input self-hashes, actual SHA-256 values, fixed
filenames, artifact-root-relative paths, versions, and the shared run state.
It does not re-run Node72 or read the older Node68–65 chain. A ready pair
produces `admission_ready=true` and exit `0`; structurally valid blocked or
failed inputs remain auditable but return `1`. Invalid artifacts also return
`1`, configuration errors return `2`, and every output keeps
`decision_ready=false`. The command has no service, HTTP, network, API-key,
refresh, scheduler, or trading side effects.

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

## Independent release-run admission pair audit

After Node73 creates the release-run admission pair, audit that pair without
starting anything:

```bash
python -m a_share_ai.cli audit-daily-research-service-release-run-admission \
  --admission reports/daily-service-release-run-admission/daily_research_service_release_run_admission.json \
  --report reports/daily-service-release-run-admission/daily_research_service_release_run_admission_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release-run-admission-audit
```

The Node74 audit reads only the two Node73 files. It verifies their schemas,
self-hashes, actual hashes, root-relative paths, report bindings, versions,
and shared internal readiness state. It does not reread Node71/72, rerun
Node73, start a service, send HTTP, use the network, read credentials, or
change inputs. Ready returns `0`; structurally consistent blocked or failed
pairs, invalid/tampered pairs, and missing evidence return `1`; bad command
configuration returns `2`. The output is
`daily_research_service_release_run_admission_audit_report.json` and preserves
`decision_ready=false`.

This audit establishes internal consistency of the supplied Node73 pair. It
cannot detect a coordinated rewrite of both files without an external trusted
reference, and it does not replace the independent Node71 run receipt or
Node72 audit.

## Audited release-run admission startup

Use Node75 when the completed release-run admission must gate the existing
loopback receipt service:

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

The three Node73/74 admission inputs and three Node70 release inputs are a
required group. `--output-dir` is required for this mode and must be within
`--artifact-root`; legacy direct, launch-manifest, Node63, and Node65 modes do
not require it and remain unchanged. Check-only validates the admission/audit
pair and release startup chain, writes and prints the exact
`daily_research_service_release_run_admission_startup_report.json` bytes, and
never binds. Only a ready chain returns `0`; blocked, failed, tampered, or
invalid evidence returns `1`; configuration errors return `2`.

For actual startup, Node75 completes the same checks before creating the
existing loopback server. It writes the ready report only after the socket is
successfully bound, then serves the existing routes. Bind or report-write
failures close the server and fail closed. The report is compact-self-hashed,
relative-path-only, records the relative paths and actual SHA-256 values of
all three Node70 release inputs, keeps `decision_ready=false`, and contains no PID,
command-line, logs, credentials, or secrets. This node is a startup binding
check, not a long-term health monitor or a replacement for the Node71/72
evidence chain.

## Node76 controlled release-run startup smoke

For one bounded deployment-style verification of the complete Node75 path, run
the Node76 smoke command:

```bash
python -m a_share_ai.cli run-daily-research-service-release-run-admission-startup-smoke \
  --daily-run-admission reports/daily-service-release-run-admission/daily_research_service_release_run_admission.json \
  --daily-run-admission-report reports/daily-service-release-run-admission/daily_research_service_release_run_admission_report.json \
  --daily-run-admission-audit reports/daily-service-release-run-admission-audit/daily_research_service_release_run_admission_audit_report.json \
  --daily-release-manifest reports/daily-service-release/daily_research_service_release_manifest.json \
  --daily-release-report reports/daily-service-release/daily_research_service_release_report.json \
  --daily-release-audit-report reports/daily-service-release-audit/daily_research_service_release_audit_report.json \
  --artifact-root reports \
  --startup-timeout-seconds 10 \
  --probe-timeout-seconds 3 \
  --output-dir reports/daily-service-release-run-admission-startup-smoke
```

Node76 writes the Node75 check-only report to `preflight/`, the actual startup
report to `startup/`, and a compact-self-hashed smoke receipt at the output
root. It then runs the existing Node60 four-route loopback probe, confirms the
child remains alive, performs the existing controlled stop, and verifies that
the loopback port is released. Exit `0` requires all of those conditions;
blocked or invalid evidence and runtime failures return `1`, while invalid
timeouts or artifact-root/output configuration return `2`. The smoke run is
manual and one-shot: it does not refresh data, call external APIs, schedule
work, expose a public listener, or set `decision_ready=true`.

## Node77 independent startup smoke audit

Node77 audits a Node76 smoke receipt offline and without starting the service:

```bash
python -m a_share_ai.cli audit-daily-research-service-release-run-admission-startup-smoke \
  --smoke-report reports/daily-service-release-run-admission-startup-smoke/daily_research_service_release_run_admission_startup_smoke_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release-run-admission-startup-smoke-audit
```

The audit independently recomputes the smoke report self-hash, each available
admission/release input schema and self-hash, all declared paths and SHA-256
values, fixed filenames, versions, status chain, and the Node75
preflight/startup chain. It performs no subprocess, socket, HTTP, network, API-key, or port
operation and does not claim to prove historical process or port-release facts.
Ready evidence returns `0`; consistent blocked/failed evidence returns `1` but
may still have `audit_ready=true`; invalid or tampered evidence returns `1`
with `audit_ready=false`; configuration errors return `2`. The generated audit
receipt is deterministic, relative-path-only, and keeps `decision_ready=false`.
If Node76 stops before release startup because admission is blocked or failed,
the three release inputs may be absent; the audit preserves that bounded
evidence state rather than treating absent release evidence as ready.

## Node78 startup smoke admission summary

Node78 combines the Node76 smoke receipt and Node77 independent audit receipt
without rerunning either node:

```bash
python -m a_share_ai.cli build-daily-research-service-release-run-admission-startup-smoke-admission \
  --smoke-report reports/daily-service-release-run-admission-startup-smoke/daily_research_service_release_run_admission_startup_smoke_report.json \
  --smoke-audit-report reports/daily-service-release-run-admission-startup-smoke-audit/daily_research_service_release_run_admission_startup_smoke_audit_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release-run-admission-startup-smoke-admission
```

It writes a manifest and a report with relative paths, SHA-256 references,
runtime state, status, sanitized issues, and `decision_ready=false`.
`admission_ready=true` requires ready smoke and audit receipts with matching
identity, state, hashes, and empty issues. A consistent blocked or failed
runtime returns `1` and remains non-ready; invalid or contradictory evidence
also returns `1` with `audit_ready=false`; invalid root/output configuration
returns `2`. This node is offline and read-only: it does not start a process,
open a socket, call HTTP, refresh data, call AI or external APIs, or authorize
trading.

## Node79 startup smoke admission pair audit

Node79 independently audits the two Node78 output files:

```bash
python -m a_share_ai.cli audit-daily-research-service-release-run-admission-startup-smoke-admission \
  --admission reports/daily-service-release-run-admission-startup-smoke-admission/daily_research_service_release_run_admission_startup_smoke_admission.json \
  --report reports/daily-service-release-run-admission-startup-smoke-admission/daily_research_service_release_run_admission_startup_smoke_admission_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release-run-admission-startup-smoke-admission-audit
```

It validates exact schemas, Node78/Node76/Node77 versions, self-hashes,
actual SHA-256 values, relative path and fixed-filename rules, report-to-
manifest bindings, and ready/blocked/failed/invalid state semantics. The
output is deterministic and keeps `decision_ready=false`. Ready evidence
returns `0`; a consistent non-ready pair returns `1` with `audit_ready=true`;
invalid or tampered evidence returns `1` with `audit_ready=false`; root/output
configuration errors return `2`. The audit does not read upstream Node76/77
files, start a process, open a socket, call HTTP, or access external services.

## Node80 startup admission binding gate

Node80 adds a final read-only binding boundary to `serve-research-receipt`. The
new group requires the three Node78 admission files, the three Node70 release
files, and `--artifact-root`:

```bash
python -m a_share_ai.cli serve-research-receipt \
  --daily-smoke-admission <node78-manifest> \
  --daily-smoke-admission-report <node78-report> \
  --daily-smoke-admission-audit-report <node79-audit-report> \
  --daily-release-manifest <node70-manifest> \
  --daily-release-report <node70-report> \
  --daily-release-audit-report <node70-audit-report> \
  --artifact-root reports --check-only
```

The gate independently checks the Node78/Node79 pair, exact fields, self-hash,
actual SHA-256, fixed filenames, relative paths, identity, timestamps, status
and readiness. It then delegates release validation to the existing Node70
loader; it does not duplicate that contract. Check-only is offline and never
binds a socket. Only a fully ready chain enters the existing loopback server;
blocked, failed, invalid, tampered, or path-escaping evidence fails closed with
exit `1`, while incomplete or conflicting CLI groups return `2`. Every summary
is deterministic, path-redacted, and keeps `decision_ready=false`; no data
refresh, AI/API call, external network, or trading action is added.

## Node81 Node80 startup gate E2E smoke

Node81 provides an explicit one-shot operational smoke test for the Node80
startup path:

```bash
python -m a_share_ai.cli run-daily-research-service-release-run-admission-startup-gate-smoke \
  --daily-smoke-admission <node78-manifest> \
  --daily-smoke-admission-report <node78-report> \
  --daily-smoke-admission-audit-report <node79-audit-report> \
  --daily-release-manifest <node70-manifest> \
  --daily-release-report <node70-report> \
  --daily-release-audit-report <node70-audit-report> \
  --artifact-root reports --output-dir reports/node81-smoke
```

It reuses the Node80 gate, starts the existing loopback receipt service, runs
the existing four-route Node60 probe, confirms the process remains alive,
performs a controlled stop, and checks that the bound port is released. The
single JSON receipt is relative-path-only, self-hashed, deterministic in shape,
and always keeps `decision_ready=false`. Blocked/invalid gate inputs fail before
process creation; startup, probe, stop, and port-release failures return `1`;
invalid timeout or output configuration returns `2`. The node adds no route,
daemon, scheduler, external network, AI/API, data refresh, or trading behavior.
