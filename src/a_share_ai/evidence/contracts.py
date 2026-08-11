"""Versioned contracts for an auditable analysis input bundle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "1.0"
BUNDLE_VERSION_V1 = "analysis-input-v1"
BUNDLE_VERSION_V2 = "analysis-input-v2"
SUPPORTED_BUNDLE_VERSIONS = frozenset({BUNDLE_VERSION_V1, BUNDLE_VERSION_V2})
MARKET_CONTEXT_SUMMARY_VERSION = "market-context-summary-v1"
# Backward-compatible alias for callers that explicitly build v1 bundles.
BUNDLE_VERSION = BUNDLE_VERSION_V1


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    """A reference to an existing report or artifact, never a copied payload."""

    name: str
    report_path: str
    report_sha256: str
    artifact_paths: tuple[str, ...]
    artifact_sha256: tuple[str, ...]
    source: str
    symbol: str
    as_of: str
    status: str
    ready: bool

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "report_path": self.report_path,
            "report_sha256": self.report_sha256,
            "artifact_paths": list(self.artifact_paths),
            "artifact_sha256": list(self.artifact_sha256),
            "source": self.source,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "status": self.status,
            "ready": self.ready,
        }


@dataclass(frozen=True, slots=True)
class AnalysisInputBundle:
    """Deterministic references and concise summaries for one stock and cutoff."""

    schema_version: str
    bundle_version: str
    source: str
    symbol: str
    as_of: str
    as_of_date: str
    evidence: tuple[EvidenceEntry, ...]
    summaries: tuple[tuple[str, Any], ...]
    analysis_input_ready: bool
    decision_ready: bool

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "bundle_version": self.bundle_version,
            "source": self.source,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "as_of_date": self.as_of_date,
            "evidence": [entry.to_mapping() for entry in self.evidence],
            "summaries": dict(self.summaries),
            "analysis_input_ready": self.analysis_input_ready,
            "decision_ready": self.decision_ready,
        }


@dataclass(frozen=True, slots=True)
class AnalysisInputReport:
    """Audit report for the bundle and every upstream validation issue."""

    schema_version: str
    bundle_version: str
    source: str
    symbol: str
    as_of: str
    bundle_sha256: str
    status: str
    analysis_input_ready: bool
    decision_ready: bool
    issues: tuple[tuple[str, str], ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "bundle_version": self.bundle_version,
            "source": self.source,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "bundle_sha256": self.bundle_sha256,
            "status": self.status,
            "analysis_input_ready": self.analysis_input_ready,
            "decision_ready": self.decision_ready,
            "issues": [{"code": code, "message": message} for code, message in self.issues],
        }
