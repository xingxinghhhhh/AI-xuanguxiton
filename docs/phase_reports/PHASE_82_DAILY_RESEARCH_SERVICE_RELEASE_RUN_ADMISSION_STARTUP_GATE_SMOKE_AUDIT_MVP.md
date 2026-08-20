# Node82 — Node81 startup-gate smoke receipt audit MVP

Node82 adds an independent, offline audit boundary for the Node81 E2E smoke
receipt. It does not rerun the smoke and does not claim to independently prove
historical port-release facts or defend against coordinated rewriting of all
inputs.

## Audit scope

- Validate the exact Node81 receipt schema, version, UTF-8 JSON and canonical
  self-hash.
- Recompute the Node81 receipt SHA-256 and the actual SHA-256 of its six
  declared Node78/Node79/Node70 files.
- Enforce artifact-root containment, normalized relative paths, fixed filenames,
  identity/time ordering, enum and type rules, and the full state machine.
- Produce one deterministic, path-redacted audit receipt with
  `decision_ready=false`.

The command never starts a process, creates a socket, runs the Node60 probe,
calls the Node70 loader, accesses HTTP/network/API services, or modifies any
input file.

## State and exit behavior

Ready evidence returns `0` with `audit_ready=true`. Legal blocked or failed
Node81 evidence returns `1` with `audit_ready=true`. Invalid, tampered,
malformed, path-escaping, type-invalid, or state-inconsistent evidence returns
`1` with `audit_ready=false`. Artifact-root or output configuration errors
return `2`.
