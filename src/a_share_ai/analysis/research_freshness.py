"""Audit whether a published research release is current for a fixed evaluation time."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..market.calendar import CalendarError, JsonTradingCalendarSource, TradingCalendar
from ..market.replay import sha256_bytes, write_atomic

RESEARCH_FRESHNESS_VERSION = "research-freshness-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
CLOSE_HOUR = 15
CLOSE_MINUTE = 0


class ResearchFreshnessError(ValueError):
    """A fail-closed freshness audit error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchFreshnessError("INPUT_JSON_INVALID", f"{label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ResearchFreshnessError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return payload, raw


def _require(payload: dict[str, Any], field: str, expected: Any, *, label: str) -> None:
    if payload.get(field) != expected:
        raise ResearchFreshnessError("FIELD_MISMATCH", f"{label}.{field} is inconsistent")


def _parse_datetime(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ResearchFreshnessError("TIME_INVALID", f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchFreshnessError("TIME_INVALID", f"{label} is not ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ResearchFreshnessError("TIME_INVALID", f"{label} must include timezone")
    return parsed


def _load_release(
    *, manifest_path: Path, report_path: Path
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    manifest, manifest_raw = _read_json(manifest_path, label="research_release_manifest")
    report, _ = _read_json(report_path, label="research_release_report")
    _require(manifest, "research_release_version", "research-release-v1", label="manifest")
    _require(report, "research_release_version", "research-release-v1", label="report")
    _require(manifest, "research_release_ready", True, label="manifest")
    _require(report, "research_release_ready", True, label="report")
    _require(manifest, "status", "ready", label="manifest")
    _require(report, "status", "ready", label="report")
    _require(manifest, "decision_ready", False, label="manifest")
    _require(report, "decision_ready", False, label="report")
    if report.get("output_sha256") != sha256_bytes(manifest_raw):
        raise ResearchFreshnessError("HASH_MISMATCH", "release report does not match manifest")
    symbol = manifest.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip() or report.get("symbol") != symbol:
        raise ResearchFreshnessError("CHAIN_MISMATCH", "release symbol is inconsistent")
    as_of = manifest.get("as_of")
    _parse_datetime(as_of, label="release_as_of")
    if report.get("as_of") != as_of:
        raise ResearchFreshnessError("CHAIN_MISMATCH", "release as_of is inconsistent")
    return manifest, report, manifest_raw


def _load_calendar(
    *, calendar_path: Path, calendar_report_path: Path
) -> tuple[TradingCalendar, dict[str, Any], bytes, str]:
    try:
        calendar_raw = calendar_path.read_bytes()
        calendar = JsonTradingCalendarSource(calendar_path).load_calendar()
    except (OSError, CalendarError) as exc:
        raise ResearchFreshnessError("CALENDAR_INVALID", str(exc)) from exc
    calendar_report, _ = _read_json(calendar_report_path, label="calendar_report")
    _require(calendar_report, "status", "complete", label="calendar_report")
    _require(calendar_report, "decision_ready", True, label="calendar_report")
    if calendar_report.get("calendar_sha256") != sha256_bytes(calendar_raw):
        raise ResearchFreshnessError("HASH_MISMATCH", "calendar report does not match calendar")
    if calendar_report.get("calendar_version") != calendar.calendar_version:
        raise ResearchFreshnessError("CHAIN_MISMATCH", "calendar version is inconsistent")
    return calendar, calendar_report, calendar_raw, sha256_bytes(calendar_raw)


def _latest_completed_trading_date(calendar: TradingCalendar, evaluation_at: datetime):
    local = evaluation_at.astimezone(SHANGHAI)
    evaluation_date = local.date()
    if evaluation_date < calendar.covered_start or evaluation_date > calendar.covered_end:
        return None
    local_time = (local.hour, local.minute, local.second, local.microsecond)
    if local_time >= (CLOSE_HOUR, CLOSE_MINUTE, 0, 0):
        candidates = [day for day in calendar.trading_dates if day <= evaluation_date]
    else:
        candidates = [day for day in calendar.trading_dates if day < evaluation_date]
    return max(candidates) if candidates else None


def audit_research_freshness(
    *,
    release_manifest_path: Path,
    release_report_path: Path,
    calendar_path: Path,
    calendar_report_path: Path,
    evaluation_at: datetime,
    output_dir: Path,
) -> dict[str, Any]:
    """Audit release freshness using only an explicit evaluation timestamp."""

    try:
        if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            raise ResearchFreshnessError("TIME_INVALID", "evaluation_at must include timezone")
        manifest, _release_report, manifest_raw = _load_release(
            manifest_path=release_manifest_path,
            report_path=release_report_path,
        )
        calendar, calendar_report, calendar_raw, calendar_sha = _load_calendar(
            calendar_path=calendar_path,
            calendar_report_path=calendar_report_path,
        )
        release_at = _parse_datetime(manifest["as_of"], label="release_as_of")
        if release_at > evaluation_at:
            raise ResearchFreshnessError(
                "RELEASE_AFTER_EVALUATION", "release_as_of is after evaluation_at"
            )
        expected_latest = _latest_completed_trading_date(calendar, evaluation_at)
        if expected_latest is None:
            report = {
                "calendar_sha256": calendar_sha,
                "calendar_version": calendar.calendar_version,
                "decision_ready": False,
                "evaluation_at": evaluation_at.isoformat(),
                "expected_latest_trading_date": None,
                "freshness_ready": False,
                "freshness_status": "calendar_unknown",
                "issues": [
                    {
                        "code": "CALENDAR_UNKNOWN",
                        "message": "calendar does not cover evaluation date",
                    }
                ],
                "release_as_of": manifest["as_of"],
                "release_manifest_sha256": sha256_bytes(manifest_raw),
                "symbol": manifest["symbol"],
                "trading_day_lag": None,
                "status": "calendar_unknown",
            }
        else:
            release_date = release_at.astimezone(SHANGHAI).date()
            if release_date not in calendar.trading_dates:
                raise ResearchFreshnessError(
                    "RELEASE_DATE_NON_TRADING", "release_as_of date is not a trading date"
                )
            later_dates = [
                day
                for day in calendar.trading_dates
                if release_date < day <= expected_latest
            ]
            lag = len(later_dates)
            fresh = release_date == expected_latest
            report = {
                "calendar_sha256": calendar_sha,
                "calendar_version": calendar.calendar_version,
                "decision_ready": False,
                "evaluation_at": evaluation_at.isoformat(),
                "expected_latest_trading_date": expected_latest.isoformat(),
                "freshness_ready": fresh,
                "freshness_status": "fresh" if fresh else "stale",
                "issues": (
                    []
                    if fresh
                    else [
                        {
                            "code": "RESEARCH_STALE",
                            "message": "release is before latest completed trading date",
                        }
                    ]
                ),
                "release_as_of": manifest["as_of"],
                "release_manifest_sha256": sha256_bytes(manifest_raw),
                "symbol": manifest["symbol"],
                "trading_day_lag": lag,
                "status": "fresh" if fresh else "stale",
            }
    except ResearchFreshnessError as exc:
        report = {
            "calendar_sha256": None,
            "calendar_version": None,
            "decision_ready": False,
            "evaluation_at": evaluation_at.isoformat(),
            "expected_latest_trading_date": None,
            "freshness_ready": False,
            "freshness_status": "invalid",
            "issues": [{"code": exc.code, "message": str(exc)}],
            "release_as_of": None,
            "release_manifest_sha256": None,
            "symbol": None,
            "trading_day_lag": None,
            "status": "invalid",
        }
    report["research_freshness_version"] = RESEARCH_FRESHNESS_VERSION
    write_atomic(output_dir / "research_freshness_report.json", _json_bytes(report))
    return report
