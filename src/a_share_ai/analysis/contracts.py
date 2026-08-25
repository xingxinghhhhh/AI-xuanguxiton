"""Versioned contracts for evidence-backed research analysis reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

ANALYSIS_REPORT_SCHEMA_VERSION = "1.0"
ANALYSIS_REPORT_VERSION = "analysis-report-v1"

EVIDENCE_IDS = (
    "market",
    "technical",
    "price_plan",
    "profitability",
    "growth",
    "announcements",
)
ANALYSIS_SECTIONS = (
    "market",
    "technical",
    "price_plan",
    "profitability",
    "growth",
    "announcements",
    "risks",
    "unknowns",
)
ALLOWED_CLAIM_KINDS = ("observation", "risk", "unknown")


@dataclass(frozen=True, slots=True)
class AnalysisReportConfig:
    """Inputs for one offline or explicitly selected real-provider report build."""

    bundle_path: Path
    input_root: Path
    response_fixture: Path | None
    output_dir: Path
    bundle_report: Path | None = None
    provider: str = "offline"
    model: str = "gpt-5.6-luna"
    timeout_seconds: float = 60.0
    env_file: Path | None = None

    def resolved_bundle_report(self) -> Path:
        return self.bundle_report or self.bundle_path.with_name("analysis_input_report.json")


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    """Expanded citation derived from one analysis-input bundle entry."""

    evidence_id: str
    report_path: str
    report_sha256: str
    artifact_paths: tuple[str, ...]
    artifact_sha256: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "artifact_paths": list(self.artifact_paths),
            "artifact_sha256": list(self.artifact_sha256),
            "evidence_id": self.evidence_id,
            "report_path": self.report_path,
            "report_sha256": self.report_sha256,
        }


@dataclass(frozen=True, slots=True)
class AnalysisClaim:
    """One observation, risk, or unknown with citation IDs."""

    claim_id: str
    kind: str
    text: str
    citation_ids: tuple[str, ...]
    observed_dates: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "citation_ids": list(self.citation_ids),
            "kind": self.kind,
            "observed_dates": list(self.observed_dates),
            "text": self.text,
        }
