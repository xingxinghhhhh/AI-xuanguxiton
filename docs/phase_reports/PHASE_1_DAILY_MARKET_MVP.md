# Phase 1：A股日频行情契约、模拟回放与数据健康门 MVP

## 目标

建立完全离线、可重复验证的只读数据闭环：

`标准化日频行情 -> JSONL 模拟回放 -> 时间/新鲜度/断线检查 -> 健康报告`

## 范围

- Python 3.11+ 项目骨架；
- 版本化 `DailyBar` 契约和只读 `MarketDataSource` 协议；
- JSONL 离线回放适配器；
- 字段、日期、重复、OHLC、数量、未来数据和状态校验；
- `connected`、`stale`、`disconnected`、`recovered`、`invalid` 状态；
- 输入/输出 SHA-256、`as_of`、原子写入和可审计健康报告；
- CLI 离线验收入口。

## 明确不包含

真实行情供应商、网络采集、AI 分析、新闻公告、Web 页面、数据库、持仓、自动交易、实时/Level-2/Tick/分钟数据。

## 安全不变量

任何 `stale`、`disconnected`、`invalid` 或校验问题都不会生成 `decision_ready=true`。

## 验收命令

```bash
python -m pytest
python -m a_share_ai.cli replay-health --input fixtures/market/valid_daily.jsonl --as-of 2026-08-10T12:00:00Z --output-dir reports/replay-001
```
