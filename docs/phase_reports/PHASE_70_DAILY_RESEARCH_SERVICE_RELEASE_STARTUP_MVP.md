# Phase 70: Daily research service release startup admission MVP

Node70 binds the Node68 release Manifest/Report and Node69 independent audit to
the existing Node65 loopback startup chain. It permits startup only after all
three layers are ready and the referenced Node66/67 and Node65 artifacts remain
valid.

## Contract

The new loader is
`daily-research-service-release-startup-v1` in
`a_share_ai.service.daily_research_service_release_startup`.

`load_daily_research_service_release_startup` validates the fixed Node68 and
Node69 filenames, exact schemas, canonical self-hashes, actual SHA-256 values,
artifact-root boundaries, symbol/timestamp equality, readiness/status fields,
and `decision_ready=false`. It then rechecks Node66/67 and the Node65 gate
references before delegating the gate validation to the existing Node65 startup
loader. It does not rebuild or audit artifacts and does not open a socket.

The check-only summary contains only artifact-root-relative paths, hashes,
symbol/timestamps, readiness and `decision_ready=false`; it does not expose
absolute paths or the internal gate configuration.

## CLI

`serve-research-receipt` accepts the complete release group:

```text
--daily-release-manifest
--daily-release-report
--daily-release-audit-report
```

The group reuses `--artifact-root` and cannot be mixed with direct, legacy
launch-manifest, daily-admission, daily-launch-gate, host, or port options.
When supplied, the loader supplies the validated Node65 host, port and receipt
inputs. `--check-only` validates the complete chain and never starts a server.
Old direct, Node55, Node63 and Node65 paths remain available when the release
group is omitted.

## Safety boundary

Blocked, failed, invalid, tampered, missing, out-of-root, or non-ready release
chains return before any socket bind. The implementation does not refresh
market data, call DeepSeek/Baostock, read API keys, access external network
endpoints, schedule work, modify input artifacts, create trading decisions, or
set `decision_ready=true`.

## Verification

The Node70 tests cover ready check-only, real loopback startup with the Node60
four-route probe, blocked/failed admission, artifact tampering, missing and
out-of-root inputs, argument combinations, bind failure, input immutability,
and old startup-path regression coverage. The full repository test and static
verification commands remain required before publishing the node.
