# Node80 — Daily research release-run admission startup gate MVP

Node80 adds a final, read-only startup binding gate for the existing loopback
research receipt service.

## Scope

- Validate the Node78 admission manifest/report and Node79 independent audit
  report before startup.
- Require exact schemas, versions, self-hashes, actual file SHA-256 values,
  fixed filenames, relative paths, identity, timestamps, status and readiness
  consistency.
- Delegate the Node70 release-manifest/report/audit validation to its existing
  startup loader.
- Reuse the existing `serve_read_only_receipt` entrypoint only after every gate
  is ready.
- Keep check-only mode offline and socket-free, with deterministic,
  path-redacted output.

The node fails closed for blocked, failed, invalid, tampered, or out-of-root
evidence. It does not add an interface, refresh data, call external services or
AI APIs, authorize trading, or set `decision_ready=true`.

## CLI

The existing command accepts the separate Node80 group:

```bash
python -m a_share_ai.cli serve-research-receipt \
  --daily-smoke-admission <node78-manifest> \
  --daily-smoke-admission-report <node78-report> \
  --daily-smoke-admission-audit-report <node79-audit-report> \
  --daily-release-manifest <node70-manifest> \
  --daily-release-report <node70-report> \
  --daily-release-audit-report <node70-audit-report> \
  --artifact-root <artifact-root> --check-only
```

Check-only returns `0` only for a fully ready chain. Non-ready evidence returns
`1`; incomplete or conflicting option groups return `2`. The summary contains
only relative paths, SHA-256 values, normalized identity/status fields,
fail-closed state, and a deterministic `output_sha256`; it never includes
absolute paths, PIDs, commands, logs, or secrets.

## Verification

The Node80 implementation is covered by focused tests for ready check-only,
offline behavior, actual entrypoint reuse, blocked/failed/invalid fail-closed
behavior, loader isolation, CLI configuration, and path redaction. The full
repository suite and static checks are required before commit and push.
