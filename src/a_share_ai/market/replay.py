"""Deterministic serialization and atomic output helpers for offline replay."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .contracts import DailyBar


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_jsonl(bars: Iterable[DailyBar]) -> bytes:
    lines = [
        json.dumps(bar.to_mapping(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for bar in bars
    ]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def write_atomic(path: Path, payload: bytes) -> None:
    """Write bytes to the same directory and atomically replace the destination."""

    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
