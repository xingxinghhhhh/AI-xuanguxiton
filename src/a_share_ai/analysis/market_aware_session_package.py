"""Build an immutable, relative-path audit manifest for a market-aware session."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_renderer import (
    FRESHNESS_REPORT_NAME,
    MARKET_AWARE_SESSION_RENDER_VERSION,
    MARKET_AWARE_SESSION_VERSION,
    TIME_POLICY,
)

MARKET_AWARE_SESSION_PACKAGE_VERSION = "market-aware-session-package-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SESSION_FIELDS = (
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
)
_ARTIFACT_ROLES = (
    "session",
    "session_report",
    "freshness_report",
    "session_markdown",
    "session_render_report",
)


class MarketAwareSessionPackageError(ValueError):
    """A fail-closed error while building a session package."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MarketAwareSessionPackageError("INPUT_JSON_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise MarketAwareSessionPackageError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return value


def _read_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionPackageError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionPackageError("INPUT_UNAVAILABLE", f"{label} is not a file")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionPackageError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {
        "path": candidate,
        "relative_path": relative,
        "raw": raw,
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
    }


def _validate_sha(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise MarketAwareSessionPackageError("HASH_INVALID", f"{label} must be a SHA-256 string")
    return value


def _parse_datetime(value: Any, *, label: str, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionPackageError("TIME_INVALID", f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionPackageError("TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionPackageError("TIME_INVALID", f"{label} must include timezone")


def _same(session: Mapping[str, Any], report: Mapping[str, Any], field: str) -> Any:
    if session.get(field) != report.get(field):
        raise MarketAwareSessionPackageError("FIELD_MISMATCH", f"session/report.{field} differs")
    return session.get(field)


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionPackageError("ISSUES_INVALID", f"{label} must be a list")
    for index, issue in enumerate(value):
        if not isinstance(issue, Mapping):
            raise MarketAwareSessionPackageError("ISSUES_INVALID", f"{label}[{index}] is invalid")
        if not isinstance(issue.get("code"), str) or not isinstance(issue.get("message"), str):
            raise MarketAwareSessionPackageError("ISSUES_INVALID", f"{label}[{index}] is invalid")


def _validate_session_chain(files: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    session = _read_json(files["session"]["raw"], label="market_aware_session")
    session_report = _read_json(
        files["session_report"]["raw"], label="market_aware_session_report"
    )
    freshness = _read_json(files["freshness_report"]["raw"], label="research_freshness_report")
    render_report = _read_json(
        files["session_render_report"]["raw"], label="market_aware_session_render_report"
    )

    for field in _SESSION_FIELDS:
        _same(session, session_report, field)
    if session.get("session_version") != MARKET_AWARE_SESSION_VERSION:
        raise MarketAwareSessionPackageError("VERSION_MISMATCH", "session version is invalid")
    if session.get("time_policy") != TIME_POLICY:
        raise MarketAwareSessionPackageError(
            "TIME_POLICY_INVALID", "session time policy is invalid"
        )
    _parse_datetime(session.get("evaluation_at"), label="evaluation_at")
    _parse_datetime(session.get("reference_at"), label="reference_at")
    _parse_datetime(session.get("as_of"), label="as_of", allow_none=True)
    if session.get("decision_ready") is not False:
        raise MarketAwareSessionPackageError(
            "DECISION_GATE_INVALID", "decision_ready must be false"
        )
    _validate_issues(session.get("issues"), label="session.issues")
    if session_report.get("output_sha256") != files["session"]["sha256"]:
        raise MarketAwareSessionPackageError(
            "HASH_MISMATCH", "session report output SHA does not match session"
        )

    freshness_sha = _validate_sha(
        session.get("freshness_report_sha256"), label="freshness_report_sha256"
    )
    if freshness_sha != files["freshness_report"]["sha256"]:
        raise MarketAwareSessionPackageError(
            "HASH_MISMATCH", "freshness report SHA does not match session"
        )
    if freshness.get("research_freshness_version") != "research-freshness-v1":
        raise MarketAwareSessionPackageError(
            "VERSION_MISMATCH", "freshness report version is invalid"
        )
    if freshness.get("decision_ready") is not False:
        raise MarketAwareSessionPackageError(
            "DECISION_GATE_INVALID", "freshness decision_ready must be false"
        )
    for field in ("evaluation_at", "freshness_status", "freshness_ready"):
        if freshness.get(field) != session.get(field):
            raise MarketAwareSessionPackageError(
                "FIELD_MISMATCH", f"freshness {field} differs from session"
            )

    if render_report.get("render_version") != MARKET_AWARE_SESSION_RENDER_VERSION:
        raise MarketAwareSessionPackageError(
            "VERSION_MISMATCH", "session render report version is invalid"
        )
    if render_report.get("session_sha256") != files["session"]["sha256"]:
        raise MarketAwareSessionPackageError(
            "HASH_MISMATCH", "render report session SHA does not match session"
        )
    if render_report.get("session_report_sha256") != files["session_report"]["sha256"]:
        raise MarketAwareSessionPackageError(
            "HASH_MISMATCH", "render report session report SHA does not match report"
        )
    if render_report.get("freshness_report_sha256") != files["freshness_report"]["sha256"]:
        raise MarketAwareSessionPackageError(
            "HASH_MISMATCH", "render report freshness SHA does not match freshness report"
        )
    if render_report.get("output_sha256") != files["session_markdown"]["sha256"]:
        raise MarketAwareSessionPackageError(
            "HASH_MISMATCH", "render report output SHA does not match Markdown"
        )
    for field in (
        "symbol",
        "as_of",
        "evaluation_at",
        "reference_at",
        "status",
        "session_ready",
        "freshness_status",
        "freshness_ready",
        "decision_ready",
    ):
        if render_report.get(field) != session.get(field):
            raise MarketAwareSessionPackageError(
                "FIELD_MISMATCH", f"render report {field} differs from session"
            )
    if render_report.get("session_render_ready") is not (session.get("session_ready") is True):
        raise MarketAwareSessionPackageError(
            "FIELD_MISMATCH", "render report session_render_ready differs from session"
        )
    for field in ("session_path", "session_report_path", "freshness_report_path"):
        value = render_report.get(field)
        if value is not None and Path(value).is_absolute():
            raise MarketAwareSessionPackageError(
                "PATH_INVALID", f"render report {field} must be relative"
            )
    expected_paths = {
        "session_path": files["session"]["relative_path"],
        "session_report_path": files["session_report"]["relative_path"],
        "freshness_report_path": files["freshness_report"]["relative_path"],
    }
    for field, expected in expected_paths.items():
        if render_report.get(field) != expected:
            raise MarketAwareSessionPackageError(
                "CHAIN_MISMATCH", f"render report {field} is inconsistent"
            )
    return {
        "as_of": session.get("as_of"),
        "evaluation_at": session.get("evaluation_at"),
        "freshness_status": session.get("freshness_status"),
        "reference_at": session.get("reference_at"),
        "session_ready": session.get("session_ready"),
        "status": session.get("status"),
        "symbol": session.get("symbol"),
    }


def _artifact_manifest(files: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": files[role]["relative_path"],
            "role": role,
            "sha256": files[role]["sha256"],
            "size_bytes": files[role]["size_bytes"],
        }
        for role in _ARTIFACT_ROLES
    ]


def _base_report() -> dict[str, Any]:
    return {
        "as_of": None,
        "artifact_count": 0,
        "artifacts": [],
        "decision_ready": False,
        "evaluation_at": None,
        "freshness_status": None,
        "issues": [],
        "output_sha256": None,
        "package_ready": False,
        "package_version": MARKET_AWARE_SESSION_PACKAGE_VERSION,
        "reference_at": None,
        "session_ready": False,
        "status": "invalid",
        "symbol": None,
    }


def build_market_aware_session_package(
    *,
    session_path: Path,
    session_report_path: Path,
    freshness_report_path: Path,
    session_markdown_path: Path,
    session_render_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a deterministic relative-path package manifest."""

    report = _base_report()
    can_write_report = False
    package_path = output_dir / "market_aware_session_package.json"
    report_path = output_dir / "market_aware_session_package_report.json"
    try:
        root = artifact_root.resolve()
        output_resolved = output_dir.resolve()
        if not root.is_dir():
            raise MarketAwareSessionPackageError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_resolved.relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionPackageError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "package output is outside artifact root"
            ) from exc
        can_write_report = True
        files = {
            "session": _read_file(session_path, root=root, label="market_aware_session"),
            "session_report": _read_file(
                session_report_path, root=root, label="market_aware_session_report"
            ),
            "freshness_report": _read_file(
                freshness_report_path, root=root, label="research_freshness_report"
            ),
            "session_markdown": _read_file(
                session_markdown_path, root=root, label="market_aware_session_markdown"
            ),
            "session_render_report": _read_file(
                session_render_report_path,
                root=root,
                label="market_aware_session_render_report",
            ),
        }
        session_dir = files["session"]["path"].parent
        if files["session_report"]["path"].parent != session_dir:
            raise MarketAwareSessionPackageError(
                "CHAIN_MISMATCH", "session and session report must share a directory"
            )
        if files["freshness_report"]["path"] != session_dir / FRESHNESS_REPORT_NAME:
            raise MarketAwareSessionPackageError(
                "CHAIN_MISMATCH", "freshness report must be next to session"
            )
        summary = _validate_session_chain(files)
        artifacts = _artifact_manifest(files)
        package = {
            "as_of": summary["as_of"],
            "artifacts": artifacts,
            "decision_ready": False,
            "evaluation_at": summary["evaluation_at"],
            "freshness_status": summary["freshness_status"],
            "package_version": MARKET_AWARE_SESSION_PACKAGE_VERSION,
            "reference_at": summary["reference_at"],
            "session_ready": summary["session_ready"],
            "status": summary["status"],
            "symbol": summary["symbol"],
        }
        package_raw = _json_bytes(package)
        write_atomic(package_path, package_raw)
        report.update(
            {
                "as_of": summary["as_of"],
                "artifact_count": len(artifacts),
                "artifacts": artifacts,
                "evaluation_at": summary["evaluation_at"],
                "freshness_status": summary["freshness_status"],
                "output_sha256": sha256_bytes(package_raw),
                "package_ready": True,
                "reference_at": summary["reference_at"],
                "session_ready": summary["session_ready"],
                "status": summary["status"],
                "symbol": summary["symbol"],
            }
        )
    except MarketAwareSessionPackageError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(report_path, _json_bytes(report))
    return report
