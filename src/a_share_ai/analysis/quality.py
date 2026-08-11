"""Offline quality and evidence-coverage audit for analysis-report-v1."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..evidence.contracts import BUNDLE_VERSION_V2, SUPPORTED_BUNDLE_VERSIONS
from ..market.replay import sha256_bytes, write_atomic
from .contracts import ANALYSIS_REPORT_VERSION, ANALYSIS_SECTIONS, EVIDENCE_IDS

ANALYSIS_QUALITY_VERSION = "analysis-quality-v1"
ALLOWED_SECTION_EVIDENCE = {
    "market": {"market"},
    "technical": {"market", "technical"},
    "price_plan": {"price_plan", "technical"},
    "profitability": {"profitability"},
    "growth": {"growth"},
    "announcements": {"announcements"},
    "risks": set(EVIDENCE_IDS),
    "unknowns": set(EVIDENCE_IDS),
}


class AnalysisQualityError(ValueError):
    """A fail-closed quality audit error with a stable issue code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisQualityError("INPUT_JSON_INVALID", f"{label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnalysisQualityError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return payload, raw


def _safe_relative(root: Path, value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise AnalysisQualityError("PATH_INVALID", f"{label} must be relative")
    root_resolved = root.resolve()
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise AnalysisQualityError(
            "PATH_OUTSIDE_INPUT_ROOT", f"{label} escapes input root"
        ) from exc
    return candidate


def _verify_hash(path: Path, expected: Any, *, label: str) -> str:
    if not isinstance(expected, str) or len(expected) != 64:
        raise AnalysisQualityError("HASH_INVALID", f"{label} must be a SHA-256 string")
    try:
        actual = sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise AnalysisQualityError("FILE_UNAVAILABLE", f"{label}: {exc}") from exc
    if actual != expected:
        raise AnalysisQualityError("HASH_MISMATCH", f"{label} hash does not match")
    return actual


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _audit_valid_inputs(
    *,
    analysis: Mapping[str, Any],
    analysis_raw: bytes,
    analysis_report: Mapping[str, Any],
    analysis_report_raw: bytes,
    render_report: Mapping[str, Any],
    render_report_raw: bytes,
    input_root: Path,
    render_report_path: Path,
) -> dict[str, Any]:
    analysis_sha = sha256_bytes(analysis_raw)
    analysis_report_sha = sha256_bytes(analysis_report_raw)
    render_report_sha = sha256_bytes(render_report_raw)

    if analysis_report.get("output_sha256") != analysis_sha:
        raise AnalysisQualityError("ANALYSIS_HASH_MISMATCH", "analysis output SHA does not match")
    if render_report.get("analysis_sha256") != analysis_sha:
        raise AnalysisQualityError(
            "RENDER_ANALYSIS_HASH_MISMATCH", "render analysis SHA does not match"
        )
    if render_report.get("analysis_report_sha256") != analysis_report_sha:
        raise AnalysisQualityError(
            "RENDER_REPORT_HASH_MISMATCH", "render report SHA does not match"
        )
    if render_report.get("status") != "ready":
        raise AnalysisQualityError("RENDER_NOT_READY", "render report is not ready")
    if render_report.get("analysis_ready") is not True:
        raise AnalysisQualityError("ANALYSIS_NOT_READY", "rendered analysis is not ready")
    if render_report.get("decision_ready") is not False:
        raise AnalysisQualityError("DECISION_GATE_INVALID", "decision_ready must be false")
    rendered_path_value = render_report.get("output_path")
    if not isinstance(rendered_path_value, str) or Path(rendered_path_value).is_absolute():
        raise AnalysisQualityError("RENDER_PATH_INVALID", "render output path must be relative")
    rendered_path = (render_report_path.parent / rendered_path_value).resolve()
    try:
        rendered_path.relative_to(render_report_path.parent.resolve())
    except ValueError as exc:
        raise AnalysisQualityError(
            "RENDER_PATH_INVALID", "render output escapes report directory"
        ) from exc
    rendered_sha = _verify_hash(
        rendered_path,
        render_report.get("output_sha256"),
        label="rendered report",
    )

    if analysis.get("analysis_version") != ANALYSIS_REPORT_VERSION:
        raise AnalysisQualityError("ANALYSIS_VERSION_INVALID", "unsupported analysis version")
    if (
        analysis.get("analysis_ready") is not True
        or analysis_report.get("analysis_ready") is not True
    ):
        raise AnalysisQualityError("ANALYSIS_NOT_READY", "analysis_ready must be true")
    if (
        analysis.get("decision_ready") is not False
        or analysis_report.get("decision_ready") is not False
    ):
        raise AnalysisQualityError("DECISION_GATE_INVALID", "decision_ready must be false")
    if analysis.get("symbol") != analysis_report.get("symbol"):
        raise AnalysisQualityError("SYMBOL_MISMATCH", "analysis and report symbols differ")
    if analysis.get("as_of") != analysis_report.get("as_of"):
        raise AnalysisQualityError("AS_OF_MISMATCH", "analysis and report as_of values differ")

    bundle_path = _safe_relative(
        input_root,
        analysis_report.get("input_bundle_path"),
        label="input_bundle_path",
    )
    bundle_sha = _verify_hash(
        bundle_path,
        analysis_report.get("input_bundle_sha256"),
        label="input bundle",
    )
    bundle, _ = _read_json(bundle_path, label="input bundle")
    bundle_version = bundle.get("bundle_version")
    if bundle_version not in SUPPORTED_BUNDLE_VERSIONS:
        raise AnalysisQualityError("BUNDLE_VERSION_INVALID", "unsupported input bundle version")
    if bundle_version == BUNDLE_VERSION_V2:
        summaries = bundle.get("summaries")
        context = (
            summaries.get("market", {}).get("market_context")
            if isinstance(summaries, dict)
            else None
        )
        if not isinstance(context, dict) or context.get("version") != "market-context-v1":
            raise AnalysisQualityError(
                "MARKET_CONTEXT_INVALID", "analysis-input-v2 must include market context"
            )
    sections = analysis.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(ANALYSIS_SECTIONS):
        raise AnalysisQualityError("SECTIONS_INVALID", "analysis must contain all sections")
    citations = analysis.get("citations")
    if not isinstance(citations, list):
        raise AnalysisQualityError("CITATIONS_INVALID", "analysis citations must be a list")
    citation_map: dict[str, dict[str, Any]] = {}
    for index, citation in enumerate(citations):
        if not isinstance(citation, dict):
            raise AnalysisQualityError("CITATION_INVALID", f"citations[{index}] must be an object")
        evidence_id = citation.get("evidence_id")
        if evidence_id not in EVIDENCE_IDS or evidence_id in citation_map:
            raise AnalysisQualityError(
                "CITATION_INVALID", f"citations[{index}] evidence ID is invalid"
            )
        report_path = _safe_relative(
            input_root,
            citation.get("report_path"),
            label=f"{evidence_id}.report_path",
        )
        _verify_hash(report_path, citation.get("report_sha256"), label=f"{evidence_id}.report")
        artifact_paths = citation.get("artifact_paths")
        artifact_shas = citation.get("artifact_sha256")
        if not isinstance(artifact_paths, list) or not isinstance(artifact_shas, list):
            raise AnalysisQualityError(
                "CITATION_INVALID", f"{evidence_id} artifact manifests invalid"
            )
        if len(artifact_paths) != len(artifact_shas):
            raise AnalysisQualityError(
                "CITATION_INVALID", f"{evidence_id} artifact manifests differ"
            )
        for artifact_index, (artifact_path, artifact_sha) in enumerate(
            zip(artifact_paths, artifact_shas)
        ):
            artifact_file = _safe_relative(
                input_root,
                artifact_path,
                label=f"{evidence_id}.artifact[{artifact_index}]",
            )
            _verify_hash(
                artifact_file,
                artifact_sha,
                label=f"{evidence_id}.artifact[{artifact_index}]",
            )
        citation_map[evidence_id] = citation
    if set(citation_map) != set(EVIDENCE_IDS):
        raise AnalysisQualityError("EVIDENCE_COVERAGE", "all six evidence IDs must be present")

    claim_count = 0
    section_counts: dict[str, int] = {}
    evidence_usage = {evidence_id: 0 for evidence_id in EVIDENCE_IDS}
    section_quality: dict[str, dict[str, Any]] = {}
    risk_count = 0
    unknown_count = 0
    for section in ANALYSIS_SECTIONS:
        claims = sections[section]
        if not isinstance(claims, list) or not claims:
            raise AnalysisQualityError("SECTION_COVERAGE", f"section {section} has no claims")
        allowed = ALLOWED_SECTION_EVIDENCE[section]
        section_citations: set[str] = set()
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                raise AnalysisQualityError("CLAIM_INVALID", f"{section}[{index}] is not an object")
            citation_ids = claim.get("citation_ids")
            if not isinstance(citation_ids, list) or not citation_ids:
                raise AnalysisQualityError(
                    "CITATION_MISSING", f"{section}[{index}] has no citations"
                )
            if any(citation_id not in citation_map for citation_id in citation_ids):
                raise AnalysisQualityError(
                    "CITATION_UNKNOWN", f"{section}[{index}] has unknown citation"
                )
            if any(citation_id not in allowed for citation_id in citation_ids):
                raise AnalysisQualityError(
                    "SECTION_EVIDENCE_MISMATCH",
                    f"section {section} cites evidence outside its allowed mapping",
                )
            section_citations.update(citation_ids)
            for citation_id in citation_ids:
                evidence_usage[citation_id] += 1
            claim_count += 1
            if claim.get("kind") == "risk":
                risk_count += 1
            if claim.get("kind") == "unknown":
                unknown_count += 1
        section_counts[section] = len(claims)
        section_quality[section] = {
            "claim_count": len(claims),
            "citation_ids": sorted(section_citations),
            "status": "pass",
        }

    if any(count == 0 for count in evidence_usage.values()):
        raise AnalysisQualityError(
            "EVIDENCE_COVERAGE", "each evidence ID must be cited at least once"
        )
    if not sections["risks"] or not sections["unknowns"]:
        raise AnalysisQualityError("RISK_UNKNOWN_COVERAGE", "risks and unknowns must be present")
    return {
        "analysis_report_sha256": analysis_report_sha,
        "analysis_sha256": analysis_sha,
        "bundle_sha256": bundle_sha,
        "claim_count": claim_count,
        "evidence_usage": evidence_usage,
        "render_report_sha256": render_report_sha,
        "rendered_report_sha256": rendered_sha,
        "risk_claim_count": risk_count,
        "section_claim_counts": section_counts,
        "section_quality": section_quality,
        "unknown_claim_count": unknown_count,
    }


def audit_analysis_quality(
    *,
    analysis_path: Path,
    analysis_report_path: Path,
    render_report_path: Path,
    input_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Run an offline quality audit and always write its audit report."""

    output_path = output_dir / "analysis_quality_report.json"
    try:
        analysis, analysis_raw = _read_json(analysis_path, label="analysis")
        analysis_report, analysis_report_raw = _read_json(
            analysis_report_path, label="analysis_report"
        )
        render_report, render_report_raw = _read_json(
            render_report_path, label="render_report"
        )
        details = _audit_valid_inputs(
            analysis=analysis,
            analysis_raw=analysis_raw,
            analysis_report=analysis_report,
            analysis_report_raw=analysis_report_raw,
            render_report=render_report,
            render_report_raw=render_report_raw,
            input_root=input_root,
            render_report_path=render_report_path,
        )
        report: dict[str, Any] = {
            "analysis_quality_version": ANALYSIS_QUALITY_VERSION,
            "analysis_ready": True,
            "analysis_version": ANALYSIS_REPORT_VERSION,
            "as_of": analysis.get("as_of"),
            "claim_count": details["claim_count"],
            "citation_coverage_percent": 100.0,
            "decision_ready": False,
            "evidence_usage": details["evidence_usage"],
            "input_bundle_sha256": details["bundle_sha256"],
            "issues": [],
            "model": analysis_report.get("model"),
            "output_sha256": details["analysis_sha256"],
            "provider": analysis_report.get("provider"),
            "quality_ready": True,
            "quality_status": "pass",
            "rendered_report_sha256": details["rendered_report_sha256"],
            "risk_claim_count": details["risk_claim_count"],
            "section_claim_counts": details["section_claim_counts"],
            "section_quality": details["section_quality"],
            "symbol": analysis.get("symbol"),
            "unknown_claim_count": details["unknown_claim_count"],
        }
    except AnalysisQualityError as exc:
        report = {
            "analysis_quality_version": ANALYSIS_QUALITY_VERSION,
            "analysis_ready": False,
            "analysis_version": ANALYSIS_REPORT_VERSION,
            "as_of": None,
            "claim_count": 0,
            "citation_coverage_percent": 0.0,
            "decision_ready": False,
            "evidence_usage": {evidence_id: 0 for evidence_id in EVIDENCE_IDS},
            "input_bundle_sha256": None,
            "issues": [_issue(exc.code, str(exc))],
            "model": None,
            "output_sha256": None,
            "provider": None,
            "quality_ready": False,
            "quality_status": "invalid",
            "rendered_report_sha256": None,
            "risk_claim_count": 0,
            "section_claim_counts": {section: 0 for section in ANALYSIS_SECTIONS},
            "section_quality": {},
            "symbol": None,
            "unknown_claim_count": 0,
        }
    except Exception as exc:  # pragma: no cover - final safety gate
        report = {
            "analysis_quality_version": ANALYSIS_QUALITY_VERSION,
            "analysis_ready": False,
            "analysis_version": ANALYSIS_REPORT_VERSION,
            "as_of": None,
            "claim_count": 0,
            "citation_coverage_percent": 0.0,
            "decision_ready": False,
            "evidence_usage": {evidence_id: 0 for evidence_id in EVIDENCE_IDS},
            "input_bundle_sha256": None,
            "issues": [_issue("INTERNAL_QUALITY_ERROR", str(exc))],
            "model": None,
            "output_sha256": None,
            "provider": None,
            "quality_ready": False,
            "quality_status": "invalid",
            "rendered_report_sha256": None,
            "risk_claim_count": 0,
            "section_claim_counts": {section: 0 for section in ANALYSIS_SECTIONS},
            "section_quality": {},
            "symbol": None,
            "unknown_claim_count": 0,
        }
    write_atomic(output_path, _json_bytes(report))
    return report
