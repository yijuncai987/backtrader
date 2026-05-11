import json

import pandas as pd
import pytest


pytest.importorskip("akshare")

from daily_a_share_value_dashboard import (  # noqa: E402
    ScreenConfig,
    build_html,
    demo_market_monitor,
    demo_screen,
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
