# Node79 — Daily research release-run admission startup smoke admission audit MVP

## Purpose

Node79 adds an independent, offline audit boundary for the Node78 admission
manifest/report pair. It confirms that the two files are internally bound,
self-hashed, root-relative, and semantically consistent without treating that
pair as external proof of any upstream runtime or port fact.

## Scope

- Read only the Node78 manifest and Node78 report supplied by the caller.
- Validate exact schemas, UTF-8 JSON, fixed filenames, artifact-root boundaries,
  normalized relative paths, actual SHA-256 values, and both input self-hashes.
- Validate Node78, Node76, and Node77 version links, report-to-manifest
  references, field equality, decision gates, and ready/blocked/failed/invalid
  state semantics.
- Write one deterministic, self-hashed audit report with sanitized issues.
- Keep the implementation offline and read-only.

The node does not read Node76/Node77 files, rerun Node76/Node77/Node78, start
a process, open a socket, call HTTP or network services, use API keys, refresh
data, generate trading fields, or set `decision_ready=true`. It cannot prove
that the pair was not jointly rewritten by an external actor.

## Output contract

The command is:

```bash
python -m a_share_ai.cli \
  audit-daily-research-service-release-run-admission-startup-smoke-admission \
  --admission PATH \
  --report PATH \
  --artifact-root PATH \
  --output-dir PATH
```

It writes:

`daily_research_service_release_run_admission_startup_smoke_admission_audit_report.json`

with version
`daily-research-service-release-run-admission-startup-smoke-admission-audit-v1`.
The report contains the manifest/report paths and actual hashes, the Node78,
Node76, and Node77 version links, runtime/admission state, `input_audit_ready`,
sanitized issues, `audit_ready`, and a canonical self-hash. It always keeps
`decision_ready=false`.

Ready pairs return `0` with `audit_ready=true`. Consistent blocked or failed
pairs return `1`, preserve their bounded state, and have `audit_ready=true` but
`admission_ready=false`. Invalid, malformed, tampered, path-escaping,
version-incompatible, or contradictory pairs return `1` with
`status=invalid` and `audit_ready=false`. Artifact-root/output configuration
errors return `2`.

## Verification

The focused suite covers ready, blocked, failed, self-hash and SHA tampering,
single-file field drift, unknown fields, invalid JSON/UTF-8/types/enums,
decision-gate violations, absolute and out-of-root paths, input preservation,
deterministic output, CLI exit codes, and the absence of subprocess, socket,
HTTP, or network operations. Node70–78 regression tests, static checks,
compilation, editable installation, and the full repository suite remain
required before publishing the node.
