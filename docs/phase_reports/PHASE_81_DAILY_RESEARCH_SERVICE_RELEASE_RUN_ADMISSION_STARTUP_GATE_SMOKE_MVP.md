# Node81 — Node80 startup gate E2E smoke MVP

Node81 validates the actual production-shaped loopback startup path after
Node80 admission. It is a one-shot operator command, not a daemon or scheduler.

## Flow

1. Reuse Node80 to validate Node78/Node79 and Node70 evidence.
2. Start the existing `serve-research-receipt` service through the Node80 CLI
   parameter group.
3. Run the existing Node60 four-route probe and require exit `0`.
4. Confirm the child process remains alive after probing.
5. Stop it using the existing controlled-stop helper.
6. Verify that the loopback port is released and write one sanitized receipt.

The receipt is
`daily_research_service_release_run_admission_startup_gate_smoke_report.json`.
It contains the Node80 evidence references, identity, gate/startup/probe/stop
states, `port_released`, sanitized issues, a deterministic self-hash, and
`decision_ready=false`. Paths are relative to `artifact-root`; PIDs, commands,
logs, environment variables, absolute paths, and secrets are excluded.

## Failure behavior

Blocked or invalid Node80 gates return `1` without creating a child process.
Startup, probe, process-liveness, controlled-stop, or port-release failures
return `1` after best-effort cleanup. Invalid timeout, artifact-root, or output
configuration returns `2`. Only a complete successful flow returns `0` and
`smoke_ready=true`.

Node81 does not modify Node70–80 artifacts or CLI semantics and adds no HTTP
routes, database, scheduler, daemon, external network, AI/API, data refresh,
broker, or trading capability.
