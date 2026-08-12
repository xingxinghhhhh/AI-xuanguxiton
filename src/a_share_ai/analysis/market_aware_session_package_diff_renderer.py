"""Render a market-aware session package structural diff as safe Markdown."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_package_diff import MARKET_AWARE_SESSION_PACKAGE_DIFF_VERSION

MARKET_AWARE_SESSION_PACKAGE_DIFF_RENDER_VERSION = "market-aware-session-package-diff-render-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ROLES = (
    "session",
    "session_report",
    "freshness_report",
    "session_markdown",
    "session_render_report",
)
_REQUIRED_DIFF_FIELDS = (
    "artifact_changes",
    "changed_artifacts",
    "changed_fields",
    "comparison_ready",
    "decision_ready",
    "diff_version",
    "field_changes",
    "issues",
    "unchanged_artifacts",
    "unchanged_fields",
)


class MarketAwareSessionPackageDiffRenderError(ValueError):
    """A fail-closed diff renderer error."""

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
        raise MarketAwareSessionPackageDiffRenderError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionPackageDiffRenderError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload, raw


def _safe_file(path: Path, *, root: Path, label: str) -> tuple[Path, str, bytes, str]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionPackageDiffRenderError(
            "PATH_OUTSIDE_INPUT_ROOT", f"{label} is outside input root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionPackageDiffRenderError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionPackageDiffRenderError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return candidate, relative, raw, sha256_bytes(raw)


def _safe_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise MarketAwareSessionPackageDiffRenderError(
            "FIELD_INVALID", f"{label} must be a string"
        )
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
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


def _safe_value(value: Any, *, label: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return _safe_text(value, label=label)


def _parse_datetime(value: Any, *, label: str, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionPackageDiffRenderError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionPackageDiffRenderError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionPackageDiffRenderError(
            "TIME_INVALID", f"{label} needs timezone"
        )


def _validate_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise MarketAwareSessionPackageDiffRenderError(
            "HASH_INVALID", f"{label} must be a SHA-256 string"
        )
    return value


def _validate_issues(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise MarketAwareSessionPackageDiffRenderError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    issues: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionPackageDiffRenderError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        code = item.get("code")
        message = item.get("message")
        if not isinstance(code, str) or not isinstance(message, str):
            raise MarketAwareSessionPackageDiffRenderError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        issues.append({"code": code, "message": message})
    return issues


def _validate_artifact_changes(value: Any, *, label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != set(_ARTIFACT_ROLES):
        raise MarketAwareSessionPackageDiffRenderError(
            "ARTIFACT_INVALID", f"{label} must contain five roles"
        )
    for role in _ARTIFACT_ROLES:
        entry = value.get(role)
        if not isinstance(entry, Mapping) or entry.get("status") not in {
            "changed",
            "unchanged",
        }:
            raise MarketAwareSessionPackageDiffRenderError(
                "ARTIFACT_INVALID", f"{label}.{role} is invalid"
            )
        for side in ("previous", "current"):
            side_value = entry.get(side)
            if not isinstance(side_value, Mapping):
                raise MarketAwareSessionPackageDiffRenderError(
                    "ARTIFACT_INVALID", f"{label}.{role}.{side} is invalid"
                )
            path = side_value.get("path")
            if not isinstance(path, str) or Path(path).is_absolute() or any(
                part == ".." for part in Path(path).parts
            ):
                raise MarketAwareSessionPackageDiffRenderError(
                    "PATH_INVALID", f"{label}.{role}.{side}.path is invalid"
                )
            _validate_sha(side_value.get("sha256"), label=f"{label}.{role}.{side}.sha256")
            size = side_value.get("size_bytes")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise MarketAwareSessionPackageDiffRenderError(
                    "SIZE_INVALID", f"{label}.{role}.{side}.size_bytes is invalid"
                )


def _validate_inputs(
    *,
    diff_path: Path,
    diff_report_path: Path,
    input_root: Path,
) -> dict[str, Any]:
    root = input_root.resolve()
    if not root.is_dir():
        raise MarketAwareSessionPackageDiffRenderError(
            "INPUT_ROOT_INVALID", "input root must be a directory"
        )
    diff_file, diff_relative, diff_raw, diff_sha = _safe_file(
        diff_path, root=root, label="market_aware_session_package_diff"
    )
    report_file, report_relative, report_raw, report_sha = _safe_file(
        diff_report_path, root=root, label="market_aware_session_package_diff_report"
    )
    diff, _ = _read_json(diff_file, label="market_aware_session_package_diff")
    report, _ = _read_json(
        report_file, label="market_aware_session_package_diff_report"
    )
    for field in _REQUIRED_DIFF_FIELDS:
        if field not in diff:
            raise MarketAwareSessionPackageDiffRenderError(
                "FIELD_MISSING", f"diff.{field} is required"
            )
    if diff.get("diff_version") != MARKET_AWARE_SESSION_PACKAGE_DIFF_VERSION:
        raise MarketAwareSessionPackageDiffRenderError(
            "VERSION_MISMATCH", "diff version is invalid"
        )
    if report.get("diff_version") != MARKET_AWARE_SESSION_PACKAGE_DIFF_VERSION:
        raise MarketAwareSessionPackageDiffRenderError(
            "VERSION_MISMATCH", "diff report version is invalid"
        )
    if report.get("output_sha256") != diff_sha:
        raise MarketAwareSessionPackageDiffRenderError(
            "HASH_MISMATCH", "diff report output SHA does not match diff"
        )
    if diff.get("decision_ready") is not False or report.get("decision_ready") is not False:
        raise MarketAwareSessionPackageDiffRenderError(
            "DECISION_GATE_INVALID", "decision_ready must be false"
        )
    if report.get("comparison_ready") != diff.get("comparison_ready"):
        raise MarketAwareSessionPackageDiffRenderError(
            "FIELD_MISMATCH", "diff/report comparison_ready differs"
        )
    _validate_issues(diff.get("issues"), label="diff.issues")
    _validate_issues(report.get("issues"), label="diff_report.issues")
    if report.get("issues") != diff.get("issues"):
        raise MarketAwareSessionPackageDiffRenderError(
            "FIELD_MISMATCH", "diff/report issues differ"
        )
    if diff.get("comparison_ready") is True:
        symbol = diff.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise MarketAwareSessionPackageDiffRenderError(
                "FIELD_INVALID", "diff.symbol must be non-empty"
            )
        _parse_datetime(diff.get("previous_as_of"), label="previous_as_of")
        _parse_datetime(diff.get("current_as_of"), label="current_as_of")
        if report.get("symbol") != symbol:
            raise MarketAwareSessionPackageDiffRenderError(
                "FIELD_MISMATCH", "diff/report symbol differs"
            )
        for field in ("previous_as_of", "current_as_of"):
            if report.get(field) != diff.get(field):
                raise MarketAwareSessionPackageDiffRenderError(
                    "FIELD_MISMATCH", f"diff/report {field} differs"
                )
        for field in (
            "changed_fields",
            "unchanged_fields",
            "changed_artifacts",
            "unchanged_artifacts",
        ):
            if not isinstance(diff.get(field), list) or not all(
                isinstance(item, str) for item in diff[field]
            ):
                raise MarketAwareSessionPackageDiffRenderError(
                    "FIELD_INVALID", f"diff.{field} is invalid"
                )
        _validate_artifact_changes(diff.get("artifact_changes"), label="diff.artifact_changes")
        if diff.get("changed_field_count") is not None:
            raise MarketAwareSessionPackageDiffRenderError(
                "FIELD_INVALID", "diff must not contain changed_field_count"
            )
    else:
        if diff.get("changed_fields") != [] or diff.get("changed_artifacts") != []:
            raise MarketAwareSessionPackageDiffRenderError(
                "FIELD_INVALID", "blocked diff change lists must be empty"
            )
    return {
        "diff": diff,
        "diff_path": diff_relative,
        "diff_report": report,
        "diff_report_path": report_relative,
        "diff_report_sha256": report_sha,
        "diff_sha256": diff_sha,
        "diff_raw": diff_raw,
        "report_raw": report_raw,
    }


def _render_markdown(metadata: Mapping[str, Any]) -> bytes:
    diff = metadata["diff"]
    ready = diff.get("comparison_ready") is True
    status = "ready" if ready else "blocked"
    lines = [
        "# Market-aware session package diff",
        "",
        f"- diff_version: `{_safe_text(diff['diff_version'], label='diff_version')}`",
        f"- comparison_status: `{status}`",
        f"- symbol: `{_safe_value(diff.get('symbol'), label='symbol')}`",
        f"- previous_as_of: `{_safe_value(diff.get('previous_as_of'), label='previous_as_of')}`",
        f"- current_as_of: `{_safe_value(diff.get('current_as_of'), label='current_as_of')}`",
        f"- decision_ready: `{_safe_value(diff.get('decision_ready'), label='decision_ready')}`",
        "",
        "## Input integrity",
        "",
        f"- diff_path: `{_safe_text(metadata['diff_path'], label='diff_path')}`",
        f"- diff_sha256: `{metadata['diff_sha256']}`",
        "- diff_report_path: `"
        f"{_safe_text(metadata['diff_report_path'], label='diff_report_path')}`",
        f"- diff_report_sha256: `{metadata['diff_report_sha256']}`",
        "",
    ]
    if ready:
        lines.extend(
            [
                "## Field changes",
                "",
                "| Field | Previous | Current | Status |",
                "| --- | --- | --- | --- |",
            ]
        )
        for field, change in diff["field_changes"].items():
            lines.append(
                f"| `{_safe_text(field, label='field')}` | "
                f"`{_safe_value(change.get('previous'), label='previous')}` | "
                f"`{_safe_value(change.get('current'), label='current')}` | "
                f"`{_safe_value(change.get('status'), label='status')}` |"
            )
        lines.extend(
            [
                "",
                "## Artifact changes",
                "",
                "| Role | Previous path | Current path | Previous bytes | Current bytes | Status |",
                "| --- | --- | --- | ---: | ---: | --- |",
            ]
        )
        for role in _ARTIFACT_ROLES:
            change = diff["artifact_changes"][role]
            previous = change["previous"]
            current = change["current"]
            lines.append(
                f"| `{_safe_text(role, label='role')}` | "
                f"`{_safe_text(previous['path'], label='previous.path')}` | "
                f"`{_safe_text(current['path'], label='current.path')}` | "
                f"{previous['size_bytes']} | {current['size_bytes']} | "
                f"`{_safe_value(change['status'], label='artifact.status')}` |"
            )
        lines.extend(["", "## SHA-256 changes", ""])
        for role in _ARTIFACT_ROLES:
            change = diff["artifact_changes"][role]
            lines.append(
                f"- `{_safe_text(role, label='role')}`: "
                f"`{change['previous']['sha256']}` → `{change['current']['sha256']}` "
                f"({_safe_value(change['status'], label='artifact.status')})"
            )
    else:
        lines.extend(["## Comparison issues", ""])
        issues = diff.get("issues") or []
        if issues:
            for issue in issues:
                lines.append(
                    f"- `{_safe_text(issue['code'], label='issue.code')}`: "
                    f"{_safe_text(issue['message'], label='issue.message')}"
                )
        else:
            lines.append("- No comparison issue was recorded.")
    lines.extend(
        [
            "",
            "This is a literal, read-only structural diff. It does not judge market "
            "direction, predict returns, or create an investment or trading instruction.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _base_report() -> dict[str, Any]:
    return {
        "comparison_ready": False,
        "current_as_of": None,
        "decision_ready": False,
        "diff_path": None,
        "diff_report_path": None,
        "diff_report_sha256": None,
        "diff_sha256": None,
        "issues": [],
        "output_sha256": None,
        "previous_as_of": None,
        "render_ready": False,
        "render_version": MARKET_AWARE_SESSION_PACKAGE_DIFF_RENDER_VERSION,
        "symbol": None,
    }


def render_market_aware_session_package_diff(
    *,
    diff_path: Path,
    diff_report_path: Path,
    input_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Render a literal package diff without changing its JSON inputs."""

    report = _base_report()
    can_write_report = False
    render_report_path = output_dir / "market_aware_session_package_diff_render_report.json"
    try:
        root = input_root.resolve()
        output_resolved = output_dir.resolve()
        if not root.is_dir():
            raise MarketAwareSessionPackageDiffRenderError(
                "INPUT_ROOT_INVALID", "input root must be a directory"
            )
        try:
            output_resolved.relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionPackageDiffRenderError(
                "PATH_OUTSIDE_INPUT_ROOT", "render output is outside input root"
            ) from exc
        can_write_report = True
        metadata = _validate_inputs(
            diff_path=diff_path,
            diff_report_path=diff_report_path,
            input_root=root,
        )
        markdown = _render_markdown(metadata)
        write_atomic(output_dir / "market_aware_session_package_diff.md", markdown)
        diff = metadata["diff"]
        report.update(
            {
                "comparison_ready": diff.get("comparison_ready"),
                "current_as_of": diff.get("current_as_of"),
                "diff_path": metadata["diff_path"],
                "diff_report_path": metadata["diff_report_path"],
                "diff_report_sha256": metadata["diff_report_sha256"],
                "diff_sha256": metadata["diff_sha256"],
                "output_sha256": sha256_bytes(markdown),
                "previous_as_of": diff.get("previous_as_of"),
                "render_ready": True,
                "symbol": diff.get("symbol"),
            }
        )
        if diff.get("comparison_ready") is not True:
            report["render_ready"] = False
            report["issues"] = list(diff.get("issues") or [])
    except MarketAwareSessionPackageDiffRenderError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(render_report_path, _json_bytes(report))
    return report
