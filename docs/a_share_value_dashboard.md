# A 股低估高息筛选看板

脚本：`daily_a_share_value_dashboard.py`

这个看板用于每天筛选 A 股中同时满足以下条件的股票：

1. 当天股价低于半年线 10% 以上；
2. 股息率大于 3%。

市盈率、市净率和对应历史分位会展示在表格中，但不再参与筛选。

## 当前口径

- 半年线：最近 120 个交易日前复权收盘价均线，避免除权、送转、配股造成价格断层。
- 半年线乖离率：`现价 / 前复权半年线 - 1`，例如 `-12%` 表示现价比半年线低 12%。
- 股息率：巨潮资讯近 365 天已实施派息合计 / 当前股价。巨潮 `派息比例` 通常是 `10派X元`，脚本按 `X / 10` 折算每股派息。
- PE/PB 十年分位：东方财富估值分析历史序列中，当前 PE 或 PB 所在的历史百分位；接口返回历史不足十年时，使用可取得的完整历史。
- 所属行业：东方财富个股资料中的 `行业` 字段。
- 沪深300成交占比：`沪深300成交量 / (上证指数成交量 + 深证综指成交量 + 北交所个股成交量汇总)`，展示最近 90 个自然日内的交易日曲线。
- 融资余额增长率：`两市融资余额 / 前一交易日两市融资余额 - 1`，两市融资余额按上海和深圳融资余额合计计算，展示最近 90 个自然日内的交易日曲线。

## 数据源

脚本通过 AKShare 或同源公开接口获取数据：

- 东方财富：沪深京 A 股实时行情、个股估值分析历史序列、个股行业。
- 巨潮资讯：个股历史分红。
- AKShare 指数与融资融券接口：沪深300、上证指数、深证综指、北交所个股历史成交量，以及沪深两市融资余额。

## 运行方式

生成真实看板：

```bash
python daily_a_share_value_dashboard.py
```

输出文件：

- `a_share_value_dashboard.html`：可对外展示的静态 HTML 看板。
- `a_share_value_dashboard.csv`：同一批入选股票的 CSV 明细。
- `a_share_value_dashboard_market_monitor.csv`：最近 90 天市场监测数据，包含沪深300成交占比和融资余额增长率。

生成后自动打开浏览器：

```bash
python daily_a_share_value_dashboard.py --open
```

小范围验证：

```bash
python daily_a_share_value_dashboard.py --limit 50 --output a_share_value_dashboard_limit50.html
```

只验证页面样式，不访问外部接口：

```bash
python daily_a_share_value_dashboard.py --demo --output a_share_value_dashboard_demo.html
```

## 本地增量数据

脚本现在使用“本地历史库 + 每日增量”的方式运行：

```text
data/a_share/
  spot/                 # 每天一次的全 A 行情快照
    20260507.csv
  value_history/         # 每只股票一份长期价格/PE/PB 历史
    000001.csv
    000002.csv
  dividends/             # 个股分红历史，按需缓存
    000001.csv
  industry/              # 个股行业，长期缓存
    000001.csv
  screening_results/     # 每天筛选结果归档
    20260507.csv
```

首次遇到一只股票时，脚本会用东方财富估值分析接口初始化它的历史库。之后每天运行时，只从当日全 A 行情里追加当天的股价、PE、PB、市值，不再每天重新下载该股票全量历史。

如果确实要重建历史库，可以运行：

```bash
python daily_a_share_value_dashboard.py --rebuild-history
```

## 性能说明

首次全市场运行会比较慢，因为每只股票都要初始化历史库。脚本还会保留运行缓存到：

```text
cache/a_share_value_dashboard/
```

后续每天运行只需要拉取当天全 A 行情，并把当天数据追加到 `data/a_share/value_history/`。前复权价格历史也会优先使用本地 `data/a_share/price_history_qfq/`，并用当天行情价增量补齐最新一行；只有新股票、缺失历史库的股票、或显式 `--rebuild-history` 时才会重新拉取全量历史。为了减少慢接口调用，脚本只有在价格条件达标后才请求股息数据。

可调参数：

```bash
python daily_a_share_value_dashboard.py ^
  --data-dir data/a_share ^
  --price-workers 4 ^
  --valuation-workers 1 ^
  --spot-workers 4 ^
  --request-pause 0.5 ^
  --request-retries 3 ^
  --failed-item-retries 1 ^
  --batch-timeout-seconds 12600 ^
  --price-window 120 ^
  --max-below-ma-pct -10 ^
  --min-dividend-yield-pct 3 ^
  --monitor-days 90 ^
  --max-percentile 30
```

`--max-percentile` 当前只影响页面展示的分位计算阈值说明兼容项，不再作为筛选条件。

默认配置已经偏向“慢但稳”：降低并发、每次外部请求前暂停、接口失败后自动重试，并在批量结束后对失败股票额外补跑一轮。批量阶段达到 `--batch-timeout-seconds` 后会跳过剩余个别项，继续生成看板，避免整次任务卡死。仍未补上的股票会写入：

```text
data/a_share/failures/YYYYMMDD.csv
```

如果东方财富分页行情接口中途断开，脚本会自动切换到 AKShare 的备用实时行情接口继续生成当天快照；备用接口也不可用时，会使用本地最近一次行情快照兜底。备用行情源字段更少，缺少当天 PE/PB 时会沿用本地历史库里最近一个非空估值用于展示和分位计算。

价格筛选和估值展示使用不同数据源：半年线与乖离率只使用 `stock_zh_a_hist(adjust="qfq")` 的前复权价格；PE/PB、总市值等估值字段继续来自东方财富估值历史。不要用 `data/a_share/value_history/` 的未复权收盘价计算技术指标。

如果临时想进一步放慢，可以继续降低并发或增加暂停：

```bash
python daily_a_share_value_dashboard.py --price-workers 2 --valuation-workers 1 --request-pause 1
```

## 已验证

2026-05-11 本地验证结果：

- `python -m py_compile daily_a_share_value_dashboard.py`：通过。
- `python -m pytest tests\test_daily_a_share_value_dashboard.py -q`：10 个测试通过。
- `python daily_a_share_value_dashboard.py --demo --output %TEMP%\a_share_value_dashboard_smoke.html`：成功生成演示面板。

2026-05-08 本地验证结果：

- `python -m py_compile daily_a_share_value_dashboard.py`：通过。
- `python daily_a_share_value_dashboard.py --demo --output a_share_value_dashboard_demo.html`：成功生成演示面板，并写出 `a_share_value_dashboard_demo_market_monitor.csv`。

历史验证记录：

- 2026-05-07 `python daily_a_share_value_dashboard.py --demo --output a_share_value_dashboard_demo.html`：成功生成演示面板。
- `python daily_a_share_value_dashboard.py --limit 1 --output a_share_value_dashboard_limit1.html`：真实接口链路成功。
- `python daily_a_share_value_dashboard.py --limit 50 --output a_share_value_dashboard_limit50.html`：真实局部筛选成功。
- 已把旧缓存中的 5495 只股票历史迁移到 `data/a_share/value_history/`。
- `python daily_a_share_value_dashboard.py --limit 5 --output a_share_value_dashboard_incremental_test.html`：增量历史库读取成功，未重新初始化历史。

注意：全市场首次运行耗时取决于网络和公开接口稳定性，不建议把并发调得过高。
