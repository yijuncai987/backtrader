# 本项目 AKShare 接口使用说明

最后整理日期：2026-05-07

这份文档只记录本项目实际用到的 AKShare 接口，目标是以后维护市场分析脚本时能快速判断：该调哪个接口、字段叫什么、单位是什么、哪些旧接口不要再优先使用。

## 项目里的使用范围

AKShare 只出现在仓库根目录的市场分析/绘图脚本里：

- `final_interactive_plot.py`：当前主版本，使用东方财富指数历史行情、东方财富两融账户统计、A 股指数通用历史行情。
- `daily_market_update.py`：检查环境并启动 `final_interactive_plot.py`。
- `interactive_plot.py`、`enhanced_plot.py`、`plot_sz_index.py`、`simple_plot.py`：较早实验脚本，部分还在使用旧数据源。

`backtrader/` 框架源码本身没有直接调用 AKShare。当前模式是：AKShare 拉取 Pandas `DataFrame`，脚本清洗日期/字段，再交给 Backtrader 或 Plotly。

本机 2026-05-07 检查到的 AKShare 版本：

```bash
python -c "import akshare as ak; print(ak.__version__)"
# 1.17.26
```

同日查看官网文档时，在线文档为 AKShare `1.18.60`，文档更新时间是 2026-05-02。本地版本明显偏旧；如果遇到接口不存在、字段不一致、数据缺失，先升级再排查业务逻辑：

```bash
pip install akshare --upgrade
```

官网安装说明目前要求 64 位系统、Python 3.9+，并建议使用前经常升级，因为 AKShare 接口依赖外部公开网站，网页和接口会变化。

## 推荐接口

### 1. 指数日线：`stock_zh_index_daily_em`

以后本项目获取 A 股主要指数日线，优先用这个东方财富接口。

```python
import akshare as ak

df = ak.stock_zh_index_daily_em(
    symbol="sh000001",
    start_date="19900101",
    end_date="20500101",
)
```

本项目用到的代码：

- `sh000001`：上证指数。
- `sh000300`：沪深 300。
- `sz399001`：深证成指。

当前文档字段：

- `date`
- `open`
- `close`
- `high`
- `low`
- `volume`
- `amount`

处理要点：

- `date` 转为 `pd.to_datetime`，设为索引后按日期升序排序。
- `amount` 在当前图表里按成交额使用，展示成“亿元”时除以 `100000000`。
- 这个接口的 `symbol` 需要带市场前缀：`sh`、`sz`、`bj`、`csi`。
- 它比旧的新浪接口更适合当前主脚本，因为多了 `amount`，可以计算沪深 300 成交额占比。

标准清洗写法：

```python
df = ak.stock_zh_index_daily_em(symbol="sh000001")
df["date"] = pd.to_datetime(df["date"])
df = df.set_index("date").sort_index()
```

### 2. 旧指数日线：`stock_zh_index_daily`

旧脚本里常见：

```python
df = ak.stock_zh_index_daily(symbol="sh000001")
```

当前文档字段：

- `date`
- `open`
- `high`
- `low`
- `close`
- `volume`

本项目里的限制：

- 这是新浪数据源的指数日线接口。
- 没有 `amount` 字段，不适合当前 `final_interactive_plot.py` 的成交额占比计算。
- 本地 AKShare docstring 提醒大量抓取可能导致 IP 被封，开发时不要高频重复调用。

建议：只在简单 Backtrader OHLCV 示例里保留；新代码优先迁移到 `stock_zh_index_daily_em`。

### 3. 两融账户统计：`stock_margin_account_info`

当前主脚本获取融资余额时应优先用这个接口。

```python
df = ak.stock_margin_account_info()
```

当前文档字段：

- `日期`
- `融资余额`
- `融券余额`
- `融资买入额`
- `融券卖出额`
- `证券公司数量`
- `营业部数量`
- `个人投资者数量`
- `机构投资者数量`
- `参与交易的投资者数量`
- `有融资融券负债的投资者数量`
- `担保物总价值`
- `平均维持担保比例`

单位：

- `融资余额`、`融券余额`、`融资买入额`、`融券卖出额`、`担保物总价值`：已经是“亿”。
- `平均维持担保比例`：百分比。
- 投资者数量、证券公司数量、营业部数量不要混到金额计算里。

项目处理方式：

```python
df["日期"] = pd.to_datetime(df["日期"])
df = df.sort_values("日期")
df["融资融券余额"] = df["融资余额"] + df["融券余额"]
df["融资余额_亿元"] = df["融资余额"]
```

当前图表只画 `融资余额`，并额外计算近似 5 个交易日变化率。脚本现在用 recent window 中向前 7 行的值近似 5 个交易日：

```python
change_pct = (current_balance - past_balance) / past_balance * 100
```

注意：这个接口的金额已经是“亿”，不要再除以 `100000000`。

### 4. 旧上交所两融汇总：`stock_margin_sse`

旧脚本里用过：

```python
df = ak.stock_margin_sse(start_date="20010106", end_date="20230922")
```

当前文档字段：

- `信用交易日期`
- `融资余额`
- `融资买入额`
- `融券余量`
- `融券余量金额`
- `融券卖出量`
- `融资融券余额`

单位：

- 金额字段是“元”，不是“亿”。

为什么不再作为主数据源：

- 只覆盖上海证券交易所，不是全市场两融数据。
- 本机 AKShare `1.17.26` 中该函数默认 `end_date` 是 `20230922`；仓库 README 也记录过这个旧源对当前需求已经偏旧。
- 如果必须使用它，务必显式传日期范围，并先把金额字段换算成“亿”，再和 `stock_margin_account_info` 对比。

### 5. A 股指数通用历史行情：`index_zh_a_hist`

当前主脚本用它取北交所代理数据：

```python
df = ak.index_zh_a_hist(
    symbol="899050",
    period="daily",
    start_date="20240101",
    end_date="20251231",
)
```

当前项目含义：

- `899050`：北证 50。脚本把它作为北交所成交额代理，加入全市场成交额分母。

当前文档字段：

- `日期`
- `开盘`
- `收盘`
- `最高`
- `最低`
- `成交量`
- `成交额`
- `振幅`
- `涨跌幅`
- `涨跌额`
- `换手率`

单位：

- `成交量`：手。
- `成交额`：元。
- `振幅`、`涨跌幅`、`换手率`：百分比。

处理要点：

- 这个通用接口的 `symbol` 不带市场前缀。
- 和 `stock_zh_index_daily_em` 合并计算时，把 `日期` 转成 datetime，把 `成交额` 统一重命名为 `amount`。

```python
df["date"] = pd.to_datetime(df["日期"])
df["amount"] = df["成交额"]
df = df.set_index("date").sort_index()
```

## 当前主脚本的数据流程

`final_interactive_plot.py` 的核心流程：

1. 用 `stock_zh_index_daily_em("sh000001")` 获取上证指数。
2. 用 `close` 计算 `MA5` 和 `MA250`。为了年线有足够数据，先取约 500 个自然日，再只展示最近 90 个自然日。
3. 用 `stock_margin_account_info()` 获取全市场两融账户统计。
4. 绘制 `融资余额`，单位直接是“亿”，并计算近似 5 个交易日变化率。
5. 获取沪深 300、上证指数、深证成指、北证 50 代理数据。
6. 计算沪深 300 成交额占比：

```python
total_amount = sh_amount + sz_amount + bj_amount
ratio = hs300_amount / total_amount * 100
```

7. 只保留 `15 <= ratio <= 75` 的结果，避免明显异常值进入图表。

## 以后新增代码时建议封装

不要在每个脚本里重复写字段识别逻辑。建议先抽出这种小函数，抓完数据立刻校验字段：

```python
import akshare as ak
import pandas as pd


def get_index_daily_em(symbol: str, start: str = "19900101", end: str = "20500101") -> pd.DataFrame:
    df = ak.stock_zh_index_daily_em(symbol=symbol, start_date=start, end_date=end)
    if df.empty:
        raise ValueError(f"No index data returned for {symbol}")

    required = {"date", "open", "close", "high", "low", "volume", "amount"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"{symbol} missing columns: {sorted(missing)}; got {df.columns.tolist()}")

    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def get_margin_account_info() -> pd.DataFrame:
    df = ak.stock_margin_account_info()
    if df.empty:
        raise ValueError("No margin account data returned")

    required = {"日期", "融资余额", "融券余额"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Margin data missing columns: {sorted(missing)}; got {df.columns.tolist()}")

    df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期")
    df["融资融券余额"] = df["融资余额"] + df["融券余额"]
    return df
```

关键习惯：AKShare 依赖外部网站，接口字段和可用性可能变化，所以抓取后第一步就要校验 `df.empty` 和 `df.columns`。

## 排查清单

- `AttributeError: module 'akshare' has no attribute ...`：先看 `ak.__version__`，升级 AKShare 后再试。
- 返回空 `DataFrame`：先重试，再检查网络/代理，最后用官网示例参数单独测试该接口。
- 中文字段 `KeyError`：抓取后立即打印 `df.columns.tolist()`；AKShare 有些接口返回英文列，有些返回中文列。
- 图表单位不对：先确认接口来源。`stock_margin_account_info` 的金额已经是“亿”；指数 `amount` 和 `index_zh_a_hist` 的 `成交额` 在本项目里按“元”处理。
- 数据看起来不新：比较每个数据集的最大日期和当前日期。两融数据常见 T+1，周末或非交易日不会更新。
- 重复调用超时或失败：开发时先本地缓存原始 `DataFrame`，避免反复抓取，尤其不要高频调用旧的新浪接口。

## 官方参考

- AKShare 项目概览：https://akshare.akfamily.xyz/introduction.html
- AKShare 安装/升级说明：https://akshare.akfamily.xyz/installation.html
- 指数数据文档，包含 `stock_zh_index_daily`、`stock_zh_index_daily_em`、`index_zh_a_hist`：https://akshare.akfamily.xyz/data/index/index.html
- 股票两融数据文档，包含 `stock_margin_account_info`、`stock_margin_sse`：https://akshare.akfamily.xyz/data/stock/stock.html
