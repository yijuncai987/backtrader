import json

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
    fetch_price_history,
    monitor_date_window,
    monitor_records_for_html,
    normalize_spot_akshare_fallback,
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
    assert config.failed_item_retries == 1


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


def test_fetch_price_history_requests_forward_adjusted_prices(monkeypatch, tmp_path):
    calls = {}

    def fake_stock_zh_a_hist(**kwargs):
        calls.update(kwargs)
        return pd.DataFrame({"日期": ["2026-05-11"], "收盘": [10.0]})

    monkeypatch.setattr(dashboard.ak, "stock_zh_a_hist", fake_stock_zh_a_hist)
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

    monkeypatch.setattr(dashboard, "fetch_price_history", lambda symbol, config: qfq_prices)
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
