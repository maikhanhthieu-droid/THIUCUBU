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

import httpx
import numpy as np
import pandas as pd
from vnstock.api.quote import Quote

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("thieucutoo")

VN_TZ = timezone(timedelta(hours=7))
DATA_DIR = Path("data")
CACHE_DIR = DATA_DIR / "cache"
DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DRY_RUN = os.getenv("DRY_RUN", "").strip().lower() in {"1", "true", "yes"} or not (TOKEN and CHAT_ID)

VNSTOCK_API_KEY = os.getenv("VNSTOCK_API_KEY", "").strip()
if VNSTOCK_API_KEY:
    os.environ.setdefault("VNSTOCK_API_KEY", VNSTOCK_API_KEY)
    os.environ.setdefault("VNDATA_API_KEY", VNSTOCK_API_KEY)


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


BATCH_SIZE = env_int("SCAN_BATCH_SIZE", 10, min_value=1)
DELAY_MIN = env_int("SCAN_DELAY_MIN_SEC", 3, min_value=0)
DELAY_MAX = max(DELAY_MIN, env_int("SCAN_DELAY_MAX_SEC", 25, min_value=0))
RANDOM_START_MAX = env_int("SCAN_RANDOM_START_MAX_SEC", 300, min_value=0)
REQUESTS_PER_MINUTE = env_int("SCAN_REQUESTS_PER_MINUTE", 10, min_value=1)
MAX_WORKERS = env_int("SCAN_MAX_WORKERS", 3, min_value=1)


SECTORS: dict[str, list[str]] = {
    "Bank": ["VCB", "BID", "CTG", "TCB", "MBB", "ACB", "VPB", "HDB", "STB", "EIB", "SHB", "TPB", "LPB", "MSB", "OCB", "ABB", "VIB"],
    "Chung khoan": ["SSI", "VND", "HCM", "VCI", "FTS", "BSI", "CTS", "AGR", "TVS", "VIX", "SHS", "MBS", "BVS", "VDS", "ORS", "VIG"],
    "Bao hiem": ["BVH", "EVF", "BMI", "MIG", "PVI"],
    "BDS dan cu": ["VIC", "VHM", "VRE", "NVL", "PDR", "DXG", "DIG", "CEO", "KDH", "NLG", "HQC", "SCR", "IJC", "HUT", "TCH", "CRE", "HDC", "NTL", "DXS", "KHG", "QCG"],
    "BDS KCN": ["BCM", "SZC", "KBC", "IDC", "VGC", "SIP", "PHR", "GVR", "D2D", "NTC"],
    "Xay dung dau tu cong": ["VCG", "LCG", "HHV", "CII", "FCN", "C4G", "G36", "DPG", "CTD", "HBC", "VPH"],
    "Thep": ["HPG", "HSG", "NKG", "VGS", "SMC", "TLH", "POM"],
    "Da xi mang nhua duong": ["KSB", "DHA", "VLB", "HT1", "BCC", "PLC"],
    "Go cao su": ["PTB", "TTF", "DRI", "DPR"],
    "Hoa chat phan bon": ["DGC", "DPM", "DCM", "CSV", "LAS", "BFC", "DDV"],
    "Cao su nhua": ["AAA", "DRC", "CSM", "BMP"],
    "Dau khi": ["GAS", "PLX", "PVD", "PVT", "BSR", "OIL", "PVS", "PVC", "PVB", "PSH"],
    "Dien tien ich": ["POW", "REE", "PC1", "HDG", "NT2", "VSH", "GEG", "TV2", "QTP", "HND"],
    "Ban le": ["MWG", "FRT", "PNJ", "DGW", "PET"],
    "Thuc pham do uong": ["MSN", "SAB", "VNM", "MCH", "KDC", "QNS", "SBT", "LSS"],
    "Det may san xuat": ["VEA", "TNG", "GIL", "MSH", "VGT", "TCM"],
    "Thuy san": ["ANV", "VHC", "FMC", "IDI", "ASM", "CMX"],
    "Nong nghiep chan nuoi": ["HAG", "DBC", "PAN", "BAF", "LTG", "TAR", "HNG"],
    "Cong nghe vien thong": ["FPT", "CMG", "ELC", "VGI", "CTR", "FOX", "LC"],
    "Logistics cang bien": ["GMD", "HAH", "VOS", "VTO", "VIP", "TCL", "SGP", "STG", "MHC"],
}

ECOSYSTEMS: dict[str, list[str]] = {
    "Vin": ["VIC", "VHM", "VRE"],
    "Gelex Tuan Muot": ["GEX", "VIX", "VGC", "GEE", "IDC", "EIB", "STG", "VLB", "MHC"],
    "Masan Techcom": [ "MCH", "TCB"],
    "TT Bau Hien": ["SHB", "SHS"],
    "PVN": ["PVD", "PVS", "GAS", "PVT", "BSR", "OIL", "PLX", "DPM", "DCM"],
    "BDS dau co": ["DIG", "CEO", "NVL", "PDR", "DXG", "HQC", "SCR", "IJC", "HUT"],
    "Bluechips": ["VCB", "BID", "CTG", "FPT", "HPG", "VNM", "MWG", "SAB", "REE"],
}

SECTOR_LEADERS: dict[str, list[str]] = {
    "Bank": ["VCB", "BID", "CTG", "TCB", "MBB"],
    "Chung khoan": ["SSI", "VND", "VCI", "HCM", "VIX"],
    "BDS": ["VIC", "VHM", "KDH", "DIG", "NVL"],
    "Thep": ["HPG", "HSG", "NKG", "VGS", "SMC"],
    "Dau khi": ["GAS", "PVD", "PVS", "BSR", "PVT"],
    "Ban le": ["MWG", "FRT", "PNJ", "DGW", "PET"],
    "Cong nghe": ["FPT", "CMG", "ELC", "VGI", "CTR"],
    "Thuy san nong nghiep": ["VHC", "ANV", "DBC", "HAG", "BAF"],
}

TICKER_TO_SECTOR: dict[str, str] = {}
for sector_name, sector_tickers in SECTORS.items():
    for ticker in sector_tickers:
        TICKER_TO_SECTOR.setdefault(ticker, sector_name)

ALL_TICKERS = sorted({ticker for tickers in SECTORS.values() for ticker in tickers})

DISCOUNT_RULES = {
    "G1": {"drop": 0.15, "lookback": 120, "name": "Sieu tru"},
    "G2": {"drop": 0.25, "lookback": 130, "name": "Bluechip beta vua"},
    "G3": {"drop": 0.30, "lookback": 170, "name": "Tai san that"},
    "G4": {"drop": 0.35, "lookback": 180, "name": "Beta cao"},
    "G5": {"drop": 0.43, "lookback": 240, "name": "Dau co midcap"},
    "G6": {"drop": 0.38, "lookback": 170, "name": "Thoi diem cao"},
    "G7": {"drop": 0.55, "lookback": 260, "name": "Penny dau co"},
}

TICKER_GROUP: dict[str, str] = {}
for ticker in ["VCB", "FPT", "VIC", "GAS", "VNM", "BID"]:
    TICKER_GROUP[ticker] = "G1"
for ticker in ["TCB", "MBB", "ACB", "HPG", "CTG", "MWG", "VPB", "HDB", "STB", "LPB", "MSB", "OCB", "VIB", "PNJ"]:
    TICKER_GROUP[ticker] = "G2"
for ticker in ["KDH", "NLG", "REE", "VHC", "DGC", "BCM", "SZC", "IDC", "VGC", "SIP", "VHM", "VRE", "GVR", "DPR", "PTB"]:
    TICKER_GROUP[ticker] = "G3"
for ticker in ["SSI", "VND", "VCI", "HCM", "MBS", "FTS", "CTS", "BSI", "VDS", "AGR", "ORS", "BVS", "VIX", "SHS", "HSG", "NKG", "PVD", "PVS", "BSR", "PLX", "PVT", "GEX", "DPM", "DCM", "CSV"]:
    TICKER_GROUP[ticker] = "G4"
for ticker in ["DIG", "CEO", "NVL", "PDR", "DXG", "HQC", "SCR", "IJC", "HUT", "TCH", "CRE", "HDC", "NTL", "DXS", "KHG", "QCG", "VGS", "SMC", "TLH", "POM"]:
    TICKER_GROUP[ticker] = "G5"
for ticker in ["ANV", "IDI", "ASM", "FMC", "CMX", "LCG", "HHV", "CII", "HAG", "DBC", "BAF", "PAN", "TAR", "LTG", "VCG", "FCN", "C4G", "G36", "DPG"]:
    TICKER_GROUP[ticker] = "G6"
for ticker in ["HNG", "VIG", "TTF", "PSH", "PVC", "PVB"]:
    TICKER_GROUP[ticker] = "G7"


@dataclass
class ScanResult:
    symbol: str
    sector: str
    close: float
    win_score: int
    setup: str
    discount_pct: float
    target_discount_pct: float
    discount_group: str
    trend_score: int
    base_score: int
    flow_score: int
    break_score: int
    risk_score: int
    rsi: float
    mfi: float
    vol_ratio: float
    obv_up: bool
    near_break: bool
    failed_break: bool
    warning: str
    reason: str


def json_load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Cannot read %s: %s", path, exc)
        return default


def json_save(path: Path, data: Any, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")


def parse_mode() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=os.getenv("SCAN_MODE", "auto"))
    args = parser.parse_args()
    mode = str(args.mode).strip().lower()
    if mode not in {"auto", "morning", "afternoon", "eod", "test"}:
        logger.warning("Unknown mode=%r, falling back to auto", mode)
        mode = "auto"
    if mode != "auto":
        return mode

    now_vn = datetime.now(VN_TZ)
    if now_vn.hour < 12:
        return "morning"
    if now_vn.hour < 15:
        return "afternoon"
    return "eod"


def cache_path(symbol: str, bars: int) -> Path:
    return CACHE_DIR / f"{symbol}_D_{bars}.parquet"


def is_cache_fresh(path: Path, ttl_minutes: int) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) <= ttl_minutes * 60


def normalize_ohlcv(raw: pd.DataFrame) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None
    df = raw.copy()
    df.columns = [str(c).lower() for c in df.columns]
    col_map = {
        "date": "time",
        "datetime": "time",
        "time": "time",
        "tradingdate": "time",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
    }
    df = df.rename(columns={c: col_map[c] for c in df.columns if c in col_map})
    required = {"time", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        return None
    df = df[["time", "open", "high", "low", "close", "volume"]].copy()
    df["time"] = pd.to_datetime(df["time"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close", "volume"]).sort_values("time").reset_index(drop=True)
    return df


def fetch_ohlcv(symbol: str, bars: int = 260, force_refresh: bool = False) -> pd.DataFrame | None:
    ttl = 480 if not force_refresh else 0
    path = cache_path(symbol, bars)
    if not force_refresh and is_cache_fresh(path, ttl):
        try:
            return pd.read_parquet(path)
        except Exception:
            pass

    days_back = max(300, int(bars * 1.7))
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    sources = ["VCI", "TCBS"]
    random.shuffle(sources)
    for source in sources:
        try:
            q = Quote(symbol=symbol, source=source)
            raw = q.history(start=start, end=end, interval="1D")
            df = normalize_ohlcv(raw)
            if df is not None and len(df) >= 80:
                df = df.tail(bars).reset_index(drop=True)
                try:
                    df.to_parquet(path, index=False)
                except Exception:
                    pass
                return df
        except Exception as exc:
            logger.warning("[%s] %s failed: %s", source, symbol, exc)
    return None


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(length).mean()
    loss = (-delta.clip(upper=0)).rolling(length).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def mfi(df: pd.DataFrame, length: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    money = tp * df["volume"]
    direction = tp.diff()
    pos = money.where(direction > 0, 0).rolling(length).sum()
    neg = money.where(direction < 0, 0).rolling(length).sum().abs()
    ratio = pos / neg.replace(0, np.nan)
    return 100 - (100 / (1 + ratio))


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        return float(value)
    except Exception:
        return default


def analyze_symbol(symbol: str, df: pd.DataFrame) -> ScanResult | None:
    if df is None or len(df) < 80:
        return None

    df = df.copy().reset_index(drop=True)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    df["ema34"] = ema(close, 34)
    df["ema89"] = ema(close, 89)
    df["ema200"] = ema(close, 200)
    df["rsi"] = rsi(close)
    df["mfi"] = mfi(df)
    df["obv"] = obv(df)
    df["obv_ema"] = ema(df["obv"], 21)
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_up = bb_mid + 2 * bb_std
    bb_dn = bb_mid - 2 * bb_std
    df["bb_width"] = (bb_up - bb_dn) / bb_mid.replace(0, np.nan)

    last = df.iloc[-1]
    close_last = safe_float(last["close"])
    vol_avg20 = safe_float(volume.rolling(20).mean().iloc[-1], 1.0)
    vol_avg5 = safe_float(volume.rolling(5).mean().iloc[-1], 1.0)
    vol_ratio = safe_float(last["volume"] / max(vol_avg20, 1.0), 0.0)

    group = TICKER_GROUP.get(symbol, "G4")
    rule = DISCOUNT_RULES[group]
    lookback = int(rule["lookback"])
    target_drop = float(rule["drop"])
    peak = safe_float(high.tail(lookback).max(), close_last)
    discount = 1 - close_last / max(peak, 1e-9)

    high20_prev = safe_float(high.shift(1).rolling(20).max().iloc[-1], peak)
    high55 = safe_float(high.rolling(55).max().iloc[-1], peak)
    base_low = safe_float(low.tail(30).min(), close_last)
    base_range = (high55 - base_low) / max(close_last, 1e-9)
    bb_width = safe_float(last["bb_width"], 0.20)
    bb_q30 = safe_float(df["bb_width"].tail(120).quantile(0.30), bb_width)
    tight_base = bb_width <= max(bb_q30, 0.025)
    vol_dry = vol_avg5 <= vol_avg20 * 0.82 or vol_ratio <= 0.72
    near_break = close_last >= high20_prev * 0.94 and close_last <= high20_prev * 1.03

    body = abs(last["close"] - last["open"])
    candle_range = max(last["high"] - last["low"], 1e-9)
    upper_wick = last["high"] - max(last["open"], last["close"])
    close_position = (last["close"] - last["low"]) / candle_range
    failed_break = bool(last["high"] > high20_prev * 1.01 and last["close"] < high20_prev and upper_wick / candle_range > 0.35 and vol_ratio > 1.25)
    weak_close = close_position < 0.45 and vol_ratio > 1.2

    trend_score = 0
    if close_last > safe_float(last["ema34"]):
        trend_score += 12
    if safe_float(last["ema34"]) > safe_float(last["ema89"]):
        trend_score += 10
    if close_last > safe_float(last["ema89"]):
        trend_score += 6
    if len(df) > 6 and safe_float(last["ema34"]) > safe_float(df["ema34"].iloc[-6]):
        trend_score += 7

    discount_score = int(clamp((discount / target_drop) * 30, 0, 35))
    base_score = 0
    if tight_base:
        base_score += 16
    if vol_dry:
        base_score += 12
    if base_range <= 0.18:
        base_score += 8
    elif base_range <= 0.25:
        base_score += 4

    rsi_last = safe_float(last["rsi"], 50)
    mfi_last = safe_float(last["mfi"], 50)
    obv_up = bool(safe_float(last["obv"]) > safe_float(last["obv_ema"]) and safe_float(last["obv"]) >= safe_float(df["obv"].iloc[-6]))
    flow_score = 0
    if 43 <= rsi_last <= 68:
        flow_score += 8
    if 45 <= mfi_last <= 75:
        flow_score += 10
    if obv_up:
        flow_score += 12
    if vol_ratio > 1.15 and close_position > 0.58:
        flow_score += 6

    break_score = 0
    if near_break:
        break_score += 14
    if close_last > high20_prev:
        break_score += 8
    if vol_dry and near_break:
        break_score += 8
    if tight_base and near_break:
        break_score += 8

    risk_penalty = 0
    warnings = []
    if failed_break:
        risk_penalty += 28
        warnings.append("FAILED_BREAK")
    if weak_close:
        risk_penalty += 12
        warnings.append("WEAK_CLOSE")
    if rsi_last > 76:
        risk_penalty += 8
        warnings.append("HOT_RSI")
    if vol_ratio > 2.5 and close_position < 0.55:
        risk_penalty += 12
        warnings.append("DISTRIBUTION")

    discount_setup = discount_score + base_score + flow_score + min(trend_score, 18) - risk_penalty
    vcp_setup = min(trend_score, 32) + base_score + flow_score + break_score - risk_penalty
    raw_score = max(discount_setup, vcp_setup)
    win_score = int(clamp(raw_score, 0, 100))
    setup = "DISCOUNT_BASE" if discount_setup >= vcp_setup else "VCP_BREAK"
    if failed_break:
        setup = "AVOID_FAILED_BREAK"

    reason_parts = []
    if discount >= target_drop:
        reason_parts.append(f"discount {discount * 100:.1f}% >= target {target_drop * 100:.0f}%")
    if tight_base:
        reason_parts.append("nen chat")
    if vol_dry:
        reason_parts.append("vol kiet")
    if obv_up:
        reason_parts.append("OBV up")
    if mfi_last >= 50:
        reason_parts.append("MFI tot")
    if near_break:
        reason_parts.append("gan nen break")
    if warnings:
        reason_parts.append("? " + ",".join(warnings))

    return ScanResult(
        symbol=symbol,
        sector=TICKER_TO_SECTOR.get(symbol, "Other"),
        close=round(close_last, 2),
        win_score=win_score,
        setup=setup,
        discount_pct=round(discount * 100, 2),
        target_discount_pct=round(target_drop * 100, 2),
        discount_group=group,
        trend_score=int(clamp(trend_score, 0, 100)),
        base_score=int(clamp(base_score, 0, 100)),
        flow_score=int(clamp(flow_score, 0, 100)),
        break_score=int(clamp(break_score, 0, 100)),
        risk_score=int(risk_penalty),
        rsi=round(rsi_last, 1),
        mfi=round(mfi_last, 1),
        vol_ratio=round(vol_ratio, 2),
        obv_up=obv_up,
        near_break=near_break,
        failed_break=failed_break,
        warning=",".join(warnings),
        reason="; ".join(reason_parts[:7]),
    )


async def send_telegram(text: str) -> bool:
    if DRY_RUN:
        print(text)
        return True
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text[:4096],
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


async def send_chunks(title: str, text: str) -> None:
    chunks = []
    current = title + "\n"
    for line in text.splitlines():
        if len(current) + len(line) + 1 > 3800:
            chunks.append(current)
            current = title + " (cont.)\n"
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    for chunk in chunks:
        await send_telegram(chunk)
        await asyncio.sleep(1)


def result_line(r: ScanResult) -> str:
    warn = " [?]" if r.warning else ""
    return (
        f"`{r.symbol}` {r.win_score}/100{warn} {r.setup} | "
        f"DD {r.discount_pct:.1f}%/{r.target_discount_pct:.0f}% {r.discount_group} | "
        f"Volx{r.vol_ratio:.1f} RSI {r.rsi:.0f} MFI {r.mfi:.0f} | {r.reason}"
    )


def summarize_sector(results: list[ScanResult]) -> list[str]:
    by_symbol = {r.symbol: r for r in results}
    rows = []
    for name, leaders in SECTOR_LEADERS.items():
        leader_results = [by_symbol[s] for s in leaders if s in by_symbol]
        if not leader_results:
            continue
        avg = sum(r.win_score for r in leader_results) / len(leader_results)
        flow = sum(1 for r in leader_results if r.obv_up and r.mfi >= 50)
        near = sum(1 for r in leader_results if r.near_break)
        failed = sum(1 for r in leader_results if r.failed_break)
        status = "LEAD" if avg >= 65 and flow >= 3 else "WATCH" if avg >= 55 else "WEAK"
        rows.append((avg, f"{status} `{name}` avg {avg:.0f} | flow {flow}/{len(leader_results)} | near-break {near} | failed {failed}"))
    return [row for _, row in sorted(rows, reverse=True)]


def portfolio_report(results: list[ScanResult]) -> str:
    portfolio = json_load(DATA_DIR / "portfolio.json", [])
    if not portfolio:
        return "Portfolio: chua co ma trong data/portfolio.json"
    by_symbol = {r.symbol: r for r in results}
    lines = ["*PORTFOLIO NOTE*"]
    for item in portfolio:
        symbol = str(item.get("symbol", "")).upper().strip()
        buy_more = int(item.get("buy_more_score", 78))
        sell = int(item.get("sell_score", 45))
        note = str(item.get("note", "")).strip()
        r = by_symbol.get(symbol)
        if not r:
            lines.append(f"`{symbol}` no data | {note}")
            continue
        if r.win_score <= sell or r.failed_break:
            action = "CANH BAN / GIAM TY TRONG"
        elif r.win_score >= buy_more:
            action = "CANH MUA THEM"
        else:
            action = "GIU / THEO DOI"
        lines.append(f"`{symbol}` {r.win_score}/100 -> *{action}* | {r.reason} | {note}")
    return "\n".join(lines)


def update_failed_breaks(results: list[ScanResult]) -> list[dict[str, Any]]:
    path = DATA_DIR / "failed_breaks.json"
    old = json_load(path, [])
    today = datetime.now(VN_TZ).date()
    keep = []
    for item in old:
        try:
            item_date = datetime.fromisoformat(item["date"]).date()
            if (today - item_date).days <= 25:
                keep.append(item)
        except Exception:
            pass
    seen = {(item.get("symbol"), item.get("date")) for item in keep}
    for r in results:
        if r.failed_break:
            date_str = today.isoformat()
            key = (r.symbol, date_str)
            if key not in seen:
                keep.append({"symbol": r.symbol, "date": date_str, "score": r.win_score, "reason": r.reason})
    json_save(path, keep)
    return keep


def save_history(symbol: str, df: pd.DataFrame, history_store: dict[str, Any], peak_store: dict[str, Any]) -> None:
    tail = df.tail(240).copy()
    tail["time"] = tail["time"].dt.strftime("%Y-%m-%d")
    for col in ["open", "high", "low", "close"]:
        tail[col] = tail[col].round(2)
    tail["volume"] = tail["volume"].round(0).astype("int64")
    history_store[symbol] = tail.to_dict(orient="records")
    group = TICKER_GROUP.get(symbol, "G4")
    lookback = int(DISCOUNT_RULES[group]["lookback"])
    peak_store[symbol] = {
        "peak": float(df["high"].tail(lookback).max()),
        "lookback": lookback,
        "updated_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
    }


def process_symbol(symbol: str, force_refresh: bool) -> tuple[str, pd.DataFrame | None, ScanResult | None]:
    try:
        df = fetch_ohlcv(symbol, bars=260, force_refresh=force_refresh)
        if df is None:
            return symbol, None, None
        return symbol, df, analyze_symbol(symbol, df)
    except Exception as exc:
        logger.exception("[%s] scan failed: %s", symbol, exc)
        return symbol, None, None


async def scan_universe(mode: str) -> tuple[list[ScanResult], dict[str, Any], dict[str, Any]]:
    tickers = ALL_TICKERS.copy()
    portfolio = json_load(DATA_DIR / "portfolio.json", [])
    for item in portfolio:
        symbol = str(item.get("symbol", "")).upper().strip()
        if symbol and symbol not in tickers:
            tickers.append(symbol)

    if mode == "test":
        tickers = ["VCB", "FPT", "HPG", "TCB", "SSI", "DIG", "VIX", "VNM", "PVD", "KDH"]
    else:
        random.shuffle(tickers)

    force_refresh = mode in {"eod", "test"}
    logger.info(
        "Mode=%s tickers=%s batch=%s rpm=%s workers=%s",
        mode,
        len(tickers),
        BATCH_SIZE,
        REQUESTS_PER_MINUTE,
        MAX_WORKERS,
    )

    results: list[ScanResult] = []
    history_store: dict[str, Any] = {}
    peak_store: dict[str, Any] = json_load(DATA_DIR / "historical_peaks.json", {})

    _, idx_df, idx_result = await asyncio.to_thread(process_symbol, "VNINDEX", force_refresh)
    if idx_df is not None and idx_result:
        results.append(idx_result)
        save_history("VNINDEX", idx_df, history_store, peak_store)

    min_batch_seconds = max(1, int((BATCH_SIZE / max(REQUESTS_PER_MINUTE, 1)) * 60))
    for start in range(0, len(tickers), BATCH_SIZE):
        batch_started = time.time()
        batch = tickers[start:start + BATCH_SIZE]
        logger.info("Batch %s-%s/%s: %s", start + 1, start + len(batch), len(tickers), ",".join(batch))
        semaphore = asyncio.Semaphore(min(MAX_WORKERS, len(batch)))

        async def run_symbol(symbol: str) -> tuple[str, pd.DataFrame | None, ScanResult | None]:
            async with semaphore:
                return await asyncio.to_thread(process_symbol, symbol, force_refresh)

        for symbol, df, result in await asyncio.gather(*(run_symbol(symbol) for symbol in batch)):
            if df is not None and result:
                results.append(result)
                save_history(symbol, df, history_store, peak_store)

        elapsed = time.time() - batch_started
        if start + BATCH_SIZE < len(tickers):
            delay = max(0, min_batch_seconds - elapsed) + random.uniform(DELAY_MIN, DELAY_MAX)
            logger.info("Sleep %.1fs before next batch", delay)
            await asyncio.sleep(delay)

    return results, history_store, peak_store


def build_report(mode: str, results: list[ScanResult]) -> str:
    results = sorted(results, key=lambda r: r.win_score, reverse=True)
    market = next((r for r in results if r.symbol == "VNINDEX"), None)
    stock_results = [r for r in results if r.symbol != "VNINDEX"]
    strong = [r for r in stock_results if r.win_score >= 72 and not r.failed_break][:7]
    break_watch = [r for r in stock_results if r.win_score >= 62 and r.near_break and not r.failed_break][:15]
    discount_watch = [r for r in stock_results if r.discount_pct >= r.target_discount_pct * 0.85 and r.win_score >= 55 and not r.failed_break][:12]
    failed = [r for r in stock_results if r.failed_break][:12]
    sectors = summarize_sector(stock_results)[:8]

    now = datetime.now(VN_TZ).strftime("%d/%m %H:%M")
    lines = [
        f"*THIEUCUTOO {mode.upper()}* `{now}`",
        f"Scan {len(stock_results)} ma | score la diem 0-100, khong phai cam ket win.",
    ]
    if market:
        lines.append(f"VNI `{market.win_score}/100` | RSI {market.rsi:.0f} MFI {market.mfi:.0f} | {market.reason}")
    lines += ["", "*7 MA MANH NHAT*"]
    lines += [result_line(r) for r in strong] or ["Khong co ma dat nguong."]
    lines += ["", "*15 MA CO THE BREAK / GOM 1-3 TUAN*"]
    lines += [result_line(r) for r in break_watch] or ["Khong co ma dat nguong."]
    lines += ["", "*MA CHIET KHAU DI NEN CAN THEO DOI*"]
    lines += [result_line(r) for r in discount_watch] or ["Khong co ma dat nguong."]
    lines += ["", "*NGANH LEAD / RISK*"]
    lines += sectors or ["Chua du du lieu nganh."]
    lines += ["", portfolio_report(results)]
    if mode == "eod":
        lines += ["", "*BREAK XIT / CAN NE*"]
        lines += [result_line(r) for r in failed] or ["Khong co break xit dang chu y."]
    return "\n".join(lines)


async def main() -> None:
    mode = parse_mode()

    if os.getenv("GITHUB_ACTIONS") and mode != "test":
        delay = random.randint(0, max(RANDOM_START_MAX, 0))
        logger.info("Random start delay %ss", delay)
        await asyncio.sleep(delay)

    results, history_store, peak_store = await scan_universe(mode)
    failed_breaks = update_failed_breaks(results)

    json_save(DATA_DIR / "results_latest.json", [asdict(r) for r in sorted(results, key=lambda x: x.win_score, reverse=True)], pretty=False)
    json_save(DATA_DIR / "history_data.json", history_store, pretty=False)
    json_save(DATA_DIR / "historical_peaks.json", peak_store, pretty=False)

    report = build_report(mode, results)
    await send_chunks("*THIEUCUTOO REPORT*", report)

    if failed_breaks and mode != "eod":
        recent = failed_breaks[-10:]
        text = "*FAILED BREAK WATCH 25D*\n" + "\n".join(f"`{x['symbol']}` {x['date']} score {x.get('score')}: {x.get('reason','')}" for x in recent)
        await send_chunks("*THIEUCUTOO RISK*", text)


if __name__ == "__main__":
    asyncio.run(main())
