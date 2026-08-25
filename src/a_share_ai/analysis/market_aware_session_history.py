"""Build a deterministic, auditable history manifest from session packages."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_package_audit import audit_market_aware_session_package

MARKET_AWARE_SESSION_HISTORY_VERSION = "market-aware-session-history-v1"
_SPEC_FIELDS = {"history_version", "packages"}
_PACKAGE_FIELDS = {"manifest_path", "report_path", "artifact_root"}


class MarketAwareSessionHistoryError(ValueError):
    """A fail-closed session history construction error."""

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
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketAwareSessionHistoryError("INPUT_JSON_INVALID", f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return payload, raw


def _relative_path(value: Any, *, root: Path, label: str) -> tuple[str, Path]:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise MarketAwareSessionHistoryError("PATH_INVALID", f"{label} must be relative")
    path = Path(value)
    if any(part == ".." for part in path.parts):
        raise MarketAwareSessionHistoryError("PATH_INVALID", f"{label} must stay relative")
    candidate = (root / value).resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryError(
            "PATH_OUTSIDE_HISTORY_ROOT", f"{label} is outside history root"
        ) from exc
    return relative, candidate


def _spec_path(value: Path, *, root: Path) -> tuple[str, Path]:
    candidate = value.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryError(
            "PATH_OUTSIDE_HISTORY_ROOT", "spec is outside history root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryError("INPUT_UNAVAILABLE", "spec is not a file")
    return relative, candidate


def _parse_as_of(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryError("AS_OF_INVALID", f"{label}.as_of is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryError("AS_OF_INVALID", f"{label}.as_of is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryError(
            "AS_OF_INVALID", f"{label}.as_of must include timezone"
        )
    return parsed


def _validate_spec(spec: Mapping[str, Any], *, root: Path) -> list[dict[str, Any]]:
    if set(spec) != _SPEC_FIELDS:
        raise MarketAwareSessionHistoryError(
            "SPEC_INVALID", "history spec contains unknown or missing fields"
        )
    if spec.get("history_version") != MARKET_AWARE_SESSION_HISTORY_VERSION:
        raise MarketAwareSessionHistoryError("VERSION_MISMATCH", "history spec version is invalid")
    packages = spec.get("packages")
    if not isinstance(packages, list) or len(packages) < 2:
        raise MarketAwareSessionHistoryError(
            "PACKAGE_COUNT_INVALID", "history requires at least two packages"
        )
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, item in enumerate(packages):
        if not isinstance(item, Mapping) or set(item) != _PACKAGE_FIELDS:
            raise MarketAwareSessionHistoryError(
                "SPEC_INVALID", f"packages[{index}] fields are invalid"
            )
        manifest_relative, manifest_path = _relative_path(
            item.get("manifest_path"), root=root, label=f"packages[{index}].manifest_path"
        )
        report_relative, report_path = _relative_path(
            item.get("report_path"), root=root, label=f"packages[{index}].report_path"
        )
        artifact_relative, artifact_root = _relative_path(
            item.get("artifact_root"), root=root, label=f"packages[{index}].artifact_root"
        )
        if not artifact_root.is_dir():
            raise MarketAwareSessionHistoryError(
                "ARTIFACT_ROOT_INVALID", f"packages[{index}].artifact_root is not a directory"
            )
        key = (manifest_relative, report_relative, artifact_relative)
        if key in seen:
            raise MarketAwareSessionHistoryError(
                "DUPLICATE_PACKAGE", f"packages[{index}] duplicates an earlier package"
            )
        seen.add(key)
        normalized.append(
            {
                "artifact_root": artifact_root,
                "artifact_root_path": artifact_relative,
                "manifest": manifest_path,
                "manifest_path": manifest_relative,
                "report": report_path,
                "report_path": report_relative,
            }
        )
    return normalized


def _audit_package(item: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix=f".session-history-audit-{index}-", dir=item["artifact_root"]
    ) as audit_dir:
        audit = audit_market_aware_session_package(
            package_path=item["manifest"],
            package_report_path=item["report"],
            artifact_root=item["artifact_root"],
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
        raise MarketAwareSessionHistoryError(
            "PACKAGE_AUDIT_FAILED", f"package[{index}]: {code}: {message}"
        )
    package, package_raw = _read_json(item["manifest"], label=f"package[{index}].manifest")
    if sha256_bytes(package_raw) != audit.get("package_sha256"):
        raise MarketAwareSessionHistoryError(
            "INPUT_CHANGED", f"package[{index}] changed after audit"
        )
    as_of = _parse_as_of(package.get("as_of"), label=f"package[{index}]")
    return {
        "artifact_root": item["artifact_root_path"],
        "as_of": package.get("as_of"),
        "audit_ready": audit.get("audit_ready"),
        "decision_ready": package.get("decision_ready"),
        "evaluation_at": package.get("evaluation_at"),
        "freshness_ready": package.get("session_ready") is True
        and package.get("freshness_status") == "fresh",
        "freshness_status": package.get("freshness_status"),
        "manifest_path": item["manifest_path"],
        "package_ready": audit.get("package_ready"),
        "package_sha256": audit.get("package_sha256"),
        "reference_at": package.get("reference_at"),
        "report_path": item["report_path"],
        "session_ready": audit.get("session_ready"),
        "status": package.get("status"),
        "symbol": package.get("symbol"),
        "_as_of_datetime": as_of,
    }


def _base_history() -> dict[str, Any]:
    return {
        "decision_ready": False,
        "first_as_of": None,
        "history_ready": False,
        "history_version": MARKET_AWARE_SESSION_HISTORY_VERSION,
        "issues": [],
        "last_as_of": None,
        "package_count": 0,
        "packages": [],
        "symbol": None,
    }


def _base_report() -> dict[str, Any]:
    return {
        "decision_ready": False,
        "first_as_of": None,
        "history_ready": False,
        "history_version": MARKET_AWARE_SESSION_HISTORY_VERSION,
        "issues": [],
        "last_as_of": None,
        "output_sha256": None,
        "package_count": 0,
        "symbol": None,
    }


def _finalize_report(report: dict[str, Any]) -> bytes:
    payload = dict(report)
    payload["output_sha256"] = None
    raw = _json_bytes(payload)
    report["output_sha256"] = sha256_bytes(raw)
    return _json_bytes(report)


def build_market_aware_session_history(
    *,
    spec_path: Path,
    history_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a deterministic history manifest from explicit package references."""

    history = _base_history()
    report = _base_report()
    history_path = output_dir / "market_aware_session_history.json"
    report_path = output_dir / "market_aware_session_history_report.json"
    can_write_report = False
    try:
        root = history_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryError(
                "HISTORY_ROOT_INVALID", "history root must be a directory"
            )
        output_resolved = output_dir.resolve()
        try:
            output_resolved.relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryError(
                "PATH_OUTSIDE_HISTORY_ROOT", "history output is outside history root"
            ) from exc
        can_write_report = True
        spec_relative, spec_file = _spec_path(spec_path, root=root)
        spec, _spec_raw = _read_json(spec_file, label="history spec")
        packages = _validate_spec(spec, root=root)
        entries = [_audit_package(item, index=index) for index, item in enumerate(packages)]
        symbols = {entry["symbol"] for entry in entries}
        if len(symbols) != 1 or None in symbols:
            raise MarketAwareSessionHistoryError(
                "SYMBOL_MISMATCH", "history packages must share one symbol"
            )
        previous = None
        for index, entry in enumerate(entries):
            current = entry["_as_of_datetime"]
            if previous is not None and current <= previous:
                raise MarketAwareSessionHistoryError(
                    "AS_OF_ORDER_INVALID", f"package[{index}] as_of is not strictly later"
                )
            previous = current
        for entry in entries:
            entry.pop("_as_of_datetime", None)
        history.update(
            {
                "first_as_of": entries[0]["as_of"],
                "history_ready": True,
                "last_as_of": entries[-1]["as_of"],
                "package_count": len(entries),
                "packages": entries,
                "spec_path": spec_relative,
                "symbol": entries[0]["symbol"],
            }
        )
        history_raw = _json_bytes(history)
        write_atomic(history_path, history_raw)
        report.update(
            {
                "first_as_of": history["first_as_of"],
                "history_ready": True,
                "last_as_of": history["last_as_of"],
                "output_sha256": sha256_bytes(history_raw),
                "package_count": len(entries),
                "spec_path": spec_relative,
                "symbol": history["symbol"],
            }
        )
    except MarketAwareSessionHistoryError as exc:
        history["issues"] = [{"code": exc.code, "message": str(exc)}]
        report["issues"] = list(history["issues"])
    except (OSError, UnicodeError) as exc:
        history["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
        report["issues"] = list(history["issues"])
    if can_write_report:
        write_atomic(report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
