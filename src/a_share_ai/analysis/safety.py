"""Deterministic safety audit for non-trading analysis output."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ..market.replay import sha256_bytes, write_atomic
from .contracts import ANALYSIS_REPORT_VERSION, ANALYSIS_SECTIONS

ANALYSIS_SAFETY_VERSION = "analysis-safety-v1"
RENDER_SCHEMA_VERSION = "1.0"
DECISION_INPUT_VERSION = "decision-input-v1"

CLAIM_FIELDS = {"claim_id", "kind", "text", "citation_ids", "observed_dates"}
ANALYSIS_FIELDS = {
    "analysis_ready",
    "analysis_version",
    "as_of",
    "citations",
    "decision_ready",
    "input_bundle_path",
    "input_bundle_sha256",
    "issues",
    "schema_version",
    "sections",
    "status",
    "symbol",
}

FORBIDDEN_FIELD_NAMES = {
    "action",
    "buy",
    "entry",
    "entry_price",
    "entryprice",
    "hold",
    "order",
    "orders",
    "position",
    "position_size",
    "positionsize",
    "recommend",
    "recommendation",
    "sell",
    "side",
    "signal",
    "stop_loss",
    "stoploss",
    "take_profit",
    "takeprofit",
    "target_price",
    "targetprice",
    "trade_action",
    "tradeaction",
    "买入",
    "卖出",
    "仓位",
    "止损",
    "止盈",
    "目标价",
    "入手价",
    "观望",
    "持有",
}

FORBIDDEN_TERMS = (
    ("ACTION_WORD", "BUY", "action"),
    ("ACTION_WORD", "SELL", "action"),
    ("ACTION_WORD", "HOLD", "action"),
    ("ACTION_WORD", "买入", "action"),
    ("ACTION_WORD", "卖出", "action"),
    ("ACTION_WORD", "观望", "action"),
    ("ACTION_WORD", "建仓", "action"),
    ("ACTION_WORD", "加仓", "action"),
    ("ACTION_WORD", "减仓", "action"),
    ("ACTION_WORD", "清仓", "action"),
    ("ACTION_WORD", "增持", "action"),
    ("ACTION_WORD", "减持", "action"),
    ("ACTION_WORD", "持有", "action"),
    ("EXECUTION_PRICE", "目标价", "execution"),
    ("EXECUTION_PRICE", "入手价", "execution"),
    ("EXECUTION_PRICE", "止盈", "execution"),
    ("EXECUTION_PRICE", "止损", "execution"),
    ("EXECUTION_PRICE", "target price", "execution"),
    ("EXECUTION_PRICE", "entry price", "execution"),
    ("EXECUTION_PRICE", "take profit", "execution"),
    ("EXECUTION_PRICE", "stop loss", "execution"),
    ("POSITION_SIZE", "仓位", "position"),
    ("POSITION_SIZE", "position size", "position"),
    ("POSITION_SIZE", "position sizing", "position"),
    ("RECOMMENDATION", "建议操作", "recommendation"),
    ("RECOMMENDATION", "可买入", "recommendation"),
    ("RECOMMENDATION", "适合介入", "recommendation"),
    ("RECOMMENDATION", "recommendation", "recommendation"),
)


class AnalysisSafetyError(ValueError):
    """A fail-closed safety audit error with a stable issue code."""

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
        raise AnalysisSafetyError("INPUT_JSON_INVALID", f"{label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnalysisSafetyError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return payload, raw


def _safe_path(root: Path, value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise AnalysisSafetyError("PATH_INVALID", f"{label} must be relative")
    if ".." in Path(value).parts:
        raise AnalysisSafetyError("PATH_INVALID", f"{label} must not escape")
    root_resolved = root.resolve()
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise AnalysisSafetyError("PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} escapes root") from exc
    return candidate


def _file_sha(path: Path, *, label: str) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise AnalysisSafetyError("FILE_UNAVAILABLE", f"{label}: {exc}") from exc


def _require(payload: Mapping[str, Any], field: str, value: Any, *, label: str) -> None:
    if payload.get(field) != value:
        raise AnalysisSafetyError("FIELD_MISMATCH", f"{label}.{field} is inconsistent")


def _require_sha(payload: Mapping[str, Any], field: str, actual: str, *, label: str) -> None:
    expected = payload.get(field)
    if not isinstance(expected, str) or len(expected) != 64 or expected != actual:
        raise AnalysisSafetyError("HASH_MISMATCH", f"{label}.{field} does not match")


def _reference(
    snapshot: Mapping[str, Any],
    name: str,
    *,
    artifact_root: Path,
    label: str,
) -> tuple[dict[str, Any], Path, str]:
    entry = snapshot.get(name)
    if not isinstance(entry, dict):
        raise AnalysisSafetyError("SNAPSHOT_FIELD_INVALID", f"snapshot.{name} is invalid")
    path = _safe_path(artifact_root, entry.get("path"), label=f"snapshot.{name}.path")
    actual = _file_sha(path, label=label)
    _require_sha(entry, "sha256", actual, label=f"snapshot.{name}")
    return entry, path, actual


def _validate_input_chain(
    *,
    decision_input_path: Path,
    decision_input_report_path: Path,
    analysis_path: Path,
    analysis_report_path: Path,
    render_report_path: Path,
    artifact_root: Path,
) -> dict[str, Any]:
    snapshot, snapshot_raw = _read_json(decision_input_path, label="decision_input_snapshot")
    snapshot_report, _snapshot_report_raw = _read_json(
        decision_input_report_path, label="decision_input_report"
    )
    _require(snapshot, "decision_input_version", DECISION_INPUT_VERSION, label="snapshot")
    _require(snapshot_report, "decision_input_version", DECISION_INPUT_VERSION, label="report")
    _require(snapshot, "status", "ready", label="snapshot")
    _require(snapshot_report, "status", "ready", label="report")
    _require(snapshot, "decision_input_ready", True, label="snapshot")
    _require(snapshot_report, "decision_input_ready", True, label="report")
    _require(snapshot, "decision_ready", False, label="snapshot")
    _require(snapshot_report, "decision_ready", False, label="report")
    _require_sha(
        snapshot_report,
        "output_sha256",
        sha256_bytes(snapshot_raw),
        label="decision_input_report",
    )

    symbol = snapshot.get("symbol")
    as_of = snapshot.get("as_of")
    if not isinstance(symbol, str) or not isinstance(as_of, str):
        raise AnalysisSafetyError("SNAPSHOT_FIELD_INVALID", "snapshot symbol/as_of is invalid")

    analysis_entry, expected_analysis_path, analysis_sha = _reference(
        snapshot, "analysis", artifact_root=artifact_root, label="analysis"
    )
    analysis_report_entry, expected_analysis_report_path, analysis_report_sha = _reference(
        snapshot, "analysis_report", artifact_root=artifact_root, label="analysis_report"
    )
    render_entry, expected_render_report_path, render_sha = _reference(
        snapshot, "render", artifact_root=artifact_root, label="render_report"
    )
    quality_entry, quality_path, quality_sha = _reference(
        snapshot, "quality", artifact_root=artifact_root, label="quality_report"
    )
    bundle_entry, bundle_path, bundle_sha = _reference(
        snapshot, "input_bundle", artifact_root=artifact_root, label="input_bundle"
    )
    bundle_report_entry, bundle_report_path, bundle_report_sha = _reference(
        snapshot,
        "input_bundle_report",
        artifact_root=artifact_root,
        label="input_bundle_report",
    )
    for supplied, expected, label in (
        (analysis_path, expected_analysis_path, "analysis"),
        (analysis_report_path, expected_analysis_report_path, "analysis_report"),
        (render_report_path, expected_render_report_path, "render_report"),
    ):
        if supplied.resolve() != expected.resolve():
            raise AnalysisSafetyError("PATH_MISMATCH", f"{label} path differs from snapshot")

    analysis, analysis_raw = _read_json(analysis_path, label="analysis")
    analysis_report, analysis_report_raw = _read_json(
        analysis_report_path, label="analysis_report"
    )
    render_report, render_report_raw = _read_json(
        render_report_path, label="render_report"
    )
    quality_report, quality_report_raw = _read_json(quality_path, label="quality_report")
    bundle, _bundle_raw = _read_json(bundle_path, label="input_bundle")
    bundle_report, bundle_report_raw = _read_json(
        bundle_report_path, label="input_bundle_report"
    )

    for payload, label in (
        (analysis, "analysis"),
        (analysis_report, "analysis_report"),
        (bundle, "input_bundle"),
        (bundle_report, "input_bundle_report"),
        (quality_report, "quality_report"),
    ):
        if payload.get("symbol") != symbol or payload.get("as_of") != as_of:
            raise AnalysisSafetyError("SYMBOL_AS_OF_MISMATCH", f"{label} symbol/as_of differs")
        if payload.get("decision_ready") is not False:
            raise AnalysisSafetyError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready is not false"
            )
    if render_report.get("symbol") != symbol or render_report.get("decision_ready") is not False:
        raise AnalysisSafetyError("RENDER_GATE_INVALID", "render report state is inconsistent")

    _require(analysis, "analysis_version", ANALYSIS_REPORT_VERSION, label="analysis")
    _require(analysis_report, "analysis_version", ANALYSIS_REPORT_VERSION, label="analysis_report")
    _require(bundle, "analysis_input_ready", True, label="input_bundle")
    _require(bundle_report, "analysis_input_ready", True, label="input_bundle_report")
    _require(bundle, "bundle_version", "analysis-input-v1", label="input_bundle")
    _require(bundle_report, "bundle_version", "analysis-input-v1", label="input_bundle_report")
    _require(
        quality_report,
        "analysis_quality_version",
        "analysis-quality-v1",
        label="quality_report",
    )
    _require(quality_report, "quality_ready", True, label="quality_report")
    _require(quality_report, "quality_status", "pass", label="quality_report")
    _require(render_report, "status", "ready", label="render_report")
    _require(render_report, "schema_version", RENDER_SCHEMA_VERSION, label="render_report")
    _require(render_report, "analysis_ready", True, label="render_report")
    _require(analysis, "analysis_ready", True, label="analysis")
    _require(analysis_report, "analysis_ready", True, label="analysis_report")
    _require(analysis, "status", "ready", label="analysis")
    _require(analysis_report, "status", "ready", label="analysis_report")
    _require(bundle_report, "status", "ready", label="input_bundle_report")

    _require_sha(analysis_report, "output_sha256", analysis_sha, label="analysis_report")
    _require_sha(bundle_report, "bundle_sha256", bundle_sha, label="input_bundle_report")
    _require_sha(quality_report, "output_sha256", analysis_sha, label="quality_report")
    if analysis_report.get("input_bundle_sha256") != bundle_sha:
        raise AnalysisSafetyError("HASH_MISMATCH", "analysis input bundle SHA differs")
    if quality_report.get("input_bundle_sha256") != bundle_sha:
        raise AnalysisSafetyError("HASH_MISMATCH", "quality input bundle SHA differs")
    if render_report.get("analysis_sha256") != analysis_sha:
        raise AnalysisSafetyError("HASH_MISMATCH", "render analysis SHA differs")
    if render_report.get("analysis_report_sha256") != analysis_report_sha:
        raise AnalysisSafetyError("HASH_MISMATCH", "render analysis-report SHA differs")
    if quality_report.get("rendered_report_sha256") != render_report.get("output_sha256"):
        raise AnalysisSafetyError("HASH_MISMATCH", "quality rendered-report SHA differs")

    input_root = _safe_path(artifact_root, snapshot.get("input_root"), label="snapshot.input_root")
    bundle_from_report = _safe_path(
        input_root,
        analysis_report.get("input_bundle_path"),
        label="analysis_report.input_bundle_path",
    )
    if bundle_from_report != bundle_path.resolve():
        raise AnalysisSafetyError("PATH_MISMATCH", "analysis report bundle path differs")
    if analysis.get("input_bundle_sha256") != bundle_sha:
        raise AnalysisSafetyError("HASH_MISMATCH", "analysis bundle SHA differs")

    rendered_path = _safe_path(
        render_report_path.parent,
        render_report.get("output_path"),
        label="render_report.output_path",
    )
    rendered_sha = _file_sha(rendered_path, label="rendered report")
    if render_report.get("output_sha256") != rendered_sha:
        raise AnalysisSafetyError("HASH_MISMATCH", "rendered report SHA differs")
    if render_entry.get("output_sha256") != rendered_sha:
        raise AnalysisSafetyError("HASH_MISMATCH", "snapshot rendered-report SHA differs")
    expected_rendered_path = _safe_path(
        artifact_root,
        render_entry.get("output_path"),
        label="snapshot.render.output_path",
    )
    if expected_rendered_path != rendered_path.resolve():
        raise AnalysisSafetyError("PATH_MISMATCH", "snapshot rendered-report path differs")

    if bundle_entry.get("symbol") != symbol or bundle_report_entry.get("symbol") != symbol:
        raise AnalysisSafetyError("SYMBOL_AS_OF_MISMATCH", "snapshot bundle metadata differs")
    if quality_entry.get("quality_ready") is not True:
        raise AnalysisSafetyError("READY_GATE_INVALID", "snapshot quality entry is not ready")
    if analysis_entry.get("analysis_ready") is not True:
        raise AnalysisSafetyError("READY_GATE_INVALID", "snapshot analysis entry is not ready")
    if render_entry.get("status") != "ready":
        raise AnalysisSafetyError("READY_GATE_INVALID", "snapshot render entry is not ready")

    return {
        "analysis": analysis,
        "analysis_raw": analysis_raw,
        "analysis_report": analysis_report,
        "analysis_report_raw": analysis_report_raw,
        "as_of": as_of,
        "bundle_report_raw": bundle_report_raw,
        "decision_input_ready": True,
        "decision_input_sha256": sha256_bytes(snapshot_raw),
        "quality_report_raw": quality_report_raw,
        "rendered_path": rendered_path,
        "rendered_sha256": rendered_sha,
        "render_report_raw": render_report_raw,
        "snapshot": snapshot,
        "snapshot_report": snapshot_report,
        "symbol": symbol,
        "analysis_sha256": analysis_sha,
        "analysis_report_sha256": analysis_report_sha,
        "render_report_sha256": render_sha,
        "quality_report_sha256": quality_sha,
        "bundle_sha256": bundle_sha,
        "bundle_report_sha256": bundle_report_sha,
    }


def _finding(
    rule_id: str, *, source: str, field: str, section: str, matched: str
) -> dict[str, str]:
    return {
        "field": field,
        "matched": matched,
        "rule_id": rule_id,
        "section": section,
        "source": source,
    }


def _canonical_key(value: str) -> str:
    return value.casefold().replace("-", "_")


def _find_forbidden_keys(value: Any, *, path: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key in sorted(value):
            if isinstance(key, str) and _canonical_key(key) in FORBIDDEN_FIELD_NAMES:
                findings.append(
                    _finding(
                        "FORBIDDEN_FIELD",
                        source="structured_json",
                        field=f"{path}.{key}",
                        section="structured_json",
                        matched=key,
                    )
                )
            findings.extend(_find_forbidden_keys(value[key], path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_find_forbidden_keys(item, path=f"{path}[{index}]"))
    return findings


def _normalized_forms(value: str) -> tuple[str, str]:
    decoded = html.unescape(unquote(value)).casefold()
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", decoded)
    return decoded, compact


def _scan_text(
    value: str, *, source: str, field: str, section: str
) -> list[dict[str, str]]:
    decoded, compact = _normalized_forms(value)
    findings: list[dict[str, str]] = []
    for rule_id, term, _category in FORBIDDEN_TERMS:
        term_folded = term.casefold()
        matched = term_folded in decoded
        if not matched and term.isascii():
            matched = re.sub(r"[^a-z0-9]+", "", term_folded) in compact
        if matched:
            findings.append(
                _finding(
                    rule_id,
                    source=source,
                    field=field,
                    section=section,
                    matched=term,
                )
            )
    return findings


def _scan_markdown(markdown: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    section = "metadata"
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        heading = re.match(r"^##\s+\d+\.\s+([a-z_]+)\s*$", line)
        if heading:
            section = heading.group(1)
        field = f"research_analysis.md:line:{line_number}"
        if line.strip().endswith("It does not create trade orders or recommendations."):
            continue
        findings.extend(
            _scan_text(
                line,
                source="rendered_markdown",
                field=field,
                section=section,
            )
        )
        if re.search(r"(?<!\\)<\s*[A-Za-z/!][^>]*>", line):
            findings.append(
                _finding(
                    "MARKUP_BYPASS",
                    source="rendered_markdown",
                    field=field,
                    section=section,
                    matched="html-tag",
                )
            )
        if re.search(r"(?<!\\)\[[^\]]+\]\([^)]*\)", line):
            findings.append(
                _finding(
                    "MARKUP_BYPASS",
                    source="rendered_markdown",
                    field=field,
                    section=section,
                    matched="markdown-link",
                )
            )
        if "```" in line:
            findings.append(
                _finding(
                    "MARKUP_BYPASS",
                    source="rendered_markdown",
                    field=field,
                    section=section,
                    matched="code-fence",
                )
            )
    return findings


def _scan_analysis(analysis: Mapping[str, Any]) -> tuple[list[dict[str, str]], int, int]:
    findings = _find_forbidden_keys(analysis, path="analysis")
    for key in sorted(analysis):
        if key not in ANALYSIS_FIELDS:
            findings.append(
                _finding(
                    "UNEXPECTED_ANALYSIS_FIELD",
                    source="structured_json",
                    field=f"analysis.{key}",
                    section="structured_json",
                    matched=key,
                )
            )
    sections = analysis.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(ANALYSIS_SECTIONS):
        raise AnalysisSafetyError("SECTIONS_INVALID", "analysis sections are invalid")
    claim_count = 0
    text_count = 0
    for section in ANALYSIS_SECTIONS:
        claims = sections[section]
        if not isinstance(claims, list):
            raise AnalysisSafetyError("CLAIMS_INVALID", f"analysis.sections.{section} is invalid")
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                raise AnalysisSafetyError("CLAIM_INVALID", f"analysis.sections.{section}[{index}]")
            for key in sorted(claim):
                if key not in CLAIM_FIELDS:
                    findings.append(
                        _finding(
                            "UNEXPECTED_CLAIM_FIELD",
                            source="structured_json",
                            field=f"analysis.sections.{section}[{index}].{key}",
                            section=section,
                            matched=key,
                        )
                    )
            text = claim.get("text")
            if not isinstance(text, str):
                raise AnalysisSafetyError(
                    "CLAIM_TEXT_INVALID", f"analysis.sections.{section}[{index}].text"
                )
            findings.extend(
                _scan_text(
                    text,
                    source="structured_json",
                    field=f"analysis.sections.{section}[{index}].text",
                    section=section,
                )
            )
            claim_count += 1
            text_count += 1
    return findings, claim_count, text_count


def audit_analysis_safety(
    *,
    decision_input_path: Path,
    decision_input_report_path: Path,
    analysis_path: Path,
    analysis_report_path: Path,
    render_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Audit the final non-trading analysis chain and always write a report."""

    try:
        chain = _validate_input_chain(
            decision_input_path=decision_input_path,
            decision_input_report_path=decision_input_report_path,
            analysis_path=analysis_path,
            analysis_report_path=analysis_report_path,
            render_report_path=render_report_path,
            artifact_root=artifact_root,
        )
        findings = _find_forbidden_keys(
            chain["snapshot"], path="decision_input_snapshot"
        )
        findings.extend(
            _find_forbidden_keys(chain["snapshot_report"], path="decision_input_report")
        )
        analysis_findings, claim_count, text_count = _scan_analysis(chain["analysis"])
        findings.extend(analysis_findings)
        markdown = chain["rendered_path"].read_text(encoding="utf-8")
        findings.extend(_scan_markdown(markdown))
        findings = list(
            dict.fromkeys(
                tuple(sorted(finding.items())) for finding in findings
            )
        )
        findings = [dict(items) for items in findings]
        report = {
            "analysis_safety_version": ANALYSIS_SAFETY_VERSION,
            "analysis_sha256": chain["analysis_sha256"],
            "as_of": chain["as_of"],
            "claim_count": claim_count,
            "decision_input_ready": True,
            "decision_input_sha256": chain["decision_input_sha256"],
            "decision_ready": False,
            "findings": findings,
            "input_bundle_sha256": chain["bundle_sha256"],
            "issues": (
                []
                if not findings
                else [
                    {
                        "code": "FORBIDDEN_ANALYSIS_LANGUAGE",
                        "message": "forbidden action or execution language was found",
                    }
                ]
            ),
            "quality_report_sha256": chain["quality_report_sha256"],
            "render_report_sha256": chain["render_report_sha256"],
            "rendered_report_sha256": chain["rendered_sha256"],
            "safety_ready": not findings,
            "scanned_text_count": text_count + len(markdown.splitlines()),
            "status": "pass" if not findings else "unsafe",
            "symbol": chain["symbol"],
        }
    except AnalysisSafetyError as exc:
        report = {
            "analysis_safety_version": ANALYSIS_SAFETY_VERSION,
            "analysis_sha256": None,
            "as_of": None,
            "claim_count": 0,
            "decision_input_ready": False,
            "decision_input_sha256": None,
            "decision_ready": False,
            "findings": [],
            "input_bundle_sha256": None,
            "issues": [{"code": exc.code, "message": str(exc)}],
            "quality_report_sha256": None,
            "render_report_sha256": None,
            "rendered_report_sha256": None,
            "safety_ready": False,
            "scanned_text_count": 0,
            "status": "invalid",
            "symbol": None,
        }
    except Exception as exc:  # pragma: no cover - final safety gate
        report = {
            "analysis_safety_version": ANALYSIS_SAFETY_VERSION,
            "analysis_sha256": None,
            "as_of": None,
            "claim_count": 0,
            "decision_input_ready": False,
            "decision_input_sha256": None,
            "decision_ready": False,
            "findings": [],
            "input_bundle_sha256": None,
            "issues": [{"code": "INTERNAL_SAFETY_ERROR", "message": str(exc)}],
            "quality_report_sha256": None,
            "render_report_sha256": None,
            "rendered_report_sha256": None,
            "safety_ready": False,
            "scanned_text_count": 0,
            "status": "invalid",
            "symbol": None,
        }
    write_atomic(output_dir / "analysis_safety_report.json", _json_bytes(report))
    return report
