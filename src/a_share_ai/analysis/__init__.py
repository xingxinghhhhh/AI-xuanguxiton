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
from .quality import ANALYSIS_QUALITY_VERSION, AnalysisQualityError, audit_analysis_quality
from .real_provider import OpenAIAnalysisProvider, ProviderTransportError, UrllibResponseTransport
from .release_diff import (
    RESEARCH_RELEASE_DIFF_VERSION,
    ResearchReleaseDiffError,
    compare_research_releases,
)
from .renderer import AnalysisRenderError, render_analysis
from .request_builder import (
    RequestBuildError,
    build_analysis_context,
    build_deepseek_request,
    build_openai_request,
    provider_output_schema,
)
from .research_freshness import (
    RESEARCH_FRESHNESS_VERSION,
    ResearchFreshnessError,
    audit_research_freshness,
)
from .research_release import (
    RESEARCH_RELEASE_VERSION,
    ResearchReleaseError,
    build_research_release,
)
from .review import ANALYSIS_REVIEW_VERSION, AnalysisReviewError, build_analysis_review
from .review_record import (
    ANALYSIS_REVIEW_RECORD_VERSION,
    AnalysisReviewRecordError,
    apply_analysis_review,
)
from .safety import ANALYSIS_SAFETY_VERSION, AnalysisSafetyError, audit_analysis_safety
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
    "AnalysisRenderError",
    "AnalysisQualityError",
    "ANALYSIS_QUALITY_VERSION",
    "AnalysisSafetyError",
    "ANALYSIS_SAFETY_VERSION",
    "AnalysisReviewError",
    "ANALYSIS_REVIEW_VERSION",
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
    "render_analysis",
    "audit_analysis_quality",
    "audit_analysis_safety",
    "build_analysis_review",
    "ANALYSIS_REVIEW_RECORD_VERSION",
    "AnalysisReviewRecordError",
    "apply_analysis_review",
    "RESEARCH_RELEASE_VERSION",
    "ResearchReleaseError",
    "build_research_release",
    "RESEARCH_FRESHNESS_VERSION",
    "ResearchFreshnessError",
    "audit_research_freshness",
    "RESEARCH_RELEASE_DIFF_VERSION",
    "ResearchReleaseDiffError",
    "compare_research_releases",
]
