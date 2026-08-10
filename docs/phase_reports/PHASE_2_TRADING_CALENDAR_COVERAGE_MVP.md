# Phase 2：A股交易日历语义与日频覆盖完整性审计 MVP

## 目标

在日频行情回放和健康门上增加交易日历语义，区分正常非交易日、正常交易日缺口、非交易日行情和日历未知：

`DailyBar 日期 -> 本地版本化交易日历 -> 覆盖审计 -> decision_ready`

## 范围

- 版本化 `TradingCalendar` 契约和只读 `TradingCalendarSource` 协议；
- 本地 JSON 交易日历 fixture；
- 完整、缺失交易日、非交易日行情、日历未知四类审计状态；
- `audit-coverage` CLI、确定性报告、输入哈希和 `as_of`；
- 保持 `replay-health` 原有行为不变。

## 安全不变量

只有 `complete` 覆盖状态可以生成 `decision_ready=true`。日历未知时不把实际日期强行解释为交易日或非交易日。

## 明确不包含

真实供应商、网络下载、自动日历更新、AI、技术指标、新闻、基本面、持仓、数据库、实时行情和交易路径。

## 验收命令

```bash
python -m pytest
ruff check .
python -m a_share_ai.cli audit-coverage --bars fixtures/market/coverage/valid_daily.jsonl --calendar fixtures/market/calendar/sample.json --start 2026-01-02 --end 2026-01-06 --as-of 2026-01-07T00:00:00Z --output-dir reports/coverage-001
```
