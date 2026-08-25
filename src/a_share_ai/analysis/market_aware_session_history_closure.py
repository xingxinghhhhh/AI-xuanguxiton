"""Close the market-aware session history evidence chain offline."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_history_manifest import (
    MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION,
)
from .market_aware_session_history_manifest_audit import (
    MARKET_AWARE_SESSION_HISTORY_MANIFEST_AUDIT_VERSION,
)
from .market_aware_session_history_manifest_render_audit import (
    MARKET_AWARE_SESSION_HISTORY_MANIFEST_RENDER_AUDIT_VERSION,
)
from .market_aware_session_history_manifest_renderer import (
    MARKET_AWARE_SESSION_HISTORY_MANIFEST_RENDER_VERSION,
)

MARKET_AWARE_SESSION_HISTORY_CLOSURE_VERSION = "market-aware-session-history-closure-v1"
_ROLES = (
    "manifest",
    "manifest_report",
    "manifest_audit_report",
    "render_report",
    "render_audit_report",
)


class MarketAwareSessionHistoryClosureError(ValueError):
    """A fail-closed closure error."""

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
        raise MarketAwareSessionHistoryClosureError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryClosureError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryClosureError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryClosureError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {
        "path": candidate,
        "relative_path": relative,
        "raw": raw,
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
    }


def _validate_self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = payload.get("output_sha256")
    if not isinstance(declared, str) or len(declared) != 64:
        raise MarketAwareSessionHistoryClosureError(
            "HASH_INVALID", f"{label}.output_sha256 is invalid"
        )
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryClosureError(
            "HASH_MISMATCH", f"{label}.output_sha256 is invalid"
        )


def _parse_datetime(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryClosureError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryClosureError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryClosureError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryClosureError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(item.get("message"), str):
            raise MarketAwareSessionHistoryClosureError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _validate_chain(
    *,
    files: Mapping[str, Mapping[str, Any]],
    manifest: Mapping[str, Any],
    manifest_report: Mapping[str, Any],
    manifest_audit_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
    render_audit_report: Mapping[str, Any],
) -> dict[str, Any]:
    versions = (
        (manifest, "manifest_version", MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION),
        (manifest_report, "manifest_version", MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION),
        (
            manifest_audit_report,
            "audit_version",
            MARKET_AWARE_SESSION_HISTORY_MANIFEST_AUDIT_VERSION,
        ),
        (render_report, "render_version", MARKET_AWARE_SESSION_HISTORY_MANIFEST_RENDER_VERSION),
        (
            render_audit_report,
            "audit_version",
            MARKET_AWARE_SESSION_HISTORY_MANIFEST_RENDER_AUDIT_VERSION,
        ),
    )
    for payload, field, expected in versions:
        if payload.get(field) != expected:
            raise MarketAwareSessionHistoryClosureError(
                "VERSION_MISMATCH", f"{field} has an invalid value"
            )
    for label, payload in (
        ("manifest", manifest),
        ("manifest_report", manifest_report),
        ("manifest_audit_report", manifest_audit_report),
        ("render_report", render_report),
        ("render_audit_report", render_audit_report),
    ):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryClosureError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
    _validate_self_hash(manifest_report, label="manifest_report")
    _validate_self_hash(manifest_audit_report, label="manifest_audit_report")
    _validate_self_hash(render_audit_report, label="render_audit_report")
    if manifest.get("manifest_ready") is not True or manifest_report.get(
        "manifest_ready"
    ) is not True:
        raise MarketAwareSessionHistoryClosureError(
            "UPSTREAM_NOT_READY", "manifest is not ready"
        )
    if manifest_audit_report.get("audit_ready") is not True:
        raise MarketAwareSessionHistoryClosureError(
            "UPSTREAM_NOT_READY", "manifest audit is not ready"
        )
    if render_audit_report.get("audit_ready") is not True:
        raise MarketAwareSessionHistoryClosureError(
            "UPSTREAM_NOT_READY", "manifest render audit is not ready"
        )
    bindings = (
        (manifest_report, "manifest", "manifest_sha256"),
        (manifest_audit_report, "manifest", "manifest_sha256"),
        (manifest_audit_report, "manifest_report", "manifest_report_sha256"),
        (render_report, "manifest", "manifest_sha256"),
        (render_report, "manifest_report", "manifest_report_sha256"),
        (render_report, "manifest_audit_report", "audit_report_sha256"),
        (render_audit_report, "manifest", "manifest_sha256"),
        (render_audit_report, "manifest_report", "manifest_report_sha256"),
        (render_audit_report, "manifest_audit_report", "manifest_audit_report_sha256"),
        (render_audit_report, "render_report", "render_report_sha256"),
    )
    for source, role, field in bindings:
        if source.get(field) != files[role]["sha256"]:
            raise MarketAwareSessionHistoryClosureError(
                "HASH_MISMATCH", f"{field} does not match {role}"
            )
    if render_audit_report.get("markdown_sha256") != render_report.get("output_sha256"):
        raise MarketAwareSessionHistoryClosureError(
            "HASH_MISMATCH", "render report Markdown SHA binding is invalid"
        )
    path_bindings = (
        (manifest_report, "manifest", "manifest_path"),
        (manifest_audit_report, "manifest", "manifest_path"),
        (manifest_audit_report, "manifest_report", "manifest_report_path"),
        (render_report, "manifest", "manifest_path"),
        (render_report, "manifest_report", "manifest_report_path"),
        (render_report, "manifest_audit_report", "audit_report_path"),
        (render_audit_report, "manifest", "manifest_path"),
        (render_audit_report, "manifest_report", "manifest_report_path"),
        (render_audit_report, "manifest_audit_report", "manifest_audit_report_path"),
        (render_audit_report, "render_report", "render_report_path"),
    )
    for source, role, field in path_bindings:
        if source.get(field) != files[role]["relative_path"]:
            raise MarketAwareSessionHistoryClosureError(
                "CHAIN_MISMATCH", f"{field} is inconsistent"
            )
    for field in ("symbol", "package_count", "first_as_of", "last_as_of"):
        for payload, label in (
            (manifest_audit_report, "manifest_audit_report"),
            (render_report, "render_report"),
            (render_audit_report, "render_audit_report"),
        ):
            if payload.get(field) != manifest.get(field):
                raise MarketAwareSessionHistoryClosureError(
                    "FIELD_MISMATCH", f"{label}.{field} differs"
                )
    if render_report.get("render_ready") is not render_audit_report.get("render_ready"):
        raise MarketAwareSessionHistoryClosureError(
            "FIELD_MISMATCH", "render readiness differs"
        )
    for field in ("history_ready", "history_audit_ready", "render_ready"):
        if field in render_audit_report and render_audit_report.get(field) != manifest.get(field):
            raise MarketAwareSessionHistoryClosureError(
                "FIELD_MISMATCH", f"{field} differs"
            )
    if render_report.get("audit_ready") is not (render_audit_report.get("audit_ready") is True):
        raise MarketAwareSessionHistoryClosureError(
            "FIELD_MISMATCH", "render audit readiness differs"
        )
    _parse_datetime(manifest.get("first_as_of"), label="manifest.first_as_of")
    _parse_datetime(manifest.get("last_as_of"), label="manifest.last_as_of")
    return {
        "audit_ready": render_audit_report.get("audit_ready") is True,
        "first_as_of": manifest.get("first_as_of"),
        "history_audit_ready": manifest.get("history_audit_ready"),
        "history_ready": manifest.get("history_ready"),
        "last_as_of": manifest.get("last_as_of"),
        "package_count": manifest.get("package_count"),
        "render_ready": render_report.get("render_ready"),
        "symbol": manifest.get("symbol"),
    }


def _base_report() -> dict[str, Any]:
    return {
        "artifact_root": ".",
        "closure_ready": False,
        "closure_version": MARKET_AWARE_SESSION_HISTORY_CLOSURE_VERSION,
        "decision_ready": False,
        "issues": [],
        "output_sha256": None,
        "status": "invalid",
    }


def _finalize_report(report: dict[str, Any]) -> bytes:
    payload = dict(report)
    payload["output_sha256"] = None
    raw = _json_bytes(payload)
    report["output_sha256"] = sha256_bytes(raw)
    return _json_bytes(report)


def _finalize_closure(closure: dict[str, Any]) -> bytes:
    payload = dict(closure)
    payload["output_sha256"] = None
    raw = _json_bytes(payload)
    closure["output_sha256"] = sha256_bytes(raw)
    return _json_bytes(closure)


def build_market_aware_session_history_closure(
    *,
    manifest_path: Path,
    manifest_report_path: Path,
    manifest_audit_report_path: Path,
    render_report_path: Path,
    render_audit_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a deterministic closure report for the existing evidence chain."""

    report = _base_report()
    can_write_report = False
    closure_path = output_dir / "market_aware_session_history_closure.json"
    report_path = output_dir / "market_aware_session_history_closure_report.json"
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryClosureError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryClosureError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "closure output is outside artifact root"
            ) from exc
        can_write_report = True
        files = {
            "manifest": _safe_file(manifest_path, root=root, label="manifest"),
            "manifest_report": _safe_file(
                manifest_report_path, root=root, label="manifest report"
            ),
            "manifest_audit_report": _safe_file(
                manifest_audit_report_path, root=root, label="manifest audit report"
            ),
            "render_report": _safe_file(
                render_report_path, root=root, label="render report"
            ),
            "render_audit_report": _safe_file(
                render_audit_report_path, root=root, label="render audit report"
            ),
        }
        payloads = {
            role: _read_json(files[role]["raw"], label=role) for role in _ROLES
        }
        summary = _validate_chain(files=files, **payloads)
        closure = {
            "audit_ready": summary["audit_ready"],
            "closure_version": MARKET_AWARE_SESSION_HISTORY_CLOSURE_VERSION,
            "decision_ready": False,
            "first_as_of": summary["first_as_of"],
            "history_audit_ready": summary["history_audit_ready"],
            "history_ready": summary["history_ready"],
            "issues": [],
            "last_as_of": summary["last_as_of"],
            "manifest_audit_sha256": files["manifest_audit_report"]["sha256"],
            "manifest_sha256": files["manifest"]["sha256"],
            "package_count": summary["package_count"],
            "render_audit_sha256": files["render_audit_report"]["sha256"],
            "render_ready": summary["render_ready"],
            "render_report_sha256": files["render_report"]["sha256"],
            "status": "ready",
            "symbol": summary["symbol"],
            "output_sha256": None,
        }
        write_atomic(closure_path, _finalize_closure(closure))
        inputs = {
            role: {
                "byte_count": files[role]["size_bytes"],
                "path": files[role]["relative_path"],
                "sha256": files[role]["sha256"],
            }
            for role in _ROLES
        }
        report.update(
            {
                "artifact_root": root.relative_to(root).as_posix(),
                "closure_ready": True,
                "closure_sha256": sha256_bytes(closure_path.read_bytes()),
                "inputs": inputs,
                "issues": [],
                "status": "ready",
            }
        )
    except MarketAwareSessionHistoryClosureError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
