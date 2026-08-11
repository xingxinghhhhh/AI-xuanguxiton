"""Validate and materialize the analysis-report-v1 offline contract."""

# The validation error messages intentionally keep field names and codes visible.
# ruff: noqa: E501

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .contracts import (
    ALLOWED_CLAIM_KINDS,
    ANALYSIS_REPORT_SCHEMA_VERSION,
    ANALYSIS_REPORT_VERSION,
    ANALYSIS_SECTIONS,
    EVIDENCE_IDS,
    AnalysisClaim,
    AnalysisReportConfig,
    EvidenceCitation,
)
from .deepseek_provider import DeepSeekAnalysisProvider
from .offline_provider import OfflineAnalysisProvider, ProviderError
from .real_provider import OpenAIAnalysisProvider

DATE_PATTERN = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
DECISION_PATTERN = re.compile(r"(?:\bBUY\b|\bSELL\b|\bHOLD\b|买入|卖出|观望)", re.IGNORECASE)
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


class AnalysisValidationError(ValueError):
    """A fail-closed analysis contract violation with a stable issue code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(mapping: Mapping[str, Any]) -> bytes:
    return (json.dumps(mapping, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisValidationError("INPUT_JSON_INVALID", f"{label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnalysisValidationError("INPUT_ROOT_INVALID", f"{label} must be a JSON object")
    return payload, raw


def _safe_input_path(root: Path, relative: Any, *, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise AnalysisValidationError("PATH_INVALID", f"{label} must be a relative path")
    root_resolved = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise AnalysisValidationError("PATH_OUTSIDE_INPUT_ROOT", f"{label} escapes input root") from exc
    return candidate


def _hash_referenced_file(root: Path, relative: Any, expected: Any, *, label: str) -> None:
    path = _safe_input_path(root, relative, label=label)
    try:
        actual = sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise AnalysisValidationError("EVIDENCE_UNAVAILABLE", f"{label}: {exc}") from exc
    if not isinstance(expected, str) or actual != expected:
        raise AnalysisValidationError(
            "EVIDENCE_HASH_MISMATCH",
            f"{label} hash does not match the bundle manifest",
        )


def _require(mapping: Mapping[str, Any], key: str, *, label: str) -> Any:
    if key not in mapping:
        raise AnalysisValidationError("FIELD_MISSING", f"{label}.{key} is required")
    return mapping[key]


def _as_of_date(value: Any, *, label: str) -> date:
    if not isinstance(value, str) or len(value) < 10:
        raise AnalysisValidationError("AS_OF_INVALID", f"{label} must contain an ISO date")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise AnalysisValidationError("AS_OF_INVALID", f"{label} has an invalid date") from exc


def _bundle_display_path(config: AnalysisReportConfig) -> str:
    bundle_path = config.bundle_path.resolve()
    try:
        return bundle_path.relative_to(config.input_root.resolve()).as_posix()
    except ValueError:
        return bundle_path.name


def _validate_bundle(
    config: AnalysisReportConfig,
) -> tuple[dict[str, Any], str, dict[str, Any], date]:
    bundle, bundle_raw = _read_json(config.bundle_path, label="analysis_input_bundle")
    bundle_sha = sha256_bytes(bundle_raw)
    if bundle.get("schema_version") != "1.0":
        raise AnalysisValidationError("BUNDLE_VERSION_INVALID", "unsupported bundle schema_version")
    if bundle.get("bundle_version") != "analysis-input-v1":
        raise AnalysisValidationError("BUNDLE_VERSION_INVALID", "unsupported bundle_version")
    if bundle.get("analysis_input_ready") is not True:
        raise AnalysisValidationError("BUNDLE_NOT_READY", "analysis input bundle is not ready")
    if bundle.get("decision_ready") is not False:
        raise AnalysisValidationError("DECISION_GATE_INVALID", "bundle decision_ready must be false")
    symbol = _require(bundle, "symbol", label="analysis_input_bundle")
    as_of = _require(bundle, "as_of", label="analysis_input_bundle")
    if not isinstance(symbol, str) or not symbol:
        raise AnalysisValidationError("SYMBOL_INVALID", "bundle symbol must be a non-empty string")
    if not isinstance(as_of, str):
        raise AnalysisValidationError("AS_OF_INVALID", "bundle as_of must be a string")
    cutoff = _as_of_date(as_of, label="analysis_input_bundle.as_of")

    bundle_report, _ = _read_json(
        config.resolved_bundle_report(), label="analysis_input_report"
    )
    if bundle_report.get("schema_version") != "1.0":
        raise AnalysisValidationError("BUNDLE_VERSION_INVALID", "input report schema_version is unsupported")
    if bundle_report.get("bundle_version") != "analysis-input-v1":
        raise AnalysisValidationError("BUNDLE_VERSION_INVALID", "input report bundle_version is unsupported")
    if bundle_report.get("bundle_sha256") != bundle_sha:
        raise AnalysisValidationError(
            "BUNDLE_HASH_MISMATCH", "analysis_input_report bundle_sha256 does not match the bundle"
        )
    if bundle_report.get("symbol") != symbol:
        raise AnalysisValidationError("SYMBOL_MISMATCH", "input report symbol differs from bundle")
    if bundle_report.get("as_of") != as_of:
        raise AnalysisValidationError("AS_OF_MISMATCH", "input report as_of differs from bundle")
    if bundle_report.get("status") != "ready":
        raise AnalysisValidationError("BUNDLE_NOT_READY", "analysis input report status is not ready")
    if bundle_report.get("analysis_input_ready") is not True:
        raise AnalysisValidationError("BUNDLE_NOT_READY", "analysis input report is not ready")
    if bundle_report.get("decision_ready") is not False:
        raise AnalysisValidationError("DECISION_GATE_INVALID", "input report decision_ready must be false")

    evidence = _require(bundle, "evidence", label="analysis_input_bundle")
    summaries = _require(bundle, "summaries", label="analysis_input_bundle")
    if not isinstance(evidence, list) or not evidence:
        raise AnalysisValidationError("EVIDENCE_INVALID", "bundle evidence must be a non-empty list")
    if not isinstance(summaries, dict) or set(summaries) != set(EVIDENCE_IDS):
        raise AnalysisValidationError("SUMMARY_INVALID", "bundle summaries must contain the six evidence IDs")
    entries: dict[str, Any] = {}
    for index, entry in enumerate(evidence):
        label = f"analysis_input_bundle.evidence[{index}]"
        if not isinstance(entry, dict):
            raise AnalysisValidationError("EVIDENCE_INVALID", f"{label} must be an object")
        name = _require(entry, "name", label=label)
        if name not in EVIDENCE_IDS:
            raise AnalysisValidationError("EVIDENCE_ID_INVALID", f"unsupported evidence id: {name}")
        if name in entries:
            raise AnalysisValidationError("EVIDENCE_ID_DUPLICATE", f"duplicate evidence id: {name}")
        if entry.get("ready") is not True:
            raise AnalysisValidationError("EVIDENCE_NOT_READY", f"evidence {name} is not ready")
        if entry.get("symbol") != symbol:
            raise AnalysisValidationError("SYMBOL_MISMATCH", f"evidence {name} symbol differs from bundle")
        report_path = _require(entry, "report_path", label=label)
        report_sha = _require(entry, "report_sha256", label=label)
        _hash_referenced_file(config.input_root, report_path, report_sha, label=f"{name}.report")
        artifact_paths = _require(entry, "artifact_paths", label=label)
        artifact_sha = _require(entry, "artifact_sha256", label=label)
        if not isinstance(artifact_paths, list) or not isinstance(artifact_sha, list):
            raise AnalysisValidationError("EVIDENCE_INVALID", f"{name} artifact manifests must be lists")
        if len(artifact_paths) != len(artifact_sha):
            raise AnalysisValidationError("EVIDENCE_INVALID", f"{name} artifact manifest lengths differ")
        for artifact_index, (path, expected) in enumerate(zip(artifact_paths, artifact_sha)):
            _hash_referenced_file(
                config.input_root,
                path,
                expected,
                label=f"{name}.artifact[{artifact_index}]",
            )
        entries[name] = entry
    if set(entries) != set(EVIDENCE_IDS):
        missing = sorted(set(EVIDENCE_IDS) - set(entries))
        raise AnalysisValidationError("EVIDENCE_MISSING", f"bundle is missing evidence: {', '.join(missing)}")
    return bundle, bundle_sha, entries, cutoff


def _validate_text(value: Any, *, label: str, cutoff: date) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisValidationError("CLAIM_INVALID", f"{label} text must be non-empty")
    if DECISION_PATTERN.search(value):
        raise AnalysisValidationError("PROHIBITED_DECISION", f"{label} contains a trading decision term")
    for date_text in DATE_PATTERN.findall(value):
        if date.fromisoformat(date_text) > cutoff:
            raise AnalysisValidationError("FUTURE_CLAIM", f"{label} contains a future date")
    return value


def _validate_provider(
    payload: Mapping[str, Any],
    *,
    bundle: Mapping[str, Any],
    entries: Mapping[str, Any],
    cutoff: date,
) -> dict[str, list[AnalysisClaim]]:
    expected_keys = {"schema_version", "analysis_version", "symbol", "as_of", "sections"}
    if set(payload) != expected_keys:
        raise AnalysisValidationError("PROVIDER_FIELDS_INVALID", "provider fields do not match analysis-report-v1")
    if payload.get("schema_version") != ANALYSIS_REPORT_SCHEMA_VERSION:
        raise AnalysisValidationError("PROVIDER_VERSION_INVALID", "provider schema_version is unsupported")
    if payload.get("analysis_version") != ANALYSIS_REPORT_VERSION:
        raise AnalysisValidationError("PROVIDER_VERSION_INVALID", "provider analysis_version is unsupported")
    if payload.get("symbol") != bundle.get("symbol"):
        raise AnalysisValidationError("SYMBOL_MISMATCH", "provider symbol differs from bundle")
    if payload.get("as_of") != bundle.get("as_of"):
        raise AnalysisValidationError("AS_OF_MISMATCH", "provider as_of differs from bundle")
    sections = payload.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(ANALYSIS_SECTIONS):
        raise AnalysisValidationError("SECTIONS_INVALID", "provider must provide exactly all analysis sections")

    seen_claim_ids: set[str] = set()
    validated: dict[str, list[AnalysisClaim]] = {}
    for section in ANALYSIS_SECTIONS:
        values = sections[section]
        if not isinstance(values, list):
            raise AnalysisValidationError("SECTION_TYPE_INVALID", f"sections.{section} must be a list")
        claims: list[AnalysisClaim] = []
        for index, value in enumerate(values):
            label = f"sections.{section}[{index}]"
            expected_claim_keys = {"claim_id", "kind", "text", "citation_ids", "observed_dates"}
            if not isinstance(value, dict) or set(value) != expected_claim_keys:
                raise AnalysisValidationError("CLAIM_FIELDS_INVALID", f"{label} fields are invalid")
            claim_id = value.get("claim_id")
            if not isinstance(claim_id, str) or not IDENTIFIER_PATTERN.fullmatch(claim_id):
                raise AnalysisValidationError("CLAIM_ID_INVALID", f"{label}.claim_id is invalid")
            if claim_id in seen_claim_ids:
                raise AnalysisValidationError("CLAIM_ID_DUPLICATE", f"duplicate claim_id: {claim_id}")
            seen_claim_ids.add(claim_id)
            kind = value.get("kind")
            if kind not in ALLOWED_CLAIM_KINDS:
                raise AnalysisValidationError("CLAIM_KIND_INVALID", f"{label}.kind is not allowed")
            text = _validate_text(value.get("text"), label=label, cutoff=cutoff)
            citation_ids = value.get("citation_ids")
            if not isinstance(citation_ids, list) or not citation_ids:
                raise AnalysisValidationError("CITATION_MISSING", f"{label}.citation_ids must be non-empty")
            if any(not isinstance(item, str) for item in citation_ids):
                raise AnalysisValidationError("CITATION_INVALID", f"{label}.citation_ids must contain strings")
            if len(set(citation_ids)) != len(citation_ids):
                raise AnalysisValidationError("CITATION_DUPLICATE", f"{label}.citation_ids contains duplicates")
            missing = [item for item in citation_ids if item not in entries]
            if missing:
                raise AnalysisValidationError(
                    "CITATION_UNKNOWN",
                    f"{label} references unknown evidence: {', '.join(missing)}",
                )
            observed_dates = value.get("observed_dates")
            if not isinstance(observed_dates, list) or any(not isinstance(item, str) for item in observed_dates):
                raise AnalysisValidationError("OBSERVED_DATE_INVALID", f"{label}.observed_dates must be ISO date strings")
            normalized_dates: list[str] = []
            for observed_date in observed_dates:
                try:
                    parsed = date.fromisoformat(observed_date)
                except ValueError as exc:
                    raise AnalysisValidationError("OBSERVED_DATE_INVALID", f"{label} has an invalid observed date") from exc
                if parsed > cutoff:
                    raise AnalysisValidationError("FUTURE_CLAIM", f"{label} contains a future observed date")
                normalized_dates.append(parsed.isoformat())
            claims.append(
                AnalysisClaim(
                    claim_id=claim_id,
                    kind=kind,
                    text=text,
                    citation_ids=tuple(sorted(citation_ids)),
                    observed_dates=tuple(sorted(set(normalized_dates))),
                )
            )
        validated[section] = sorted(claims, key=lambda item: item.claim_id)
    return validated


def _citation_mappings(entries: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        EvidenceCitation(
            evidence_id=evidence_id,
            report_path=entries[evidence_id]["report_path"],
            report_sha256=entries[evidence_id]["report_sha256"],
            artifact_paths=tuple(entries[evidence_id]["artifact_paths"]),
            artifact_sha256=tuple(entries[evidence_id]["artifact_sha256"]),
        ).to_mapping()
        for evidence_id in EVIDENCE_IDS
    ]


def _invalid_output(
    *,
    bundle: Mapping[str, Any] | None,
    bundle_sha: str | None,
    input_bundle_path: str,
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "analysis_ready": False,
        "analysis_version": ANALYSIS_REPORT_VERSION,
        "as_of": bundle.get("as_of") if bundle else None,
        "citations": [],
        "decision_ready": False,
        "input_bundle_path": input_bundle_path,
        "input_bundle_sha256": bundle_sha,
        "issues": issues,
        "schema_version": ANALYSIS_REPORT_SCHEMA_VERSION,
        "sections": {section: [] for section in ANALYSIS_SECTIONS},
        "status": "invalid",
        "symbol": bundle.get("symbol") if bundle else None,
    }


class AnalysisReportSource:
    """Build a validated report from an offline or explicitly selected provider."""

    def __init__(self, config: AnalysisReportConfig) -> None:
        self.config = config

    def _provider(self) -> Any:
        if self.config.provider == "offline":
            if self.config.response_fixture is None:
                raise ProviderError("offline provider requires --response-fixture")
            return OfflineAnalysisProvider(self.config.response_fixture)
        if self.config.provider == "openai":
            return OpenAIAnalysisProvider(
                model=self.config.model,
                timeout_seconds=self.config.timeout_seconds,
                env_file=self.config.env_file,
            )
        if self.config.provider == "deepseek":
            return DeepSeekAnalysisProvider(
                model=self.config.model,
                timeout_seconds=self.config.timeout_seconds,
                env_file=self.config.env_file,
            )
        raise ProviderError(f"unsupported analysis provider: {self.config.provider}")

    def _audit_fields(self, provider: Any | None) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "model": self.config.model
            if self.config.provider in {"openai", "deepseek"}
            else None,
            "provider": self.config.provider,
        }
        if provider is not None and hasattr(provider, "audit_metadata"):
            fields.update(provider.audit_metadata())
        return fields

    def _report(
        self,
        *,
        analysis_ready: bool,
        analysis_bytes: bytes,
        as_of: Any,
        bundle_sha: str | None,
        input_bundle_path: str,
        issues: list[dict[str, str]],
        status: str,
        symbol: Any,
        provider: Any | None,
    ) -> dict[str, Any]:
        report: dict[str, Any] = {
            "analysis_ready": analysis_ready,
            "analysis_version": ANALYSIS_REPORT_VERSION,
            "as_of": as_of,
            "decision_ready": False,
            "input_bundle_path": input_bundle_path,
            "input_bundle_sha256": bundle_sha,
            "issues": issues,
            "output_sha256": sha256_bytes(analysis_bytes),
            "schema_version": ANALYSIS_REPORT_SCHEMA_VERSION,
            "status": status,
            "symbol": symbol,
        }
        report.update(self._audit_fields(provider))
        return report

    def capture(self) -> dict[str, Any]:
        bundle: dict[str, Any] | None = None
        bundle_sha: str | None = None
        provider: Any | None = None
        input_bundle_path = _bundle_display_path(self.config)
        try:
            bundle, bundle_sha, entries, cutoff = _validate_bundle(self.config)
            provider = self._provider()
            provider_payload = provider.load(bundle=bundle, entries=entries)
            sections = _validate_provider(
                provider_payload,
                bundle=bundle,
                entries=entries,
                cutoff=cutoff,
            )
            analysis = {
                "analysis_ready": True,
                "analysis_version": ANALYSIS_REPORT_VERSION,
                "as_of": bundle["as_of"],
                "citations": _citation_mappings(entries),
                "decision_ready": False,
                "input_bundle_path": input_bundle_path,
                "input_bundle_sha256": bundle_sha,
                "issues": [],
                "schema_version": ANALYSIS_REPORT_SCHEMA_VERSION,
                "sections": {
                    section: [item.to_mapping() for item in sections[section]]
                    for section in ANALYSIS_SECTIONS
                },
                "status": "ready",
                "symbol": bundle["symbol"],
            }
            analysis_bytes = _json_bytes(analysis)
            report = self._report(
                analysis_ready=True,
                analysis_bytes=analysis_bytes,
                as_of=bundle["as_of"],
                bundle_sha=bundle_sha,
                input_bundle_path=input_bundle_path,
                issues=[],
                status="ready",
                symbol=bundle["symbol"],
                provider=provider,
            )
        except AnalysisValidationError as exc:
            issues = [{"code": exc.code, "message": str(exc)}]
            analysis = _invalid_output(
                bundle=bundle,
                bundle_sha=bundle_sha,
                input_bundle_path=input_bundle_path,
                issues=issues,
            )
            analysis_bytes = _json_bytes(analysis)
            report = self._report(
                analysis_ready=False,
                analysis_bytes=analysis_bytes,
                as_of=bundle.get("as_of") if bundle else None,
                bundle_sha=bundle_sha,
                input_bundle_path=input_bundle_path,
                issues=issues,
                status="invalid",
                symbol=bundle.get("symbol") if bundle else None,
                provider=provider,
            )
        except ProviderError as exc:
            issues = [{"code": "PROVIDER_INVALID", "message": str(exc)}]
            analysis = _invalid_output(
                bundle=bundle,
                bundle_sha=bundle_sha,
                input_bundle_path=input_bundle_path,
                issues=issues,
            )
            analysis_bytes = _json_bytes(analysis)
            report = self._report(
                analysis_ready=False,
                analysis_bytes=analysis_bytes,
                as_of=bundle.get("as_of") if bundle else None,
                bundle_sha=bundle_sha,
                input_bundle_path=input_bundle_path,
                issues=issues,
                status="invalid",
                symbol=bundle.get("symbol") if bundle else None,
                provider=provider,
            )
        except Exception as exc:  # pragma: no cover - final safety gate
            issues = [{"code": "INTERNAL_VALIDATION_ERROR", "message": str(exc)}]
            analysis = _invalid_output(
                bundle=bundle,
                bundle_sha=bundle_sha,
                input_bundle_path=input_bundle_path,
                issues=issues,
            )
            analysis_bytes = _json_bytes(analysis)
            report = self._report(
                analysis_ready=False,
                analysis_bytes=analysis_bytes,
                as_of=bundle.get("as_of") if bundle else None,
                bundle_sha=bundle_sha,
                input_bundle_path=input_bundle_path,
                issues=issues,
                status="invalid",
                symbol=bundle.get("symbol") if bundle else None,
                provider=provider,
            )

        write_atomic(self.config.output_dir / "research_analysis.json", analysis_bytes)
        write_atomic(self.config.output_dir / "research_analysis_report.json", _json_bytes(report))
        if provider is not None and hasattr(provider, "write_audit_files"):
            provider.write_audit_files(self.config.output_dir)
        return report
