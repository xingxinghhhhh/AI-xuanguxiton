"""Offline provider protocol and deterministic JSON fixture implementation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol


class ProviderError(ValueError):
    """Raised when an offline provider cannot return a JSON object."""


class AnalysisProvider(Protocol):
    """Read-only interface shared by offline and real analysis providers."""

    def load(
        self,
        *,
        bundle: Mapping[str, Any] | None = None,
        entries: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """Return one structured analysis payload."""


class OfflineAnalysisProvider:
    """Load a UTF-8 JSON fixture without network access or credentials."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(
        self,
        *,
        bundle: Mapping[str, Any] | None = None,
        entries: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        del bundle, entries
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderError(f"cannot load provider fixture: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProviderError("provider fixture root must be a JSON object")
        return payload
