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
from .daily_research_service_launch_gate_audit import (
    DAILY_RESEARCH_SERVICE_LAUNCH_GATE_AUDIT_VERSION,
    DailyResearchServiceLaunchGateAuditError,
    audit_daily_research_service_launch_gate,
)
from .daily_research_service_launch_gate_startup import (
    DailyResearchServiceLaunchGateStartupConfig,
    DailyResearchServiceLaunchGateStartupError,
    daily_research_service_launch_gate_startup_check_report,
    load_daily_research_service_launch_gate_startup,
)
from .daily_research_service_probe import (
    DAILY_RESEARCH_SERVICE_PROBE_VERSION,
    DEFAULT_DAILY_RESEARCH_PROBE_TIMEOUT_SECONDS,
    DailyResearchServiceProbeError,
    daily_research_service_probe_exit_code,
    probe_daily_research_service,
)
from .daily_research_service_release import (
    DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
    DailyResearchServiceReleaseError,
    build_daily_research_service_release,
)
from .daily_research_service_release_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_VERSION,
    DailyResearchServiceReleaseAuditError,
    audit_daily_research_service_release,
)
from .daily_research_service_release_run import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION,
    DailyResearchServiceReleaseRunError,
    run_daily_research_service_release,
)
from .daily_research_service_release_run_admission import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION,
    DailyResearchServiceReleaseRunAdmissionError,
    build_daily_research_service_release_run_admission,
)
from .daily_research_service_release_run_admission_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION,
    DailyResearchServiceReleaseRunAdmissionAuditError,
    audit_daily_research_service_release_run_admission,
)
from .daily_research_service_release_run_admission_startup import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_VERSION,
    DailyResearchServiceReleaseRunAdmissionStartupConfig,
    DailyResearchServiceReleaseRunAdmissionStartupError,
    run_daily_research_service_release_run_admission_startup,
)
from .daily_research_service_release_run_admission_startup_smoke import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION,
    DailyResearchServiceReleaseRunAdmissionStartupSmokeError,
    run_daily_research_service_release_run_admission_startup_smoke,
)
from .daily_research_service_release_run_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_VERSION,
    DailyResearchServiceReleaseRunAuditError,
    audit_daily_research_service_release_run,
)
from .daily_research_service_release_startup import (
    DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION,
    DailyResearchServiceReleaseStartupConfig,
    DailyResearchServiceReleaseStartupError,
    daily_research_service_release_startup_check_report,
    load_daily_research_service_release_startup,
)
from .daily_research_service_run import (
    DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RUN_VERSION,
    DailyResearchServiceRunError,
    run_daily_research_service,
)
from .daily_research_service_run_audit import (
    DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION,
    DailyResearchServiceRunAuditError,
    audit_daily_research_service_run,
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
    "DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RUN_VERSION",
    "DailyResearchServiceRunError",
    "run_daily_research_service",
    "DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION",
    "DailyResearchServiceRunAuditError",
    "audit_daily_research_service_run",
    "DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_VERSION",
    "DailyResearchServiceReleaseError",
    "build_daily_research_service_release",
    "DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_VERSION",
    "DailyResearchServiceReleaseAuditError",
    "audit_daily_research_service_release",
    "DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION",
    "DailyResearchServiceReleaseStartupConfig",
    "DailyResearchServiceReleaseStartupError",
    "daily_research_service_release_startup_check_report",
    "load_daily_research_service_release_startup",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION",
    "DailyResearchServiceReleaseRunError",
    "run_daily_research_service_release",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_VERSION",
    "DailyResearchServiceReleaseRunAuditError",
    "audit_daily_research_service_release_run",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION",
    "DailyResearchServiceReleaseRunAdmissionError",
    "build_daily_research_service_release_run_admission",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION",
    "DailyResearchServiceReleaseRunAdmissionAuditError",
    "audit_daily_research_service_release_run_admission",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_VERSION",
    "DailyResearchServiceReleaseRunAdmissionStartupConfig",
    "DailyResearchServiceReleaseRunAdmissionStartupError",
    "run_daily_research_service_release_run_admission_startup",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION",
    "DailyResearchServiceReleaseRunAdmissionStartupSmokeError",
    "run_daily_research_service_release_run_admission_startup_smoke",
    "DAILY_RESEARCH_SERVICE_LAUNCH_GATE_VERSION",
    "DailyResearchServiceLaunchGateConfig",
    "DailyResearchServiceLaunchGateError",
    "build_daily_research_service_launch_gate",
    "check_daily_research_service_launch_gate",
    "daily_research_service_launch_gate_check_report",
    "load_daily_research_service_launch_gate",
    "DAILY_RESEARCH_SERVICE_LAUNCH_GATE_AUDIT_VERSION",
    "DailyResearchServiceLaunchGateAuditError",
    "audit_daily_research_service_launch_gate",
    "DailyResearchServiceLaunchGateStartupConfig",
    "DailyResearchServiceLaunchGateStartupError",
    "daily_research_service_launch_gate_startup_check_report",
    "load_daily_research_service_launch_gate_startup",
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
