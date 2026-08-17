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

## Benchmark market-context snapshot

The `market-context-v1` command captures a read-only, point-in-time snapshot of
three fixed Baostock indexes: `000001.SH` (上证综合指数), `399001.SZ` (深证成指),
and `399006.SZ` (创业板指). It does not ask DeepSeek for a market judgment and
does not produce a trading conclusion.

```bash
python -m a_share_ai.cli capture-market-context \
  --start 2026-01-02 \
  --end 2026-01-06 \
  --as-of 2026-01-06T12:00:00+00:00 \
  --received-at 2026-01-07T00:00:00+00:00 \
  --calendar fixtures/market/calendar/sample.json \
  --output-dir reports/market-context-001
```

The command writes `request.json`, `raw_response.json`,
`market_context_snapshot.json`, and `market_context_report.json`. It validates
calendar coverage, point-in-time boundaries, complete coverage for all three
indexes, Decimal OHLCV values, and SHA-256 audit hashes. Any provider or data
failure keeps `market_context_ready=false` and `decision_ready=false`.

## Analysis input v2 with market context

To include the fixed benchmark snapshot in the existing evidence bundle, pass
both market-context files to `build-analysis-input`. This creates an explicit
`analysis-input-v2` bundle; existing v1 bundles are never rewritten or upgraded
implicitly.

```bash
python -m a_share_ai.cli build-analysis-input \
  --symbol 600000.SH \
  --as-of 2026-01-06T12:00:00+00:00 \
  --input-root reports \
  --market-context-snapshot reports/market-context-001/market_context_snapshot.json \
  --market-context-report reports/market-context-001/market_context_report.json \
  ...
```

The two paths are required as a pair. The v2 bundle keeps the same six evidence
IDs and adds the snapshot/report references and SHA-256 values to the existing
`market` evidence. It verifies the market-context version, readiness gate,
calendar hash/version, exact `as_of`, fixed index set, record hashes and
point-in-time dates. All analysis, quality, decision-input, and safety outputs
continue to keep `decision_ready=false`.

The v2 market summary also includes the deterministic
`market-context-summary-v1` features `latest_trade_date`, `latest_close`,
`return_1d`, `return_5d`, `return_20d`, and `record_count` for each fixed index.
Returns use `close_latest / close_n_periods_ago - 1`; unavailable warmup periods
are represented as `null`.

## Analysis input v2 relative strength summary

When a v2 bundle includes market context, the technical summary also contains a
deterministic `relative-strength-v1` object. For each fixed benchmark index it
provides the stock return fields from `technical-v1`, the benchmark return
fields, and `relative_return_1d`, `relative_return_5d`, and
`relative_return_20d`, calculated as `stock_return_n - benchmark_return_n`.
Insufficient stock or benchmark history stays `null`; the summary does not
assign strong/weak labels, rankings, or trading signals. The v1 bundle and
technical indicator calculation remain unchanged.

## Market-aware offline end-to-end replay

To verify that the v2 market summary and relative-strength summary survive the
complete read-only research chain, run the offline replay with an existing v2
bundle and the normal provider response fixture:

```bash
python -m a_share_ai.cli replay-market-aware-analysis \
  --bundle reports/analysis-input-001/analysis_input_bundle.json \
  --bundle-report reports/analysis-input-001/analysis_input_report.json \
  --input-root reports \
  --response-fixture fixtures/analysis/report/valid_provider.json \
  --output-dir reports/market-aware-replay-001
```

The `market-aware-replay-v1` report runs offline analysis, Markdown rendering,
quality, decision-input, safety, and pending-only human review in order. It
records stage paths and SHA-256 values and verifies that
`market-context-summary-v1` and `relative-strength-v1` are present. The replay
stops on the first failed stage, never calls DeepSeek, never creates a review
result or research release, and keeps `decision_ready=false`.

## Market-aware DeepSeek smoke test

After an explicit v2 input bundle is available, a single controlled DeepSeek
smoke test can be run with the configured local environment file:

```bash
python -m a_share_ai.cli market-aware-smoke \
  --bundle reports/market-aware-smoke-node27-20260811/input/analysis_input/analysis_input_bundle.json \
  --bundle-report reports/market-aware-smoke-node27-20260811/input/analysis_input/analysis_input_report.json \
  --input-root reports/market-aware-smoke-node27-20260811/input \
  --env-file .env.local \
  --model deepseek-v4-flash \
  --output-dir reports/market-aware-smoke-node27-20260811/smoke
```

The command makes one request with no retry, fallback, or provider switching,
records redacted provider/request/response audit metadata, and then reuses the
offline replay chain. A provider or downstream schema failure stops the chain;
it never fabricates readiness or creates a review result/research release.

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

## Research freshness audit

Check whether a published research package corresponds to the latest completed
trading day at a fixed evaluation time:

```bash
python -m a_share_ai.cli audit-research-freshness \
  --release-manifest reports/analysis-deepseek-001/release/research_release_manifest.json \
  --release-report reports/analysis-deepseek-001/release/research_release_report.json \
  --calendar fixtures/market/calendar/sample.json \
  --calendar-report reports/calendar_report.json \
  --evaluation-at 2026-08-11T08:00:00+00:00 \
  --output-dir reports/analysis-deepseek-001/freshness
```

The `research-freshness-v1` report uses Asia/Shanghai and a fixed 15:00 close:
before the close, the current day is not complete; after the close, it may be
the latest completed trading day; weekends and holidays fall back to the prior
trading day. It returns `fresh`, `stale`, `calendar_unknown`, or `invalid`, and
always keeps `decision_ready=false`. It does not use system current time or
make any investment or transaction judgement.

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

## Manual analysis review packet

After the safety gate passes, create a deterministic, pending-only checklist
for human review:

```bash
python -m a_share_ai.cli build-analysis-review \
  --decision-input reports/analysis-deepseek-001/decision-input/decision_input_snapshot.json \
  --decision-input-report reports/analysis-deepseek-001/decision-input/decision_input_report.json \
  --safety-report reports/analysis-deepseek-001/safety/analysis_safety_report.json \
  --analysis reports/analysis-deepseek-001/research_analysis.json \
  --analysis-report reports/analysis-deepseek-001/research_analysis_report.json \
  --artifact-root reports \
  --output-dir reports/analysis-deepseek-001/review
```

This writes `analysis_review_packet.json` and
`analysis_review_report.json`. Each claim appears once with its evidence paths
and SHA-256 values, `review_status=pending`, `review_complete=false`, and
`decision_ready=false`. It does not write or accept human review decisions.

## Human review record

Submit a complete JSON record after a human has checked each claim and its
evidence mapping:

```bash
python -m a_share_ai.cli apply-analysis-review \
  --packet reports/analysis-deepseek-001/review/analysis_review_packet.json \
  --packet-report reports/analysis-deepseek-001/review/analysis_review_report.json \
  --submission reports/analysis-deepseek-001/review/review_submission.json \
  --output-dir reports/analysis-deepseek-001/review-result
```

The submission contains the packet SHA-256 and one item per `review_id`.
Allowed statuses are `confirmed`, `challenged`, and `follow_up`; the latter
two require notes and every `reviewed_at` timestamp must include a timezone.
Unknown, duplicate, missing, extra, or transaction-related fields fail closed.
The result records `review_complete` and `review_gate_pass`, but always keeps
`decision_ready=false`; a passed review gate is not trade authorization.

## Research release manifest

After the safety audit and a complete all-confirmed human review, build a
single-stock release manifest for downstream readers or a future decision
module:

```bash
python -m a_share_ai.cli build-research-release \
  --decision-input reports/analysis-deepseek-001/decision-input/decision_input_snapshot.json \
  --decision-input-report reports/analysis-deepseek-001/decision-input/decision_input_report.json \
  --safety-report reports/analysis-deepseek-001/safety/analysis_safety_report.json \
  --analysis-review-packet reports/analysis-deepseek-001/review/analysis_review_packet.json \
  --analysis-review-result reports/analysis-deepseek-001/review-result/analysis_review_result.json \
  --analysis-review-result-report reports/analysis-deepseek-001/review-result/analysis_review_result_report.json \
  --artifact-root reports \
  --output-dir reports/analysis-deepseek-001/release
```

This writes `research_release_manifest.json` and
`research_release_report.json`. The `research-release-v1` manifest contains
only controlled relative paths, versions, statuses, and SHA-256 values for the
six upstream artifacts. It blocks challenged/follow-up reviews, hash or
symbol/cutoff mismatches, path escapes, future cutoffs, and upstream failures.
`research_release_ready=true` means the research package is internally
consistent and ready to read; it never changes `decision_ready=false` and is
not trade authorization.

## Market-aware review-to-release replay

For the `analysis-input-v2` market-aware chain, the review and release steps
can be replayed together after a human supplies an explicit submission:

```bash
python -m a_share_ai.cli replay-market-aware-release \
  --packet reports/analysis-deepseek-001/review/analysis_review_packet.json \
  --packet-report reports/analysis-deepseek-001/review/analysis_review_report.json \
  --submission reports/analysis-deepseek-001/review/review_submission.json \
  --artifact-root reports/analysis-deepseek-001 \
  --output-dir reports/analysis-deepseek-001/release-replay
```

The command only consumes `review_submission.json`; it never generates or
modifies a human decision. It requires the market context and relative
strength summaries, records both stage reports and hashes, and stops before
release when the submission is invalid or not fully `confirmed`. A successful
replay writes the existing review result and research release artifacts plus
`market_aware_release_replay_report.json`; `decision_ready` remains `false`.

## Market-aware research session admission

After a reviewed market-aware release exists, build the read-only session admission
report with explicit evaluation and reference times:

```bash
python -m a_share_ai.cli build-market-aware-session \
  --release-manifest reports/analysis-deepseek-001/release-replay/release/research_release_manifest.json \
  --release-report reports/analysis-deepseek-001/release-replay/release/research_release_report.json \
  --replay-report reports/analysis-deepseek-001/release-replay/market_aware_release_replay_report.json \
  --calendar fixtures/market/calendar/sample.json \
  --calendar-report reports/calendar_report.json \
  --evaluation-at 2026-08-10T13:00:00+00:00 \
  --reference-at 2026-08-12T12:00:00+00:00 \
  --artifact-root reports/analysis-deepseek-001 \
  --output-dir reports/analysis-deepseek-001/session
```

The `market-aware-session-v1` report admits a session only when the release,
review, v2 market summaries, and `research-freshness-v1` audit are all valid.
`--reference-at` is explicit and required: an evaluation after it returns
`TIME_IN_FUTURE`, while a release after the evaluation returns
`RELEASE_AFTER_EVALUATION`; only then can an uncovered calendar produce
`CALENDAR_UNKNOWN`. A ready session still keeps `decision_ready=false` and
contains no trading instruction or authorization.

## Render a market-aware session report

Render the session admission metadata as deterministic Markdown without another
API call:

```bash
python -m a_share_ai.cli render-market-aware-session \
  --session reports/analysis-deepseek-001/session/market_aware_session.json \
  --session-report reports/analysis-deepseek-001/session/market_aware_session_report.json \
  --artifact-root reports/analysis-deepseek-001 \
  --output-dir reports/analysis-deepseek-001/session-render
```

The `market-aware-session-render-v1` renderer verifies the session/report
fields, their actual SHA-256 values, the same-directory
`research_freshness_report.json`, versions, status gates, time fields, and
artifact-root boundaries. Release, bundle, and calendar hashes are shown as
declared summaries only; the renderer does not infer upstream paths that the
session contract does not contain. Blocked sessions may be rendered as blocked
Markdown, but never as ready and always keep `decision_ready=false`.

## Build a market-aware session audit package

Package the session admission and rendering artifacts into a deterministic,
relative-path manifest for offline handoff and later verification:

```bash
python -m a_share_ai.cli build-market-aware-session-package \
  --session reports/analysis-deepseek-001/session/market_aware_session.json \
  --session-report reports/analysis-deepseek-001/session/market_aware_session_report.json \
  --freshness-report reports/analysis-deepseek-001/session/research_freshness_report.json \
  --session-markdown reports/analysis-deepseek-001/session-render/market_aware_session.md \
  --session-render-report reports/analysis-deepseek-001/session-render/market_aware_session_render_report.json \
  --artifact-root reports/analysis-deepseek-001 \
  --output-dir reports/analysis-deepseek-001/session-package
```

The `market-aware-session-package-v1` manifest records five fixed artifact
roles, relative paths, byte sizes, and actual SHA-256 values. It also records
the session status and time summary. Blocked sessions may be packaged, but
`package_ready` and `session_ready` remain distinct and
`decision_ready=false` always. Declared release, bundle, and calendar hashes
remain summaries only; no missing upstream path is inferred.

## Independently audit a market-aware session package

Re-open a package as an offline consumer and verify the manifest, package
report, and all five declared artifacts without rebuilding the package:

```bash
python -m a_share_ai.cli audit-market-aware-session-package \
  --package reports/analysis-deepseek-001/session-package/market_aware_session_package.json \
  --package-report reports/analysis-deepseek-001/session-package/market_aware_session_package_report.json \
  --artifact-root reports/analysis-deepseek-001 \
  --output-dir reports/analysis-deepseek-001/session-package-audit
```

The `market-aware-session-package-audit-v1` report independently checks
relative paths, actual byte sizes, SHA-256 values, versions, timezone-aware
time ordering, and the session/freshness/renderer relation chain. A successful
audit means package integrity only: a stale or blocked package can have
`audit_ready=true` while retaining `session_ready=false` and
`decision_ready=false`. The audit report is written only when its output
directory is inside `artifact-root`.

## Compare two market-aware session packages

Compare two packages that have each passed the independent Node33 audit:

```bash
python -m a_share_ai.cli compare-market-aware-session-packages \
  --previous-package reports/previous/session-package/market_aware_session_package.json \
  --previous-package-report reports/previous/session-package/market_aware_session_package_report.json \
  --previous-artifact-root reports/previous \
  --current-package reports/current/session-package/market_aware_session_package.json \
  --current-package-report reports/current/session-package/market_aware_session_package_report.json \
  --current-artifact-root reports/current \
  --output-dir reports/current/session-package-diff
```

The `market-aware-session-package-diff-v1` outputs report literal changes in
session/package fields and the five artifact roles' relative paths, byte
sizes, and SHA-256 values. The symbols must match and the current `as_of` must
be later. This is a structural diff only; it keeps `decision_ready=false` and
does not infer whether any change is favorable or actionable.

## Render a market-aware session package diff

Render the Node34 structural diff for readable, offline handoff:

```bash
python -m a_share_ai.cli render-market-aware-session-package-diff \
  --diff reports/current/session-package-diff/market_aware_session_package_diff.json \
  --diff-report reports/current/session-package-diff/market_aware_session_package_diff_report.json \
  --input-root reports/current \
  --output-dir reports/current/session-package-diff-render
```

The `market-aware-session-package-diff-render-v1` output shows literal field
and five-artifact changes, validates the Node34 diff/report SHA binding, and
keeps `decision_ready=false`. It is a safe Markdown view only: it does not
judge market direction, predict returns, or create an investment or trading
instruction.

## Audit a rendered market-aware session package diff

Independently verify the Node35 diff JSON, report, Markdown, and render report:

```bash
python -m a_share_ai.cli audit-market-aware-session-package-diff-render \
  --diff reports/current/session-package-diff/market_aware_session_package_diff.json \
  --diff-report reports/current/session-package-diff/market_aware_session_package_diff_report.json \
  --markdown reports/current/session-package-diff-render/market_aware_session_package_diff.md \
  --render-report reports/current/session-package-diff-render/market_aware_session_package_diff_render_report.json \
  --artifact-root reports/current \
  --output-dir reports/current/session-package-diff-render-audit
```

The `market-aware-session-package-diff-render-audit-v1` report independently
checks versions, relative paths, byte sizes, SHA-256 bindings, symbol/time
metadata, and comparison/render status. A blocked render can audit as
complete while remaining blocked; `audit_ready` does not mean research
validity or trading authorization, and `decision_ready` remains false.

## Build a market-aware session history

Compose explicit package references into a deterministic chronological history:

```bash
python -m a_share_ai.cli build-market-aware-session-history \
  --spec reports/session-history/session_history_spec.json \
  --history-root reports/session-history \
  --output-dir reports/session-history/history
```

The `market-aware-session-history-v1` spec contains at least two package
manifest/report/artifact-root references, all relative to `history-root`. Each
package is independently checked with the Node33 package audit; symbols must
match and `as_of` values must be strictly increasing. Stale or blocked
sessions remain visibly blocked in the history and are never upgraded to
ready. The history is a file-based manifest only and keeps
`decision_ready=false`.

## Audit a market-aware session history

Independently verify a history manifest, its report, and every referenced
package:

```bash
python -m a_share_ai.cli audit-market-aware-session-history \
  --history reports/session-history/history/market_aware_session_history.json \
  --history-report reports/session-history/history/market_aware_session_history_report.json \
  --history-root reports/session-history \
  --output-dir reports/session-history/history-audit
```

The `market-aware-session-history-audit-v1` report reuses the Node33 package
audit and checks history/report hashes, relative references, package SHA and
state bindings, symbols, freshness, and strict `as_of` ordering. It verifies
integrity only: stale or blocked sessions remain blocked and
`decision_ready=false`.

## Render a market-aware session history

Render the Node37 history and Node38 audit as a deterministic, offline
Markdown timeline:

```bash
python -m a_share_ai.cli render-market-aware-session-history \
  --history reports/session-history/history/market_aware_session_history.json \
  --history-report reports/session-history/history/market_aware_session_history_report.json \
  --history-audit-report reports/session-history/history-audit/market_aware_session_history_audit_report.json \
  --history-root reports/session-history \
  --output-dir reports/session-history/history-render
```

The `market-aware-session-history-render-v1` output records the actual input
paths, byte sizes, SHA-256 values, package timeline, readiness flags, and
Markdown SHA. Stale or blocked histories remain visibly blocked, and
`render_ready` is true only when both history and independent audit readiness
are true. This is a read-only status view: it does not infer market trends,
returns, investment value, or trading authorization, and always keeps
`decision_ready=false`.

## Audit a rendered market-aware session history

Independently verify the Node39 Markdown render and its report against the
Node37 history and Node38 history audit:

```bash
python -m a_share_ai.cli audit-market-aware-session-history-render \
  --history reports/session-history/history/market_aware_session_history.json \
  --history-report reports/session-history/history/market_aware_session_history_report.json \
  --history-audit-report reports/session-history/history-audit/market_aware_session_history_audit_report.json \
  --markdown reports/session-history/history-render/market_aware_session_history.md \
  --render-report reports/session-history/history-render/market_aware_session_history_render_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-render-audit
```

The `market-aware-session-history-render-audit-v1` report checks the five
input files' actual paths, sizes, SHA-256 values, versions, status fields,
time bounds, package literals, and `decision_ready=false` chain. It does not
re-render or interpret the history; stale or blocked state remains literal,
and `audit_ready` is only an artifact-integrity result.

## Build a market-aware session history manifest

Create a portable evidence manifest for the six Node37–40 history artifacts:

```bash
python -m a_share_ai.cli build-market-aware-session-history-manifest \
  --history reports/session-history/history/market_aware_session_history.json \
  --history-report reports/session-history/history/market_aware_session_history_report.json \
  --history-audit-report reports/session-history/history-audit/market_aware_session_history_audit_report.json \
  --markdown reports/session-history/history-render/market_aware_session_history.md \
  --render-report reports/session-history/history-render/market_aware_session_history_render_report.json \
  --render-audit-report reports/session-history/history-render-audit/market_aware_session_history_render_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-manifest
```

The `market-aware-session-history-manifest-v1` output records fixed artifact
roles, controlled relative paths, byte counts, SHA-256 values, readiness
states, and `decision_ready=false`. It requires the Node40 render audit to be
`audit_ready=true`, but does not rerun or reinterpret any upstream artifact.

## Audit a market-aware session history manifest

Independently verify the Node41 manifest, report, and all six declared
artifacts:

```bash
python -m a_share_ai.cli audit-market-aware-session-history-manifest \
  --manifest reports/session-history/history-manifest/market_aware_session_history_manifest.json \
  --manifest-report reports/session-history/history-manifest/market_aware_session_history_manifest_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-manifest-audit
```

The `market-aware-session-history-manifest-audit-v1` report recomputes actual
paths, byte counts, SHA-256 values, artifact roles, status fields, and
`decision_ready=false`. It verifies integrity only and preserves any stale or
blocked history state.

## Render a market-aware session history manifest

Render the Node41 manifest and Node42 audit into a deterministic Markdown
evidence table:

```bash
python -m a_share_ai.cli render-market-aware-session-history-manifest \
  --manifest reports/session-history/history-manifest/market_aware_session_history_manifest.json \
  --manifest-report reports/session-history/history-manifest/market_aware_session_history_manifest_report.json \
  --audit-report reports/session-history/history-manifest-audit/market_aware_session_history_manifest_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-manifest-render
```

The `market-aware-session-history-manifest-render-v1` output shows only
literal paths, byte counts, SHA-256 values, history bounds, readiness, and
issues. It is a read-only handoff view and always keeps
`decision_ready=false`.

## Audit a rendered market-aware session history manifest

Independently verify the Node43 Markdown/render report chain:

```bash
python -m a_share_ai.cli audit-market-aware-session-history-manifest-render \
  --manifest reports/session-history/history-manifest/market_aware_session_history_manifest.json \
  --manifest-report reports/session-history/history-manifest/market_aware_session_history_manifest_report.json \
  --manifest-audit-report reports/session-history/history-manifest-audit/market_aware_session_history_manifest_audit_report.json \
  --markdown reports/session-history/history-manifest-render/market_aware_session_history_manifest.md \
  --render-report reports/session-history/history-manifest-render/market_aware_session_history_manifest_render_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-manifest-render-audit
```

The `market-aware-session-history-manifest-render-audit-v1` report checks
paths, versions, canonical report hashes, SHA bindings, six artifact roles,
Markdown SHA, and readiness fields without re-rendering or re-auditing the
inputs. It always keeps `decision_ready=false`.

## Close the market-aware session history evidence chain

Generate a deterministic closure status from the Node41–44 reports:

```bash
python -m a_share_ai.cli build-market-aware-session-history-closure \
  --manifest reports/session-history/history-manifest/market_aware_session_history_manifest.json \
  --manifest-report reports/session-history/history-manifest/market_aware_session_history_manifest_report.json \
  --manifest-audit-report reports/session-history/history-manifest-audit/market_aware_session_history_manifest_audit_report.json \
  --render-report reports/session-history/history-manifest-render/market_aware_session_history_manifest_render_report.json \
  --render-audit-report reports/session-history/history-manifest-render-audit/market_aware_session_history_manifest_render_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure
```

The `market-aware-session-history-closure-v1` output records only the
evidence-chain status, time bounds, readiness flags, controlled input paths,
and SHA-256 values. It does not add research or trading semantics and always
keeps `decision_ready=false`.

## Render the market-aware session history closure

Render the Node45 closure as a deterministic, read-only Markdown view:

```bash
python -m a_share_ai.cli render-market-aware-session-history-closure \
  --closure reports/session-history/history-closure/market_aware_session_history_closure.json \
  --closure-report reports/session-history/history-closure/market_aware_session_history_closure_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure-render
```

This writes `market_aware_session_history_closure.md` and
`market_aware_session_history_closure_render_report.json`. The view only
shows literal closure status, time bounds, readiness flags, evidence hashes,
issues; the report also records the Markdown `markdown_sha256`. It never infers
research quality, market trends, returns, or
trading authorization. It always keeps `decision_ready=false`.

## Audit the market-aware session history closure render

Audit the Node46 Markdown and render report without re-rendering them:

```bash
python -m a_share_ai.cli audit-market-aware-session-history-closure-render \
  --closure reports/session-history/history-closure/market_aware_session_history_closure.json \
  --closure-report reports/session-history/history-closure/market_aware_session_history_closure_report.json \
  --markdown reports/session-history/history-closure-render/market_aware_session_history_closure.md \
  --render-report reports/session-history/history-closure-render/market_aware_session_history_closure_render_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure-render-audit
```

This writes `market_aware_session_history_closure_render_audit_report.json`.
It verifies the Node45/46 path, SHA, state, metadata, UTF-8, and Markdown
binding; it keeps `decision_ready=false` and does not infer a trading result.

## Build the market-aware session history closure admission

Create one deterministic read-only admission summary from the Node45–47
evidence chain:

```bash
python -m a_share_ai.cli build-market-aware-session-history-closure-admission \
  --closure reports/session-history/history-closure/market_aware_session_history_closure.json \
  --closure-report reports/session-history/history-closure/market_aware_session_history_closure_report.json \
  --markdown reports/session-history/history-closure-render/market_aware_session_history_closure.md \
  --render-report reports/session-history/history-closure-render/market_aware_session_history_closure_render_report.json \
  --render-audit-report reports/session-history/history-closure-render-audit/market_aware_session_history_closure_render_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure-admission
```

This writes `market_aware_session_history_closure_admission.json` and
`market_aware_session_history_closure_admission_report.json`. The admission
flag means only that the existing offline evidence chain passed its literal
gates; it is not a research conclusion, investment decision, or trading
authorization. It always keeps `decision_ready=false`.

## Audit the market-aware session history closure admission

Audit the Node48 admission and all of its declared evidence bindings:

```bash
python -m a_share_ai.cli audit-market-aware-session-history-closure-admission \
  --admission reports/session-history/history-closure-admission/market_aware_session_history_closure_admission.json \
  --admission-report reports/session-history/history-closure-admission/market_aware_session_history_closure_admission_report.json \
  --closure reports/session-history/history-closure/market_aware_session_history_closure.json \
  --closure-report reports/session-history/history-closure/market_aware_session_history_closure_report.json \
  --markdown reports/session-history/history-closure-render/market_aware_session_history_closure.md \
  --render-report reports/session-history/history-closure-render/market_aware_session_history_closure_render_report.json \
  --render-audit-report reports/session-history/history-closure-render-audit/market_aware_session_history_closure_render_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure-admission-audit
```

This writes `market_aware_session_history_closure_admission_audit_report.json`.
It is a read-only integrity audit and always keeps `decision_ready=false`.

## Render the market-aware session history closure admission

Render the Node48 admission and Node49 audit as a deterministic Markdown view:

```bash
python -m a_share_ai.cli render-market-aware-session-history-closure-admission \
  --admission reports/session-history/history-closure-admission/market_aware_session_history_closure_admission.json \
  --admission-report reports/session-history/history-closure-admission/market_aware_session_history_closure_admission_report.json \
  --admission-audit-report reports/session-history/history-closure-admission-audit/market_aware_session_history_closure_admission_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure-admission-render
```

This writes `market_aware_session_history_closure_admission.md` and its
`market_aware_session_history_closure_admission_render_report.json`. The view
preserves ready/stale/blocked status and always keeps `decision_ready=false`.

## Audit the market-aware session history closure admission render

Independently audit the Node50 Markdown, render report, and their Node48/49
bindings:

```bash
python -m a_share_ai.cli audit-market-aware-session-history-closure-admission-render \
  --admission reports/session-history/history-closure-admission/market_aware_session_history_closure_admission.json \
  --admission-report reports/session-history/history-closure-admission/market_aware_session_history_closure_admission_report.json \
  --admission-audit-report reports/session-history/history-closure-admission-audit/market_aware_session_history_closure_admission_audit_report.json \
  --markdown reports/session-history/history-closure-admission-render/market_aware_session_history_closure_admission.md \
  --render-report reports/session-history/history-closure-admission-render/market_aware_session_history_closure_admission_render_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure-admission-render-audit
```

This writes `market_aware_session_history_closure_admission_render_audit_report.json`.
It independently recomputes the Markdown SHA and always keeps
`decision_ready=false`.

## Build the market-aware session history final receipt

Build one deterministic JSON receipt from the Node48 admission, Node50 render,
and Node51 render audit:

```bash
python -m a_share_ai.cli build-market-aware-session-history-final-receipt \
  --admission reports/session-history/history-closure-admission/market_aware_session_history_closure_admission.json \
  --admission-report reports/session-history/history-closure-admission/market_aware_session_history_closure_admission_report.json \
  --render-report reports/session-history/history-closure-admission-render/market_aware_session_history_closure_admission_render_report.json \
  --render-audit-report reports/session-history/history-closure-admission-render-audit/market_aware_session_history_closure_admission_render_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/final-receipt
```

This writes `market_aware_session_history_final_receipt.json` and its report.
The receipt only summarizes evidence-chain status and always keeps
`decision_ready=false`.

## Compare two research releases

To inspect what changed between two completed single-stock research packages,
run the literal structural diff audit:

```bash
python -m a_share_ai.cli compare-research-releases \
  --previous-manifest reports/previous/release/research_release_manifest.json \
  --previous-report reports/previous/release/research_release_report.json \
  --current-manifest reports/current/release/research_release_manifest.json \
  --current-report reports/current/release/research_release_report.json \
  --previous-artifact-root reports/previous \
  --current-artifact-root reports/current \
  --output-dir reports/current/release-diff
```

This writes `research_release_diff.json` and
`research_release_diff_report.json` using
`research-release-diff-v1`. Claim IDs are classified as unchanged, added,
removed, or changed; changed claims report only literal differences in kind,
text, citation IDs, or observed dates. Evidence, artifact, risk, and unknown
changes are reported separately. The command rejects time reversal, symbol or
SHA mismatches, path escapes, incomplete reviews, unverified evidence, and
transaction fields. It never performs semantic good/bad analysis and always
keeps `decision_ready=false`.

## Run the read-only research receipt service

Node53 provides a small standard-library HTTP boundary for an already validated
Node52 final receipt. It reads only the receipt and its report, binds to
`127.0.0.1` by default, and never rebuilds the evidence chain or calls an
external provider:

```bash
python -m a_share_ai.cli serve-research-receipt \
  --receipt reports/session-history/final-receipt/market_aware_session_history_final_receipt.json \
  --receipt-report reports/session-history/final-receipt/market_aware_session_history_final_receipt_report.json \
  --artifact-root reports/session-history \
  --host 127.0.0.1 \
  --port 8765
```

The process refuses to listen when either input is outside `--artifact-root`,
has invalid JSON or self-hashes, has a mismatched receipt SHA, disagrees on
status/readiness metadata, or sets `decision_ready` to true. The read-only
routes are:

- `GET /healthz`: process and receipt are loaded; returns HTTP 200.
- `GET /readyz`: returns HTTP 200 only when `receipt_ready=true`, otherwise
  HTTP 503 for a valid stale or blocked receipt.
- `GET /v1/research/receipt`: returns a fixed summary without local paths or
  source files.

Only `127.0.0.1` and `::1` are accepted as hosts. This is an operator-facing
local service boundary, not an authenticated public deployment, database,
scheduler, trading API, or investment recommendation service. Every response
keeps `decision_ready=false`.
