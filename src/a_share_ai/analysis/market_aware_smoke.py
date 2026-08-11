"""One-shot DeepSeek smoke test around the market-aware offline replay chain."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_replay import replay_market_aware_analysis

MARKET_AWARE_SMOKE_VERSION = "market-aware-smoke-v1"


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def smoke_market_aware_analysis(
    *,
    bundle_path: Path,
    bundle_report_path: Path,
    input_root: Path,
    env_file: Path,
    output_dir: Path,
    model: str = "deepseek-v4-flash",
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Perform exactly one DeepSeek request, then run only offline audits."""

    if timeout_seconds <= 0:
        report = {
            "as_of": None,
            "analysis_ready": False,
            "decision_ready": False,
            "issues": [{"code": "INPUT_INVALID", "message": "timeout must be positive"}],
            "market_aware_smoke_version": MARKET_AWARE_SMOKE_VERSION,
            "model": model,
            "provider": "deepseek",
            "provider_status": "not_started",
            "relative_strength_version": None,
            "review_complete": False,
            "review_packet_ready": False,
            "smoke_version": MARKET_AWARE_SMOKE_VERSION,
            "status": "invalid",
        }
        write_atomic(output_dir / "market_aware_smoke_report.json", _json_bytes(report))
        return report

    replay_report = replay_market_aware_analysis(
        bundle_path=bundle_path,
        bundle_report_path=bundle_report_path,
        input_root=input_root,
        response_fixture=None,
        output_dir=output_dir,
        provider="deepseek",
        model=model,
        env_file=env_file,
    )
    analysis_report = _read_json(output_dir / "analysis" / "research_analysis_report.json")
    report = {
        "analysis_ready": replay_report.get("analysis_ready") is True,
        "analysis_report_sha256": (
            sha256_bytes((output_dir / "analysis" / "research_analysis_report.json").read_bytes())
            if (output_dir / "analysis" / "research_analysis_report.json").exists()
            else None
        ),
        "as_of": replay_report.get("as_of"),
        "decision_ready": False,
        "issues": replay_report.get("issues", []),
        "market_context_summary_version": replay_report.get(
            "market_context_summary_version"
        ),
        "model": model,
        "provider": "deepseek",
        "provider_error": analysis_report.get("provider_error"),
        "provider_status": analysis_report.get("provider_status", "not_started"),
        "relative_strength_version": replay_report.get("relative_strength_version"),
        "request_sha256": analysis_report.get("request_sha256"),
        "response_sha256": analysis_report.get("response_sha256"),
        "review_complete": False,
        "review_packet_ready": replay_report.get("review_packet_ready") is True,
        "replay_report_sha256": (
            sha256_bytes((output_dir / "market_aware_replay_report.json").read_bytes())
            if (output_dir / "market_aware_replay_report.json").exists()
            else None
        ),
        "replay_report_path": "market_aware_replay_report.json",
        "smoke_version": MARKET_AWARE_SMOKE_VERSION,
        "stages": replay_report.get("stages", {}),
        "status": replay_report.get("status", "invalid"),
        "symbol": replay_report.get("symbol"),
    }
    write_atomic(output_dir / "market_aware_smoke_report.json", _json_bytes(report))
    return report

