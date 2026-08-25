"""Render a market-aware session admission report as safe deterministic Markdown."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic

MARKET_AWARE_SESSION_RENDER_VERSION = "market-aware-session-render-v1"
MARKET_AWARE_SESSION_VERSION = "market-aware-session-v1"
TIME_POLICY = "explicit-reference-v1"
FRESHNESS_REPORT_NAME = "research_freshness_report.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_STATUSES = {"ready", "stale", "calendar_unknown", "invalid"}
_FRESHNESS_STATUSES = {"fresh", "stale", "calendar_unknown", "invalid"}


class MarketAwareSessionRenderError(ValueError):
    """A fail-closed error while validating session render inputs."""

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
        value = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketAwareSessionRenderError("INPUT_JSON_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise MarketAwareSessionRenderError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return value, raw


def _safe_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise MarketAwareSessionRenderError("FIELD_INVALID", f"{label} must be a string")
    return (
        value.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("#", "\\#")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _parse_datetime(value: Any, *, label: str, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionRenderError("TIME_INVALID", f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionRenderError("TIME_INVALID", f"{label} is not ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionRenderError("TIME_INVALID", f"{label} must include timezone")


def _safe_file(path: Path, *, root: Path, label: str) -> tuple[Path, str, bytes, str]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionRenderError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionRenderError("INPUT_UNAVAILABLE", f"{label} is not a file")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionRenderError("INPUT_UNAVAILABLE", f"{label} is unavailable") from exc
    return candidate, relative, raw, sha256_bytes(raw)


def _validate_sha(value: Any, *, label: str, allow_none: bool = True) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise MarketAwareSessionRenderError("HASH_INVALID", f"{label} must be a SHA-256 string")
    return value


def _same_field(session: Mapping[str, Any], report: Mapping[str, Any], field: str) -> Any:
    if session.get(field) != report.get(field):
        raise MarketAwareSessionRenderError("FIELD_MISMATCH", f"session/report.{field} differs")
    return session.get(field)


def _validate_issues(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise MarketAwareSessionRenderError("ISSUES_INVALID", f"{label} must be a list")
    issues: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionRenderError("ISSUES_INVALID", f"{label}[{index}] is invalid")
        code = item.get("code")
        message = item.get("message")
        if not isinstance(code, str) or not code or not isinstance(message, str):
            raise MarketAwareSessionRenderError("ISSUES_INVALID", f"{label}[{index}] is invalid")
        issues.append({"code": code, "message": message})
    return issues


def _validate_freshness(
    *,
    session_file: Path,
    root: Path,
    session: Mapping[str, Any],
    freshness_sha: str | None,
) -> dict[str, Any]:
    freshness_path = session_file.parent / FRESHNESS_REPORT_NAME
    if freshness_sha is None:
        if session.get("freshness_status") != "invalid":
            raise MarketAwareSessionRenderError(
                "FRESHNESS_REPORT_MISSING", "freshness report hash is required"
            )
        return {
            "freshness_report_path": None,
            "freshness_report_sha256": None,
            "freshness_report_present": False,
        }

    resolved, relative, raw, actual_sha = _safe_file(
        freshness_path, root=root, label="research_freshness_report"
    )
    if actual_sha != freshness_sha:
        raise MarketAwareSessionRenderError(
            "HASH_MISMATCH", "research freshness report SHA does not match session"
        )
    freshness, _ = _read_json(resolved, label="research_freshness_report")
    if freshness.get("research_freshness_version") != "research-freshness-v1":
        raise MarketAwareSessionRenderError(
            "VERSION_MISMATCH", "research freshness report version is invalid"
        )
    if freshness.get("decision_ready") is not False:
        raise MarketAwareSessionRenderError(
            "DECISION_GATE_INVALID", "research freshness decision_ready must be false"
        )
    for field in ("evaluation_at", "freshness_status", "freshness_ready"):
        if freshness.get(field) != session.get(field):
            raise MarketAwareSessionRenderError(
                "FIELD_MISMATCH", f"research freshness {field} differs from session"
            )
    return {
        "freshness_report_path": relative,
        "freshness_report_sha256": actual_sha,
        "freshness_report_present": True,
    }


def _validate_inputs(
    *,
    session_path: Path,
    session_report_path: Path,
    artifact_root: Path,
) -> dict[str, Any]:
    root = artifact_root.resolve()
    if not root.is_dir():
        raise MarketAwareSessionRenderError(
            "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
        )
    session_file, session_relative, session_raw, session_sha = _safe_file(
        session_path, root=root, label="market_aware_session"
    )
    report_file, report_relative, report_raw, report_sha = _safe_file(
        session_report_path, root=root, label="market_aware_session_report"
    )
    if session_file.parent != report_file.parent:
        raise MarketAwareSessionRenderError(
            "CHAIN_MISMATCH", "session and session report must share a directory"
        )
    session, _ = _read_json(session_file, label="market_aware_session")
    report, _ = _read_json(report_file, label="market_aware_session_report")

    for field in (
        "session_version",
        "symbol",
        "as_of",
        "evaluation_at",
        "reference_at",
        "time_policy",
        "status",
        "session_ready",
        "freshness_status",
        "freshness_ready",
        "decision_ready",
        "issues",
    ):
        _same_field(session, report, field)
    if session.get("session_version") != MARKET_AWARE_SESSION_VERSION:
        raise MarketAwareSessionRenderError("VERSION_MISMATCH", "session version is invalid")
    if session.get("time_policy") != TIME_POLICY:
        raise MarketAwareSessionRenderError("TIME_POLICY_INVALID", "session time policy is invalid")
    _parse_datetime(session.get("evaluation_at"), label="evaluation_at")
    _parse_datetime(session.get("reference_at"), label="reference_at")
    _parse_datetime(session.get("as_of"), label="as_of", allow_none=True)
    if session.get("decision_ready") is not False:
        raise MarketAwareSessionRenderError("DECISION_GATE_INVALID", "decision_ready must be false")
    status = session.get("status")
    freshness_status = session.get("freshness_status")
    if status not in _SOURCE_STATUSES or freshness_status not in _FRESHNESS_STATUSES:
        raise MarketAwareSessionRenderError("STATUS_INVALID", "session status is invalid")
    if status == "ready" and (
        session.get("session_ready") is not True
        or freshness_status != "fresh"
        or session.get("freshness_ready") is not True
    ):
        raise MarketAwareSessionRenderError(
            "STATUS_INVALID", "ready session gates are inconsistent"
        )
    if status != "ready" and session.get("session_ready") is not False:
        raise MarketAwareSessionRenderError("STATUS_INVALID", "blocked session must not be ready")
    if (freshness_status == "fresh") != (session.get("freshness_ready") is True):
        raise MarketAwareSessionRenderError("STATUS_INVALID", "freshness readiness is inconsistent")
    _validate_issues(session.get("issues"), label="session.issues")
    if session.get("symbol") is not None and (
        not isinstance(session.get("symbol"), str) or not session["symbol"].strip()
    ):
        raise MarketAwareSessionRenderError("FIELD_INVALID", "symbol is invalid")
    for field in ("release_manifest_sha256", "bundle_sha256", "calendar_sha256"):
        _validate_sha(session.get(field), label=field)
    declared_freshness_sha = _validate_sha(
        session.get("freshness_report_sha256"), label="freshness_report_sha256"
    )
    if report.get("output_sha256") is not None:
        declared_output_sha = _validate_sha(report.get("output_sha256"), label="output_sha256")
        if declared_output_sha != session_sha:
            raise MarketAwareSessionRenderError(
                "HASH_MISMATCH", "session report output SHA does not match session"
            )
    freshness = _validate_freshness(
        session_file=session_file,
        root=root,
        session=session,
        freshness_sha=declared_freshness_sha,
    )
    return {
        "artifact_root": root,
        "session": session,
        "session_sha256": session_sha,
        "session_path": session_relative,
        "session_report_sha256": report_sha,
        "session_report_path": report_relative,
        "freshness_report_sha256": freshness["freshness_report_sha256"],
        "freshness_report_path": freshness["freshness_report_path"],
        "freshness_report_present": freshness["freshness_report_present"],
    }


def _value(value: Any, *, label: str) -> str:
    if value is None:
        return "not available"
    return _safe_text(str(value), label=label)


def _bool(value: Any) -> str:
    return str(value).lower()


def _render_markdown(metadata: Mapping[str, Any]) -> bytes:
    session = metadata["session"]
    session_ready = session["session_ready"] is True
    render_status = "ready" if session_ready else "blocked"
    symbol = _value(session.get("symbol"), label="symbol")
    as_of = _value(session.get("as_of"), label="as_of")
    status = _value(session.get("status"), label="status")
    session_path = _value(metadata["session_path"], label="session_path")
    session_report_path = _value(
        metadata["session_report_path"], label="session_report_path"
    )
    evaluation_at = _value(session.get("evaluation_at"), label="evaluation_at")
    reference_at = _value(session.get("reference_at"), label="reference_at")
    time_policy = _value(session.get("time_policy"), label="time_policy")
    freshness_status = _value(session.get("freshness_status"), label="freshness_status")
    freshness_report_sha = _value(
        metadata["freshness_report_sha256"], label="freshness_report_sha256"
    )
    market_context_version = _value(
        session.get("market_context_summary_version"),
        label="market_context_summary_version",
    )
    relative_strength_version = _value(
        session.get("relative_strength_version"), label="relative_strength_version"
    )
    release_manifest_sha = _value(
        session.get("release_manifest_sha256"), label="release_manifest_sha256"
    )
    bundle_sha = _value(session.get("bundle_sha256"), label="bundle_sha256")
    calendar_sha = _value(session.get("calendar_sha256"), label="calendar_sha256")
    freshness_path = metadata["freshness_report_path"]
    freshness_reference = (
        _value(freshness_path, label="freshness_report_path")
        if freshness_path is not None
        else "not available for this invalid time/input result"
    )
    lines = [
        "# Market-aware Research Session",
        "",
        "## 1. Session status",
        "",
        f"- symbol: `{symbol}`",
        f"- as_of: `{as_of}`",
        f"- status: `{status}`",
        f"- session_ready: `{_bool(session.get('session_ready'))}`",
        f"- session_render_status: `{render_status}`",
        f"- session_path: `{session_path}`",
        f"- session_report_path: `{session_report_path}`",
        "",
        "## 2. Time and freshness",
        "",
        f"- evaluation_at: `{evaluation_at}`",
        f"- reference_at: `{reference_at}`",
        f"- time_policy: `{time_policy}`",
        f"- freshness_status: `{freshness_status}`",
        f"- freshness_ready: `{_bool(session.get('freshness_ready'))}`",
        f"- freshness_report: `{freshness_reference}`",
        f"- freshness_report_sha256: `{freshness_report_sha}`",
        "",
        "## 3. Market-aware summary versions",
        "",
        f"- market_context_summary_version: `{market_context_version}`",
        f"- relative_strength_version: `{relative_strength_version}`",
        "- These versions are displayed from the session report; no unavailable "
        "source path is inferred.",
        "",
        "## 4. Review, release, and decision gate",
        "",
        f"- research_release_ready: `{_bool(session.get('research_release_ready'))}`",
        f"- review_complete: `{_bool(session.get('review_complete'))}`",
        f"- review_gate_pass: `{_bool(session.get('review_gate_pass'))}`",
        f"- decision_ready: `{_bool(session.get('decision_ready'))}`",
        "- session_ready is an evidence freshness/readability gate, not trading authorization.",
        "",
        "## 5. Declared hash summaries",
        "",
        "The following values are declared summaries only; this renderer does not "
        "claim to locate or re-validate a missing upstream path:",
        "",
        f"- release_manifest_sha256: `{release_manifest_sha}`",
        f"- bundle_sha256: `{bundle_sha}`",
        f"- calendar_sha256: `{calendar_sha}`",
        "",
        "## 6. Issues and safety boundary",
        "",
    ]
    issues = session.get("issues") or []
    if issues:
        for issue in issues:
            lines.append(
                f"- `{_safe_text(issue['code'], label='issue.code')}`: "
                f"{_safe_text(issue['message'], label='issue.message')}"
            )
    else:
        lines.append("- No admission issues recorded.")
    lines.extend(
        [
            "",
            "This report is read-only and deterministic for fixed inputs. It does "
            "not make an investment judgement, create a trade instruction, or "
            "authorize an order.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _base_report() -> dict[str, Any]:
    return {
        "decision_ready": False,
        "evaluation_at": None,
        "freshness_report_path": None,
        "freshness_report_sha256": None,
        "freshness_ready": False,
        "freshness_status": "invalid",
        "issues": [],
        "output_sha256": None,
        "reference_at": None,
        "render_version": MARKET_AWARE_SESSION_RENDER_VERSION,
        "session_ready": False,
        "session_render_ready": False,
        "session_report_path": None,
        "session_report_sha256": None,
        "session_sha256": None,
        "session_path": None,
        "status": "invalid",
        "symbol": None,
    }


def render_market_aware_session(
    *,
    session_path: Path,
    session_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Render a session admission report without mutating upstream artifacts."""

    report = _base_report()
    can_write_report = False
    render_report_path = output_dir / "market_aware_session_render_report.json"
    try:
        root = artifact_root.resolve()
        output_resolved = output_dir.resolve()
        if not root.is_dir():
            raise MarketAwareSessionRenderError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_resolved.relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionRenderError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "render output is outside artifact root"
            ) from exc
        can_write_report = True
        metadata = _validate_inputs(
            session_path=session_path,
            session_report_path=session_report_path,
            artifact_root=root,
        )
        session = metadata["session"]
        markdown = _render_markdown(metadata)
        markdown_path = output_dir / "market_aware_session.md"
        write_atomic(markdown_path, markdown)
        report.update(
            {
                "as_of": session.get("as_of"),
                "evaluation_at": session.get("evaluation_at"),
                "freshness_report_path": metadata["freshness_report_path"],
                "freshness_report_sha256": metadata["freshness_report_sha256"],
                "freshness_ready": session.get("freshness_ready"),
                "freshness_status": session.get("freshness_status"),
                "output_sha256": sha256_bytes(markdown),
                "reference_at": session.get("reference_at"),
                "session_ready": session.get("session_ready"),
                "session_render_ready": session.get("session_ready") is True,
                "session_report_path": metadata["session_report_path"],
                "session_report_sha256": metadata["session_report_sha256"],
                "session_sha256": metadata["session_sha256"],
                "session_path": metadata["session_path"],
                "status": session.get("status"),
                "symbol": session.get("symbol"),
                "issues": list(session.get("issues") or []),
            }
        )
    except MarketAwareSessionRenderError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(render_report_path, _json_bytes(report))
    return report
