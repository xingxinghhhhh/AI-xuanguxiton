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

## Evidence-backed offline analysis report

The `analysis-report-v1` node converts an already validated
`analysis-input-v1` bundle into a deterministic research report. It uses an
offline JSON response fixture as the provider boundary, expands stable bundle
names into a top-level citation index with paths and SHA-256 values, and never
produces a trade decision.

```bash
python -m a_share_ai.cli analyze-input \
  --bundle reports/analysis-input-001/analysis_input_bundle.json \
  --input-root reports \
  --response-fixture fixtures/analysis/report/valid_provider.json \
  --output-dir reports/analysis-report-001
```

The command writes `research_analysis.json` and
`research_analysis_report.json`. Any bundle, evidence, provider, symbol,
cutoff, citation, future-date, or prohibited-decision violation is
fail-closed with `analysis_ready=false`; `decision_ready` remains false for
every result. No real AI API, credentials, network, database, or trading path
is used.

## Explicit OpenAI research provider

The real provider is opt-in and single-request only. It sends only the validated
bundle summaries, symbol, cutoff, and fixed evidence IDs to the OpenAI Responses
API. Local evidence paths, hashes, credentials, and original files are not sent.
The response is validated by the same `analysis-report-v1` fail-closed validator;
`decision_ready` remains `false` even when `analysis_ready` is `true`.

Configure the key locally in the ignored `.env.local` file:

```env
OPENAI_API_KEY=your_key_here
```

Run the real provider explicitly; it never falls back to the offline fixture:

```bash
python -m a_share_ai.cli analyze-input \
  --provider openai \
  --model gpt-5.6-luna \
  --bundle reports/analysis-input-001/analysis_input_bundle.json \
  --bundle-report reports/analysis-input-001/analysis_input_report.json \
  --input-root reports \
  --output-dir reports/analysis-openai-001
```

The command writes `ai_request.json`, `ai_response.json`,
`research_analysis.json`, and `research_analysis_report.json`. Request and
response SHA-256 values, provider status, model, and timestamps are recorded;
the API key is never written to those files or CLI output. Ordinary tests use a
fake transport and remain offline.

## Explicit DeepSeek research provider

DeepSeek is also opt-in and single-request only. It uses the official Chat
Completions JSON mode rather than the OpenAI Responses API schema. The returned
JSON is still passed through the same local `analysis-report-v1` validator, so
invalid fields, citations, dates, or trading instructions fail closed.

Configure the key locally in the ignored `.env.local` file:

```env
DEEPSEEK_API_KEY=your_key_here
```

Run it explicitly with the default `deepseek-v4-flash` model:

```bash
python -m a_share_ai.cli analyze-input \
  --provider deepseek \
  --model deepseek-v4-flash \
  --bundle reports/analysis-input-001/analysis_input_bundle.json \
  --bundle-report reports/analysis-input-001/analysis_input_report.json \
  --input-root reports \
  --env-file .env.local \
  --output-dir reports/analysis-deepseek-001
```

The DeepSeek request contains no local paths, hashes, original files, or API
key. It writes the same audit artifacts as the OpenAI provider and never falls
back or retries.

## Render a readable research report

After a successful `analyze-input` run, render the validated JSON and evidence
citations as deterministic Markdown without another API call:

```bash
python -m a_share_ai.cli render-analysis \
  --analysis reports/analysis-deepseek-001/research_analysis.json \
  --analysis-report reports/analysis-deepseek-001/research_analysis_report.json \
  --input-root reports \
  --output-dir reports/analysis-deepseek-001/rendered
```

This writes `research_analysis.md` and `analysis_render_report.json`. It checks
the analysis SHA, evidence paths and hashes, escapes model text for Markdown,
and refuses reports that are not analysis-ready or that set `decision_ready` to
true.

## Offline analysis quality and evidence audit

The `analysis-quality-v1` node audits a rendered `analysis-report-v1` result
without another API call. It verifies all eight sections, 100% claim citation
coverage, the fixed section-to-evidence mapping, use of all six evidence IDs,
input and rendered-report hashes, and the non-trading decision gate.

```bash
python -m a_share_ai.cli audit-analysis-quality \
  --analysis reports/analysis-deepseek-001/research_analysis.json \
  --analysis-report reports/analysis-deepseek-001/research_analysis_report.json \
  --render-report reports/analysis-deepseek-001/rendered/analysis_render_report.json \
  --input-root reports \
  --output-dir reports/analysis-deepseek-001/quality
```

The command writes `analysis_quality_report.json` with `quality_ready=true`
only when the complete evidence audit passes. It never calls an AI provider,
reads credentials, or sets `decision_ready=true`.

## Decision input readiness snapshot

After the analysis, render, and quality artifacts all pass, build a final
cross-checked input snapshot for a future decision module. This is an input
readiness gate only; it is not a trading authorization and always keeps
`decision_ready=false`.

```bash
python -m a_share_ai.cli build-decision-input \
  --analysis reports/analysis-deepseek-001/research_analysis.json \
  --analysis-report reports/analysis-deepseek-001/research_analysis_report.json \
  --render-report reports/analysis-deepseek-001/rendered/analysis_render_report.json \
  --quality-report reports/analysis-deepseek-001/quality/analysis_quality_report.json \
  --bundle reports/analysis-input-001/analysis_input_bundle.json \
  --bundle-report reports/analysis-input-001/analysis_input_report.json \
  --input-root reports \
  --artifact-root reports \
  --output-dir reports/analysis-deepseek-001/decision-input
```

The command writes `decision_input_snapshot.json` and
`decision_input_report.json`, verifies the upstream paths, hashes, versions,
symbol, cutoff time, readiness states, and future-date guard, and never calls
AI, reads API keys, or produces BUY/SELL/HOLD output.

## Analysis safety audit

Before a future decision module consumes the snapshot, audit the final
structured claims and rendered Markdown for action-oriented or execution
language:

```bash
python -m a_share_ai.cli audit-analysis-safety \
  --decision-input reports/analysis-deepseek-001/decision-input/decision_input_snapshot.json \
  --decision-input-report reports/analysis-deepseek-001/decision-input/decision_input_report.json \
  --analysis reports/analysis-deepseek-001/research_analysis.json \
  --analysis-report reports/analysis-deepseek-001/research_analysis_report.json \
  --render-report reports/analysis-deepseek-001/rendered/analysis_render_report.json \
  --artifact-root reports \
  --output-dir reports/analysis-deepseek-001/safety
```

The command writes `analysis_safety_report.json`. It checks the complete input
chain and reports stable rule IDs and locations for forbidden actions, prices,
positions, recommendations, unexpected decision fields, and Markdown/HTML/link
or code-fence bypasses. `safety_ready=true` is independent of
`decision_input_ready` and never changes `decision_ready=false`.
