import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from a_share_ai.events.cninfo_announcements import (
    CninfoAnnouncementConfig,
    CninfoAnnouncementSource,
    CninfoContentError,
    CninfoProviderError,
)

FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "events"


class FakeCninfoClient:
    def __init__(self, fixture_name: str) -> None:
        self.payload = json.loads((FIXTURE_ROOT / fixture_name).read_text(encoding="utf-8"))
        self.page_index = 0
        self.queries: list[dict[str, object]] = []

    def lookup_stock(self, code: str) -> dict[str, object]:
        if "lookup_error" in self.payload:
            raise CninfoProviderError(str(self.payload["lookup_error"]))
        return self.payload["lookup"][0]

    def query_announcements(self, params: dict[str, object]) -> dict[str, object]:
        self.queries.append(params)
        page = self.payload["pages"][self.page_index]
        self.page_index += 1
        return page

    def fetch_content(self, url: str) -> bytes:
        announcement_id = url.rsplit("/", 1)[-1].split(".", 1)[0]
        content = self.payload.get("content", {}).get(announcement_id)
        if isinstance(content, dict) and "error" in content:
            raise CninfoContentError(str(content["error"]))
        if content is None:
            raise CninfoContentError("fixture content missing")
        return content.encode("utf-8")


def make_config(*, as_of: date = date(2025, 12, 30)) -> CninfoAnnouncementConfig:
    return CninfoAnnouncementConfig(
        symbol="600000.SH",
        start_date=date(2025, 12, 29),
        end_date=date(2026, 1, 2),
        as_of=as_of,
        received_at=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )


def test_ready_filters_future_and_saves_official_content(tmp_path: Path) -> None:
    client = FakeCninfoClient("valid.json")
    report = CninfoAnnouncementSource(make_config(), client=client).capture(tmp_path)

    assert report["status"] == "ready"
    assert report["announcement_ready"] is True
    assert report["decision_ready"] is False
    assert report["record_count"] == 2
    assert report["selected_announcement_ids"] == ["1224905574", "1224900843"]
    snapshot = json.loads((tmp_path / "announcements_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["records"][0]["published_date"] == "2025-12-30"
    assert snapshot["records"][0]["content_status"] == "available"
    assert (tmp_path / "content" / "1224905574.PDF").read_bytes() == b"pdf-one"
    assert client.queries[0]["stock"] == "600000,gssh0600000"
    assert client.queries[0]["seDate"] == "2025-12-29~2026-01-02"


def test_empty_and_future_only_are_not_ready(tmp_path: Path) -> None:
    empty_report = CninfoAnnouncementSource(
        make_config(), client=FakeCninfoClient("empty.json")
    ).capture(tmp_path / "empty")
    future_report = CninfoAnnouncementSource(
        make_config(), client=FakeCninfoClient("future_only.json")
    ).capture(tmp_path / "future")

    assert empty_report["status"] == "empty"
    assert empty_report["announcement_ready"] is False
    assert future_report["status"] == "future_only"
    assert future_report["record_count"] == 0
    assert future_report["decision_ready"] is False


@pytest.mark.parametrize("fixture_name", ["invalid.json", "duplicate.json", "order_invalid.json"])
def test_invalid_provider_shapes_fail_closed(tmp_path: Path, fixture_name: str) -> None:
    report = CninfoAnnouncementSource(
        make_config(), client=FakeCninfoClient(fixture_name)
    ).capture(tmp_path / fixture_name.removesuffix(".json"))

    assert report["status"] == "invalid"
    assert report["announcement_ready"] is False
    assert report["decision_ready"] is False
    assert report["record_count"] == 0


def test_content_failure_keeps_metadata_but_is_not_ready(tmp_path: Path) -> None:
    report = CninfoAnnouncementSource(
        make_config(), client=FakeCninfoClient("content_unavailable.json")
    ).capture(tmp_path)

    assert report["status"] == "content_unavailable"
    assert report["announcement_ready"] is False
    snapshot = json.loads((tmp_path / "announcements_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["records"][0]["title"] == "关于公司章程修订获核准的公告"
    assert snapshot["records"][0]["content_status"] == "unavailable"


def test_provider_failure_and_repeated_capture_hashes_are_auditable(tmp_path: Path) -> None:
    provider_report = CninfoAnnouncementSource(
        make_config(), client=FakeCninfoClient("provider_error.json")
    ).capture(tmp_path / "provider")
    first = CninfoAnnouncementSource(
        make_config(), client=FakeCninfoClient("valid.json")
    ).capture(tmp_path / "first")
    second = CninfoAnnouncementSource(
        make_config(), client=FakeCninfoClient("valid.json")
    ).capture(tmp_path / "second")

    assert provider_report["status"] == "provider_error"
    assert provider_report["decision_ready"] is False
    assert first["request_sha256"] == second["request_sha256"]
    assert first["raw_response_sha256"] == second["raw_response_sha256"]
    assert first["snapshot_sha256"] == second["snapshot_sha256"]
