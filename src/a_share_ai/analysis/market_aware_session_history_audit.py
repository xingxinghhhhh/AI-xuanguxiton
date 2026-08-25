"""Independently audit a market-aware session history manifest."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_history import MARKET_AWARE_SESSION_HISTORY_VERSION
from .market_aware_session_package_audit import audit_market_aware_session_package

MARKET_AWARE_SESSION_HISTORY_AUDIT_VERSION = "market-aware-session-history-audit-v1"
_PACKAGE_FIELDS = {
    "artifact_root",
    "as_of",
    "audit_ready",
    "decision_ready",
    "evaluation_at",
    "freshness_ready",
    "freshness_status",
    "manifest_path",
    "package_ready",
    "package_sha256",
    "reference_at",
    "report_path",
    "session_ready",
    "status",
    "symbol",
}


class MarketAwareSessionHistoryAuditError(ValueError):
    """A fail-closed history audit error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MarketAwareSessionHistoryAuditError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryAuditError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryAuditError(
            "PATH_OUTSIDE_HISTORY_ROOT", f"{label} is outside history root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryAuditError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {
        "path": candidate,
        "relative_path": relative,
        "raw": raw,
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
    }


def _relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise MarketAwareSessionHistoryAuditError(
            "PATH_INVALID", f"{label} must be relative"
        )
    if any(part == ".." for part in Path(value).parts):
        raise MarketAwareSessionHistoryAuditError(
            "PATH_INVALID", f"{label} must stay relative"
        )
    return value.replace("\\", "/")


def _parse_as_of(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryAuditError("AS_OF_INVALID", f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryAuditError("AS_OF_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryAuditError(
            "AS_OF_INVALID", f"{label} must include timezone"
        )
    return parsed


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryAuditError("ISSUES_INVALID", f"{label} must be a list")
    for index, issue in enumerate(value):
        if not isinstance(issue, Mapping):
            raise MarketAwareSessionHistoryAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(issue.get("code"), str) or not isinstance(issue.get("message"), str):
            raise MarketAwareSessionHistoryAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _audit_package(
    entry: Mapping[str, Any], *, root: Path, index: int
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest_relative = _relative_path(
        entry.get("manifest_path"), label=f"packages[{index}].manifest_path"
    )
    report_relative = _relative_path(
        entry.get("report_path"), label=f"packages[{index}].report_path"
    )
    artifact_relative = _relative_path(
        entry.get("artifact_root"), label=f"packages[{index}].artifact_root"
    )
    manifest = (root / manifest_relative).resolve()
    package_report = (root / report_relative).resolve()
    artifact_root = (root / artifact_relative).resolve()
    for candidate, label in (
        (manifest, "manifest"),
        (package_report, "package report"),
        (artifact_root, "artifact root"),
    ):
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise MarketAwareSessionHistoryAuditError(
                "PATH_OUTSIDE_HISTORY_ROOT", f"package[{index}] {label} escapes history root"
            ) from exc
    if not artifact_root.is_dir():
        raise MarketAwareSessionHistoryAuditError(
            "ARTIFACT_ROOT_INVALID", f"package[{index}] artifact root is invalid"
        )
    with tempfile.TemporaryDirectory(
        prefix=f".history-audit-{index}-", dir=artifact_root
    ) as audit_dir:
        audit = audit_market_aware_session_package(
            package_path=manifest,
            package_report_path=package_report,
            artifact_root=artifact_root,
            output_dir=Path(audit_dir),
        )
    if audit.get("audit_ready") is not True:
        issue = audit.get("issues")
        detail = issue[0] if isinstance(issue, list) and issue else {}
        code = (
            detail.get("code", "PACKAGE_AUDIT_FAILED")
            if isinstance(detail, Mapping)
            else "PACKAGE_AUDIT_FAILED"
        )
        message = (
            detail.get("message", "package audit failed")
            if isinstance(detail, Mapping)
            else "package audit failed"
        )
        raise MarketAwareSessionHistoryAuditError(
            "PACKAGE_AUDIT_FAILED", f"package[{index}]: {code}: {message}"
        )
    package = _read_json(manifest.read_bytes(), label=f"package[{index}]")
    artifacts = package.get("artifacts")
    session_entry = next(
        (item for item in artifacts if isinstance(item, Mapping) and item.get("role") == "session"),
        None,
    ) if isinstance(artifacts, list) else None
    if not isinstance(session_entry, Mapping) or not isinstance(session_entry.get("path"), str):
        raise MarketAwareSessionHistoryAuditError(
            "ARTIFACT_INVALID", f"package[{index}] session artifact is missing"
        )
    session_relative = _relative_path(
        session_entry["path"], label=f"package[{index}] session.path"
    )
    session_path = (artifact_root / session_relative).resolve()
    try:
        session_path.relative_to(artifact_root)
    except ValueError as exc:
        raise MarketAwareSessionHistoryAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"package[{index}] session escapes artifact root"
        ) from exc
    session = _read_json(session_path.read_bytes(), label=f"package[{index}] session")
    actual = {
        "artifact_root": artifact_relative,
        "as_of": package.get("as_of"),
        "audit_ready": audit.get("audit_ready"),
        "decision_ready": package.get("decision_ready"),
        "evaluation_at": package.get("evaluation_at"),
        "freshness_ready": session.get("freshness_ready"),
        "freshness_status": package.get("freshness_status"),
        "manifest_path": manifest_relative,
        "package_ready": audit.get("package_ready"),
        "package_sha256": audit.get("package_sha256"),
        "reference_at": package.get("reference_at"),
        "report_path": report_relative,
        "session_ready": audit.get("session_ready"),
        "status": package.get("status"),
        "symbol": package.get("symbol"),
    }
    return actual, package, audit


def _base_report() -> dict[str, Any]:
    return {
        "audit_ready": False,
        "audit_version": MARKET_AWARE_SESSION_HISTORY_AUDIT_VERSION,
        "decision_ready": False,
        "first_as_of": None,
        "history_path": None,
        "history_report_path": None,
        "history_report_sha256": None,
        "history_report_size_bytes": 0,
        "history_sha256": None,
        "history_size_bytes": 0,
        "issues": [],
        "last_as_of": None,
        "output_sha256": None,
        "package_count": 0,
        "packages": [],
        "symbol": None,
    }


def _finalize_report(report: dict[str, Any]) -> bytes:
    payload = dict(report)
    payload["output_sha256"] = None
    raw = _json_bytes(payload)
    report["output_sha256"] = sha256_bytes(raw)
    return _json_bytes(report)


def audit_market_aware_session_history(
    *,
    history_path: Path,
    history_report_path: Path,
    history_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Audit a Node37 history manifest and its referenced packages."""

    report = _base_report()
    can_write_report = False
    audit_report_path = output_dir / "market_aware_session_history_audit_report.json"
    try:
        root = history_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryAuditError(
                "HISTORY_ROOT_INVALID", "history root must be a directory"
            )
        output_resolved = output_dir.resolve()
        try:
            output_resolved.relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryAuditError(
                "PATH_OUTSIDE_HISTORY_ROOT", "audit output is outside history root"
            ) from exc
        can_write_report = True
        history_file = _safe_file(history_path, root=root, label="history")
        history_report_file = _safe_file(
            history_report_path, root=root, label="history report"
        )
        history = _read_json(history_file["raw"], label="history")
        history_report = _read_json(history_report_file["raw"], label="history report")
        if history.get("history_version") != MARKET_AWARE_SESSION_HISTORY_VERSION:
            raise MarketAwareSessionHistoryAuditError(
                "VERSION_MISMATCH", "history version is invalid"
            )
        if history_report.get("history_version") != MARKET_AWARE_SESSION_HISTORY_VERSION:
            raise MarketAwareSessionHistoryAuditError(
                "VERSION_MISMATCH", "history report version is invalid"
            )
        if (
            history.get("decision_ready") is not False
            or history_report.get("decision_ready") is not False
        ):
            raise MarketAwareSessionHistoryAuditError(
                "DECISION_GATE_INVALID", "history decision_ready must be false"
            )
        report_output_sha = history_report.get("output_sha256")
        if report_output_sha != history_file["sha256"]:
            report_payload = dict(history_report)
            report_payload["output_sha256"] = None
            if report_output_sha != sha256_bytes(_json_bytes(report_payload)):
                raise MarketAwareSessionHistoryAuditError(
                    "HASH_MISMATCH", "history report output SHA is invalid"
                )
        _validate_issues(history.get("issues"), label="history.issues")
        _validate_issues(history_report.get("issues"), label="history_report.issues")
        if history.get("issues") != history_report.get("issues"):
            raise MarketAwareSessionHistoryAuditError(
                "FIELD_MISMATCH", "history/report issues differ"
            )
        packages = history.get("packages")
        if not isinstance(packages, list) or len(packages) < 2:
            raise MarketAwareSessionHistoryAuditError(
                "PACKAGE_COUNT_INVALID", "history must contain at least two packages"
            )
        actual_packages: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for index, entry in enumerate(packages):
            if not isinstance(entry, Mapping) or set(entry) != _PACKAGE_FIELDS:
                raise MarketAwareSessionHistoryAuditError(
                    "PACKAGE_ENTRY_INVALID", f"history.packages[{index}] is invalid"
                )
            reference = (
                _relative_path(entry["manifest_path"], label=f"packages[{index}].manifest_path"),
                _relative_path(entry["report_path"], label=f"packages[{index}].report_path"),
                _relative_path(entry["artifact_root"], label=f"packages[{index}].artifact_root"),
            )
            if reference in seen:
                raise MarketAwareSessionHistoryAuditError(
                    "DUPLICATE_PACKAGE", f"history package[{index}] is duplicated"
                )
            seen.add(reference)
            actual, _package, _audit = _audit_package(
                entry, root=root, index=index
            )
            if dict(entry) != actual:
                raise MarketAwareSessionHistoryAuditError(
                    "FIELD_MISMATCH", f"history package[{index}] differs from package"
                )
            actual_packages.append(actual)
        symbols = {entry["symbol"] for entry in actual_packages}
        if len(symbols) != 1 or None in symbols:
            raise MarketAwareSessionHistoryAuditError(
                "SYMBOL_MISMATCH", "history packages must share one symbol"
            )
        previous = None
        for index, entry in enumerate(actual_packages):
            current = _parse_as_of(entry["as_of"], label=f"packages[{index}]")
            if previous is not None and current <= previous:
                raise MarketAwareSessionHistoryAuditError(
                    "AS_OF_ORDER_INVALID", f"package[{index}] as_of is not strictly later"
                )
            previous = current
        shared = (
            "history_ready",
            "first_as_of",
            "last_as_of",
            "package_count",
            "symbol",
        )
        for field in shared:
            if history.get(field) != history_report.get(field):
                raise MarketAwareSessionHistoryAuditError(
                    "FIELD_MISMATCH", f"history/report.{field} differs"
                )
        if history.get("package_count") != len(actual_packages):
            raise MarketAwareSessionHistoryAuditError(
                "FIELD_MISMATCH", "history package_count differs from entries"
            )
        if history.get("first_as_of") != actual_packages[0]["as_of"] or history.get(
            "last_as_of"
        ) != actual_packages[-1]["as_of"]:
            raise MarketAwareSessionHistoryAuditError(
                "FIELD_MISMATCH", "history boundary times differ from entries"
            )
        report.update(
            {
                "audit_ready": True,
                "first_as_of": history["first_as_of"],
                "history_path": history_file["relative_path"],
                "history_report_path": history_report_file["relative_path"],
                "history_report_sha256": history_report_file["sha256"],
                "history_report_size_bytes": history_report_file["size_bytes"],
                "history_sha256": history_file["sha256"],
                "history_size_bytes": history_file["size_bytes"],
                "last_as_of": history["last_as_of"],
                "package_count": len(actual_packages),
                "packages": actual_packages,
                "symbol": history["symbol"],
            }
        )
    except MarketAwareSessionHistoryAuditError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(audit_report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
