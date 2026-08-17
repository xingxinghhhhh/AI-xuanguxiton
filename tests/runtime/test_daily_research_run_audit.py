import json
from pathlib import Path

from a_share_ai.evidence.contracts import (
    BUNDLE_VERSION_V2,
    MARKET_CONTEXT_SUMMARY_VERSION,
    RELATIVE_STRENGTH_VERSION,
)
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.runtime.daily_research_run import DAILY_RESEARCH_RUN_VERSION
from a_share_ai.runtime.daily_research_run_audit import (
    DAILY_RESEARCH_RUN_AUDIT_VERSION,
    audit_daily_research_run,
)

STAGES = (
    (
        "daily_bars",
        (
            "market/request.json",
            "market/raw_response.json",
            "market/normalized_daily.jsonl",
            "market/capture_report.json",
        ),
    ),
    (
        "market_context",
        (
            "market-context/request.json",
            "market-context/raw_response.json",
            "market-context/market_context_snapshot.json",
            "market-context/market_context_report.json",
        ),
    ),
    ("data_quality", ("quality/health_report.json", "quality/coverage_report.json")),
    (
        "technical_features",
        ("technical/technical_features.jsonl", "technical/technical_report.json"),
    ),
    ("price_plan", ("price-plan/technical_price_plan.json", "price-plan/price_plan_report.json")),
    (
        "profitability",
        (
            "fundamentals/profitability/request.json",
            "fundamentals/profitability/raw_response.json",
            "fundamentals/profitability/profitability_snapshot.json",
            "fundamentals/profitability/profitability_report.json",
        ),
    ),
    (
        "growth",
        (
            "fundamentals/growth/request.json",
            "fundamentals/growth/raw_response.json",
            "fundamentals/growth/growth_snapshot.json",
            "fundamentals/growth/growth_report.json",
        ),
    ),
    (
        "announcements",
        (
            "announcements/request.json",
            "announcements/raw_response.json",
            "announcements/announcements_snapshot.json",
            "announcements/announcements_report.json",
        ),
    ),
    (
        "analysis_input_v2",
        ("analysis-input/analysis_input_bundle.json", "analysis-input/analysis_input_report.json"),
    ),
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _write_chain(root: Path, *, status: str = "ready", failed_index: int | None = None) -> Path:
    artifacts: dict[str, list[dict[str, str]]] = {}
    for index, (_, paths) in enumerate(STAGES):
        stage_artifacts: list[dict[str, str]] = []
        stage_status = (
            "ready"
            if failed_index is None or index < failed_index
            else ("failed" if index == failed_index else "skipped")
        )
        if stage_status != "skipped":
            for relative in paths:
                payload: object = {"decision_ready": False}
                if relative == "technical/technical_report.json" or relative in {
                    "quality/health_report.json",
                    "quality/coverage_report.json",
                }:
                    payload = {"decision_ready": True}
                if relative == "analysis-input/analysis_input_bundle.json":
                    payload = {
                        "bundle_version": BUNDLE_VERSION_V2,
                        "decision_ready": False,
                        "summaries": {
                            "market": {
                                "market_context": {
                                    "market_context_summary_version": MARKET_CONTEXT_SUMMARY_VERSION
                                }
                            },
                            "technical": {
                                "relative_strength": {"version": RELATIVE_STRENGTH_VERSION}
                            },
                        },
                    }
                elif relative == "analysis-input/analysis_input_report.json":
                    payload = {"analysis_input_ready": True, "decision_ready": False}
                if relative.endswith(".jsonl"):
                    data = b"fixture\n"
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                else:
                    path = root / relative
                    _write_json(path, payload)
                stage_artifacts.append(
                    {"path": relative, "sha256": sha256_bytes(path.read_bytes())}
                )
        artifacts[STAGES[index][0]] = stage_artifacts

    report = {
        "run_version": DAILY_RESEARCH_RUN_VERSION,
        "source_mode": "public-read-only",
        "symbol": "600000.SH",
        "start_date": "2026-08-01",
        "end_date": "2026-08-07",
        "as_of": "2026-08-10T12:00:00+00:00",
        "received_at": "2026-08-10T12:00:00+00:00",
        "status": status,
        "stages": [
            {
                "name": name,
                "status": "ready"
                if failed_index is None or index < failed_index
                else ("failed" if index == failed_index else "skipped"),
                "skipped": failed_index is not None and index > failed_index,
                "error_code": None
                if failed_index is None or index < failed_index
                else ("PROVIDER_ERROR" if index == failed_index else "UPSTREAM_FAILED"),
                "artifacts": artifacts[name],
                "issues": []
                if failed_index is None or index < failed_index
                else [{"code": "PROVIDER_ERROR", "message": "fixture failure"}],
            }
            for index, (name, _) in enumerate(STAGES)
        ],
        "analysis_input_path": None
        if failed_index is not None
        else "analysis-input/analysis_input_bundle.json",
        "analysis_input_sha256": None
        if failed_index is not None
        else sha256_bytes((root / "analysis-input/analysis_input_bundle.json").read_bytes()),
        "analysis_input_ready": failed_index is None,
        "market_context_summary_version": None
        if failed_index is not None
        else MARKET_CONTEXT_SUMMARY_VERSION,
        "relative_strength_version": None
        if failed_index is not None
        else RELATIVE_STRENGTH_VERSION,
        "issues": []
        if failed_index is None
        else [{"code": "PROVIDER_ERROR", "message": "fixture failure"}],
        "decision_ready": False,
    }
    report_path = root / "daily_research_run_report.json"
    _write_json(report_path, report)
    return report_path


def test_ready_run_audit_is_independent_and_self_hashed(tmp_path: Path) -> None:
    root = tmp_path / "run"
    report_path = _write_chain(root)

    report = audit_daily_research_run(
        run_report_path=report_path,
        artifact_root=root,
        output_dir=root / "audit",
    )

    assert report["audit_version"] == DAILY_RESEARCH_RUN_AUDIT_VERSION
    assert report["status"] == "ready"
    assert report["audit_ready"] is True
    assert report["stage_count"] == 9
    assert report["failed_stage"] is None
    assert report["decision_ready"] is False
    assert report["output_sha256"] == sha256_bytes(
        (
            json.dumps(
                {**report, "output_sha256": None}, ensure_ascii=False, sort_keys=True, indent=2
            )
            + "\n"
        ).encode()
    )
    assert (root / "audit/daily_research_run_audit_report.json").is_file()


def test_blocked_run_requires_one_failure_then_skipped(tmp_path: Path) -> None:
    root = tmp_path / "run"
    report_path = _write_chain(root, status="blocked", failed_index=5)

    report = audit_daily_research_run(
        run_report_path=report_path,
        artifact_root=root,
        output_dir=root / "audit",
    )

    assert report["audit_ready"] is True
    assert report["status"] == "ready"
    assert report["run_status"] == "blocked"
    assert report["failed_stage"] == "profitability"


def test_audit_accepts_node56_input_root_prefixed_paths(tmp_path: Path) -> None:
    root = tmp_path / "daily-run-001"
    report_path = _write_chain(root)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    for stage in payload["stages"]:
        for artifact in stage["artifacts"]:
            artifact["path"] = f"{root.name}/{artifact['path']}"
    payload["analysis_input_path"] = f"{root.name}/{payload['analysis_input_path']}"
    _write_json(report_path, payload)

    report = audit_daily_research_run(
        run_report_path=report_path,
        artifact_root=root,
        output_dir=root / "audit",
    )

    assert report["audit_ready"] is True


def test_audit_rejects_tampered_artifact_hash(tmp_path: Path) -> None:
    root = tmp_path / "run"
    report_path = _write_chain(root)
    (root / "market/capture_report.json").write_text('{"decision_ready": true}\n', encoding="utf-8")

    report = audit_daily_research_run(
        run_report_path=report_path,
        artifact_root=root,
        output_dir=root / "audit",
    )

    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] == "HASH_MISMATCH"


def test_audit_rejects_decision_ready_true(tmp_path: Path) -> None:
    root = tmp_path / "run"
    report_path = _write_chain(root)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["decision_ready"] = True
    _write_json(report_path, payload)

    report = audit_daily_research_run(
        run_report_path=report_path,
        artifact_root=root,
        output_dir=root / "audit",
    )

    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] == "DECISION_GATE_INVALID"


def test_audit_rejects_stage_order_and_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "run"
    report_path = _write_chain(root)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["stages"][0]["name"] = "market_context"
    _write_json(report_path, payload)

    report = audit_daily_research_run(
        run_report_path=report_path,
        artifact_root=root,
        output_dir=root / "audit",
    )
    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] == "STAGE_ORDER_INVALID"

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["stages"][0]["name"] = "daily_bars"
    payload["stages"][0]["artifacts"][0]["path"] = "../outside.json"
    _write_json(report_path, payload)
    report = audit_daily_research_run(
        run_report_path=report_path,
        artifact_root=root,
        output_dir=root / "audit-2",
    )
    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] == "PATH_INVALID"
