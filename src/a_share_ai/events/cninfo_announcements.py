"""Read-only CNINFO announcement capture with cutoff and evidence auditing."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from ..market.replay import sha256_bytes, write_atomic
from .contracts import (
    SCHEMA_VERSION,
    AnnouncementRecord,
    AnnouncementSnapshot,
    empty_snapshot_mapping,
)

CNINFO_BASE_URL = "https://www.cninfo.com.cn"
CNINFO_STATIC_URL = "https://static.cninfo.com.cn"
SHANGHAI = ZoneInfo("Asia/Shanghai")
SYMBOL_PATTERN = re.compile(r"^(?P<code>\d{6})\.(?P<market>SH|SZ)$", re.IGNORECASE)
ANNOUNCEMENT_ID_PATTERN = re.compile(r"^\d{6,20}$")


class CninfoError(RuntimeError):
    """Raised when CNINFO cannot return a trustworthy response."""


class CninfoProviderError(CninfoError):
    """Raised when the public provider or its response is unavailable."""


class CninfoDataError(CninfoError):
    """Raised when the provider payload violates the announcement contract."""


class CninfoContentError(CninfoError):
    """Raised when an official announcement attachment cannot be fetched."""


class AnnouncementState(StrEnum):
    READY = "ready"
    EMPTY = "empty"
    FUTURE_ONLY = "future_only"
    PROVIDER_ERROR = "provider_error"
    SOURCE_UNAVAILABLE = "source_unavailable"
    INVALID = "invalid"
    CONTENT_UNAVAILABLE = "content_unavailable"


@dataclass(frozen=True, slots=True)
class CninfoAnnouncementConfig:
    symbol: str
    start_date: date
    end_date: date
    as_of: date
    received_at: datetime
    page_size: int = 30

    def __post_init__(self) -> None:
        normalized_symbol = self.symbol.strip().upper()
        provider_symbol_for(normalized_symbol)
        if self.start_date > self.end_date:
            raise CninfoError("start_date must be on or before end_date")
        if self.page_size < 1 or self.page_size > 100:
            raise CninfoError("page_size must be between 1 and 100")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise CninfoError("received_at must include a timezone")
        object.__setattr__(self, "symbol", normalized_symbol)

    @property
    def code(self) -> str:
        return self.symbol.split(".", 1)[0]

    def request_mapping(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "code": self.code,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "as_of": self.as_of.isoformat(),
            "received_at": self.received_at.isoformat(),
            "page_size": self.page_size,
            "lookup_endpoint": f"{CNINFO_BASE_URL}/new/information/topSearch/query",
            "announcement_endpoint": f"{CNINFO_BASE_URL}/new/hisAnnouncement/query",
            "content_base_url": CNINFO_STATIC_URL,
        }


@dataclass(frozen=True, slots=True)
class AnnouncementIssue:
    code: str
    message: str

    def to_mapping(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AnnouncementReport:
    schema_version: str
    source: str
    symbol: str
    as_of: str
    request_sha256: str
    raw_response_sha256: str | None
    snapshot_sha256: str
    record_count: int
    selected_announcement_ids: tuple[str, ...]
    status: str
    announcement_ready: bool
    decision_ready: bool
    issues: tuple[AnnouncementIssue, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "request_sha256": self.request_sha256,
            "raw_response_sha256": self.raw_response_sha256,
            "snapshot_sha256": self.snapshot_sha256,
            "record_count": self.record_count,
            "selected_announcement_ids": list(self.selected_announcement_ids),
            "status": self.status,
            "announcement_ready": self.announcement_ready,
            "decision_ready": self.decision_ready,
            "issues": [issue.to_mapping() for issue in self.issues],
        }


class CninfoClient(Protocol):
    def lookup_stock(self, code: str) -> Mapping[str, Any]:
        """Resolve a six-digit code to the public CNINFO organization id."""

    def query_announcements(self, params: Mapping[str, object]) -> Mapping[str, Any]:
        """Return one public announcement page."""

    def fetch_content(self, url: str) -> bytes:
        """Fetch one official attachment without changing remote state."""


class CninfoHttpClient:
    """Small standard-library client for the public CNINFO endpoints."""

    user_agent = "a-share-ai-system/0.1 read-only announcement audit"

    def _post_json(self, path: str, values: Mapping[str, object]) -> Any:
        body = urlencode({key: str(value) for key, value in values.items()}).encode("utf-8")
        request = Request(
            f"{CNINFO_BASE_URL}{path}",
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": (
                    f"{CNINFO_BASE_URL}/new/commonUrl/pageOfSearch?url=disclosure%2Flist%2Fsearch"
                ),
                "User-Agent": self.user_agent,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:  # nosec B310 - fixed official HTTPS host
                payload = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise CninfoProviderError(f"CNINFO POST failed: {exc}") from exc
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CninfoProviderError("CNINFO returned malformed JSON") from exc
        if not isinstance(parsed, (dict, list)):
            raise CninfoProviderError("CNINFO JSON response must be an object or list")
        return parsed

    def lookup_stock(self, code: str) -> Mapping[str, Any]:
        response = self._post_json(
            "/new/information/topSearch/query",
            {"keyWord": code, "maxNum": 10, "plate": ""},
        )
        if not isinstance(response, list):
            raise CninfoProviderError("CNINFO stock lookup response must be a list")
        matches = [item for item in response if isinstance(item, dict) and item.get("code") == code]
        if not matches:
            raise CninfoProviderError(f"CNINFO could not resolve stock code {code}")
        return matches[0]

    def query_announcements(self, params: Mapping[str, object]) -> Mapping[str, Any]:
        response = self._post_json("/new/hisAnnouncement/query", params)
        if not isinstance(response, dict):
            raise CninfoProviderError("CNINFO announcement response must be an object")
        return response

    def fetch_content(self, url: str) -> bytes:
        request = Request(
            url,
            headers={
                "Accept": "application/pdf,application/octet-stream",
                "User-Agent": self.user_agent,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=20) as response:  # nosec B310 - URL validated below
                content = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise CninfoContentError(f"official attachment fetch failed: {exc}") from exc
        if not content:
            raise CninfoContentError("official attachment is empty")
        return content


@dataclass(frozen=True, slots=True)
class CninfoCapture:
    lookup: Mapping[str, Any]
    pages: tuple[Mapping[str, Any], ...]
    records: tuple[AnnouncementRecord, ...]
    content_meta: tuple[dict[str, object], ...]


def provider_symbol_for(symbol: str) -> str:
    match = SYMBOL_PATTERN.fullmatch(symbol.strip().upper())
    if not match:
        raise CninfoError("symbol must match six digits followed by .SH or .SZ")
    return f"{match.group('code')}.{match.group('market').upper()}"


def _text(value: Any, field: str, *, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise CninfoDataError(f"{field} is missing")
        return None
    if not isinstance(value, str):
        raise CninfoDataError(f"{field} must be a string")
    result = value.strip()
    if required and not result:
        raise CninfoDataError(f"{field} is empty")
    return result or None


def _optional_text(value: Any, field: str) -> str | None:
    return _text(value, field, required=False)


def _announcement_datetime(value: Any) -> datetime:
    if isinstance(value, bool) or value is None:
        raise CninfoDataError("announcementTime is missing")
    try:
        milliseconds = int(value)
    except (TypeError, ValueError) as exc:
        raise CninfoDataError("announcementTime must be epoch milliseconds") from exc
    try:
        return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).astimezone(SHANGHAI)
    except (OverflowError, OSError, ValueError) as exc:
        raise CninfoDataError("announcementTime is outside supported datetime range") from exc


def _announcement_url(adjunct_url: str) -> str:
    if adjunct_url.startswith("https://"):
        if not adjunct_url.startswith(f"{CNINFO_STATIC_URL}/"):
            raise CninfoDataError("adjunctUrl must point to static.cninfo.com.cn")
        return adjunct_url
    if not adjunct_url or adjunct_url.startswith("/") or ".." in adjunct_url.split("/"):
        raise CninfoDataError("adjunctUrl must be a relative official path")
    return f"{CNINFO_STATIC_URL}/{adjunct_url}"


def _raw_item_sha256(item: Mapping[str, Any]) -> str:
    raw = (
        json.dumps(dict(item), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    return sha256_bytes(raw)


def _parse_record(item: Mapping[str, Any], *, symbol: str, org_id: str) -> AnnouncementRecord:
    sec_code = _text(item.get("secCode"), "secCode")
    if sec_code != symbol.split(".", 1)[0]:
        raise CninfoDataError("announcement secCode does not match requested symbol")
    returned_org_id = _text(item.get("orgId"), "orgId")
    if returned_org_id != org_id:
        raise CninfoDataError("announcement orgId does not match stock lookup")
    announcement_id = _text(item.get("announcementId"), "announcementId")
    if not ANNOUNCEMENT_ID_PATTERN.fullmatch(announcement_id or ""):
        raise CninfoDataError("announcementId must be a numeric identifier")
    adjunct_url = _text(item.get("adjunctUrl"), "adjunctUrl")
    adjunct_type = (_optional_text(item.get("adjunctType"), "adjunctType") or "BIN").upper()
    raw_size = item.get("adjunctSize")
    if raw_size in (None, ""):
        adjunct_size = None
    else:
        try:
            adjunct_size = int(raw_size)
        except (TypeError, ValueError) as exc:
            raise CninfoDataError("adjunctSize must be an integer") from exc
        if adjunct_size < 0:
            raise CninfoDataError("adjunctSize must be non-negative")
    return AnnouncementRecord(
        symbol=symbol,
        sec_code=sec_code,
        sec_name=_text(item.get("secName"), "secName"),
        org_id=returned_org_id,
        announcement_id=announcement_id,
        title=_text(item.get("announcementTitle"), "announcementTitle"),
        published_at=_announcement_datetime(item.get("announcementTime")),
        announcement_type=_optional_text(item.get("announcementType"), "announcementType"),
        announcement_type_name=_optional_text(
            item.get("announcementTypeName"), "announcementTypeName"
        ),
        source_url=_announcement_url(adjunct_url),
        adjunct_type=adjunct_type,
        adjunct_size=adjunct_size,
        column_id=_optional_text(item.get("columnId"), "columnId"),
        page_column=_optional_text(item.get("pageColumn"), "pageColumn"),
        raw_item_sha256=_raw_item_sha256(item),
        content_status="pending",
        content_path=None,
        content_sha256=None,
        content_error=None,
    )


def _replace_content(
    record: AnnouncementRecord, *, output_dir: Path, content: bytes
) -> AnnouncementRecord:
    extension = re.sub(r"[^A-Za-z0-9]", "", record.adjunct_type).upper() or "BIN"
    relative_path = Path("content") / f"{record.announcement_id}.{extension}"
    write_atomic(output_dir / relative_path, content)
    values = {field: getattr(record, field) for field in record.__dataclass_fields__}
    values.update(
        content_status="available",
        content_path=relative_path.as_posix(),
        content_sha256=sha256_bytes(content),
        content_error=None,
    )
    return AnnouncementRecord(**values)


def _replace_content_error(record: AnnouncementRecord, message: str) -> AnnouncementRecord:
    values = {field: getattr(record, field) for field in record.__dataclass_fields__}
    values.update(
        content_status="unavailable",
        content_path=None,
        content_sha256=None,
        content_error=message,
    )
    return AnnouncementRecord(**values)


def _page_records(page: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    announcements = page.get("announcements")
    if announcements is None:
        return ()
    if not isinstance(announcements, list) or any(
        not isinstance(item, dict) for item in announcements
    ):
        raise CninfoDataError("announcements must be a list of objects")
    return announcements


def _empty_content_meta(record: AnnouncementRecord) -> dict[str, object]:
    return {
        "announcement_id": record.announcement_id,
        "url": record.source_url,
        "status": record.content_status,
        "path": record.content_path,
        "sha256": record.content_sha256,
        "error": record.content_error,
    }


class CninfoAnnouncementSource:
    """Single-symbol, low-frequency, public CNINFO announcement source."""

    def __init__(
        self, config: CninfoAnnouncementConfig, client: CninfoClient | None = None
    ) -> None:
        self.config = config
        self._client = client or CninfoHttpClient()

    @property
    def name(self) -> str:
        return "cninfo-announcements"

    def _query(
        self,
    ) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...], list[AnnouncementRecord]]:
        lookup = self._client.lookup_stock(self.config.code)
        if lookup.get("code") != self.config.code:
            raise CninfoDataError("stock lookup code does not match requested symbol")
        org_id = _text(lookup.get("orgId"), "lookup.orgId")
        pages: list[Mapping[str, Any]] = []
        records: list[AnnouncementRecord] = []
        page_num = 1
        previous_published_at: datetime | None = None
        seen_ids: set[str] = set()
        while True:
            params = {
                "pageNum": page_num,
                "pageSize": self.config.page_size,
                "column": "szse",
                "tabName": "fulltext",
                "plate": "",
                "stock": f"{self.config.code},{org_id}",
                "searchkey": "",
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": (
                    f"{self.config.start_date.isoformat()}~{self.config.end_date.isoformat()}"
                ),
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            page = self._client.query_announcements(params)
            pages.append(page)
            for item in _page_records(page):
                record = _parse_record(item, symbol=self.config.symbol, org_id=org_id)
                if record.announcement_id in seen_ids:
                    raise CninfoDataError("duplicate announcementId across pages")
                seen_ids.add(record.announcement_id)
                if (
                    previous_published_at is not None
                    and record.published_at > previous_published_at
                ):
                    raise CninfoDataError("announcement pages are not in descending time order")
                previous_published_at = record.published_at
                records.append(record)
            total_records = page.get("totalRecordNum")
            has_more = page.get("hasMore") is True
            if not _page_records(page) or (
                isinstance(total_records, int) and len(records) >= total_records
            ):
                break
            if not has_more and len(_page_records(page)) < self.config.page_size:
                break
            page_num += 1
            if page_num > 1000:
                raise CninfoDataError("announcement pagination exceeded safety limit")
        return lookup, tuple(pages), records

    def _capture_contents(
        self, records: list[AnnouncementRecord], output_dir: Path
    ) -> tuple[list[AnnouncementRecord], tuple[dict[str, object], ...]]:
        captured: list[AnnouncementRecord] = []
        content_meta: list[dict[str, object]] = []
        for record in records:
            try:
                content = self._client.fetch_content(record.source_url)
                captured_record = _replace_content(record, output_dir=output_dir, content=content)
            except CninfoContentError as exc:
                captured_record = _replace_content_error(record, str(exc))
            except Exception as exc:  # pragma: no cover - defensive provider boundary
                captured_record = _replace_content_error(
                    record, f"official attachment fetch failed: {exc}"
                )
            captured.append(captured_record)
            content_meta.append(_empty_content_meta(captured_record))
        return captured, tuple(content_meta)

    def capture(self, output_dir: Path) -> dict[str, object]:
        request = self.config.request_mapping()
        request_bytes = (
            json.dumps(request, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        raw_bytes = b""
        snapshot_mapping: dict[str, object]
        records: list[AnnouncementRecord] = []
        try:
            lookup, pages, records = self._query()
            eligible = [record for record in records if record.published_date <= self.config.as_of]
            raw_response: dict[str, object] = {
                "lookup": dict(lookup),
                "pages": [dict(page) for page in pages],
            }
            raw_bytes = (
                json.dumps(raw_response, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
            if not records:
                status = AnnouncementState.EMPTY
                issues = (AnnouncementIssue("NO_ANNOUNCEMENTS", "no announcements were returned"),)
                content_meta: tuple[dict[str, object], ...] = ()
            elif not eligible:
                status = AnnouncementState.FUTURE_ONLY
                issues = (
                    AnnouncementIssue("FUTURE_ANNOUNCEMENTS", "all announcements are after as_of"),
                )
                content_meta = ()
                records = []
            else:
                records, content_meta = self._capture_contents(eligible, output_dir)
                unavailable = [record for record in records if record.content_status != "available"]
                if unavailable:
                    status = AnnouncementState.CONTENT_UNAVAILABLE
                    issues = (
                        AnnouncementIssue(
                            "CONTENT_UNAVAILABLE",
                            f"{len(unavailable)} official attachment(s) could not be fetched",
                        ),
                    )
                else:
                    status = AnnouncementState.READY
                    issues = ()
            snapshot = AnnouncementSnapshot(
                schema_version=SCHEMA_VERSION,
                source=self.name,
                symbol=self.config.symbol,
                as_of=self.config.as_of.isoformat(),
                start_date=self.config.start_date.isoformat(),
                end_date=self.config.end_date.isoformat(),
                received_at=self.config.received_at.isoformat(),
                records=tuple(records),
                announcement_ready=status is AnnouncementState.READY,
                decision_ready=False,
            )
            snapshot_mapping = snapshot.to_mapping()
            raw_response["content"] = list(content_meta)
            raw_bytes = (
                json.dumps(raw_response, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
        except CninfoDataError as exc:
            status = AnnouncementState.INVALID
            issues = (AnnouncementIssue("INVALID", str(exc)),)
            snapshot_mapping = empty_snapshot_mapping(
                symbol=self.config.symbol,
                as_of=self.config.as_of,
                start_date=self.config.start_date,
                end_date=self.config.end_date,
                received_at=self.config.received_at,
            )
        except CninfoProviderError as exc:
            status = AnnouncementState.PROVIDER_ERROR
            issues = (AnnouncementIssue("PROVIDER_ERROR", str(exc)),)
            snapshot_mapping = empty_snapshot_mapping(
                symbol=self.config.symbol,
                as_of=self.config.as_of,
                start_date=self.config.start_date,
                end_date=self.config.end_date,
                received_at=self.config.received_at,
            )
        except CninfoError as exc:
            status = AnnouncementState.SOURCE_UNAVAILABLE
            issues = (AnnouncementIssue("SOURCE_UNAVAILABLE", str(exc)),)
            snapshot_mapping = empty_snapshot_mapping(
                symbol=self.config.symbol,
                as_of=self.config.as_of,
                start_date=self.config.start_date,
                end_date=self.config.end_date,
                received_at=self.config.received_at,
            )
        snapshot_bytes = (
            json.dumps(snapshot_mapping, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        report = AnnouncementReport(
            schema_version=SCHEMA_VERSION,
            source=self.name,
            symbol=self.config.symbol,
            as_of=self.config.as_of.isoformat(),
            request_sha256=sha256_bytes(request_bytes),
            raw_response_sha256=sha256_bytes(raw_bytes) if raw_bytes else None,
            snapshot_sha256=sha256_bytes(snapshot_bytes),
            record_count=len(snapshot_mapping.get("records", [])),
            selected_announcement_ids=tuple(
                str(record.get("announcement_id"))
                for record in snapshot_mapping.get("records", [])
                if isinstance(record, dict) and record.get("announcement_id") is not None
            ),
            status=status.value,
            announcement_ready=bool(snapshot_mapping.get("announcement_ready")),
            decision_ready=False,
            issues=issues,
        )
        report_bytes = (
            json.dumps(report.to_mapping(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        write_atomic(output_dir / "request.json", request_bytes)
        write_atomic(output_dir / "raw_response.json", raw_bytes)
        write_atomic(output_dir / "announcements_snapshot.json", snapshot_bytes)
        write_atomic(output_dir / "announcements_report.json", report_bytes)
        return report.to_mapping()
