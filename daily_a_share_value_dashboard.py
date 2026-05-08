#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily A-share value dashboard.

Screen A-share stocks that simultaneously satisfy:
- latest price is at least 10% below the 120-trading-day moving average;
- dividend yield is greater than 3%;
PE and PB are calculated for display only.

The default run uses AKShare live data and writes a self-contained HTML panel.
Use --demo to verify the dashboard rendering without external data.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import time
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

import akshare as ak
import pandas as pd
import requests


DEFAULT_OUTPUT = "a_share_value_dashboard.html"
DEFAULT_CACHE_DIR = Path("cache") / "a_share_value_dashboard"
DEFAULT_DATA_DIR = Path("data") / "a_share"


@dataclass
class ScreenConfig:
    price_window: int = 120
    max_below_ma_pct: float = -10.0
    min_dividend_yield_pct: float = 3.0
    valuation_years: int = 10
    max_percentile: float = 30.0
    price_workers: int = 8
    valuation_workers: int = 3
    industry_workers: int = 6
    spot_workers: int = 8
    spot_timeout: float = 60.0
    dividend_lookback_days: int = 365
    monitor_days: int = 90
    request_pause: float = 0.0
    limit: int | None = None
    refresh: bool = False
    rebuild_history: bool = False
    cache_dir: Path = DEFAULT_CACHE_DIR
    data_dir: Path = DEFAULT_DATA_DIR
    output: Path = Path(DEFAULT_OUTPUT)
    open_browser: bool = False


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def ensure_cache_dir(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "price").mkdir(exist_ok=True)
    (cache_dir / "valuation").mkdir(exist_ok=True)
    (cache_dir / "value").mkdir(exist_ok=True)
    (cache_dir / "industry").mkdir(exist_ok=True)
    (cache_dir / "market_monitor").mkdir(exist_ok=True)


def ensure_data_dir(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "spot").mkdir(exist_ok=True)
    (data_dir / "value_history").mkdir(exist_ok=True)
    (data_dir / "dividends").mkdir(exist_ok=True)
    (data_dir / "industry").mkdir(exist_ok=True)
    (data_dir / "screening_results").mkdir(exist_ok=True)
    (data_dir / "market_monitor").mkdir(exist_ok=True)


def today_stamp() -> str:
    return date.today().strftime("%Y%m%d")


def is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime).date()
    return modified == date.today()


def read_csv_cache(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return None


def write_csv_cache(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def read_csv(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return None


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
        if value in {"", "-", "--", "None", "nan"}:
            return None
    try:
        num = float(value)
    except Exception:
        return None
    if math.isnan(num) or math.isinf(num):
        return None
    return num


def pick_column(columns: Iterable[str], candidates: Iterable[str]) -> str | None:
    normalized = {str(col).lower().replace(" ", "").replace("_", ""): col for col in columns}
    for candidate in candidates:
        key = candidate.lower().replace(" ", "").replace("_", "")
        if key in normalized:
            return normalized[key]

    # Substring fallback for AKShare field-name changes.
    for candidate in candidates:
        c = candidate.lower().replace(" ", "").replace("_", "")
        for key, original in normalized.items():
            if c and c in key:
                return original
    return None


def normalize_percent_like(value: float | None) -> float | None:
    if value is None:
        return None
    # Some sources return 0.035, others return 3.5.
    if abs(value) <= 1:
        return value * 100
    return value


def percentile_rank(current: float | None, history: pd.Series) -> float | None:
    current = to_float(current)
    if current is None or current <= 0:
        return None
    values = pd.to_numeric(history, errors="coerce")
    values = values[(values > 0) & values.notna()]
    if values.empty:
        return None
    return float((values <= current).sum() / len(values) * 100)


def fetch_spot(config: ScreenConfig) -> pd.DataFrame:
    data_path = config.data_dir / "spot" / f"{today_stamp()}.csv"
    if not config.refresh:
        data_cached = read_csv(data_path)
        if data_cached is not None and not data_cached.empty:
            if "代码" in data_cached.columns:
                data_cached["代码"] = data_cached["代码"].astype(str).str.zfill(6)
            log(f"读取当日行情库：{data_path}")
            return data_cached

    cache_path = config.cache_dir / f"spot_{today_stamp()}.csv"
    if not config.refresh and is_cache_fresh(cache_path):
        cached = read_csv_cache(cache_path)
        if cached is not None and not cached.empty:
            if "代码" in cached.columns:
                cached["代码"] = cached["代码"].astype(str).str.zfill(6)
            log(f"读取行情缓存：{cache_path}")
            write_csv(cached, data_path)
            return cached

    log("拉取沪深京 A 股实时行情：东方财富分页接口")
    spot = fetch_spot_eastmoney(config)
    if spot.empty:
        raise RuntimeError("ak.stock_zh_a_spot_em() 返回空数据")

    required = {"代码", "名称", "最新价"}
    missing = required.difference(spot.columns)
    if missing:
        raise RuntimeError(f"实时行情缺少字段：{sorted(missing)}；实际字段：{spot.columns.tolist()}")

    spot["代码"] = spot["代码"].astype(str).str.zfill(6)
    spot = spot[pd.to_numeric(spot["最新价"], errors="coerce") > 0].copy()
    write_csv_cache(spot, cache_path)
    write_csv(spot, data_path)
    return spot


def fetch_spot_page(page: int, page_size: int, timeout: float) -> tuple[int, list[dict]]:
    url = "https://82.push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": str(page),
        "pz": str(page_size),
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
        "fields": (
            "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,"
            "f20,f21,f23,f24,f25,f115"
        ),
    }
    last_error = None
    for _ in range(3):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            data = response.json().get("data") or {}
            return int(data.get("total") or 0), data.get("diff") or []
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"东方财富行情第 {page} 页失败：{last_error}")


def fetch_spot_eastmoney(config: ScreenConfig) -> pd.DataFrame:
    page_size = 100
    total, first_rows = fetch_spot_page(1, page_size, config.spot_timeout)
    if total <= 0 or not first_rows:
        raise RuntimeError("东方财富行情接口返回空数据")

    pages = math.ceil(total / page_size)
    log(f"实时行情总数 {total}，分页 {pages} 页")
    all_rows = list(first_rows)
    if pages > 1:
        page_numbers = list(range(2, pages + 1))
        with ThreadPoolExecutor(max_workers=max(1, config.spot_workers)) as executor:
            future_map = {
                executor.submit(fetch_spot_page, page, page_size, config.spot_timeout): page
                for page in page_numbers
            }
            for idx, future in enumerate(as_completed(future_map), start=1):
                _, rows = future.result()
                all_rows.extend(rows)
                if idx == len(page_numbers) or idx % 10 == 0:
                    log(f"实时行情分页：{idx + 1}/{pages}")

    df = pd.DataFrame(all_rows)
    rename_map = {
        "f12": "代码",
        "f14": "名称",
        "f2": "最新价",
        "f3": "涨跌幅",
        "f4": "涨跌额",
        "f5": "成交量",
        "f6": "成交额",
        "f7": "振幅",
        "f8": "换手率",
        "f9": "市盈率-动态",
        "f10": "量比",
        "f15": "最高",
        "f16": "最低",
        "f17": "今开",
        "f18": "昨收",
        "f20": "总市值",
        "f21": "流通市值",
        "f23": "市净率",
        "f24": "60日涨跌幅",
        "f25": "年初至今涨跌幅",
        "f115": "市盈率TTM",
    }
    df.rename(columns=rename_map, inplace=True)
    keep_cols = [col for col in rename_map.values() if col in df.columns]
    df = df[keep_cols].copy()
    for col in [c for c in keep_cols if c not in {"代码", "名称"}]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["代码"] = df["代码"].astype(str).str.zfill(6)
    return df


def fetch_baidu_valuation_history(symbol: str, indicator: str, config: ScreenConfig) -> pd.DataFrame:
    safe_indicator = "pe" if "盈" in indicator else "pb"
    cache_path = config.cache_dir / "valuation" / f"{symbol}_{safe_indicator}_{today_stamp()}.csv"
    if not config.refresh and is_cache_fresh(cache_path):
        cached = read_csv_cache(cache_path)
        if cached is not None and not cached.empty:
            return cached

    if config.request_pause > 0:
        time.sleep(config.request_pause)

    df = ak.stock_zh_valuation_baidu(symbol=symbol, indicator=indicator, period="近十年")
    if df.empty:
        raise RuntimeError("估值历史为空")
    write_csv_cache(df, cache_path)
    return df


def fetch_value_history_em(symbol: str, config: ScreenConfig) -> pd.DataFrame:
    cache_path = config.cache_dir / "value" / f"{symbol}_{today_stamp()}.csv"
    if not config.refresh and is_cache_fresh(cache_path):
        cached = read_csv_cache(cache_path)
        if cached is not None and not cached.empty:
            return cached

    df = ak.stock_value_em(symbol=symbol)
    if df.empty:
        raise RuntimeError("东方财富估值分析为空")
    write_csv_cache(df, cache_path)
    return df


def legacy_value_cache_path(symbol: str, config: ScreenConfig) -> Path | None:
    value_dir = config.cache_dir / "value"
    if not value_dir.exists():
        return None
    matches = sorted(value_dir.glob(f"{symbol}_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def spot_to_value_row(row: pd.Series) -> dict:
    pe_ttm = to_float(row.get("市盈率TTM"))
    if pe_ttm is None:
        pe_ttm = to_float(row.get("市盈率-动态"))
    return {
        "数据日期": date.today().isoformat(),
        "当日收盘价": to_float(row.get("最新价")),
        "当日涨跌幅": to_float(row.get("涨跌幅")),
        "总市值": to_float(row.get("总市值")),
        "流通市值": to_float(row.get("流通市值")),
        "总股本": None,
        "流通股本": None,
        "PE(TTM)": pe_ttm,
        "PE(静)": to_float(row.get("市盈率-动态")),
        "市净率": to_float(row.get("市净率")),
        "PEG值": None,
        "市现率": None,
        "市销率": None,
    }


def normalize_value_history(df: pd.DataFrame) -> pd.DataFrame:
    expected_cols = [
        "数据日期",
        "当日收盘价",
        "当日涨跌幅",
        "总市值",
        "流通市值",
        "总股本",
        "流通股本",
        "PE(TTM)",
        "PE(静)",
        "市净率",
        "PEG值",
        "市现率",
        "市销率",
    ]
    work = df.copy()
    for col in expected_cols:
        if col not in work.columns:
            work[col] = None
    work = work[expected_cols]
    work["数据日期"] = pd.to_datetime(work["数据日期"], errors="coerce")
    work = work[work["数据日期"].notna()].copy()
    for col in expected_cols:
        if col != "数据日期":
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.sort_values("数据日期").drop_duplicates(subset=["数据日期"], keep="last")
    work["数据日期"] = work["数据日期"].dt.date.astype(str)
    return work


def fetch_incremental_value_history(row: pd.Series, config: ScreenConfig) -> pd.DataFrame:
    symbol = str(row["代码"]).zfill(6)
    data_path = config.data_dir / "value_history" / f"{symbol}.csv"

    history = None if config.rebuild_history else read_csv(data_path)
    if history is None or history.empty:
        legacy_path = legacy_value_cache_path(symbol, config)
        if legacy_path is not None and not config.rebuild_history:
            legacy = read_csv_cache(legacy_path)
            if legacy is not None and not legacy.empty:
                history = legacy
                log(f"初始化历史库：{symbol} <- {legacy_path.name}")

    if history is None or history.empty:
        history = fetch_value_history_em(symbol, config)
        log(f"初始化历史库：{symbol} <- stock_value_em")

    history = normalize_value_history(history)
    today_row = pd.DataFrame([spot_to_value_row(row)])
    history = normalize_value_history(
        pd.concat(
            [history.dropna(axis=1, how="all"), today_row.dropna(axis=1, how="all")],
            ignore_index=True,
        )
    )
    write_csv(history, data_path)
    return history


def fetch_dividend_history(symbol: str, config: ScreenConfig) -> pd.DataFrame:
    data_path = config.data_dir / "dividends" / f"{symbol}.csv"
    if not config.refresh:
        data_cached = read_csv(data_path)
        if data_cached is not None:
            return data_cached

    cache_path = config.cache_dir / "valuation" / f"{symbol}_dividend_{today_stamp()}.csv"
    if not config.refresh and is_cache_fresh(cache_path):
        cached = read_csv_cache(cache_path)
        if cached is not None:
            write_csv(cached, data_path)
            return cached

    if config.request_pause > 0:
        time.sleep(config.request_pause)

    df = ak.stock_dividend_cninfo(symbol=symbol)
    if df is None:
        df = pd.DataFrame()
    write_csv_cache(df, cache_path)
    write_csv(df, data_path)
    return df


def latest_value_and_percentile(df: pd.DataFrame) -> tuple[float | None, float | None, str]:
    if df.empty or not {"date", "value"}.issubset(df.columns):
        return None, None, ""
    work = df.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["value"] = pd.to_numeric(work["value"], errors="coerce")
    work = work[(work["date"].notna()) & (work["value"].notna())].sort_values("date")
    if work.empty:
        return None, None, ""
    latest = to_float(work["value"].iloc[-1])
    rank = percentile_rank(latest, work["value"])
    return latest, rank, work["date"].iloc[-1].date().isoformat()


def baidu_valuation_value_and_percentile(
    symbol: str,
    indicator: str,
    config: ScreenConfig,
) -> tuple[float | None, float | None, str]:
    df = fetch_baidu_valuation_history(symbol, indicator, config)
    return latest_value_and_percentile(df)


def dividend_yield_ttm(symbol: str, latest_price: float | None, config: ScreenConfig) -> tuple[float | None, str]:
    latest_price = to_float(latest_price)
    if latest_price is None or latest_price <= 0:
        return None, ""

    df = fetch_dividend_history(symbol, config)
    if df.empty or "派息比例" not in df.columns:
        return None, ""

    date_col = pick_column(df.columns, ["除权日", "派息日", "股权登记日", "实施方案公告日期"])
    if date_col is None:
        return None, ""

    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work["派息比例"] = pd.to_numeric(work["派息比例"], errors="coerce")
    cutoff = pd.Timestamp(date.today() - timedelta(days=config.dividend_lookback_days))
    end = pd.Timestamp(date.today())
    recent = work[
        (work[date_col].notna())
        & (work[date_col] >= cutoff)
        & (work[date_col] <= end)
        & (work["派息比例"].notna())
        & (work["派息比例"] > 0)
    ].copy()

    if recent.empty:
        return None, ""

    # CNInfo 派息比例 is usually "10派X元"; dividend per share is X / 10.
    dividend_per_share = float(recent["派息比例"].sum()) / 10
    dividend_yield = dividend_per_share / latest_price * 100
    data_date = recent[date_col].max().date().isoformat()
    return dividend_yield, data_date


def valuation_metrics(row: pd.Series, config: ScreenConfig) -> dict:
    symbol = str(row["代码"]).zfill(6)
    latest_price = to_float(row.get("最新价"))
    try:
        pe_error = ""
        pb_error = ""
        try:
            pe_df = fetch_baidu_valuation_history(symbol, "市盈率(TTM)", config)
            pe, pe_pct, pe_date = latest_value_and_percentile(pe_df)
        except Exception as exc:
            pe, pe_pct, pe_date = None, None, ""
            pe_error = f"PE: {exc}"
        try:
            pb_df = fetch_baidu_valuation_history(symbol, "市净率", config)
            pb, pb_pct, pb_date = latest_value_and_percentile(pb_df)
        except Exception as exc:
            pb, pb_pct, pb_date = None, None, ""
            pb_error = f"PB: {exc}"

        dividend_yield, dividend_date = dividend_yield_ttm(symbol, latest_price, config)
        pe_pass = pe_pct is not None and pe_pct <= config.max_percentile
        pb_pass = pb_pct is not None and pb_pct <= config.max_percentile
        div_pass = dividend_yield is not None and dividend_yield > config.min_dividend_yield_pct
        if pe_pct is None and pb_pct is None:
            raise RuntimeError("; ".join([e for e in [pe_error, pb_error] if e]) or "PE/PB 估值历史不可用")

        return {
            "代码": symbol,
            "市盈率": pe,
            "市净率": pb,
            "股息率": dividend_yield,
            "市盈率10年分位": pe_pct,
            "市净率10年分位": pb_pct,
            "股息率达标": div_pass,
            "估值分位达标": pe_pass or pb_pass,
            "估值数据日期": pe_date or pb_date,
            "股息数据日期": dividend_date,
            "估值错误": "; ".join([e for e in [pe_error, pb_error] if e])[:300],
        }
    except Exception as exc:
        return {
            "代码": symbol,
            "市盈率": None,
            "市净率": None,
            "股息率": None,
            "市盈率10年分位": None,
            "市净率10年分位": None,
            "股息率达标": False,
            "估值分位达标": False,
            "估值数据日期": "",
            "股息数据日期": "",
            "估值错误": str(exc)[:300],
        }


def fetch_price_history(symbol: str, config: ScreenConfig) -> pd.DataFrame:
    cache_path = config.cache_dir / "price" / f"{symbol}_{today_stamp()}.csv"
    if not config.refresh and is_cache_fresh(cache_path):
        cached = read_csv_cache(cache_path)
        if cached is not None and not cached.empty:
            return cached

    end = date.today()
    start = end - timedelta(days=max(420, config.price_window * 3))
    df = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="",
        timeout=15,
    )
    if df.empty:
        raise RuntimeError("价格历史为空")
    write_csv_cache(df, cache_path)
    return df


def price_metrics(row: pd.Series, config: ScreenConfig) -> dict:
    symbol = str(row["代码"]).zfill(6)
    try:
        df = fetch_price_history(symbol, config)
        close_col = pick_column(df.columns, ["收盘", "close"])
        date_col = pick_column(df.columns, ["日期", "date"])
        if close_col is None:
            raise RuntimeError(f"价格字段无法识别：{df.columns.tolist()}")
        df[close_col] = pd.to_numeric(df[close_col], errors="coerce")
        df = df[df[close_col].notna()].copy()
        if len(df) < config.price_window:
            raise RuntimeError(f"历史交易日不足 {config.price_window}：{len(df)}")

        ma = float(df[close_col].tail(config.price_window).mean())
        latest_price = to_float(row.get("最新价"))
        if latest_price is None:
            latest_price = float(df[close_col].iloc[-1])
        deviation = (latest_price / ma - 1) * 100
        price_date = ""
        if date_col and date_col in df.columns:
            price_date = str(df[date_col].iloc[-1])
        return {
            "代码": symbol,
            "现价": latest_price,
            "半年线": ma,
            "半年线乖离率": deviation,
            "价格达标": deviation <= config.max_below_ma_pct,
            "价格数据日期": price_date,
            "价格错误": "",
        }
    except Exception as exc:
        return {
            "代码": symbol,
            "现价": to_float(row.get("最新价")),
            "半年线": None,
            "半年线乖离率": None,
            "价格达标": False,
            "价格数据日期": "",
            "价格错误": str(exc)[:300],
        }


def price_valuation_metrics(row: pd.Series, config: ScreenConfig) -> dict:
    symbol = str(row["代码"]).zfill(6)
    latest_price = to_float(row.get("最新价"))
    try:
        fallback_errors = []
        df = fetch_incremental_value_history(row, config)
        date_col = pick_column(df.columns, ["数据日期", "date", "日期"])
        close_col = pick_column(df.columns, ["当日收盘价", "收盘", "close"])
        pe_col = pick_column(df.columns, ["PE(TTM)", "pe_ttm", "市盈率ttm"])
        pb_col = pick_column(df.columns, ["市净率", "pb"])
        cap_col = pick_column(df.columns, ["总市值"])

        required_missing = [
            name
            for name, col in [("date", date_col), ("close", close_col)]
            if col is None
        ]
        if required_missing:
            raise RuntimeError(f"东方财富估值字段缺失：{required_missing}；实际字段：{df.columns.tolist()}")

        work = df.copy()
        work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
        work[close_col] = pd.to_numeric(work[close_col], errors="coerce")
        if pe_col:
            work[pe_col] = pd.to_numeric(work[pe_col], errors="coerce")
        if pb_col:
            work[pb_col] = pd.to_numeric(work[pb_col], errors="coerce")
        if cap_col:
            work[cap_col] = pd.to_numeric(work[cap_col], errors="coerce")
        work = work[(work[date_col].notna()) & (work[close_col].notna())].sort_values(date_col)
        if len(work) < config.price_window:
            raise RuntimeError(f"历史交易日不足 {config.price_window}：{len(work)}")

        cutoff = pd.Timestamp(date.today() - timedelta(days=365 * config.valuation_years + 30))
        recent = work[work[date_col] >= cutoff].copy()
        if recent.empty:
            recent = work.copy()

        ma = float(work[close_col].tail(config.price_window).mean())
        if latest_price is None:
            latest_price = float(work[close_col].iloc[-1])
        deviation = (latest_price / ma - 1) * 100
        latest = recent.iloc[-1]
        pe = to_float(latest[pe_col]) if pe_col else None
        pb = to_float(latest[pb_col]) if pb_col else None
        pe_pct = percentile_rank(pe, recent[pe_col]) if pe_col else None
        pb_pct = percentile_rank(pb, recent[pb_col]) if pb_col else None

        spot_pe = to_float(row.get("市盈率TTM"))
        if spot_pe is None:
            spot_pe = to_float(row.get("市盈率-动态"))
        spot_pb = to_float(row.get("市净率"))
        if pe is None and spot_pe is not None:
            pe = spot_pe
        if pb is None and spot_pb is not None:
            pb = spot_pb
        if pe_pct is None and pe is not None and pe_col:
            pe_pct = percentile_rank(pe, recent[pe_col])
        if pb_pct is None and pb is not None and pb_col:
            pb_pct = percentile_rank(pb, recent[pb_col])

        pe_fallback_date = ""
        pb_fallback_date = ""
        if pe is None or pe_pct is None:
            try:
                fallback_pe, fallback_pe_pct, pe_fallback_date = baidu_valuation_value_and_percentile(
                    symbol, "市盈率(TTM)", config
                )
                if pe is None:
                    pe = fallback_pe
                if pe_pct is None:
                    pe_pct = fallback_pe_pct
            except Exception as exc:
                fallback_errors.append(f"PE备用源: {exc}")
        if pb is None or pb_pct is None:
            try:
                fallback_pb, fallback_pb_pct, pb_fallback_date = baidu_valuation_value_and_percentile(
                    symbol, "市净率", config
                )
                if pb is None:
                    pb = fallback_pb
                if pb_pct is None:
                    pb_pct = fallback_pb_pct
            except Exception as exc:
                fallback_errors.append(f"PB备用源: {exc}")

        pe_pass = pe_pct is not None and pe_pct <= config.max_percentile
        pb_pass = pb_pct is not None and pb_pct <= config.max_percentile
        value_date = latest[date_col].date().isoformat()
        valuation_date = value_date or pe_fallback_date or pb_fallback_date
        market_cap = to_float(latest[cap_col]) if cap_col else to_float(row.get("总市值"))
        missing_metrics = []
        if pe is None:
            missing_metrics.append("PE")
        if pe_pct is None:
            missing_metrics.append("PE分位")
        if pb is None:
            missing_metrics.append("PB")
        if pb_pct is None:
            missing_metrics.append("PB分位")
        metric_error = ""
        if missing_metrics:
            metric_error = "估值字段仍缺失：" + ",".join(missing_metrics)
            if fallback_errors:
                metric_error = f"{metric_error}；{'; '.join(fallback_errors)}"

        return {
            "代码": symbol,
            "现价": latest_price,
            "半年线": ma,
            "半年线乖离率": deviation,
            "价格达标": deviation <= config.max_below_ma_pct,
            "价格数据日期": value_date,
            "市盈率": pe,
            "市净率": pb,
            "市盈率10年分位": pe_pct,
            "市净率10年分位": pb_pct,
            "估值分位达标": pe_pass or pb_pass,
            "估值数据日期": valuation_date,
            "总市值_估值源": market_cap,
            "价格估值错误": metric_error[:300],
        }
    except Exception as exc:
        return {
            "代码": symbol,
            "现价": latest_price,
            "半年线": None,
            "半年线乖离率": None,
            "价格达标": False,
            "价格数据日期": "",
            "市盈率": None,
            "市净率": None,
            "市盈率10年分位": None,
            "市净率10年分位": None,
            "估值分位达标": False,
            "估值数据日期": "",
            "总市值_估值源": None,
            "价格估值错误": str(exc)[:300],
        }


def dividend_metrics(row: pd.Series, config: ScreenConfig) -> dict:
    symbol = str(row["代码"]).zfill(6)
    latest_price = to_float(row.get("现价"))
    try:
        dividend_yield, dividend_date = dividend_yield_ttm(symbol, latest_price, config)
        div_pass = dividend_yield is not None and dividend_yield > config.min_dividend_yield_pct
        return {
            "代码": symbol,
            "股息率": dividend_yield,
            "股息率达标": div_pass,
            "股息数据日期": dividend_date,
            "股息错误": "",
        }
    except Exception as exc:
        return {
            "代码": symbol,
            "股息率": None,
            "股息率达标": False,
            "股息数据日期": "",
            "股息错误": str(exc)[:300],
        }

def industry_for_symbol(symbol: str, config: ScreenConfig) -> dict:
    data_path = config.data_dir / "industry" / f"{symbol}.csv"
    if not config.refresh:
        data_cached = read_csv(data_path)
        if data_cached is not None and not data_cached.empty:
            return {"代码": symbol, "所属行业": str(data_cached.iloc[0]["所属行业"]), "行业错误": ""}

    cache_path = config.cache_dir / "industry" / f"{symbol}.csv"
    if not config.refresh and cache_path.exists():
        cached = read_csv_cache(cache_path)
        if cached is not None and not cached.empty:
            write_csv(cached, data_path)
            return {"代码": symbol, "所属行业": str(cached.iloc[0]["所属行业"]), "行业错误": ""}

    try:
        df = ak.stock_individual_info_em(symbol=symbol, timeout=10)
        if df.empty or not {"item", "value"}.issubset(df.columns):
            raise RuntimeError("个股信息为空或字段异常")
        industry_rows = df[df["item"].astype(str) == "行业"]
        industry = str(industry_rows.iloc[0]["value"]) if not industry_rows.empty else "未知"
        write_csv_cache(pd.DataFrame([{"所属行业": industry}]), cache_path)
        write_csv(pd.DataFrame([{"所属行业": industry}]), data_path)
        return {"代码": symbol, "所属行业": industry, "行业错误": ""}
    except Exception as exc:
        return {"代码": symbol, "所属行业": "未知", "行业错误": str(exc)[:200]}


def parallel_map(
    label: str,
    rows: Iterable,
    worker: Callable,
    max_workers: int,
) -> list:
    rows = list(rows)
    if not rows:
        return []
    results = []
    total = len(rows)
    log(f"{label}：开始处理 {total} 条，workers={max_workers}")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(worker, row): row for row in rows}
        for idx, future in enumerate(as_completed(future_map), start=1):
            results.append(future.result())
            if idx == total or idx % 50 == 0:
                log(f"{label}：{idx}/{total}")
    return results


def build_real_screen(config: ScreenConfig) -> tuple[pd.DataFrame, dict]:
    ensure_cache_dir(config.cache_dir)
    ensure_data_dir(config.data_dir)
    spot = fetch_spot(config)
    spot = spot.sort_values("代码").copy()
    if config.limit:
        spot = spot.head(config.limit).copy()

    diagnostics = {
        "universe_count": len(spot),
        "valuation_error_count": 0,
        "price_error_count": 0,
        "dividend_error_count": 0,
        "industry_error_count": 0,
        "valuation_prefilter_count": 0,
        "price_prefilter_count": 0,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "真实数据",
    }

    value_rows = parallel_map(
        "半年线与估值",
        [row for _, row in spot.iterrows()],
        lambda row: price_valuation_metrics(row, config),
        max_workers=max(1, config.price_workers),
    )
    values = pd.DataFrame(value_rows)
    if values.empty:
        diagnostics["price_error_count"] = len(spot)
        return values, diagnostics
    if "价格估值错误" not in values.columns:
        values["价格估值错误"] = ""
    if "代码" in values.columns:
        values["代码"] = values["代码"].astype(str).str.zfill(6)
    diagnostics["price_error_count"] = int((values["价格估值错误"].astype(str) != "").sum())

    merged = spot.merge(values, on="代码", how="left")
    if "总市值_估值源" in merged.columns:
        merged["总市值"] = merged["总市值_估值源"].combine_first(merged.get("总市值"))

    price_prefiltered = merged[merged["价格达标"] == True].copy()
    diagnostics["price_prefilter_count"] = len(price_prefiltered)

    if price_prefiltered.empty:
        return price_prefiltered, diagnostics

    dividend_rows = parallel_map(
        "股息率",
        [row for _, row in price_prefiltered.iterrows()],
        lambda row: dividend_metrics(row, config),
        max_workers=max(1, config.valuation_workers),
    )
    dividends = pd.DataFrame(dividend_rows)
    if "代码" in dividends.columns:
        dividends["代码"] = dividends["代码"].astype(str).str.zfill(6)
    diagnostics["dividend_error_count"] = int((dividends["股息错误"].astype(str) != "").sum())

    merged = price_prefiltered.merge(dividends, on="代码", how="left")
    final_df = merged[merged["股息率达标"] == True].copy()
    diagnostics["valuation_prefilter_count"] = len(final_df)

    industry_rows = parallel_map(
        "所属行业",
        final_df["代码"].astype(str).tolist(),
        lambda symbol: industry_for_symbol(symbol, config),
        max_workers=max(1, config.industry_workers),
    )
    if industry_rows:
        industry = pd.DataFrame(industry_rows)
        diagnostics["industry_error_count"] = int((industry["行业错误"].astype(str) != "").sum())
        final_df = final_df.merge(industry, on="代码", how="left")
    else:
        final_df["所属行业"] = ""
    result_path = config.data_dir / "screening_results" / f"{today_stamp()}.csv"
    write_csv(final_df, result_path)
    return final_df, diagnostics


def demo_screen(config: ScreenConfig) -> tuple[pd.DataFrame, dict]:
    rows = [
        {
            "代码": "000001",
            "名称": "平安银行",
            "所属行业": "银行Ⅱ",
            "现价": 9.82,
            "半年线": 11.24,
            "半年线乖离率": -12.63,
            "市盈率": 4.28,
            "市净率": 0.43,
            "股息率": 7.52,
            "市盈率10年分位": 18.4,
            "市净率10年分位": 9.7,
            "估值数据日期": date.today().isoformat(),
            "价格数据日期": date.today().isoformat(),
            "总市值": 190_000_000_000,
        },
        {
            "代码": "600000",
            "名称": "浦发银行",
            "所属行业": "银行Ⅱ",
            "现价": 8.12,
            "半年线": 9.32,
            "半年线乖离率": -12.88,
            "市盈率": 5.13,
            "市净率": 0.38,
            "股息率": 4.86,
            "市盈率10年分位": 27.2,
            "市净率10年分位": 11.6,
            "估值数据日期": date.today().isoformat(),
            "价格数据日期": date.today().isoformat(),
            "总市值": 238_000_000_000,
        },
    ]
    diagnostics = {
        "universe_count": 2,
        "valuation_error_count": 0,
        "price_error_count": 0,
        "dividend_error_count": 0,
        "industry_error_count": 0,
        "valuation_prefilter_count": 2,
        "price_prefilter_count": 2,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "演示数据",
    }
    return pd.DataFrame(rows), diagnostics


def monitor_date_window(config: ScreenConfig) -> tuple[date, date]:
    end = date.today()
    start = end - timedelta(days=max(30, config.monitor_days) - 1)
    return start, end


def fetch_market_index_history(symbol: str, label: str, config: ScreenConfig) -> pd.DataFrame:
    start, end = monitor_date_window(config)
    cache_path = config.cache_dir / "market_monitor" / f"index_{symbol}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    if not config.refresh and is_cache_fresh(cache_path):
        cached = read_csv_cache(cache_path)
        if cached is not None and not cached.empty:
            return cached

    em_symbol = f"sz{symbol}" if symbol.startswith("399") else f"sh{symbol}"
    try:
        df = ak.stock_zh_index_daily_em(
            symbol=em_symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
    except Exception:
        df = ak.index_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
    if df is None or df.empty:
        raise RuntimeError(f"{label}({symbol}) 指数历史为空")

    date_col = pick_column(df.columns, ["日期", "date"])
    volume_col = pick_column(df.columns, ["成交量", "volume"])
    if date_col is None or volume_col is None:
        raise RuntimeError(f"{label}({symbol}) 缺少日期或成交量字段：{df.columns.tolist()}")

    work = pd.DataFrame(
        {
            "日期": pd.to_datetime(df[date_col], errors="coerce"),
            f"{label}成交量": pd.to_numeric(df[volume_col], errors="coerce"),
        }
    )
    work = work[(work["日期"].notna()) & (work[f"{label}成交量"].notna())].copy()
    work = work.sort_values("日期").drop_duplicates(subset=["日期"], keep="last")
    work["日期"] = work["日期"].dt.date.astype(str)
    write_csv_cache(work, cache_path)
    return work


def fetch_bj_stock_history_for_volume(symbol: str, config: ScreenConfig) -> pd.DataFrame:
    start, end = monitor_date_window(config)
    cache_path = config.cache_dir / "market_monitor" / "bj_stock_volume" / f"{symbol}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    if not config.refresh and is_cache_fresh(cache_path):
        cached = read_csv_cache(cache_path)
        if cached is not None:
            return cached

    try:
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="",
            timeout=15,
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=["日期", "北交所成交量"])
        date_col = pick_column(df.columns, ["日期", "date"])
        volume_col = pick_column(df.columns, ["成交量", "volume"])
        if date_col is None or volume_col is None:
            return pd.DataFrame(columns=["日期", "北交所成交量"])
        work = pd.DataFrame(
            {
                "日期": pd.to_datetime(df[date_col], errors="coerce"),
                "北交所成交量": pd.to_numeric(df[volume_col], errors="coerce"),
            }
        )
        work = work[(work["日期"].notna()) & (work["北交所成交量"].notna())].copy()
        work["日期"] = work["日期"].dt.date.astype(str)
        write_csv_cache(work, cache_path)
        return work
    except Exception:
        return pd.DataFrame(columns=["日期", "北交所成交量"])


def fetch_bj_market_volume_history(config: ScreenConfig) -> pd.DataFrame:
    start, end = monitor_date_window(config)
    cache_path = config.cache_dir / "market_monitor" / f"bj_market_volume_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    if not config.refresh and is_cache_fresh(cache_path):
        cached = read_csv_cache(cache_path)
        if cached is not None and not cached.empty:
            return cached

    codes_df = ak.stock_info_bj_name_code()
    if codes_df is None or codes_df.empty:
        raise RuntimeError("北交所股票列表为空")
    code_col = pick_column(codes_df.columns, ["证券代码", "代码", "股票代码"])
    if code_col is None:
        raise RuntimeError("北交所股票列表缺少代码字段")

    symbols = sorted({str(code).strip().zfill(6) for code in codes_df[code_col].dropna()})
    histories = parallel_map(
        "北交所成交量",
        symbols,
        lambda symbol: fetch_bj_stock_history_for_volume(symbol, config),
        max_workers=max(1, config.spot_workers),
    )
    valid = [df for df in histories if df is not None and not df.empty]
    if not valid:
        raise RuntimeError("北交所个股历史成交量全部为空")

    merged = pd.concat(valid, ignore_index=True)
    merged["北交所成交量"] = pd.to_numeric(merged["北交所成交量"], errors="coerce")
    merged = (
        merged[merged["北交所成交量"].notna()]
        .groupby("日期", as_index=False)["北交所成交量"]
        .sum()
        .sort_values("日期")
    )
    write_csv_cache(merged, cache_path)
    return merged


def build_volume_ratio_monitor(config: ScreenConfig) -> pd.DataFrame:
    hs300 = fetch_market_index_history("000300", "沪深300", config)
    markets = [
        fetch_market_index_history("000001", "上证指数", config),
        fetch_market_index_history("399106", "深证综指", config),
        fetch_bj_market_volume_history(config),
    ]

    merged = hs300[["日期", "沪深300成交量"]].copy()
    for df in markets:
        merged = merged.merge(df[["日期", df.columns[1]]], on="日期", how="outer")

    market_cols = ["上证指数成交量", "深证综指成交量", "北交所成交量"]
    for col in ["沪深300成交量", *market_cols]:
        if col not in merged.columns:
            merged[col] = None
        merged[col] = pd.to_numeric(merged[col], errors="coerce")

    merged["市场总成交量"] = merged[market_cols].sum(axis=1, min_count=1)
    merged["沪深300成交占比"] = merged.apply(
        lambda row: row["沪深300成交量"] / row["市场总成交量"] * 100
        if to_float(row["沪深300成交量"]) is not None
        and to_float(row["市场总成交量"]) is not None
        and row["市场总成交量"] > 0
        else None,
        axis=1,
    )
    return merged[["日期", "沪深300成交量", "市场总成交量", "沪深300成交占比"]]


def fetch_margin_macro_history(name: str, fetcher: Callable[[], pd.DataFrame], config: ScreenConfig) -> pd.DataFrame:
    cache_path = config.cache_dir / "market_monitor" / f"margin_{name}_{today_stamp()}.csv"
    if not config.refresh and is_cache_fresh(cache_path):
        cached = read_csv_cache(cache_path)
        if cached is not None and not cached.empty:
            return cached

    df = fetcher()
    if df is None or df.empty:
        raise RuntimeError(f"{name} 融资融券历史为空")
    write_csv_cache(df, cache_path)
    return df


def normalize_margin_history(df: pd.DataFrame, label: str) -> pd.DataFrame:
    date_col = pick_column(df.columns, ["日期", "信用交易日期", "date"])
    balance_col = pick_column(df.columns, ["融资余额"])
    if date_col is None or balance_col is None:
        raise RuntimeError(f"{label} 融资余额字段无法识别：{df.columns.tolist()}")

    work = pd.DataFrame(
        {
            "日期": pd.to_datetime(df[date_col].astype(str), errors="coerce"),
            f"{label}融资余额": pd.to_numeric(df[balance_col], errors="coerce"),
        }
    )
    work = work[(work["日期"].notna()) & (work[f"{label}融资余额"].notna())].copy()
    work = work.sort_values("日期").drop_duplicates(subset=["日期"], keep="last")
    work["日期"] = work["日期"].dt.date.astype(str)
    return work


def build_margin_change_monitor(config: ScreenConfig) -> pd.DataFrame:
    sh = normalize_margin_history(
        fetch_margin_macro_history("sh", ak.macro_china_market_margin_sh, config),
        "上海",
    )
    sz = normalize_margin_history(
        fetch_margin_macro_history("sz", ak.macro_china_market_margin_sz, config),
        "深圳",
    )
    merged = sh.merge(sz, on="日期", how="outer")
    merged["上海融资余额"] = pd.to_numeric(merged["上海融资余额"], errors="coerce")
    merged["深圳融资余额"] = pd.to_numeric(merged["深圳融资余额"], errors="coerce")
    merged["融资余额"] = merged[["上海融资余额", "深圳融资余额"]].sum(axis=1, min_count=2)
    merged = merged[merged["融资余额"].notna()].sort_values("日期").copy()
    merged["融资余额增长率"] = merged["融资余额"].pct_change() * 100
    return merged[["日期", "融资余额", "融资余额增长率"]]


def build_market_monitor(config: ScreenConfig) -> tuple[pd.DataFrame, dict]:
    start, _ = monitor_date_window(config)
    diagnostics = {
        "market_monitor_error": "",
        "market_monitor_source": "成交量：沪深300 / (上证指数 + 深证综指 + 北交所个股成交量汇总)；融资余额：上海 + 深圳两市融资余额",
    }
    pieces = []
    errors = []

    try:
        pieces.append(build_volume_ratio_monitor(config))
    except Exception as exc:
        errors.append(f"成交占比：{exc}")

    try:
        pieces.append(build_margin_change_monitor(config))
    except Exception as exc:
        errors.append(f"融资余额：{exc}")

    if not pieces:
        diagnostics["market_monitor_error"] = "；".join(errors)
        diagnostics["market_monitor_count"] = 0
        return pd.DataFrame(), diagnostics

    monitor = pieces[0]
    for piece in pieces[1:]:
        monitor = monitor.merge(piece, on="日期", how="outer")
    monitor["日期"] = pd.to_datetime(monitor["日期"], errors="coerce")
    monitor = monitor[(monitor["日期"].notna()) & (monitor["日期"] >= pd.Timestamp(start))].copy()
    monitor = monitor.sort_values("日期")
    monitor["日期"] = monitor["日期"].dt.date.astype(str)
    diagnostics["market_monitor_error"] = "；".join(errors)
    diagnostics["market_monitor_count"] = len(monitor)
    return monitor, diagnostics


def demo_market_monitor(config: ScreenConfig) -> tuple[pd.DataFrame, dict]:
    start, end = monitor_date_window(config)
    rows = []
    balance = 2_400_000_000_000.0
    idx = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            ratio = 18 + math.sin(idx / 5) * 2.5 + (idx % 7) * 0.15
            daily_change = math.sin(idx / 6) * 0.18 + 0.03
            balance *= 1 + daily_change / 100
            rows.append(
                {
                    "日期": current.isoformat(),
                    "沪深300成交量": 260_000_000 + idx * 500_000,
                    "市场总成交量": 1_500_000_000 + idx * 2_000_000,
                    "沪深300成交占比": ratio,
                    "融资余额": balance,
                    "融资余额增长率": daily_change,
                }
            )
            idx += 1
        current += timedelta(days=1)
    return pd.DataFrame(rows), {
        "market_monitor_error": "",
        "market_monitor_count": len(rows),
        "market_monitor_source": "演示数据",
    }


def monitor_records_for_html(df: pd.DataFrame) -> str:
    columns = ["日期", "沪深300成交占比", "沪深300成交量", "市场总成交量", "融资余额", "融资余额增长率"]
    if df is None or df.empty:
        return "[]"

    work = df.copy()
    for col in columns:
        if col not in work.columns:
            work[col] = None
    records = []
    for _, row in work[columns].iterrows():
        record = {}
        for col in columns:
            value = row[col]
            if pd.isna(value):
                record[col] = None
            elif col == "日期":
                record[col] = str(value)
            else:
                record[col] = float(value)
        records.append(record)
    return json.dumps(records, ensure_ascii=False).replace("</", "<\\/")


def fmt_num(value, digits: int = 2, suffix: str = "") -> str:
    value = to_float(value)
    if value is None:
        return "-"
    return f"{value:,.{digits}f}{suffix}"


def fmt_market_cap(value) -> str:
    value = to_float(value)
    if value is None:
        return "-"
    return f"{value / 100000000:,.0f} 亿"


def render_table_rows(df: pd.DataFrame) -> str:
    rows = []
    for _, row in df.iterrows():
        rows.append(
            "<tr>"
            f"<td><strong>{html.escape(str(row.get('代码', '')))}</strong></td>"
            f"<td>{html.escape(str(row.get('名称', '')))}</td>"
            f"<td>{html.escape(str(row.get('所属行业', '未知')))}</td>"
            f"<td class='num'>{fmt_num(row.get('现价'))}</td>"
            f"<td class='num'>{fmt_num(row.get('半年线'))}</td>"
            f"<td class='num danger'>{fmt_num(row.get('半年线乖离率'), 2, '%')}</td>"
            f"<td class='num'>{fmt_num(row.get('股息率'), 2, '%')}</td>"
            f"<td class='num'>{fmt_num(row.get('市盈率'))}</td>"
            f"<td class='num'>{fmt_num(row.get('市盈率10年分位'), 1, '%')}</td>"
            f"<td class='num'>{fmt_num(row.get('市净率'))}</td>"
            f"<td class='num'>{fmt_num(row.get('市净率10年分位'), 1, '%')}</td>"
            f"<td class='num'>{fmt_market_cap(row.get('总市值'))}</td>"
            f"<td>{html.escape(str(row.get('估值数据日期', '')))}</td>"
            f"<td>{html.escape(str(row.get('价格数据日期', '')))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_html(
    df: pd.DataFrame,
    diagnostics: dict,
    config: ScreenConfig,
    market_monitor: pd.DataFrame | None = None,
) -> str:
    final_count = len(df)
    avg_dividend = df["股息率"].mean() if "股息率" in df.columns and not df.empty else None
    avg_deviation = df["半年线乖离率"].mean() if "半年线乖离率" in df.columns and not df.empty else None
    industries = df["所属行业"].nunique() if "所属行业" in df.columns and not df.empty else 0

    sorted_df = df.copy()
    if not sorted_df.empty and "股息率" in sorted_df.columns:
        sorted_df = sorted_df.sort_values(["股息率", "半年线乖离率"], ascending=[False, True])

    empty_note = ""
    if sorted_df.empty:
        empty_note = (
            "<div class='empty'>"
            "今日没有筛出同时满足价格和股息条件的股票，或外部数据源未完整返回。"
            "请查看顶部诊断信息；首次全市场运行可能耗时较长。"
            "</div>"
        )

    warning = ""
    if any(int(diagnostics.get(key, 0)) > 0 for key in ["valuation_error_count", "price_error_count", "dividend_error_count", "industry_error_count"]):
        warning = (
            "<div class='warning'>"
            "有部分股票数据获取失败。AKShare 依赖外部网站，建议稍后重跑，或升级 AKShare 后使用缓存续跑。"
            "</div>"
        )

    rows = render_table_rows(sorted_df)
    monitor_json = monitor_records_for_html(market_monitor if market_monitor is not None else pd.DataFrame())
    monitor_error = html.escape(str(diagnostics.get("market_monitor_error", "")))
    monitor_source = html.escape(str(diagnostics.get("market_monitor_source", "")))
    monitor_count = int(diagnostics.get("market_monitor_count", 0) or 0)
    monitor_warning = ""
    if monitor_error:
        monitor_warning = f"<div class='warning'>市场监测数据部分获取失败：{monitor_error}</div>"
    css = """
    :root {
      --bg: #f5f7fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d8dee9;
      --accent: #0f766e;
      --accent-2: #2563eb;
      --danger: #b42318;
      --soft: #e8f4f1;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      letter-spacing: 0;
    }
    .hero {
      background: linear-gradient(120deg, #0f766e 0%, #1d4ed8 100%);
      color: white;
      padding: 34px 42px 28px;
    }
    .hero h1 {
      margin: 0 0 10px;
      font-size: 30px;
      line-height: 1.2;
      font-weight: 760;
      letter-spacing: 0;
    }
    .hero p {
      margin: 0;
      max-width: 980px;
      color: rgba(255,255,255,.88);
      font-size: 15px;
      line-height: 1.7;
    }
    .wrap { max-width: 1440px; margin: 0 auto; padding: 24px 28px 42px; }
    .cards {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 14px;
      margin-top: -44px;
      margin-bottom: 18px;
    }
    .card {
      background: var(--panel);
      border: 1px solid rgba(216,222,233,.9);
      border-radius: 8px;
      padding: 16px 18px;
      box-shadow: 0 10px 28px rgba(20, 30, 50, .08);
      min-height: 96px;
    }
    .card .label { color: var(--muted); font-size: 13px; margin-bottom: 8px; }
    .card .value { font-size: 24px; font-weight: 750; line-height: 1.1; }
    .card .hint { color: var(--muted); font-size: 12px; margin-top: 8px; line-height: 1.35; }
    .toolbar {
      display: flex;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
      margin: 16px 0;
      flex-wrap: wrap;
    }
    .search {
      width: min(420px, 100%);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px 12px;
      font-size: 14px;
      background: white;
    }
    .meta {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }
    .tabs {
      display: flex;
      gap: 8px;
      align-items: center;
      margin: 10px 0 18px;
      border-bottom: 1px solid var(--line);
    }
    .tab-btn {
      appearance: none;
      border: 0;
      border-bottom: 3px solid transparent;
      background: transparent;
      color: var(--muted);
      padding: 12px 14px 10px;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
    }
    .tab-btn.active {
      color: var(--accent-2);
      border-bottom-color: var(--accent-2);
    }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
    .warning, .empty {
      border-radius: 8px;
      padding: 13px 15px;
      margin: 14px 0;
      line-height: 1.6;
    }
    .warning { background: #fff4e5; color: #8a4b00; border: 1px solid #ffd8a8; }
    .empty { background: #eef4ff; color: #1e3a8a; border: 1px solid #c7d7fe; }
    .table-wrap {
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: auto;
      box-shadow: 0 8px 22px rgba(20, 30, 50, .05);
    }
    table {
      border-collapse: separate;
      border-spacing: 0;
      width: 100%;
      min-width: 1180px;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid #edf0f5;
      padding: 11px 10px;
      white-space: nowrap;
      text-align: left;
    }
    th {
      position: sticky;
      top: 0;
      z-index: 2;
      background: #f8fafc;
      color: #344054;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      user-select: none;
    }
    th::after {
      content: "↕";
      color: #98a2b3;
      font-size: 11px;
      margin-left: 6px;
    }
    th.sorted-asc::after {
      content: "↑";
      color: var(--accent-2);
    }
    th.sorted-desc::after {
      content: "↓";
      color: var(--accent-2);
    }
    th.sorted-asc, th.sorted-desc {
      color: var(--accent-2);
      background: #eef4ff;
    }
    tr:hover td { background: #f6fbfa; }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .danger { color: var(--danger); font-weight: 700; }
    .monitor-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      margin-bottom: 14px;
      flex-wrap: wrap;
    }
    .monitor-head h2 {
      margin: 0 0 6px;
      font-size: 20px;
      line-height: 1.25;
    }
    .monitor-head p {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
      max-width: 860px;
    }
    .chart-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }
    .chart-box {
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
      min-height: 360px;
      padding: 14px;
      box-shadow: 0 8px 22px rgba(20, 30, 50, .05);
    }
    .chart-title {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: #344054;
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 8px;
    }
    .chart-subtitle {
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
    }
    .chart-canvas {
      position: relative;
      width: 100%;
      height: 292px;
    }
    .chart-canvas svg {
      display: block;
      width: 100%;
      height: 100%;
    }
    .chart-empty {
      height: 292px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      background: #f8fafc;
      border-radius: 6px;
      font-size: 13px;
    }
    .chart-tooltip {
      position: absolute;
      min-width: 148px;
      padding: 8px 10px;
      border-radius: 6px;
      background: rgba(17, 24, 39, .92);
      color: white;
      box-shadow: 0 10px 24px rgba(15, 23, 42, .22);
      font-size: 12px;
      line-height: 1.45;
      pointer-events: none;
      opacity: 0;
      transform: translate(-50%, calc(-100% - 10px));
      transition: opacity .12s ease;
      z-index: 4;
      white-space: nowrap;
    }
    .chart-tooltip.visible { opacity: 1; }
    .chart-tooltip strong {
      display: block;
      font-size: 12px;
      margin-bottom: 2px;
    }
    .chart-tooltip span { color: rgba(255,255,255,.82); }
    .foot {
      margin-top: 16px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.7;
    }
    @media (max-width: 960px) {
      .hero { padding: 26px 20px 72px; }
      .hero h1 { font-size: 24px; }
      .wrap { padding: 18px 14px 32px; }
      .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: -58px; }
      .card .value { font-size: 21px; }
      .chart-grid { grid-template-columns: 1fr; }
      .tabs { overflow-x: auto; }
    }
    """
    script = """
    const marketMonitorData = __MARKET_MONITOR_JSON__;
    let monitorRendered = false;

    function switchTab(tabName) {
      document.querySelectorAll('.tab-btn').forEach(btn => {
        const active = btn.dataset.tab === tabName;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      document.querySelectorAll('.tab-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === `tab-${tabName}`);
      });
      if (tabName === 'monitor') {
        renderMarketCharts();
      }
    }

    function filterTable() {
      const q = document.getElementById('search').value.toLowerCase();
      document.querySelectorAll('#stock-table tbody tr').forEach(row => {
        row.style.display = row.innerText.toLowerCase().includes(q) ? '' : 'none';
      });
    }
    function sortTable(col) {
      const table = document.getElementById('stock-table');
      const tbody = table.tBodies[0];
      const rows = Array.from(tbody.rows);
      const previousCol = table.getAttribute('data-sort-col');
      const previousDir = table.getAttribute('data-sort-dir');
      const asc = previousCol != String(col) || previousDir === 'desc';
      const parseCell = (cell) => {
        const raw = cell.innerText.trim();
        const normalized = raw.replace(/[,%亿]/g, '').trim();
        const numeric = parseFloat(normalized);
        return {
          raw,
          numeric,
          isNumber: raw !== '-' && raw !== '' && !Number.isNaN(numeric)
        };
      };
      rows.sort((a, b) => {
        const av = parseCell(a.cells[col]);
        const bv = parseCell(b.cells[col]);
        if (av.isNumber || bv.isNumber) {
          if (!av.isNumber) return 1;
          if (!bv.isNumber) return -1;
          return asc ? av.numeric - bv.numeric : bv.numeric - av.numeric;
        }
        return asc ? av.raw.localeCompare(bv.raw, 'zh-Hans-CN') : bv.raw.localeCompare(av.raw, 'zh-Hans-CN');
      });
      rows.forEach(row => tbody.appendChild(row));
      table.setAttribute('data-sort-col', col);
      table.setAttribute('data-sort-dir', asc ? 'asc' : 'desc');
      table.querySelectorAll('th').forEach((th, index) => {
        th.classList.remove('sorted-asc', 'sorted-desc');
        th.setAttribute('aria-sort', 'none');
        if (index === col) {
          th.classList.add(asc ? 'sorted-asc' : 'sorted-desc');
          th.setAttribute('aria-sort', asc ? 'ascending' : 'descending');
        }
      });
    }
    function formatNumber(value, digits = 2, suffix = '') {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
      return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits }) + suffix;
    }
    function renderLineChart(targetId, key, unit, color) {
      const target = document.getElementById(targetId);
      if (!target) return;
      const points = marketMonitorData
        .map(row => ({ date: row['日期'], value: row[key] }))
        .filter(row => row.date && row.value !== null && row.value !== undefined && !Number.isNaN(Number(row.value)));
      if (points.length < 2) {
        target.innerHTML = "<div class='chart-empty'>暂无足够数据</div>";
        return;
      }
      const width = 760;
      const height = 292;
      const margin = { top: 20, right: 24, bottom: 34, left: 58 };
      const values = points.map(p => Number(p.value));
      let min = Math.min(...values);
      let max = Math.max(...values);
      if (min === max) {
        min -= 1;
        max += 1;
      }
      const pad = (max - min) * 0.12;
      min -= pad;
      max += pad;
      const x = index => margin.left + index * (width - margin.left - margin.right) / (points.length - 1);
      const y = value => margin.top + (max - Number(value)) * (height - margin.top - margin.bottom) / (max - min);
      const step = (width - margin.left - margin.right) / (points.length - 1);
      const line = points.map((p, index) => `${x(index).toFixed(2)},${y(p.value).toFixed(2)}`).join(' ');
      const yTicks = Array.from({ length: 5 }, (_, index) => min + (max - min) * index / 4);
      const xTickIndexes = Array.from(new Set([0, Math.floor((points.length - 1) / 2), points.length - 1]));
      const grid = yTicks.map(value => {
        const yy = y(value);
        return `<line x1="${margin.left}" y1="${yy}" x2="${width - margin.right}" y2="${yy}" stroke="#edf0f5"/><text x="${margin.left - 10}" y="${yy + 4}" text-anchor="end" fill="#667085" font-size="11">${formatNumber(value, 2, unit)}</text>`;
      }).join('');
      const xLabels = xTickIndexes.map(index => {
        const p = points[index];
        return `<text x="${x(index)}" y="${height - 10}" text-anchor="${index === 0 ? 'start' : index === points.length - 1 ? 'end' : 'middle'}" fill="#667085" font-size="11">${p.date.slice(5)}</text>`;
      }).join('');
      const dots = points.map((p, index) => {
        const title = `${p.date}：${formatNumber(p.value, 2, unit)}`;
        return `<circle cx="${x(index).toFixed(2)}" cy="${y(p.value).toFixed(2)}" r="3" fill="${color}"><title>${title}</title></circle>`;
      }).join('');
      target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${key}曲线">
        ${grid}
        <line x1="${margin.left}" y1="${height - margin.bottom}" x2="${width - margin.right}" y2="${height - margin.bottom}" stroke="#d8dee9"/>
        <polyline fill="none" stroke="${color}" stroke-width="2.5" points="${line}"/>
        ${dots}
        ${xLabels}
        <line class="hover-guide" x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${height - margin.bottom}" stroke="#98a2b3" stroke-dasharray="4 4" opacity="0"/>
        <circle class="hover-dot" cx="${margin.left}" cy="${margin.top}" r="5" fill="${color}" stroke="white" stroke-width="2" opacity="0"/>
      </svg><div class="chart-tooltip" role="status" aria-live="polite"></div>`;
      const svg = target.querySelector('svg');
      const tooltip = target.querySelector('.chart-tooltip');
      const guide = target.querySelector('.hover-guide');
      const hoverDot = target.querySelector('.hover-dot');
      const hideTooltip = () => {
        tooltip.classList.remove('visible');
        guide.setAttribute('opacity', '0');
        hoverDot.setAttribute('opacity', '0');
      };
      const showTooltip = event => {
        const rect = svg.getBoundingClientRect();
        const svgX = (event.clientX - rect.left) * width / rect.width;
        const nearest = Math.max(0, Math.min(points.length - 1, Math.round((svgX - margin.left) / step)));
        const point = points[nearest];
        const cx = x(nearest);
        const cy = y(point.value);
        guide.setAttribute('x1', cx.toFixed(2));
        guide.setAttribute('x2', cx.toFixed(2));
        guide.setAttribute('opacity', '1');
        hoverDot.setAttribute('cx', cx.toFixed(2));
        hoverDot.setAttribute('cy', cy.toFixed(2));
        hoverDot.setAttribute('opacity', '1');
        tooltip.innerHTML = `<strong>${point.date}</strong><span>${key}：${formatNumber(point.value, 2, unit)}</span>`;
        tooltip.style.left = `${cx / width * rect.width}px`;
        tooltip.style.top = `${cy / height * rect.height}px`;
        tooltip.classList.add('visible');
      };
      target.addEventListener('pointermove', showTooltip);
      target.addEventListener('pointerleave', hideTooltip);
    }
    function renderMarketCharts() {
      if (monitorRendered) return;
      renderLineChart('volume-ratio-chart', '沪深300成交占比', '%', '#0f766e');
      renderLineChart('margin-change-chart', '融资余额增长率', '%', '#2563eb');
      const latest = [...marketMonitorData].reverse().find(row => row['沪深300成交占比'] !== null || row['融资余额增长率'] !== null);
      if (latest) {
        const latestNode = document.getElementById('monitor-latest');
        if (latestNode) {
          latestNode.textContent = `最新 ${latest['日期']}：成交占比 ${formatNumber(latest['沪深300成交占比'], 2, '%')}，融资余额增长率 ${formatNumber(latest['融资余额增长率'], 2, '%')}`;
        }
      }
      monitorRendered = true;
    }
    window.addEventListener('resize', () => {
      const panel = document.getElementById('tab-monitor');
      if (panel && panel.classList.contains('active')) {
        monitorRendered = false;
        renderMarketCharts();
      }
    });
    """
    script = script.replace("__MARKET_MONITOR_JSON__", monitor_json)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>A 股低估高息筛选看板</title>
  <style>{css}</style>
</head>
<body>
  <section class="hero">
    <h1>A 股低估高息筛选看板</h1>
    <p>筛选条件：现价低于 120 日半年线 10% 以上，且股息率大于 3%。市盈率和市净率仅展示，不参与筛选。数据来自 AKShare 聚合的东方财富、百度估值与巨潮资讯接口。</p>
  </section>
  <main class="wrap">
    <section class="cards">
      <div class="card"><div class="label">入选股票</div><div class="value">{final_count}</div><div class="hint">同时满足价格和股息条件</div></div>
      <div class="card"><div class="label">覆盖股票</div><div class="value">{diagnostics.get("universe_count", 0)}</div><div class="hint">{html.escape(str(diagnostics.get("mode", "")))}</div></div>
      <div class="card"><div class="label">平均股息率</div><div class="value">{fmt_num(avg_dividend, 2, "%")}</div><div class="hint">入选股票均值</div></div>
      <div class="card"><div class="label">平均半年线乖离</div><div class="value">{fmt_num(avg_deviation, 2, "%")}</div><div class="hint">越低代表越偏离半年线</div></div>
      <div class="card"><div class="label">覆盖行业</div><div class="value">{industries}</div><div class="hint">按东方财富个股行业</div></div>
    </section>
    <nav class="tabs" role="tablist" aria-label="看板标签页">
      <button class="tab-btn active" data-tab="screen" role="tab" aria-selected="true" onclick="switchTab('screen')">低估高息筛选</button>
      <button class="tab-btn" data-tab="monitor" role="tab" aria-selected="false" onclick="switchTab('monitor')">市场监测</button>
    </nav>
    <section id="tab-screen" class="tab-panel active" role="tabpanel">
      <div class="toolbar">
        <input id="search" class="search" oninput="filterTable()" placeholder="搜索代码、名称、行业">
        <div class="meta">
          生成时间：{html.escape(str(diagnostics.get("generated_at", "")))}<br>
          价格预筛：{diagnostics.get("price_prefilter_count", 0)}；股息筛后：{diagnostics.get("valuation_prefilter_count", 0)}；价格/估值数据失败：{diagnostics.get("price_error_count", 0)}；股息失败：{diagnostics.get("dividend_error_count", 0)}；行业失败：{diagnostics.get("industry_error_count", 0)}
        </div>
      </div>
      {warning}
      {empty_note}
      <div class="table-wrap">
        <table id="stock-table">
          <thead>
            <tr>
              <th onclick="sortTable(0)">代码</th>
              <th onclick="sortTable(1)">名称</th>
              <th onclick="sortTable(2)">所属行业</th>
              <th onclick="sortTable(3)" class="num">股价</th>
              <th onclick="sortTable(4)" class="num">半年线</th>
              <th onclick="sortTable(5)" class="num">半年线乖离率</th>
              <th onclick="sortTable(6)" class="num">股息率</th>
              <th onclick="sortTable(7)" class="num">市盈率</th>
              <th onclick="sortTable(8)" class="num">PE 10年分位</th>
              <th onclick="sortTable(9)" class="num">市净率</th>
              <th onclick="sortTable(10)" class="num">PB 10年分位</th>
              <th onclick="sortTable(11)" class="num">总市值</th>
              <th onclick="sortTable(12)">估值日期</th>
              <th onclick="sortTable(13)">价格日期</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
      <div class="foot">
        口径说明：半年线使用最近 {config.price_window} 个交易日收盘价均值；半年线乖离率 = 现价 / 半年线 - 1；PE/PB 和历史分位仅用于参考展示，不参与筛选。该看板只做量化筛选，不构成投资建议。
      </div>
    </section>
    <section id="tab-monitor" class="tab-panel" role="tabpanel">
      <div class="monitor-head">
        <div>
          <h2>最近90天市场监测</h2>
          <p>沪深300成交占比 = 沪深300成交量 / 沪深北三个市场成交量合计；融资余额增长率 = 当天融资余额 / 前一交易日融资余额 - 1。</p>
        </div>
        <div class="meta">
          样本交易日：{monitor_count}<br>
          <span id="monitor-latest">切换后显示最新值</span>
        </div>
      </div>
      {monitor_warning}
      <div class="chart-grid">
        <section class="chart-box">
          <div class="chart-title">
            <span>沪深300成交占比</span>
            <span class="chart-subtitle">单位：%</span>
          </div>
          <div id="volume-ratio-chart" class="chart-canvas"></div>
        </section>
        <section class="chart-box">
          <div class="chart-title">
            <span>融资余额增长率</span>
            <span class="chart-subtitle">单位：%</span>
          </div>
          <div id="margin-change-chart" class="chart-canvas"></div>
        </section>
      </div>
      <div class="foot">
        市场监测口径：{monitor_source}。时间窗口使用最近 {config.monitor_days} 个自然日。
      </div>
    </section>
  </main>
  <script>{script}</script>
</body>
</html>"""


def write_dashboard(
    df: pd.DataFrame,
    diagnostics: dict,
    config: ScreenConfig,
    market_monitor: pd.DataFrame | None = None,
) -> Path:
    output = config.output
    output.parent.mkdir(parents=True, exist_ok=True) if output.parent != Path(".") else None
    html_text = build_html(df, diagnostics, config, market_monitor)
    output.write_text(html_text, encoding="utf-8")
    csv_output = output.with_suffix(".csv")
    export_cols = [
        "代码",
        "名称",
        "所属行业",
        "现价",
        "半年线",
        "半年线乖离率",
        "股息率",
        "市盈率",
        "市盈率10年分位",
        "市净率",
        "市净率10年分位",
        "总市值",
        "估值数据日期",
        "股息数据日期",
        "价格数据日期",
    ]
    export = df.copy()
    for col in export_cols:
        if col not in export.columns:
            export[col] = None
    export[export_cols].to_csv(csv_output, index=False, encoding="utf-8-sig")
    if market_monitor is not None and not market_monitor.empty:
        market_monitor.to_csv(
            output.with_name(f"{output.stem}_market_monitor.csv"),
            index=False,
            encoding="utf-8-sig",
        )
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="每日 A 股低估高息筛选看板")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出 HTML 文件路径")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="缓存目录")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="本地增量数据目录")
    parser.add_argument("--price-window", type=int, default=120, help="半年线交易日窗口，默认 120")
    parser.add_argument("--valuation-years", type=int, default=10, help="估值分位历史年限，默认 10")
    parser.add_argument("--max-below-ma-pct", type=float, default=-10.0, help="现价相对半年线最大乖离率，默认 -10")
    parser.add_argument("--min-dividend-yield-pct", type=float, default=3.0, help="最低股息率，默认 3")
    parser.add_argument("--max-percentile", type=float, default=30.0, help="PE/PB 最高历史分位，默认 30")
    parser.add_argument("--valuation-workers", type=int, default=3, help="估值接口并发数，默认 3")
    parser.add_argument("--price-workers", type=int, default=8, help="价格接口并发数，默认 8")
    parser.add_argument("--industry-workers", type=int, default=6, help="行业接口并发数，默认 6")
    parser.add_argument("--spot-workers", type=int, default=8, help="实时行情分页并发数，默认 8")
    parser.add_argument("--spot-timeout", type=float, default=60.0, help="实时行情单页超时时间，默认 60 秒")
    parser.add_argument("--request-pause", type=float, default=0.0, help="估值接口每次调用前暂停秒数")
    parser.add_argument("--dividend-lookback-days", type=int, default=365, help="股息率回看天数，默认 365")
    parser.add_argument("--monitor-days", type=int, default=90, help="市场监测自然日窗口，默认 90 天")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 只股票，调试用")
    parser.add_argument("--refresh", action="store_true", help="忽略当日缓存，重新拉取")
    parser.add_argument("--rebuild-history", action="store_true", help="强制重建每只股票的历史库")
    parser.add_argument("--demo", action="store_true", help="使用演示数据生成看板，不访问外部接口")
    parser.add_argument("--open", action="store_true", help="生成后用默认浏览器打开")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ScreenConfig(
        price_window=args.price_window,
        max_below_ma_pct=args.max_below_ma_pct,
        min_dividend_yield_pct=args.min_dividend_yield_pct,
        valuation_years=args.valuation_years,
        max_percentile=args.max_percentile,
        price_workers=args.price_workers,
        valuation_workers=args.valuation_workers,
        industry_workers=args.industry_workers,
        spot_workers=args.spot_workers,
        spot_timeout=args.spot_timeout,
        request_pause=args.request_pause,
        dividend_lookback_days=args.dividend_lookback_days,
        monitor_days=args.monitor_days,
        limit=args.limit,
        refresh=args.refresh,
        rebuild_history=args.rebuild_history,
        cache_dir=Path(args.cache_dir),
        data_dir=Path(args.data_dir),
        output=Path(args.output),
        open_browser=args.open,
    )

    log("A 股低估高息筛选开始")
    try:
        if args.demo:
            df, diagnostics = demo_screen(config)
            market_monitor, monitor_diagnostics = demo_market_monitor(config)
        else:
            df, diagnostics = build_real_screen(config)
            market_monitor, monitor_diagnostics = build_market_monitor(config)
        diagnostics.update(monitor_diagnostics)
        output = write_dashboard(df, diagnostics, config, market_monitor)
        log(f"看板已生成：{output.resolve()}")
        log(f"入选股票数：{len(df)}")
        log(f"市场监测样本数：{len(market_monitor)}")
        if config.open_browser:
            webbrowser.open(output.resolve().as_uri())
        return 0
    except Exception as exc:
        log(f"生成失败：{exc}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
