import json
from pathlib import Path

import pytest

from a_share_ai.market.adapter import JsonlReplaySource, ReplayInputError
from a_share_ai.market.replay import canonical_jsonl, sha256_bytes

FIXTURE = Path(__file__).parents[2] / "fixtures" / "market" / "valid_daily.jsonl"


def test_jsonl_replay_reads_all_fixture_records() -> None:
    bars = list(JsonlReplaySource(FIXTURE).iter_daily_bars())

    assert len(bars) == 4
    assert {bar.symbol for bar in bars} == {"600000.SH", "000001.SZ"}


def test_jsonl_replay_rejects_malformed_line(tmp_path: Path) -> None:
    input_path = tmp_path / "bad.jsonl"
    input_path.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(ReplayInputError, match="Expecting") as error:
        list(JsonlReplaySource(input_path).iter_daily_bars())

    assert error.value.line_number == 1


def test_canonical_output_is_deterministic() -> None:
    bars = list(JsonlReplaySource(FIXTURE).iter_daily_bars())

    first = canonical_jsonl(bars)
    second = canonical_jsonl(bars)

    assert first == second
    assert sha256_bytes(first) == sha256_bytes(second)
    assert json.loads(first.splitlines()[0])['schema_version'] == "1.0"
