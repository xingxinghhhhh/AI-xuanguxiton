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
