# Phase 3：Baostock 免费日频行情适配器与原始捕获 MVP

## 目标

打通第一条真实但只读的历史日线链路：

`Baostock 响应 -> 标准 DailyBar -> replay-health -> audit-coverage -> 可回放原始证据`

## 范围

- 单股票、明确日期区间、日频查询；
- `600000.SH` / `000001.SZ` 到 Baostock `sh.600000` / `sz.000001` 的严格映射；
- 固定不复权 `adjustflag=3`；
- 不依赖 pandas 解析响应，按响应字段名映射并使用 `Decimal`；
- 保存 request、raw response、normalized JSONL 和 SHA-256 捕获报告；
- Baostock 作为可选依赖，基础离线安装不强制安装；
- provider error、空字段、异常响应和缺失依赖均 fail closed；
- 继续使用现有健康门和交易日历覆盖审计。

## 明确不包含

多数据源、自动重试、全市场扫描、实时/分钟/Tick/Level-2、技术指标、基本面、新闻、AI、数据库、定时任务、持仓和交易。

## 验收证据

- 39 项测试通过；
- 真实单股票历史区间烟测成功捕获 4 条记录；
- 捕获结果通过 `replay-health`，再通过 `audit-coverage`；
- 采集报告、原始响应和标准化输出均可追溯；
- 捕获报告不直接生成 `decision_ready=true`。
