"""Small, read-only service entry points for the A-share system."""

from .daily_research_service_launch_gate import (
    DAILY_RESEARCH_SERVICE_LAUNCH_GATE_VERSION,
    DailyResearchServiceLaunchGateConfig,
    DailyResearchServiceLaunchGateError,
    build_daily_research_service_launch_gate,
    check_daily_research_service_launch_gate,
    daily_research_service_launch_gate_check_report,
    load_daily_research_service_launch_gate,
)
from .daily_research_service_probe import (
    DAILY_RESEARCH_SERVICE_PROBE_VERSION,
    DEFAULT_DAILY_RESEARCH_PROBE_TIMEOUT_SECONDS,
    DailyResearchServiceProbeError,
    daily_research_service_probe_exit_code,
    probe_daily_research_service,
)
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
    "DAILY_RESEARCH_SERVICE_PROBE_VERSION",
    "DEFAULT_DAILY_RESEARCH_PROBE_TIMEOUT_SECONDS",
    "DailyResearchServiceProbeError",
    "probe_daily_research_service",
    "daily_research_service_probe_exit_code",
    "DAILY_RESEARCH_SERVICE_LAUNCH_GATE_VERSION",
    "DailyResearchServiceLaunchGateConfig",
    "DailyResearchServiceLaunchGateError",
    "build_daily_research_service_launch_gate",
    "check_daily_research_service_launch_gate",
    "daily_research_service_launch_gate_check_report",
    "load_daily_research_service_launch_gate",
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
