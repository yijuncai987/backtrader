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


def ensure_data_dir(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "spot").mkdir(exist_ok=True)
    (data_dir / "value_history").mkdir(exist_ok=True)
    (data_dir / "dividends").mkdir(exist_ok=True)
    (data_dir / "industry").mkdir(exist_ok=True)
    (data_dir / "screening_results").mkdir(exist_ok=True)


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
        df = fetch_incremental_value_history(row, config)
        date_col = pick_column(df.columns, ["数据日期", "date", "日期"])
        close_col = pick_column(df.columns, ["当日收盘价", "收盘", "close"])
        pe_col = pick_column(df.columns, ["PE(TTM)", "pe_ttm", "市盈率ttm"])
        pb_col = pick_column(df.columns, ["市净率", "pb"])
        cap_col = pick_column(df.columns, ["总市值"])

        required_missing = [
            name
            for name, col in [("date", date_col), ("close", close_col), ("pe", pe_col), ("pb", pb_col)]
            if col is None
        ]
        if required_missing:
            raise RuntimeError(f"东方财富估值字段缺失：{required_missing}；实际字段：{df.columns.tolist()}")

        work = df.copy()
        work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
        work[close_col] = pd.to_numeric(work[close_col], errors="coerce")
        work[pe_col] = pd.to_numeric(work[pe_col], errors="coerce")
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
        pe = to_float(latest[pe_col])
        pb = to_float(latest[pb_col])
        pe_pct = percentile_rank(pe, recent[pe_col])
        pb_pct = percentile_rank(pb, recent[pb_col])
        pe_pass = pe_pct is not None and pe_pct <= config.max_percentile
        pb_pass = pb_pct is not None and pb_pct <= config.max_percentile
        value_date = latest[date_col].date().isoformat()
        market_cap = to_float(latest[cap_col]) if cap_col else to_float(row.get("总市值"))

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
            "估值数据日期": value_date,
            "总市值_估值源": market_cap,
            "价格估值错误": "",
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


def build_html(df: pd.DataFrame, diagnostics: dict, config: ScreenConfig) -> str:
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
    }
    """
    script = """
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
    """
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
    <p>筛选条件：现价低于 120 日半年线 10% 以上，且股息率大于 3%。市盈率和市净率仅展示，不参与筛选。数据来自 AKShare 聚合的东方财富与巨潮资讯接口。</p>
  </section>
  <main class="wrap">
    <section class="cards">
      <div class="card"><div class="label">入选股票</div><div class="value">{final_count}</div><div class="hint">同时满足价格和股息条件</div></div>
      <div class="card"><div class="label">覆盖股票</div><div class="value">{diagnostics.get("universe_count", 0)}</div><div class="hint">{html.escape(str(diagnostics.get("mode", "")))}</div></div>
      <div class="card"><div class="label">平均股息率</div><div class="value">{fmt_num(avg_dividend, 2, "%")}</div><div class="hint">入选股票均值</div></div>
      <div class="card"><div class="label">平均半年线乖离</div><div class="value">{fmt_num(avg_deviation, 2, "%")}</div><div class="hint">越低代表越偏离半年线</div></div>
      <div class="card"><div class="label">覆盖行业</div><div class="value">{industries}</div><div class="hint">按东方财富个股行业</div></div>
    </section>
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
  </main>
  <script>{script}</script>
</body>
</html>"""


def write_dashboard(df: pd.DataFrame, diagnostics: dict, config: ScreenConfig) -> Path:
    output = config.output
    output.parent.mkdir(parents=True, exist_ok=True) if output.parent != Path(".") else None
    html_text = build_html(df, diagnostics, config)
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
        else:
            df, diagnostics = build_real_screen(config)
        output = write_dashboard(df, diagnostics, config)
        log(f"看板已生成：{output.resolve()}")
        log(f"入选股票数：{len(df)}")
        if config.open_browser:
            webbrowser.open(output.resolve().as_uri())
        return 0
    except Exception as exc:
        log(f"生成失败：{exc}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
