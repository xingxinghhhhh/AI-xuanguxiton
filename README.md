# A股选股系统

这是 A 股投研与决策辅助系统的第一个离线 MVP。当前节点只处理只读日频行情：

```text
标准化日频行情 -> JSONL 模拟回放 -> 数据健康门 -> 健康报告
```

系统不会连接券商、同花顺或其他真实数据源，不会下单，也不会输出买入或卖出建议。

## 环境

- Python 3.11+
- pytest（测试）
- ruff（代码检查，可选）

项目不依赖 pandas、行情 SDK 或网络服务。

如需启用 Baostock 适配器，单独安装可选依赖：

```bash
python -m pip install -e ".[baostock]"
```

## 安装与运行

```bash
python -m pip install -e .
python -m a_share_ai.cli replay-health \
  --input fixtures/market/valid_daily.jsonl \
  --as-of 2026-08-10T12:00:00Z \
  --output-dir reports/replay-001
```

健康报告写入：

- `reports/replay-001/replay_output.jsonl`：标准化、可回放的行情输出；
- `reports/replay-001/health_report.json`：输入/输出哈希、时间范围、健康状态和问题列表。

交易日历覆盖审计：

```bash
python -m a_share_ai.cli audit-coverage \
  --bars fixtures/market/coverage/valid_daily.jsonl \
  --calendar fixtures/market/calendar/sample.json \
  --start 2026-01-02 \
  --end 2026-01-06 \
  --as-of 2026-01-07T00:00:00Z \
  --output-dir reports/coverage-001
```

覆盖状态为 `complete` 才能进入 `decision_ready=true`；缺失交易日、非交易日行情或日历未覆盖时均返回非零退出码。

## Baostock 单股票历史日线捕获

该适配器固定单股票、日频和不复权（`adjustflag=3`），并保存请求、原始响应和标准化 JSONL。捕获报告本身始终为 `decision_ready=false`，必须继续通过既有健康与覆盖命令：

```bash
python -m a_share_ai.cli capture-baostock-daily \
  --symbol 600000.SH \
  --start 2025-06-03 \
  --end 2025-06-06 \
  --received-at 2026-08-10T12:00:00Z \
  --output-dir reports/baostock-001

python -m a_share_ai.cli replay-health \
  --input reports/baostock-001/normalized_daily.jsonl \
  --as-of 2026-08-10T12:00:00Z \
  --output-dir reports/baostock-001/replay-health
```

不支持自动切换数据源、补值、全市场扫描或交易。

CLI 在 `decision_ready=true` 时返回 0；数据无效、过期或断线时返回 1，并仍生成可审计报告。

## 测试

```bash
python -m pytest
ruff check .
```

## 安全边界

- 只读行情数据，不包含订单、账户、余额或持仓接口；
- 不读取 API key、密码或浏览器凭据；
- 过期、断线、无效数据不会进入可决策状态；
- 所有报告通过临时文件写入后原子替换；
- 后续真实供应商只能实现 `MarketDataSource` 协议并输出标准模型。
## Official announcement snapshots

The read-only CNINFO adapter captures one A-share symbol's official announcement
metadata and PDF evidence. It resolves the public organization id, queries a bounded
date range, filters announcements by `as_of`, and writes request/raw/snapshot/report
files plus content hashes. It never classifies an announcement or produces a trade
decision.

```bash
python -m a_share_ai.cli capture-announcements \
  --symbol 600000.SH \
  --start 2025-12-29 \
  --end 2025-12-31 \
  --as-of 2025-12-31 \
  --received-at 2026-08-10T12:00:00Z \
  --output-dir reports/announcements-001
```

The command is intentionally single-symbol and low-frequency. Future, malformed,
duplicated, out-of-order, unavailable, or incomplete evidence is fail-closed and
keeps `decision_ready=false`.
