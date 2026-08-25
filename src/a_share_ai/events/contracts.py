"""Versioned contracts for official announcement evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class AnnouncementRecord:
    """One immutable official announcement metadata record."""

    symbol: str
    sec_code: str
    sec_name: str
    org_id: str
    announcement_id: str
    title: str
    published_at: datetime
    announcement_type: str | None
    announcement_type_name: str | None
    source_url: str
    adjunct_type: str
    adjunct_size: int | None
    column_id: str | None
    page_column: str | None
    raw_item_sha256: str
    content_status: str
    content_path: str | None
    content_sha256: str | None
    content_error: str | None

    @property
    def published_date(self) -> date:
        return self.published_at.date()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "sec_code": self.sec_code,
            "sec_name": self.sec_name,
            "org_id": self.org_id,
            "announcement_id": self.announcement_id,
            "title": self.title,
            "published_at": self.published_at.isoformat(),
            "published_date": self.published_date.isoformat(),
            "announcement_type": self.announcement_type,
            "announcement_type_name": self.announcement_type_name,
            "source_url": self.source_url,
            "adjunct_type": self.adjunct_type,
            "adjunct_size": self.adjunct_size,
            "column_id": self.column_id,
            "page_column": self.page_column,
            "raw_item_sha256": self.raw_item_sha256,
            "content_status": self.content_status,
            "content_path": self.content_path,
            "content_sha256": self.content_sha256,
            "content_error": self.content_error,
        }


@dataclass(frozen=True, slots=True)
class AnnouncementSnapshot:
    """A cutoff-aware, auditable collection of official announcements."""

    schema_version: str
    source: str
    symbol: str
    as_of: str
    start_date: str
    end_date: str
    received_at: str
    records: tuple[AnnouncementRecord, ...]
    announcement_ready: bool
    decision_ready: bool

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "received_at": self.received_at,
            "records": [record.to_mapping() for record in self.records],
            "announcement_ready": self.announcement_ready,
            "decision_ready": self.decision_ready,
        }


def empty_snapshot_mapping(
    *, symbol: str, as_of: date, start_date: date, end_date: date, received_at: datetime
) -> dict[str, Any]:
    """Return a deterministic fail-closed snapshot shape for error states."""

    return AnnouncementSnapshot(
        schema_version=SCHEMA_VERSION,
        source="cninfo-announcements",
        symbol=symbol,
        as_of=as_of.isoformat(),
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        received_at=received_at.isoformat(),
        records=(),
        announcement_ready=False,
        decision_ready=False,
    ).to_mapping()
