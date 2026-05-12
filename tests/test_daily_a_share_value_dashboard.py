import json
from datetime import date as real_date

import pandas as pd
import pytest


pytest.importorskip("akshare")

import daily_a_share_value_dashboard as dashboard  # noqa: E402
from daily_a_share_value_dashboard import (  # noqa: E402
    ScreenConfig,
    build_html,
    demo_market_monitor,
    demo_screen,
    ensure_cache_dir,
    ensure_data_dir,
    fill_latest_price_from_previous_close,
    fetch_price_history,
    monitor_date_window,
    monitor_records_for_html,
    normalize_spot_akshare_fallback,
    write_dashboard,
)


def test_demo_market_monitor_uses_default_90_day_window():
    config = ScreenConfig()

    start, end = monitor_date_window(config)
    monitor, diagnostics = demo_market_monitor(config)

    assert (end - start).days + 1 == 90
    assert diagnostics["market_monitor_count"] == len(monitor)
    assert not monitor.empty
    assert "沪深300成交占比" in monitor.columns
    assert "融资余额增长率" in monitor.columns
    assert monitor["日期"].min() >= start.isoformat()
    assert monitor["日期"].max() <= end.isoformat()


def test_monitor_records_for_html_serializes_chart_metrics():
    config = ScreenConfig()
    monitor, _ = demo_market_monitor(config)

    records = json.loads(monitor_records_for_html(monitor))

    assert len(records) == len(monitor)
    assert records[0]["沪深300成交占比"] is not None
    assert any(record["融资余额增长率"] is not None for record in records)


def test_default_live_fetching_prefers_stability():
    config = ScreenConfig()

    assert config.price_workers == 4
    assert config.valuation_workers == 1
    assert config.request_pause == 0.5
    assert config.request_retries == 3
    assert config.failed_item_retries == 0
    assert config.batch_timeout_seconds == 7200.0
    assert config.price_prescreen_margin_pct == 0.0


def test_current_market_data_date_uses_previous_day_before_close():
    assert dashboard.current_market_data_date(
        dashboard.datetime(2026, 5, 13, 1, 0)
    ) == real_date(2026, 5, 12)
    assert dashboard.current_market_data_date(
        dashboard.datetime(2026, 5, 12, 15, 30)
    ) == real_date(2026, 5, 12)


def test_normalize_spot_akshare_fallback_strips_market_prefixes():
    raw = pd.DataFrame(
        [
            {"代码": "bj920000", "名称": "安徽凤凰", "最新价": "16.24", "涨跌幅": "-1.63"},
            {"代码": "sh600000", "名称": "浦发银行", "最新价": "8.12", "涨跌幅": "0.1"},
            {"代码": "sz000001", "名称": "平安银行", "最新价": "9.82", "涨跌幅": "0.2"},
        ]
    )

    normalized = normalize_spot_akshare_fallback(raw)

    assert normalized["代码"].tolist() == ["920000", "600000", "000001"]
    assert normalized["最新价"].tolist() == [16.24, 8.12, 9.82]


def test_fill_latest_price_from_previous_close_keeps_premarket_rows():
    raw = pd.DataFrame(
        [
            {"代码": "600000", "名称": "浦发银行", "最新价": 0, "昨收": 8.88},
            {"代码": "000001", "名称": "平安银行", "最新价": None, "昨收": 9.91},
            {"代码": "300501", "名称": "海顺新材", "最新价": 8.9, "昨收": 8.7},
            {"代码": "688001", "名称": "华兴源创", "最新价": 0, "昨收": None},
        ]
    )

    filled = fill_latest_price_from_previous_close(raw)
    tradable = filled[pd.to_numeric(filled["最新价"], errors="coerce") > 0]

    assert filled["最新价"].tolist()[:3] == [8.88, 9.91, 8.9]
    assert tradable["代码"].tolist() == ["600000", "000001", "300501"]


def test_read_latest_spot_snapshot_skips_tiny_partial_files(tmp_path):
    data_dir = tmp_path / "data"
    ensure_data_dir(data_dir)
    small = pd.DataFrame([{"代码": "000001", "名称": "坏快照", "最新价": 1.0}])
    usable = pd.DataFrame(
        {
            "代码": [f"{idx:06d}" for idx in range(1000)],
            "名称": [f"股票{idx}" for idx in range(1000)],
            "最新价": [1.0] * 1000,
        }
    )
    small.to_csv(data_dir / "spot" / "20260512.csv", index=False, encoding="utf-8-sig")
    usable.to_csv(data_dir / "spot" / "20260511.csv", index=False, encoding="utf-8-sig")

    snapshot, path = dashboard.read_latest_spot_snapshot(data_dir)

    assert path.name == "20260511.csv"
    assert len(snapshot) == 1000


def test_read_csv_normalizes_leading_zero_codes(tmp_path):
    path = tmp_path / "codes.csv"
    path.write_text("代码,名称\n000596,古井贡酒\nbj920000,安徽凤凰\n", encoding="utf-8-sig")

    df = dashboard.read_csv(path)

    assert df["代码"].tolist() == ["000596", "920000"]


def test_industry_for_symbol_accepts_numeric_like_codes(tmp_path):
    config = ScreenConfig(cache_dir=tmp_path / "cache", data_dir=tmp_path / "data", request_pause=0)
    ensure_cache_dir(config.cache_dir)
    ensure_data_dir(config.data_dir)
    (config.data_dir / "industry" / "000596.csv").write_text(
        "所属行业\n白酒Ⅱ\n",
        encoding="utf-8-sig",
    )

    result = dashboard.industry_for_symbol("596", config)

    assert result["代码"] == "000596"
    assert result["所属行业"] == "白酒Ⅱ"


def test_industry_for_symbol_falls_back_to_cninfo(monkeypatch, tmp_path):
    def fail_individual_info(**kwargs):
        raise RuntimeError("东方财富断开")

    def fake_industry_change(**kwargs):
        return pd.DataFrame(
            [
                {
                    "证券代码": "300641",
                    "分类标准": "申银万国行业分类标准",
                    "行业大类": "其他化学制品",
                    "行业中类": "化学制品",
                    "行业次类": "化工",
                    "行业门类": "原材料",
                    "变更日期": "2021-07-30",
                }
            ]
        )

    monkeypatch.setattr(dashboard.ak, "stock_individual_info_em", fail_individual_info)
    monkeypatch.setattr(dashboard.ak, "stock_industry_change_cninfo", fake_industry_change)
    config = ScreenConfig(
        cache_dir=tmp_path / "cache",
        data_dir=tmp_path / "data",
        request_pause=0,
        request_retries=0,
    )
    ensure_cache_dir(config.cache_dir)
    ensure_data_dir(config.data_dir)

    result = dashboard.industry_for_symbol("300641", config)

    assert result["所属行业"] == "其他化学制品"
    assert result["行业错误"] == ""
    assert dashboard.read_csv(config.data_dir / "industry" / "300641.csv")["所属行业"].iloc[0] == "其他化学制品"


def test_write_dashboard_preserves_existing_files_when_new_result_is_empty(tmp_path):
    output = tmp_path / "index.html"
    csv_output = tmp_path / "index.csv"
    output.write_text("old html", encoding="utf-8")
    csv_output.write_text("old csv", encoding="utf-8")
    config = ScreenConfig(output=output)

    write_dashboard(pd.DataFrame(), {"universe_count": 5000}, config)

    assert output.read_text(encoding="utf-8") == "old html"
    assert csv_output.read_text(encoding="utf-8") == "old csv"


def test_write_dashboard_preserves_existing_files_when_price_fetch_mostly_fails(tmp_path):
    output = tmp_path / "index.html"
    csv_output = tmp_path / "index.csv"
    output.write_text("old html", encoding="utf-8")
    csv_output.write_text("old csv", encoding="utf-8")
    config = ScreenConfig(output=output)
    new_result = pd.DataFrame([{"代码": "000501", "名称": "武商集团"}])

    write_dashboard(
        new_result,
        {
            "universe_count": 5000,
            "price_prescreen_candidate_count": 849,
            "price_error_count": 809,
        },
        config,
    )

    assert output.read_text(encoding="utf-8") == "old html"
    assert csv_output.read_text(encoding="utf-8") == "old csv"


def test_fetch_price_history_requests_forward_adjusted_prices(monkeypatch, tmp_path):
    calls = {}

    def fake_stock_zh_a_daily(**kwargs):
        calls.update(kwargs)
        return pd.DataFrame({"date": ["2026-05-11"], "close": [10.0]})

    monkeypatch.setattr(dashboard.ak, "stock_zh_a_daily", fake_stock_zh_a_daily)
    config = ScreenConfig(
        cache_dir=tmp_path / "cache",
        data_dir=tmp_path / "data",
        request_pause=0,
        request_retries=0,
    )
    ensure_cache_dir(config.cache_dir)
    ensure_data_dir(config.data_dir)

    fetch_price_history("300501", config)

    assert calls["adjust"] == "qfq"
    assert calls["symbol"] == "sz300501"


def test_fetch_price_history_falls_back_to_tencent_when_sina_fails(monkeypatch, tmp_path):
    calls = {}

    def fail_stock_zh_a_daily(**kwargs):
        raise RuntimeError("新浪断开")

    def fake_stock_zh_a_hist_tx(**kwargs):
        calls.update(kwargs)
        return pd.DataFrame({"date": ["2026-05-11"], "close": [10.0]})

    def fail_stock_zh_a_hist(**kwargs):
        pytest.fail("Tencent fallback should avoid EastMoney when it succeeds")

    monkeypatch.setattr(dashboard.ak, "stock_zh_a_daily", fail_stock_zh_a_daily)
    monkeypatch.setattr(dashboard.ak, "stock_zh_a_hist_tx", fake_stock_zh_a_hist_tx)
    monkeypatch.setattr(dashboard.ak, "stock_zh_a_hist", fail_stock_zh_a_hist)
    config = ScreenConfig(
        cache_dir=tmp_path / "cache",
        data_dir=tmp_path / "data",
        request_pause=0,
        request_retries=0,
    )
    ensure_cache_dir(config.cache_dir)
    ensure_data_dir(config.data_dir)

    result = fetch_price_history("300501", config)

    assert calls["adjust"] == "qfq"
    assert calls["symbol"] == "sz300501"
    assert result["close"].iloc[-1] == 10.0


def test_local_price_prescreen_uses_cached_value_history(tmp_path):
    config = ScreenConfig(
        cache_dir=tmp_path / "cache",
        data_dir=tmp_path / "data",
        price_window=3,
        max_below_ma_pct=-10.0,
    )
    ensure_cache_dir(config.cache_dir)
    ensure_data_dir(config.data_dir)
    path = config.data_dir / "value_history" / "300501.csv"
    history = pd.DataFrame(
        {
            "数据日期": ["2026-05-09", "2026-05-10", "2026-05-11"],
            "当日收盘价": [10.0, 10.0, 10.0],
        }
    )
    history.to_csv(path, index=False, encoding="utf-8-sig")

    result = dashboard.local_price_prescreen_metrics(
        pd.Series({"代码": "300501", "名称": "海顺新材", "最新价": 8.5}),
        config,
    )

    assert result["价格预筛达标"] is True
    assert result["本地半年线"] == pytest.approx(9.5)
    assert result["本地半年线乖离率"] == pytest.approx(-10.526315789)
    assert not (config.data_dir / "price_history_qfq" / "300501.csv").exists()


def test_incremental_value_history_extends_missing_live_valuation(monkeypatch, tmp_path):
    class FixedDate(real_date):
        @classmethod
        def today(cls):
            return cls(2026, 5, 12)

    monkeypatch.setattr(dashboard, "date", FixedDate)
    monkeypatch.setattr(dashboard, "current_market_data_date", lambda: real_date(2026, 5, 12))
    config = ScreenConfig(cache_dir=tmp_path / "cache", data_dir=tmp_path / "data")
    ensure_cache_dir(config.cache_dir)
    ensure_data_dir(config.data_dir)
    path = config.data_dir / "value_history" / "000596.csv"
    pd.DataFrame(
        {
            "数据日期": ["2026-05-08", "2026-05-11"],
            "当日收盘价": [106.36, 104.97],
            "总市值": [56_221_900_000.0, None],
            "PE(TTM)": [19.89, None],
            "市净率": [2.15, None],
        }
    ).to_csv(path, index=False, encoding="utf-8-sig")

    result = dashboard.fetch_incremental_value_history(
        pd.Series({"代码": 596, "名称": "古井贡酒", "最新价": 102.54}),
        config,
    )
    latest = result.iloc[-1]

    assert latest["数据日期"] == "2026-05-12"
    assert latest["PE(TTM)"] == pytest.approx(19.89 * 102.54 / 106.36)
    assert latest["市净率"] == pytest.approx(2.15 * 102.54 / 106.36)
    assert latest["总市值"] == pytest.approx(56_221_900_000.0 * 102.54 / 106.36)


def test_fetch_price_history_uses_local_history_when_refreshing(monkeypatch, tmp_path):
    def fail_stock_zh_a_hist(**kwargs):
        pytest.fail("local price history should avoid a full external refresh")

    monkeypatch.setattr(dashboard.ak, "stock_zh_a_hist", fail_stock_zh_a_hist)
    config = ScreenConfig(
        cache_dir=tmp_path / "cache",
        data_dir=tmp_path / "data",
        refresh=True,
    )
    ensure_cache_dir(config.cache_dir)
    ensure_data_dir(config.data_dir)
    path = config.data_dir / "price_history_qfq" / "300501.csv"
    history = pd.DataFrame(
        {
            "日期": pd.date_range(end=real_date.today(), periods=120, freq="D").astype(str),
            "收盘": [10.0] * 120,
        }
    )
    history.to_csv(path, index=False, encoding="utf-8-sig")

    result = fetch_price_history("300501", config)

    assert len(result) == 120
    assert result["收盘"].iloc[-1] == 10.0


def test_fetch_price_history_appends_spot_price_to_local_history(monkeypatch, tmp_path):
    class FixedDate(real_date):
        @classmethod
        def today(cls):
            return cls(2026, 5, 12)

    def fail_stock_zh_a_hist(**kwargs):
        pytest.fail("stale local price history should be updated incrementally from spot")

    monkeypatch.setattr(dashboard, "date", FixedDate)
    monkeypatch.setattr(dashboard, "current_market_data_date", lambda: real_date(2026, 5, 12))
    monkeypatch.setattr(dashboard.ak, "stock_zh_a_hist", fail_stock_zh_a_hist)
    config = ScreenConfig(
        cache_dir=tmp_path / "cache",
        data_dir=tmp_path / "data",
        refresh=True,
    )
    ensure_cache_dir(config.cache_dir)
    ensure_data_dir(config.data_dir)
    path = config.data_dir / "price_history_qfq" / "300501.csv"
    history = pd.DataFrame(
        {
            "日期": pd.date_range(end="2026-05-11", periods=120, freq="B").astype(str),
            "收盘": [10.0] * 120,
        }
    )
    history.to_csv(path, index=False, encoding="utf-8-sig")

    result = fetch_price_history("300501", config, latest_price=8.9)

    assert result["日期"].iloc[-1] == "2026-05-12"
    assert result["收盘"].iloc[-1] == 8.9


def test_price_valuation_metrics_does_not_use_unadjusted_value_close(monkeypatch):
    dates = pd.date_range("2026-01-01", periods=120, freq="B")
    qfq_prices = pd.DataFrame({"日期": dates.astype(str), "收盘": [10.0] * 120})
    unadjusted_value_history = pd.DataFrame(
        {
            "数据日期": dates.astype(str),
            "当日收盘价": [20.0] * 120,
            "PE(TTM)": [12.0] * 120,
            "市净率": [1.1] * 120,
            "总市值": [100_000_000.0] * 120,
        }
    )

    monkeypatch.setattr(dashboard, "fetch_price_history", lambda symbol, config, latest_price=None: qfq_prices)
    monkeypatch.setattr(
        dashboard,
        "fetch_incremental_value_history",
        lambda row, config: unadjusted_value_history,
    )

    result = dashboard.price_valuation_metrics(
        pd.Series({"代码": "300501", "名称": "海顺新材", "最新价": 8.9}),
        ScreenConfig(request_pause=0, request_retries=0),
    )

    assert result["半年线"] == 10.0
    assert result["半年线乖离率"] == pytest.approx(-11.0)
    assert result["价格达标"] is True


def test_build_html_separates_price_errors_from_valuation_gaps():
    config = ScreenConfig()
    stocks, diagnostics = demo_screen(config)
    monitor, monitor_diagnostics = demo_market_monitor(config)
    diagnostics.update(monitor_diagnostics)
    diagnostics.update(
        {
            "price_error_count": 2,
            "history_short_count": 1,
            "valuation_incomplete_count": 3,
            "dividend_error_count": 0,
            "industry_error_count": 0,
            "price_retry_count": 4,
        }
    )

    html = build_html(stocks, diagnostics, config, monitor)

    assert "价格失败：2" in html
    assert "历史不足：1" in html
    assert "估值缺失：3" in html
    assert "重试股票：4" in html


def test_build_html_contains_market_monitor_charts():
    config = ScreenConfig()
    stocks, diagnostics = demo_screen(config)
    monitor, monitor_diagnostics = demo_market_monitor(config)
    diagnostics.update(monitor_diagnostics)

    html = build_html(stocks, diagnostics, config, monitor)

    assert "市场监测" in html
    assert "最近90天市场监测" in html
    assert "volume-ratio-chart" in html
    assert "margin-change-chart" in html
    assert "沪深300成交占比" in html
    assert "融资余额增长率" in html
