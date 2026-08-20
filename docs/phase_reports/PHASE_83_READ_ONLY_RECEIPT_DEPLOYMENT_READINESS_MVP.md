# Node83 — Read-only receipt single-host deployment readiness MVP

Node83 adds an operator-triggered, platform-neutral pre-deployment check for
the existing read-only receipt service. It validates a deployment specification
and binds it to the Node81 smoke receipt and Node82 independent audit receipt.

The check does not start `serve-research-receipt`, run HTTP probes, call
subprocesses, refresh data, call AI or external APIs, or authorize trading.
The configured host and port are deployment-spec-only values because Node81 and
Node82 receipts do not contain host/port fields. Port availability is only the
result of a loopback bind-and-close check at the instant of validation.

## Command

```bash
python -m a_share_ai.cli check-read-only-receipt-deployment \
  --spec reports/read-only-receipt-deployment-spec.json \
  --artifact-root reports \
  --output-dir reports/read-only-receipt-deployment-readiness
```

The specification is a fixed-schema JSON object with the deployment version and
mode, relative Node81/Node82 receipt paths and SHA-256 values, a loopback host,
a valid port, and `decision_ready=false`. The output is
`read_only_receipt_service_deployment_readiness_report.json` with a deterministic
self-hash and only relative paths.

Ready evidence requires valid, self-consistent Node81/Node82 receipts and a
currently bindable configured loopback port. Legal upstream blocked/failed
evidence or an occupied port returns `deployment_status=blocked` and exit `1`.
Malformed, tampered, out-of-root, type-invalid, or inconsistent evidence is
`invalid` and exit `1`; configuration errors return `2`. The report always keeps
`decision_ready=false` and uses the fixed manual rollback policy
`manual-previous-validated-spec-v1`.
