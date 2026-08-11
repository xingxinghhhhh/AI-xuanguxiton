"""Analysis features built from validated market data."""

from .contracts import (
    ALLOWED_CLAIM_KINDS,
    ANALYSIS_REPORT_SCHEMA_VERSION,
    ANALYSIS_REPORT_VERSION,
    ANALYSIS_SECTIONS,
    EVIDENCE_IDS,
    AnalysisClaim,
    AnalysisReportConfig,
)
from .deepseek_provider import (
    DeepSeekAnalysisProvider,
    DeepSeekTransportError,
    UrllibDeepSeekTransport,
)
from .offline_provider import AnalysisProvider, OfflineAnalysisProvider, ProviderError
from .real_provider import OpenAIAnalysisProvider, ProviderTransportError, UrllibResponseTransport
from .request_builder import (
    RequestBuildError,
    build_analysis_context,
    build_deepseek_request,
    build_openai_request,
    provider_output_schema,
)
from .technical_features import (
    INDICATOR_VERSION,
    TechnicalFeatureReport,
    TechnicalFeatureSnapshot,
    build_feature_report,
    compute_feature_snapshots,
    serialize_feature_snapshots,
)
from .validator import AnalysisReportSource, AnalysisValidationError

__all__ = [
    "ANALYSIS_REPORT_SCHEMA_VERSION",
    "ANALYSIS_REPORT_VERSION",
    "ANALYSIS_SECTIONS",
    "ALLOWED_CLAIM_KINDS",
    "AnalysisProvider",
    "AnalysisClaim",
    "AnalysisReportConfig",
    "AnalysisReportSource",
    "AnalysisValidationError",
    "DeepSeekAnalysisProvider",
    "DeepSeekTransportError",
    "INDICATOR_VERSION",
    "OfflineAnalysisProvider",
    "OpenAIAnalysisProvider",
    "ProviderError",
    "ProviderTransportError",
    "RequestBuildError",
    "UrllibResponseTransport",
    "UrllibDeepSeekTransport",
    "EVIDENCE_IDS",
    "TechnicalFeatureReport",
    "TechnicalFeatureSnapshot",
    "build_feature_report",
    "compute_feature_snapshots",
    "serialize_feature_snapshots",
    "build_analysis_context",
    "build_openai_request",
    "build_deepseek_request",
    "provider_output_schema",
]
