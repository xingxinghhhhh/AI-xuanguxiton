"""Small, read-only service entry points for the A-share system."""

from .launch_config import (
    READ_ONLY_RECEIPT_SERVICE_LAUNCH_VERSION,
    ReadOnlyReceiptLaunchConfig,
    ReadOnlyReceiptLaunchError,
    check_read_only_receipt_launch,
    launch_check_report,
    load_read_only_receipt_launch_config,
    serve_read_only_receipt_launch,
)
from .read_only_receipt_probe import (
    READ_ONLY_RECEIPT_SERVICE_PROBE_VERSION,
    ReadOnlyReceiptProbeError,
    probe_exit_code,
    probe_read_only_receipt_service,
)
from .read_only_receipt_server import (
    READ_ONLY_RECEIPT_SERVICE_VERSION,
    ReadOnlyReceiptServiceError,
    create_read_only_receipt_server,
    load_daily_research_admission_summary,
    serve_read_only_receipt,
)

__all__ = [
    "READ_ONLY_RECEIPT_SERVICE_VERSION",
    "ReadOnlyReceiptServiceError",
    "create_read_only_receipt_server",
    "load_daily_research_admission_summary",
    "serve_read_only_receipt",
    "READ_ONLY_RECEIPT_SERVICE_PROBE_VERSION",
    "ReadOnlyReceiptProbeError",
    "probe_exit_code",
    "probe_read_only_receipt_service",
    "READ_ONLY_RECEIPT_SERVICE_LAUNCH_VERSION",
    "ReadOnlyReceiptLaunchConfig",
    "ReadOnlyReceiptLaunchError",
    "check_read_only_receipt_launch",
    "launch_check_report",
    "load_read_only_receipt_launch_config",
    "serve_read_only_receipt_launch",
]
