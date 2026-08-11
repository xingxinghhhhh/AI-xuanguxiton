"""Render a validated analysis-report-v1 result as deterministic Markdown."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .contracts import ANALYSIS_REPORT_VERSION, ANALYSIS_SECTIONS, EVIDENCE_IDS

SECTION_TITLES = {
    "market": "market",
    "technical": "technical",
    "price_plan": "price_plan",
    "profitability": "profitability",
    "growth": "growth",
    "announcements": "announcements",
    "risks": "risks",
    "unknowns": "unknowns",
}


class AnalysisRenderError(ValueError):
    """A fail-closed error while validating render inputs."""

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
        raise AnalysisRenderError("INPUT_JSON_INVALID", f"{label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnalysisRenderError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return payload, raw


def _safe_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise AnalysisRenderError("FIELD_INVALID", f"{label} must be a string")
    # Collapsing newlines prevents injected headings or block structures.
    return (
        value.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _relative_path(root: Path, value: Any, *, label: str) -> tuple[str, Path]:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise AnalysisRenderError("PATH_INVALID", f"{label} must be a relative path")
    root_resolved = root.resolve()
    candidate = (root / value).resolve()
    try:
        relative = candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise AnalysisRenderError("PATH_OUTSIDE_INPUT_ROOT", f"{label} escapes input root") from exc
    return relative.as_posix(), candidate


def _verify_hash(path: Path, expected: Any, *, label: str) -> str:
    if not isinstance(expected, str) or len(expected) != 64:
        raise AnalysisRenderError("HASH_INVALID", f"{label} must be a SHA-256 string")
    try:
        actual = sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise AnalysisRenderError("CITATION_UNAVAILABLE", f"{label}: {exc}") from exc
    if actual != expected:
        raise AnalysisRenderError("CITATION_HASH_MISMATCH", f"{label} hash does not match")
    return actual


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _validate_inputs(
    analysis: Mapping[str, Any],
    analysis_raw: bytes,
    analysis_report: Mapping[str, Any],
    analysis_report_raw: bytes,
    input_root: Path,
) -> dict[str, Any]:
    if analysis_report.get("output_sha256") != sha256_bytes(analysis_raw):
        raise AnalysisRenderError(
            "ANALYSIS_HASH_MISMATCH", "analysis report output_sha256 does not match analysis"
        )
    if (
        analysis_report.get("status") != "ready"
        or analysis_report.get("analysis_ready") is not True
    ):
        raise AnalysisRenderError("ANALYSIS_NOT_READY", "analysis report is not ready")
    if analysis_report.get("decision_ready") is not False:
        raise AnalysisRenderError(
            "DECISION_GATE_INVALID", "analysis report decision_ready must be false"
        )
    if analysis.get("analysis_version") != ANALYSIS_REPORT_VERSION:
        raise AnalysisRenderError("ANALYSIS_VERSION_INVALID", "unsupported analysis_version")
    if analysis.get("analysis_ready") is not True:
        raise AnalysisRenderError("ANALYSIS_NOT_READY", "analysis_ready must be true")
    if analysis.get("decision_ready") is not False:
        raise AnalysisRenderError("DECISION_GATE_INVALID", "decision_ready must be false")
    if analysis.get("status") != "ready":
        raise AnalysisRenderError("ANALYSIS_NOT_READY", "analysis status must be ready")
    if analysis.get("symbol") != analysis_report.get("symbol"):
        raise AnalysisRenderError("SYMBOL_MISMATCH", "analysis and report symbols differ")
    if analysis.get("as_of") != analysis_report.get("as_of"):
        raise AnalysisRenderError("AS_OF_MISMATCH", "analysis and report as_of values differ")

    sections = analysis.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(ANALYSIS_SECTIONS):
        raise AnalysisRenderError("SECTIONS_INVALID", "analysis must contain all sections")

    citations = analysis.get("citations")
    if not isinstance(citations, list) or len(citations) != len(EVIDENCE_IDS):
        raise AnalysisRenderError("CITATIONS_INVALID", "analysis must contain six citations")
    citation_map: dict[str, dict[str, Any]] = {}
    for index, citation in enumerate(citations):
        label = f"citations[{index}]"
        if not isinstance(citation, dict):
            raise AnalysisRenderError("CITATIONS_INVALID", f"{label} must be an object")
        evidence_id = citation.get("evidence_id")
        if evidence_id not in EVIDENCE_IDS or evidence_id in citation_map:
            raise AnalysisRenderError("CITATION_INVALID", f"{label}.evidence_id is invalid")
        report_path, report_file = _relative_path(
            input_root, citation.get("report_path"), label=f"{label}.report_path"
        )
        report_sha = _verify_hash(
            report_file, citation.get("report_sha256"), label=f"{label}.report"
        )
        artifact_paths = citation.get("artifact_paths")
        artifact_shas = citation.get("artifact_sha256")
        if not isinstance(artifact_paths, list) or not isinstance(artifact_shas, list):
            raise AnalysisRenderError(
                "CITATION_INVALID", f"{label} artifact manifests must be lists"
            )
        if len(artifact_paths) != len(artifact_shas):
            raise AnalysisRenderError("CITATION_INVALID", f"{label} artifact manifests differ")
        resolved_artifacts: list[str] = []
        for artifact_index, (artifact_path, artifact_sha) in enumerate(
            zip(artifact_paths, artifact_shas)
        ):
            artifact_relative, artifact_file = _relative_path(
                input_root,
                artifact_path,
                label=f"{label}.artifact_paths[{artifact_index}]",
            )
            _verify_hash(
                artifact_file,
                artifact_sha,
                label=f"{label}.artifact[{artifact_index}]",
            )
            resolved_artifacts.append(artifact_relative)
        citation_map[evidence_id] = {
            "artifact_paths": resolved_artifacts,
            "artifact_sha256": [str(value) for value in artifact_shas],
            "evidence_id": evidence_id,
            "report_path": report_path,
            "report_sha256": report_sha,
        }

    for section in ANALYSIS_SECTIONS:
        claims = sections[section]
        if not isinstance(claims, list):
            raise AnalysisRenderError("SECTION_INVALID", f"sections.{section} must be a list")
        for index, claim in enumerate(claims):
            label = f"sections.{section}[{index}]"
            if not isinstance(claim, dict):
                raise AnalysisRenderError("CLAIM_INVALID", f"{label} must be an object")
            expected = {"claim_id", "kind", "text", "citation_ids", "observed_dates"}
            if set(claim) != expected:
                raise AnalysisRenderError("CLAIM_FIELDS_INVALID", f"{label} fields are invalid")
            _safe_text(claim["claim_id"], label=f"{label}.claim_id")
            if claim["kind"] not in {"observation", "risk", "unknown"}:
                raise AnalysisRenderError("CLAIM_KIND_INVALID", f"{label}.kind is invalid")
            _safe_text(claim["text"], label=f"{label}.text")
            citation_ids = claim["citation_ids"]
            if not isinstance(citation_ids, list) or not citation_ids:
                raise AnalysisRenderError("CITATION_MISSING", f"{label}.citation_ids is empty")
            for citation_id in citation_ids:
                if citation_id not in citation_map:
                    raise AnalysisRenderError("CITATION_UNKNOWN", f"{label} cites unknown evidence")
            dates = claim["observed_dates"]
            if not isinstance(dates, list) or any(not isinstance(value, str) for value in dates):
                raise AnalysisRenderError(
                    "OBSERVED_DATE_INVALID", f"{label}.observed_dates is invalid"
                )
    return {
        "analysis_sha256": sha256_bytes(analysis_raw),
        "analysis_report_sha256": sha256_bytes(analysis_report_raw),
        "citations": citation_map,
        "model": analysis_report.get("model"),
        "provider": analysis_report.get("provider"),
    }


def _render_markdown(analysis: Mapping[str, Any], metadata: Mapping[str, Any]) -> bytes:
    symbol = _safe_text(analysis["symbol"], label="symbol")
    as_of = _safe_text(analysis["as_of"], label="as_of")
    analysis_version = _safe_text(analysis["analysis_version"], label="analysis_version")
    provider = _safe_text(str(metadata.get("provider") or "unknown"), label="provider")
    model = _safe_text(str(metadata.get("model") or "unknown"), label="model")
    lines = [
        f"# A-share Research Report: {symbol}",
        "",
        "## 1. Metadata and status",
        "",
        f"- symbol: `{symbol}`",
        f"- as_of: `{as_of}`",
        f"- analysis_version: `{analysis_version}`",
        f"- analysis_ready: `{str(analysis['analysis_ready']).lower()}`",
        f"- decision_ready: `{str(analysis['decision_ready']).lower()}`",
        f"- provider: `{provider}`",
        f"- model: `{model}`",
        "- This is a research report, not a trading decision.",
        "",
    ]
    sections = analysis["sections"]
    for index, section in enumerate(ANALYSIS_SECTIONS, start=2):
        lines.extend([f"## {index}. {SECTION_TITLES[section]}", ""])
        claims = sections[section]
        if not claims:
            lines.extend(["No claims.", ""])
            continue
        for claim in claims:
            claim_id = _safe_text(claim["claim_id"], label="claim_id")
            kind = _safe_text(claim["kind"], label="kind")
            text = _safe_text(claim["text"], label="text")
            citation_ids = ", ".join(
                f"`{_safe_text(value, label='citation_id')}`" for value in claim["citation_ids"]
            )
            dates = ", ".join(
                f"`{_safe_text(value, label='observed_date')}`"
                for value in claim["observed_dates"]
            )
            lines.extend(
                [
                    f"### {claim_id}",
                    f"- kind: `{kind}`",
                    f"- text: {text}",
                    f"- citation_ids: {citation_ids}",
                    f"- observed_dates: {dates or 'none'}",
                    "",
                ]
            )
    lines.extend(["## 10. Evidence citations", ""])
    for evidence_id in EVIDENCE_IDS:
        citation = metadata["citations"][evidence_id]
        artifacts = ", ".join(
            f"`{_safe_text(path, label='artifact_path')}`" for path in citation["artifact_paths"]
        ) or "none"
        lines.extend(
            [
                    f"- `{evidence_id}`: report `"
                    f"{_safe_text(citation['report_path'], label='report_path')}`",
                f"  - report_sha256: `{citation['report_sha256']}`",
                f"  - artifacts: {artifacts}",
            ]
        )
    lines.extend(
        [
            "",
            "## 11. Safety boundary",
            "",
            "This renderer performs no network request and reads no credentials. "
            "It only renders a validated, evidence-backed analysis. It does not "
            "create trade orders or recommendations.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def render_analysis(
    *,
    analysis_path: Path,
    analysis_report_path: Path,
    input_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Render an analysis report and always write a render audit report."""

    render_report_path = output_dir / "analysis_render_report.json"
    try:
        analysis, analysis_raw = _read_json(analysis_path, label="analysis")
        analysis_report, analysis_report_raw = _read_json(
            analysis_report_path, label="analysis_report"
        )
        metadata = _validate_inputs(
            analysis,
            analysis_raw,
            analysis_report,
            analysis_report_raw,
            input_root,
        )
        markdown = _render_markdown(analysis, metadata)
        markdown_path = output_dir / "research_analysis.md"
        write_atomic(markdown_path, markdown)
        report = {
            "analysis_ready": True,
            "analysis_report_sha256": metadata["analysis_report_sha256"],
            "analysis_sha256": metadata["analysis_sha256"],
            "decision_ready": False,
            "input_analysis_path": _display_path(analysis_path, input_root),
            "input_analysis_report_path": _display_path(analysis_report_path, input_root),
            "issues": [],
            "output_path": _display_path(markdown_path, output_dir),
            "output_sha256": sha256_bytes(markdown),
            "schema_version": "1.0",
            "status": "ready",
            "symbol": analysis["symbol"],
        }
    except AnalysisRenderError as exc:
        report = {
            "analysis_ready": False,
            "analysis_report_sha256": None,
            "analysis_sha256": None,
            "decision_ready": False,
            "input_analysis_path": _display_path(analysis_path, input_root),
            "input_analysis_report_path": _display_path(analysis_report_path, input_root),
            "issues": [{"code": exc.code, "message": str(exc)}],
            "output_path": None,
            "output_sha256": None,
            "schema_version": "1.0",
            "status": "invalid",
            "symbol": None,
        }
    except Exception as exc:  # pragma: no cover - final safety gate
        report = {
            "analysis_ready": False,
            "analysis_report_sha256": None,
            "analysis_sha256": None,
            "decision_ready": False,
            "input_analysis_path": _display_path(analysis_path, input_root),
            "input_analysis_report_path": _display_path(analysis_report_path, input_root),
            "issues": [{"code": "INTERNAL_RENDER_ERROR", "message": str(exc)}],
            "output_path": None,
            "output_sha256": None,
            "schema_version": "1.0",
            "status": "invalid",
            "symbol": None,
        }
    write_atomic(render_report_path, _json_bytes(report))
    return report
