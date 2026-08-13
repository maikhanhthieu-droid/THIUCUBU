#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import math
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import scan
import scan_safe
import scoring
import weekly_sniper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("thieucutoo.weekend")

VN_TZ = timezone(timedelta(hours=7))
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
FUNDAMENTAL_HISTORY_PATH = DATA_DIR / "fundamental_history.json"
_FUNDAMENTAL_HISTORY_CACHE: dict[str, list[dict[str, Any]]] | None = None

try:
    from vnstock import Fundamental as VnFundamental
except Exception as exc:  # pragma: no cover - depends on vnstock build
    VnFundamental = None
    logger.warning("Cannot import vnstock.Fundamental: %s", exc)

try:
    from vnstock import Company as VnCompany
except Exception as exc:  # pragma: no cover - depends on vnstock build
    VnCompany = None
    logger.warning("Cannot import vnstock.Company: %s", exc)


def env_int(name: str, default: int, min_value: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using %s", name, raw, default)
        return default
    return max(min_value, value)


def env_float(name: str, default: float, min_value: float = 0.0, max_value: float | None = None) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using %s", name, raw, default)
        return default
    value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


TOP_N = env_int("WEEKEND_TOP_N", 20, min_value=5)
CONVICTION_LIMIT = env_int("WEEKEND_CONVICTION_LIMIT", 2, min_value=1)
MIN_SCORE = env_int("WEEKEND_MIN_SCORE", 58, min_value=0)
MIN_SECTOR_SCORE = env_int("WEEKEND_MIN_SECTOR_SCORE", 50, min_value=0)
HISTORY_BARS = env_int("WEEKEND_HISTORY_BARS", 780, min_value=520)
FUNDAMENTAL_DELAY_MIN = env_float("WEEKEND_FUNDAMENTAL_DELAY_MIN_SEC", 0.7, min_value=0.0)
FUNDAMENTAL_DELAY_MAX = max(
    FUNDAMENTAL_DELAY_MIN,
    env_float("WEEKEND_FUNDAMENTAL_DELAY_MAX_SEC", 2.2, min_value=0.0),
)
RANDOM_START_MAX = env_int("WEEKEND_RANDOM_START_MAX_SEC", 600, min_value=0)
WEEKLY_INDEX_DF: pd.DataFrame | None = None

FINANCIAL_SECTORS = {"Bank", "Chung khoan", "Bao hiem"}
PB_HEAVY_SECTORS = {
    "Bank",
    "Chung khoan",
    "Bao hiem",
    "BDS dan cu",
    "BDS KCN",
    "Dau khi",
    "Dien tien ich",
    "Thep",
    "Xay dung dau tu cong",
}


@dataclass
class FundamentalSnapshot:
    symbol: str
    pe: float | None
    pb: float | None
    roe: float | None
    roa: float | None
    debt_to_equity: float | None
    current_ratio: float | None
    profit_margin: float | None
    eps: float | None
    period: str
    source: str


@dataclass
class SectorSnapshot:
    sector: str
    score: int
    avg_win_score: float
    median_pe: float | None
    median_pb: float | None
    median_roe: float | None
    failed_ratio: float
    count: int


@dataclass
class Opportunity:
    symbol: str
    sector: str
    close: float
    action: str
    opportunity_score: int
    valuation_score: int
    quality_score: int
    technical_score: int
    sector_score: int
    risk_score: int
    pe: float | None
    pb: float | None
    sector_pe: float | None
    sector_pb: float | None
    pe_discount_pct: float | None
    pb_discount_pct: float | None
    roe: float | None
    roa: float | None
    debt_to_equity: float | None
    discount_pct: float
    target_discount_pct: float
    setup: str
    bull_case: str
    bear_case: str
    data_source: str
    grade: str = "D"
    confidence: int = 0
    structure_score: int = 0
    timing_score: int = 0
    structure_state: str = "NO_DATA"
    trigger: str = "WAIT"
    risk_reward: float | None = None
    buy_zone_low: float | None = None
    buy_zone_high: float | None = None
    breakout_price: float | None = None
    invalidation_price: float | None = None
    selected: bool = False
    thesis_status: str = "watch"
    score_version: str = scoring.SCORE_VERSION
    weekly: dict[str, Any] | None = None
    as_of: str | None = None
    cache_status: str = "unknown"


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def clean_key(value: Any) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
        if not value or value.lower() in {"nan", "none", "null", "-"}:
            return None
    try:
        result = float(value)
    except Exception:
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def as_percent(value: Any) -> float | None:
    result = safe_float(value)
    if result is None:
        return None
    if -1.5 <= result <= 1.5:
        return result * 100
    return result


def median(values: list[float]) -> float | None:
    values = sorted(v for v in values if v is not None and math.isfinite(v))
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def load_fundamental_history() -> dict[str, list[dict[str, Any]]]:
    global _FUNDAMENTAL_HISTORY_CACHE
    if _FUNDAMENTAL_HISTORY_CACHE is not None:
        return _FUNDAMENTAL_HISTORY_CACHE
    raw = scan.json_load(FUNDAMENTAL_HISTORY_PATH, {})
    if not isinstance(raw, dict):
        _FUNDAMENTAL_HISTORY_CACHE = {}
        return _FUNDAMENTAL_HISTORY_CACHE
    _FUNDAMENTAL_HISTORY_CACHE = {
        str(symbol).upper(): [row for row in rows if isinstance(row, dict)][-104:]
        for symbol, rows in raw.items()
        if isinstance(rows, list)
    }
    return _FUNDAMENTAL_HISTORY_CACHE


def historical_multiple(symbol: str, field: str) -> float | None:
    rows = load_fundamental_history().get(symbol.upper(), [])
    values = [safe_float(row.get(field)) for row in rows]
    return median([value for value in values if value is not None and value > 0])


def save_fundamental_history(packets: list[dict[str, Any]]) -> None:
    global _FUNDAMENTAL_HISTORY_CACHE
    history = load_fundamental_history()
    now = datetime.now(VN_TZ).isoformat(timespec="seconds")
    for packet in packets:
        fund = packet.get("fundamental")
        if fund is None:
            continue
        symbol = str(packet.get("symbol") or "").upper()
        rows = history.setdefault(symbol, [])
        record = {
            "captured_at": now,
            "period": fund.period,
            "pe": fund.pe,
            "pb": fund.pb,
            "roe": fund.roe,
            "roa": fund.roa,
            "eps": fund.eps,
            "source": fund.source,
        }
        # Replace another observation from the same VN date so reruns do not
        # distort the historical median.
        vn_day = now[:10]
        rows = [row for row in rows if str(row.get("captured_at") or "")[:10] != vn_day]
        rows.append(record)
        history[symbol] = rows[-104:]
    scan.json_save(FUNDAMENTAL_HISTORY_PATH, history, pretty=False)
    _FUNDAMENTAL_HISTORY_CACHE = history


def fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def fmt_pct(value: float | None, digits: int = 0, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.{digits}f}%"


def recent_market_data(as_of: Any, max_age_days: int = 10) -> bool:
    try:
        value = datetime.fromisoformat(str(as_of)).date()
        return 0 <= (datetime.now(VN_TZ).date() - value).days <= max_age_days
    except (TypeError, ValueError):
        return False


def set_weekly_index(df: pd.DataFrame | None) -> None:
    global WEEKLY_INDEX_DF
    WEEKLY_INDEX_DF = df


ALIASES = {
    "pe": ["pe", "p/e", "pe_ratio", "peratio", "priceToEarning", "priceToEarnings", "price_to_earning"],
    "pb": ["pb", "p/b", "pb_ratio", "pbratio", "priceToBook", "price_to_book", "priceToBookRatio"],
    "roe": ["roe", "returnOnEquity", "return_on_equity"],
    "roa": ["roa", "returnOnAssets", "return_on_assets"],
    "debt": ["debt_to_equity", "debtToEquity", "debt/equity"],
    "current": ["current_ratio", "currentRatio"],
    "margin": ["profit_margin", "net_margin", "netMargin"],
    "eps": ["eps", "earningPerShare", "earningsPerShare"],
    "period": ["date", "year_report", "year", "quarter", "report_date"],
}


def value_from_row(row: pd.Series, names: list[str]) -> Any:
    columns = {clean_key(col): col for col in row.index}
    for name in names:
        key = clean_key(name)
        if key in columns:
            return row[columns[key]]
    return None


def latest_ratio_row(df: pd.DataFrame) -> pd.Series | None:
    if df is None or df.empty:
        return None
    data = df.copy()
    sort_cols = [col for col in data.columns if clean_key(col) in {"date", "yearreport", "year", "quarter", "reportdate"}]
    if sort_cols:
        try:
            return data.sort_values(sort_cols).iloc[-1]
        except Exception:
            pass
    return data.iloc[-1]


def snapshot_from_df(symbol: str, df: pd.DataFrame, source: str) -> FundamentalSnapshot | None:
    row = latest_ratio_row(df)
    if row is None:
        return None
    pe = safe_float(value_from_row(row, ALIASES["pe"]))
    pb = safe_float(value_from_row(row, ALIASES["pb"]))
    if pe is None and pb is None:
        return None
    period_raw = value_from_row(row, ALIASES["period"])
    return FundamentalSnapshot(
        symbol=symbol,
        pe=pe,
        pb=pb,
        roe=as_percent(value_from_row(row, ALIASES["roe"])),
        roa=as_percent(value_from_row(row, ALIASES["roa"])),
        debt_to_equity=safe_float(value_from_row(row, ALIASES["debt"])),
        current_ratio=safe_float(value_from_row(row, ALIASES["current"])),
        profit_margin=as_percent(value_from_row(row, ALIASES["margin"])),
        eps=safe_float(value_from_row(row, ALIASES["eps"])),
        period=str(period_raw) if period_raw is not None else "",
        source=source,
    )


def call_ratio_method(method: Any, args: tuple[Any, ...], source: str, symbol: str) -> FundamentalSnapshot | None:
    kwargs_options = (
        {"period": "quarter", "orient": "time_series"},
        {"orient": "time_series"},
        {"period": "quarter"},
        {},
    )
    for kwargs in kwargs_options:
        try:
            df = method(*args, **kwargs)
        except TypeError:
            continue
        except Exception as exc:
            logger.debug("%s %s failed: %s", source, symbol, exc)
            continue
        snap = snapshot_from_df(symbol, df, source)
        if snap:
            return snap
    return None


def fetch_fundamental(symbol: str) -> FundamentalSnapshot | None:
    if VnFundamental is not None:
        try:
            fun = VnFundamental()
            equity = getattr(fun, "equity", None)
            if callable(equity):
                try:
                    equity_obj = equity(symbol)
                    method = getattr(equity_obj, "ratio", None)
                    if callable(method):
                        snap = call_ratio_method(method, (), "Fundamental.equity().ratio", symbol)
                        if snap:
                            return snap
                except Exception as exc:
                    logger.debug("Fundamental equity object %s failed: %s", symbol, exc)
            method = getattr(equity, "ratio", None)
            if callable(method):
                snap = call_ratio_method(method, (symbol,), "Fundamental.equity.ratio", symbol)
                if snap:
                    return snap
        except Exception as exc:
            logger.debug("Fundamental %s failed: %s", symbol, exc)

    if VnCompany is not None:
        for source in ("VCI", "KBS"):
            try:
                company = VnCompany(symbol=symbol, source=source)
                method = getattr(company, "ratio_summary", None)
                if callable(method):
                    df = method()
                    snap = snapshot_from_df(symbol, df, f"Company.ratio_summary({source})")
                    if snap:
                        return snap
            except Exception as exc:
                logger.debug("Company %s %s failed: %s", source, symbol, exc)
    return None


def build_universe(mode: str) -> list[str]:
    if mode == "test":
        return ["VCB", "TCB", "FPT", "HPG", "KDH", "DIG", "CEO", "PDR", "SSI", "VIX", "PVD", "VNM"]
    tickers = list(scan.ALL_TICKERS)
    portfolio = scan.json_load(DATA_DIR / "portfolio.json", [])
    for item in portfolio:
        symbol = str(item.get("symbol", "")).upper().strip()
        if symbol and symbol not in tickers:
            tickers.append(symbol)
    notes = scan.json_load(DATA_DIR / "notes.json", {})
    if isinstance(notes, dict):
        tickers.extend(str(symbol).upper() for symbol in notes)
    memory = scan.json_load(DATA_DIR / "memory_state.json", {})
    if isinstance(memory, dict):
        for bucket in ("strong_stocks", "watchlist"):
            for item in memory.get(bucket, []):
                if isinstance(item, dict):
                    tickers.append(str(item.get("symbol") or "").upper())
        tickers.extend(str(symbol).upper() for symbol in memory.get("session_focus", []))
    latest = scan.json_load(DATA_DIR / "results_latest.json", [])
    if isinstance(latest, list):
        for item in latest:
            if not isinstance(item, dict):
                continue
            if int(item.get("win_score") or 0) >= 60 or str(item.get("action")) in {"CANH_MUA", "CANH_GOM"}:
                tickers.append(str(item.get("symbol") or "").upper())
    return sorted({symbol for symbol in tickers if 3 <= len(symbol) <= 12 and symbol.isalnum()})


def fetch_symbol_packet(symbol: str, force_refresh: bool) -> dict[str, Any]:
    sector = scan.TICKER_TO_SECTOR.get(symbol, "Other")
    df = scan_safe.fetch_ohlcv_safe(symbol, bars=HISTORY_BARS, force_refresh=force_refresh)
    tech = scan.analyze_symbol(symbol, df) if df is not None else None
    weekly = weekly_sniper.analyze_weekly_structure(df, WEEKLY_INDEX_DF)
    time.sleep(random.uniform(FUNDAMENTAL_DELAY_MIN, FUNDAMENTAL_DELAY_MAX))
    fundamental = fetch_fundamental(symbol)
    close = safe_float(df["close"].iloc[-1]) if df is not None and not df.empty else None
    if close is None and tech is not None:
        close = tech.close
    as_of = (str(df.attrs.get("as_of") or "") or None) if df is not None else None
    cache_status = str(df.attrs.get("cache_status") or "unknown") if df is not None else "missing"
    return {
        "symbol": symbol,
        "sector": sector,
        "df": df,
        "tech": tech,
        "fundamental": fundamental,
        "historical_pe": historical_multiple(symbol, "pe"),
        "historical_pb": historical_multiple(symbol, "pb"),
        "weekly": weekly,
        "as_of": as_of,
        "cache_status": cache_status,
        "close": close,
    }


def build_sector_snapshots(packets: list[dict[str, Any]]) -> dict[str, SectorSnapshot]:
    by_sector: dict[str, list[dict[str, Any]]] = {}
    for packet in packets:
        by_sector.setdefault(packet["sector"], []).append(packet)

    snapshots: dict[str, SectorSnapshot] = {}
    for sector, items in by_sector.items():
        pe_values = []
        pb_values = []
        roe_values = []
        win_values = []
        failed = 0
        for item in items:
            fund = item["fundamental"]
            tech = item["tech"]
            if fund is not None:
                if fund.pe is not None and fund.pe > 0:
                    pe_values.append(fund.pe)
                if fund.pb is not None and fund.pb > 0:
                    pb_values.append(fund.pb)
                if fund.roe is not None:
                    roe_values.append(fund.roe)
            if tech is not None:
                win_values.append(float(tech.win_score))
                if tech.failed_break:
                    failed += 1
        avg_win = sum(win_values) / len(win_values) if win_values else 45.0
        median_roe = median(roe_values)
        quality_bonus = 0.0
        if median_roe is not None:
            quality_bonus = clamp((median_roe - 8) * 1.1, -10, 16)
        failed_ratio = failed / max(len(items), 1)
        score = int(clamp(avg_win * 0.78 + quality_bonus + (4 if len(items) >= 4 else 0) - failed_ratio * 20))
        snapshots[sector] = SectorSnapshot(
            sector=sector,
            score=score,
            avg_win_score=round(avg_win, 1),
            median_pe=median(pe_values),
            median_pb=median(pb_values),
            median_roe=median_roe,
            failed_ratio=round(failed_ratio, 3),
            count=len(items),
        )
    return snapshots


def relative_discount(value: float | None, benchmark: float | None) -> float | None:
    if value is None or benchmark is None or value <= 0 or benchmark <= 0:
        return None
    return (benchmark - value) / benchmark * 100


def valuation_score(packet: dict[str, Any], sector: SectorSnapshot) -> tuple[int, float | None, float | None]:
    fund = packet["fundamental"]
    tech = packet["tech"]
    if fund is None:
        return 35, None, None

    pe_discount = relative_discount(fund.pe, sector.median_pe)
    pb_discount = relative_discount(fund.pb, sector.median_pb)
    score = 50.0

    pb_weight = 0.55 if packet["sector"] in PB_HEAVY_SECTORS else 0.32
    pe_weight = 0.52 if packet["sector"] not in PB_HEAVY_SECTORS else 0.35
    if pb_discount is not None:
        score += clamp(pb_discount * pb_weight, -18, 28)
    if pe_discount is not None:
        score += clamp(pe_discount * pe_weight, -16, 25)

    own_pe_discount = relative_discount(fund.pe, packet.get("historical_pe"))
    own_pb_discount = relative_discount(fund.pb, packet.get("historical_pb"))
    if own_pe_discount is not None:
        score += clamp(own_pe_discount * 0.24, -9, 13)
    if own_pb_discount is not None:
        score += clamp(own_pb_discount * 0.22, -8, 12)

    if fund.pb is not None and fund.pb > 0:
        if fund.pb <= 1.0:
            score += 12
        elif fund.pb <= 1.6:
            score += 7
        elif fund.pb <= 2.3:
            score += 3
    if fund.pe is not None and fund.pe > 0:
        if fund.pe <= 8:
            score += 10
        elif fund.pe <= 13:
            score += 6
        elif fund.pe <= 18:
            score += 2

    if tech is not None and tech.target_discount_pct > 0:
        discount_ratio = tech.discount_pct / tech.target_discount_pct
        if discount_ratio >= 1.0:
            score += 11
        elif discount_ratio >= 0.85:
            score += 6

    if fund.pe is not None and fund.pe <= 0:
        score -= 18
    if fund.pb is not None and fund.pb <= 0:
        score -= 12
    return int(clamp(score)), pe_discount, pb_discount


def quality_score(packet: dict[str, Any]) -> int:
    fund = packet["fundamental"]
    sector = packet["sector"]
    if fund is None:
        return 35
    score = 50.0
    if fund.roe is not None:
        score += clamp((fund.roe - 10) * 1.15, -16, 25)
    if fund.roa is not None:
        score += clamp((fund.roa - 4) * 1.25, -10, 15)
    if fund.profit_margin is not None:
        score += clamp((fund.profit_margin - 6) * 0.45, -8, 10)
    if sector not in FINANCIAL_SECTORS:
        if fund.debt_to_equity is not None:
            if fund.debt_to_equity <= 0.8:
                score += 8
            elif fund.debt_to_equity <= 1.5:
                score += 3
            elif fund.debt_to_equity >= 2.5:
                score -= 12
        if fund.current_ratio is not None:
            if fund.current_ratio >= 1.2:
                score += 5
            elif fund.current_ratio < 0.85:
                score -= 8
    if fund.eps is not None and fund.eps <= 0:
        score -= 22
    return int(clamp(score))


def technical_score(packet: dict[str, Any]) -> int:
    tech = packet["tech"]
    if tech is None:
        return 35
    score = float(tech.win_score)
    if tech.near_break and not tech.failed_break:
        score += 5
    if tech.failed_break:
        score -= 28
    return int(clamp(score))


def risk_score(packet: dict[str, Any], quality: int, sector: SectorSnapshot, pe_disc: float | None, pb_disc: float | None) -> tuple[int, list[str]]:
    fund = packet["fundamental"]
    tech = packet["tech"]
    risk = 0.0
    flags: list[str] = []

    if fund is None:
        risk += 22
        flags.append("thieu PE/PB")
    else:
        if fund.pe is not None and fund.pe <= 0:
            risk += 22
            flags.append("PE am")
        if fund.eps is not None and fund.eps <= 0:
            risk += 22
            flags.append("EPS am")
        if packet["sector"] not in FINANCIAL_SECTORS and fund.debt_to_equity is not None and fund.debt_to_equity >= 2.5:
            risk += 14
            flags.append("no cao")
        if fund.roe is not None and fund.roe < 5:
            risk += 10
            flags.append("ROE yeu")
    if tech is None:
        risk += 12
        flags.append("thieu chart")
    elif tech.failed_break:
        risk += 34
        flags.append("failed break")
    if quality < 45:
        risk += 10
        flags.append("chat luong thap")
    if sector.score < 45:
        risk += 12
        flags.append("nganh yeu")
    if pe_disc is not None and pb_disc is not None and pe_disc < -10 and pb_disc < -10:
        risk += 10
        flags.append("dinh gia cao hon nganh")
    return int(clamp(risk)), flags


def build_bull_case(packet: dict[str, Any], sector: SectorSnapshot, pe_disc: float | None, pb_disc: float | None) -> str:
    fund = packet["fundamental"]
    tech = packet["tech"]
    parts: list[str] = []
    if pb_disc is not None and pb_disc >= 20:
        parts.append(f"PB re hon nganh {pb_disc:.0f}%")
    if pe_disc is not None and pe_disc >= 20:
        parts.append(f"PE re hon nganh {pe_disc:.0f}%")
    if tech is not None and tech.discount_pct >= tech.target_discount_pct * 0.85:
        parts.append(f"gia chiet khau {tech.discount_pct:.0f}%")
    if sector.score >= 60:
        parts.append("nganh co dong tien")
    if fund is not None and fund.roe is not None and fund.roe >= 15:
        parts.append(f"ROE {fund.roe:.0f}%")
    if tech is not None and tech.near_break and not tech.failed_break:
        parts.append("gan nen break")
    return "; ".join(parts[:4]) or "dinh gia/ky thuat dang can theo doi"


def build_opportunities(packets: list[dict[str, Any]], sectors: dict[str, SectorSnapshot]) -> list[Opportunity]:
    opportunities: list[Opportunity] = []
    for packet in packets:
        fund = packet["fundamental"]
        tech = packet["tech"]
        if fund is None or (fund.pe is None and fund.pb is None):
            continue
        sector = sectors[packet["sector"]]
        val_score, pe_disc, pb_disc = valuation_score(packet, sector)
        qual_score = quality_score(packet)
        tech_score = technical_score(packet)
        weekly = packet.get("weekly") or weekly_sniper.empty_structure()
        risk, risk_flags = risk_score(packet, qual_score, sector, pe_disc, pb_disc)
        risk += 18 if weekly.state in {"NO_DATA", "NO_SETUP"} else 0
        risk += 14 if "BROKEN_STRUCTURE" in weekly.flags else 0
        risk += 10 if weekly.risk_reward is None or weekly.risk_reward < 1.5 else 0
        if fund.eps is not None and fund.eps <= 0:
            risk_flags.append("nguy cơ value trap: EPS âm")
        if weekly.flags:
            risk_flags.extend(flag.lower().replace("_", " ") for flag in weekly.flags[:2])
        data_current = (
            packet.get("cache_status") in {"live", "fresh_cache"}
            and recent_market_data(packet.get("as_of"))
        )
        if not data_current:
            risk += 20
            risk_flags.append("dữ liệu giá không còn mới")
        risk = int(clamp(risk))
        score = scoring.weekend_score(
            valuation=val_score,
            quality=qual_score,
            structure=weekly.score,
            timing=weekly.timing_score,
            sector=sector.score,
            risk=risk,
        )
        # Hard caps make the top band interpretable. A cheap multiple cannot
        # compensate for weak quality or a broken weekly structure.
        if qual_score < 50:
            score = min(score, 67)
        if val_score < 58:
            score = min(score, 71)
        if weekly.score < 65:
            score = min(score, 69)
        if risk > 38:
            score = min(score, 69)
        if score < MIN_SCORE or sector.score < MIN_SECTOR_SCORE:
            continue
        strict_candidate = (
            score >= 76
            and val_score >= 66
            and qual_score >= 56
            and weekly.score >= 72
            and weekly.timing_score >= 62
            and weekly.confidence >= 70
            and weekly.state in {"EARLY_MARKUP", "READY_TO_ACCUMULATE"}
            and weekly.risk_reward is not None
            and weekly.risk_reward >= 1.7
            and risk <= 34
            and data_current
            and tech is not None
            and not tech.failed_break
        )
        if strict_candidate:
            action = "UNG_VIEN_GOM"
        elif weekly.state in {"EARLY_MARKUP", "READY_TO_ACCUMULATE", "PREP_BASE"} and score >= 68:
            action = "CHO_DIEM_GOM"
        else:
            action = "THEO_DOI_DINH_GIA"
        bull_case = build_bull_case(packet, sector, pe_disc, pb_disc)
        if weekly.state in {"EARLY_MARKUP", "READY_TO_ACCUMULATE"}:
            bull_case = f"{bull_case}; cấu trúc tuần {weekly.state.lower().replace('_', ' ')}"
        bear_case = "; ".join(risk_flags[:4]) if risk_flags else "cho diem mua va quan tri ty trong"
        fundamental_fields = [fund.pe, fund.pb, fund.roe, fund.roa, fund.eps]
        completeness = sum(value is not None for value in fundamental_fields) / len(fundamental_fields)
        confidence = int(clamp(weekly.confidence * 0.68 + completeness * 100 * 0.32, 0, 96))
        opportunities.append(
            Opportunity(
                symbol=packet["symbol"],
                sector=packet["sector"],
                close=float(packet["close"] or 0),
                action=action,
                opportunity_score=score,
                valuation_score=val_score,
                quality_score=qual_score,
                technical_score=tech_score,
                sector_score=sector.score,
                risk_score=risk,
                pe=fund.pe,
                pb=fund.pb,
                sector_pe=sector.median_pe,
                sector_pb=sector.median_pb,
                pe_discount_pct=pe_disc,
                pb_discount_pct=pb_disc,
                roe=fund.roe,
                roa=fund.roa,
                debt_to_equity=fund.debt_to_equity,
                discount_pct=tech.discount_pct if tech is not None else 0.0,
                target_discount_pct=tech.target_discount_pct if tech is not None else 0.0,
                setup=tech.setup if tech is not None else "NO_CHART",
                bull_case=bull_case,
                bear_case=bear_case,
                data_source=fund.source,
                grade=scoring.grade(score),
                confidence=confidence,
                structure_score=weekly.score,
                timing_score=weekly.timing_score,
                structure_state=weekly.state,
                trigger=weekly.trigger,
                risk_reward=weekly.risk_reward,
                buy_zone_low=weekly.buy_zone_low,
                buy_zone_high=weekly.buy_zone_high,
                breakout_price=weekly.breakout_price,
                invalidation_price=weekly.invalidation_price,
                thesis_status="candidate" if strict_candidate else "waiting_price",
                weekly=weekly.to_dict(),
                as_of=packet.get("as_of"),
                cache_status=str(packet.get("cache_status") or "unknown"),
            )
        )
    ranked = sorted(
        opportunities,
        key=lambda item: (
            item.action == "UNG_VIEN_GOM",
            item.opportunity_score,
            item.structure_score,
            item.confidence,
        ),
        reverse=True,
    )
    eligible = [item for item in ranked if item.action == "UNG_VIEN_GOM"]
    limit = min(CONVICTION_LIMIT, 2)
    selected: list[Opportunity] = []
    used_sectors: set[str] = set()
    for item in eligible:
        if len(selected) >= limit:
            break
        if item.sector in used_sectors and any(other.sector not in used_sectors for other in eligible):
            continue
        item.selected = True
        item.action = "UU_TIEN_GOM"
        item.thesis_status = "high_conviction"
        selected.append(item)
        used_sectors.add(item.sector)
    return ranked


def opportunity_line(item: Opportunity) -> str:
    return (
        f"`{item.symbol}` {item.grade} · {item.opportunity_score}/97 {item.action} | {item.sector} | "
        f"PE {fmt_num(item.pe)} vs {fmt_num(item.sector_pe)} ({fmt_pct(item.pe_discount_pct, signed=True)}) | "
        f"PB {fmt_num(item.pb, 2)} vs {fmt_num(item.sector_pb, 2)} ({fmt_pct(item.pb_discount_pct, signed=True)}) | "
        f"DD {item.discount_pct:.0f}/{item.target_discount_pct:.0f}% | "
        f"V/Q/T/S {item.valuation_score}/{item.quality_score}/{item.technical_score}/{item.sector_score} | "
        f"{item.bull_case} | risk: {item.bear_case}"
    )


def sector_line(item: SectorSnapshot) -> str:
    return (
        f"`{item.sector}` score {item.score} | avg tech {item.avg_win_score:.0f} | "
        f"PE {fmt_num(item.median_pe)} PB {fmt_num(item.median_pb, 2)} ROE {fmt_pct(item.median_roe)} | "
        f"failed {item.failed_ratio * 100:.0f}% | {item.count} ma"
    )


def build_report(opportunities: list[Opportunity], sectors: dict[str, SectorSnapshot], mode: str) -> str:
    now = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M")
    top = opportunities[:TOP_N]
    selected = [item for item in top if item.selected][:2]
    prep = [item for item in top if not item.selected and item.structure_state in {"EARLY_MARKUP", "READY_TO_ACCUMULATE", "PREP_BASE"}]
    sector_rows = sorted(sectors.values(), key=lambda item: item.score, reverse=True)[:8]

    lines = [
        f"*THIEUCUBU WEEKLY CONVICTION* `{now}`",
        "Score v2 (tối đa 97): định giá + chất lượng + cấu trúc tuần + thời điểm + rủi ro. Không phải khuyến nghị mua bán.",
        "",
        "*💎 TỐI ĐA 2 MÃ ƯU TIÊN GOM*",
    ]
    lines += [opportunity_line(item) for item in selected] or ["Tuần này chưa có mã đồng thời đủ 5 cửa; không ép chọn."]
    lines += ["", "*🟢 CẤU TRÚC ĐANG CHUẨN BỊ*"]
    lines += [opportunity_line(item) for item in prep[:8]] or ["Chưa có mã chuẩn bị đủ rõ."]
    lines += ["", "*👀 WATCHLIST ĐỊNH GIÁ / CHỜ GIÁ*"]
    lines += [opportunity_line(item) for item in top if not item.selected][:10] or ["Chưa có mã đạt ngưỡng lọc."]
    lines += ["", "*NGÀNH ĐÁNG CHÚ Ý*"]
    lines += [sector_line(item) for item in sector_rows] or ["Chưa đủ dữ liệu ngành."]
    if mode == "test":
        lines += ["", "`TEST MODE`: chỉ quét một tập mã mẫu."]
    return "\n".join(lines)


def update_investment_theses(opportunities: list[Opportunity], updated_at: str) -> dict[str, Any]:
    path = DATA_DIR / "investment_theses.json"
    existing = scan.json_load(path, {})
    if not isinstance(existing, dict):
        existing = {}
    stocks = existing.get("stocks")
    if not isinstance(stocks, dict):
        stocks = {}
    for item in opportunities[:TOP_N]:
        if item.opportunity_score < 66 and not item.selected:
            continue
        previous = stocks.get(item.symbol) if isinstance(stocks.get(item.symbol), dict) else {}
        stocks[item.symbol] = {
            "symbol": item.symbol,
            "status": item.thesis_status,
            "first_detected": previous.get("first_detected") or updated_at,
            "last_reviewed": updated_at,
            "last_score": item.opportunity_score,
            "grade": item.grade,
            "confidence": item.confidence,
            "strategies": ["position", "investment"] if item.selected else ["watch"],
            "preferred_buy_zone": [item.buy_zone_low, item.buy_zone_high],
            "breakout_price": item.breakout_price,
            "invalidation_price": item.invalidation_price,
            "thesis": item.bull_case,
            "risks": item.bear_case,
            "review_after_sessions": 5 if item.selected else 10,
            "score_version": item.score_version,
        }
    payload = {
        "schema_version": "thieucubu.investment_theses.v1",
        "updated_at": updated_at,
        "stocks": stocks,
    }
    scan.json_save(path, payload, pretty=True)
    return payload


def save_outputs(opportunities: list[Opportunity], sectors: dict[str, SectorSnapshot]) -> None:
    now = datetime.now(VN_TZ).isoformat(timespec="seconds")
    selected = [item for item in opportunities if item.selected][:2]
    latest = {
        "schema_version": "thieucubu.weekend_opportunities.v2",
        "score_version": scoring.SCORE_VERSION,
        "updated_at": now,
        "selection_policy": {
            "max_convictions": 2,
            "may_return_zero": True,
            "requires": ["valuation", "business_quality", "weekly_structure", "timing", "risk_reward"],
        },
        "convictions": [asdict(item) for item in selected],
        "top": [asdict(item) for item in opportunities[:TOP_N]],
        "sectors": [asdict(item) for item in sorted(sectors.values(), key=lambda x: x.score, reverse=True)],
    }
    scan.json_save(DATA_DIR / "weekend_opportunities_latest.json", latest, pretty=False)
    scan.json_save(
        DATA_DIR / "candidate_book_latest.json",
        {
            "schema_version": "thieucubu.candidate_book.v1",
            "updated_at": now,
            "convictions": latest["convictions"],
            "watchlist": [asdict(item) for item in opportunities if not item.selected][:TOP_N],
        },
        pretty=False,
    )
    update_investment_theses(opportunities, now)

    history_path = DATA_DIR / "weekend_opportunities_history.json"
    history = scan.json_load(history_path, [])
    history.append({"updated_at": now, "convictions": latest["convictions"], "top": latest["top"][:10]})
    history = history[-60:]
    scan.json_save(history_path, history, pretty=False)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=os.getenv("WEEKEND_MODE", "full"))
    args = parser.parse_args()
    mode = str(args.mode).strip().lower()
    if mode not in {"full", "test"}:
        logger.warning("Unknown mode=%r, using full", mode)
        mode = "full"

    if os.getenv("GITHUB_ACTIONS") and mode != "test":
        delay = random.randint(0, max(RANDOM_START_MAX, 0))
        logger.info("Weekend random start delay %ss", delay)
        await asyncio.sleep(delay)

    tickers = build_universe(mode)
    random.shuffle(tickers)
    logger.info("Weekend opportunity scan mode=%s tickers=%s", mode, len(tickers))

    packets = []
    force_refresh = mode == "test"
    set_weekly_index(
        await asyncio.to_thread(scan_safe.fetch_ohlcv_safe, "VNINDEX", HISTORY_BARS, force_refresh)
    )
    for index, symbol in enumerate(tickers, start=1):
        logger.info("[%s/%s] Analyze %s", index, len(tickers), symbol)
        try:
            packet = await asyncio.to_thread(fetch_symbol_packet, symbol, force_refresh)
            packets.append(packet)
        except Exception as exc:
            logger.exception("[%s] weekend scan failed: %s", symbol, exc)

    sectors = build_sector_snapshots(packets)
    save_fundamental_history(packets)
    opportunities = build_opportunities(packets, sectors)
    save_outputs(opportunities, sectors)

    report = build_report(opportunities, sectors, mode)
    await scan.send_chunks("*THIEUCUBU WEEKEND*", report)
    logger.info("Weekend opportunities found: %s", len(opportunities))


if __name__ == "__main__":
    asyncio.run(main())
