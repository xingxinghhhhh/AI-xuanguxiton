# Node76 — Daily research release-run admission startup smoke MVP

## Purpose

Node75 validates that the audited admission chain may bind the existing
loopback service. Node76 closes the next operational gap with one controlled,
manual smoke run that proves the full local start, probe, liveness, stop, and
port-release sequence.

## Scope

- Run the Node75 check-only path into `output-dir/preflight/`.
- Start the existing Node75 service path once into `output-dir/startup/`.
- Reuse the Node60 four-route loopback probe.
- Confirm the child is alive after a successful probe.
- Reuse the Node66/71 controlled process-stop classification.
- Verify the loopback port is released and write one sanitized smoke receipt.

The node does not change Node70–75 contracts, refresh market data, call AI or
external APIs, add a scheduler/daemon/database/UI/authentication layer, connect
to a broker, place orders, or set `decision_ready=true`.

## Output contract

The final file is
`daily_research_service_release_run_admission_startup_smoke_report.json`.
It records the Node73/74 admission paths and the three Node70 release paths
with actual SHA-256 values, Node75 preflight/startup receipt paths and hashes,
identity fields, startup/probe/stop state, sanitized issues, and a compact
self-hash. All paths are artifact-root-relative; process IDs, command lines,
logs, credentials, secrets, and absolute paths are excluded.

Status is `ready` only after a ready preflight, a ready Node75 startup, exit
code `0` from all four Node60 probes, a live process check, a controlled stop,
and port release. Configuration errors return `2`; evidence or runtime
failures return `1`; `decision_ready` is always `false`.

## Verification

The focused test module covers ready end-to-end execution, blocked/failed
preflight fail-closed behavior, deterministic self-hash/path output, timeout
validation, and output-root escape rejection. The full service suite and full
repository suite remain required before publishing the node.
