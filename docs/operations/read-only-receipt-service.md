# Read-only receipt service operations

## Start

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
