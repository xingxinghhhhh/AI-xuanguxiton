"""Offline adapter implementation used by the first MVP."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from .contracts import ContractError, DailyBar


class ReplayInputError(ValueError):
    """Raised when a JSONL replay record cannot be decoded or normalized."""

    def __init__(self, message: str, *, line_number: int) -> None:
        super().__init__(message)
        self.line_number = line_number


class JsonlReplaySource:
    """A local JSONL source implementing the provider-neutral read-only protocol."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def name(self) -> str:
        return "jsonl-replay"

    def iter_daily_bars(self) -> Iterator[DailyBar]:
        try:
            handle = self.path.open("r", encoding="utf-8", newline="")
        except OSError as exc:
            raise ReplayInputError(f"cannot open input: {exc}", line_number=0) from exc
        with handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ContractError("record must be a JSON object")
                    yield DailyBar.from_mapping(record)
                except (json.JSONDecodeError, ContractError) as exc:
                    raise ReplayInputError(str(exc), line_number=line_number) from exc
