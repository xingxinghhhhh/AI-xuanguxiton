# Phase 55: read-only service launch manifest MVP

## Goal

Node55 turns the Node53 manual service arguments into a strict, versioned launch
manifest. The manifest is a small deployment handoff that can be checked before
binding a port and can be switched back to an older receipt manifest for a
read-only rollback.

## Contract

The standard file name is `read_only_receipt_service_launch.json` and it is
validated with `--artifact-root`:

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

Unknown fields are rejected. The manifest itself and both relative artifact
paths must remain inside `artifact-root`; hosts are limited to `127.0.0.1` and
`::1`. Declared file hashes must match the current bytes. The Node52 receipt,
report self-hashes, cross-fields, readiness state, and `decision_ready=false`
are then validated through the Node53 loader.

## Runtime

Check a manifest without listening:

```bash
python -m a_share_ai.cli serve-research-receipt \
  --launch-manifest reports/service/read_only_receipt_service_launch.json \
  --artifact-root reports/service \
  --check-only
```

Start the same validated configuration:

```bash
python -m a_share_ai.cli serve-research-receipt \
  --launch-manifest reports/service/read_only_receipt_service_launch.json \
  --artifact-root reports/service
```

The direct `--receipt`/`--receipt-report` form remains available for
compatibility. After startup, Node54 remains the readiness gate:

```bash
python -m a_share_ai.cli probe-research-receipt-service \
  --base-url http://127.0.0.1:8765
```

`--check-only` uses the same manifest, hash, and Node52/53 receipt validation
as real startup but never binds a socket. A valid stale or blocked receipt is
preserved as stale or blocked; validation does not upgrade it to ready.

## Scope and rollback

Node55 adds no network provider, database, scheduler, authentication, UI,
trading path, or runtime dependency. Rollback is selecting the previous valid
launch manifest, then re-running `--check-only` and the Node54 probe. Removing
the Node55 module, tests, documentation, and CLI branch returns the repository
to Node54 without changing Node52/53 artifacts or response fields.
