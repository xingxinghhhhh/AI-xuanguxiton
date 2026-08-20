# Node77 — Daily research release-run admission startup smoke audit MVP

## Purpose

Node77 adds an independent, offline audit boundary for the Node76 one-shot
startup smoke receipt. It turns the runtime receipt into a separately
recomputable deployment artifact without overstating what a file audit can
prove about a historical process or port.

## Scope

- Recompute the Node76 smoke report self-hash and actual file SHA-256 values.
- Validate normalized artifact-root-relative paths and fixed filenames.
- Independently validate all six admission/release input schemas, self-hashes,
  versions, decision gates, status/readiness chains, cross-references, and
  actual SHA-256 values when present.
- Validate the Node75 check-only and serve report schemas, self-hashes, modes,
  versions, identities, readiness, status, and input-chain consistency,
  including explicit ready/blocked/failed smoke-state bindings.
- Preserve valid ready, blocked, and failed smoke outcomes as auditable states.
- Keep the audit entirely offline and read-only.

The node does not start a process, open a socket, call HTTP, inspect current
ports, rerun Node70–76, modify upstream artifacts, refresh data, call AI or
external APIs, connect to a broker, trade, or set `decision_ready=true`.

## Output contract

The command writes
`daily_research_service_release_run_admission_startup_smoke_audit_report.json`.
It records the smoke, Node75, admission, and release versions; all declared
relative paths and actual SHA-256 values; identity and runtime state; audit
status; sanitized issues; and a compact self-hash. It never emits absolute
paths, PIDs, commands, logs, credentials, secrets, or environment variables.

`audit_ready=true` means the supplied receipt and its referenced evidence are
internally consistent. For a valid blocked/failed run, `run_ready` remains
false and the CLI returns `1`. Only a valid ready receipt returns `0`; invalid
evidence returns `1` with `audit_ready=false`; configuration errors return
`2`. If Node76 stops at blocked/failed admission before release startup, the
three release inputs may be absent; the audit validates the complete available
admission chain without claiming missing release evidence. The audit does not
independently prove historical port release.

## Verification

The focused suite covers ready, blocked, failed, and invalid reports; explicit
status-chain mismatches; independent tampering of each of the six upstream
receipts; unknown fields; self-hash/path/SHA changes; deterministic output;
input preservation; CLI configuration handling; and the absence of subprocess,
socket, or HTTP operations. Full repository tests and static/build gates remain
required before publishing the node.
