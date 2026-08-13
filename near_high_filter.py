#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import scan
import scan_safe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("thieucutoo.near_high")

VN_TZ = timezone(timedelta(hours=7))
DATA_DIR = Path("data")
SKIPLIST_PATH = DATA_DIR / "near_high_skiplist.json"


@dataclass
class NearHighItem:
    symbol: str
    sector: str
    close: float
    high_6y: float
    distance_pct: float
    over_high: bool
    bars: int


def env_int(name: str, default: int, min_value: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(min_value, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r, using %s", name, raw, default)
        return default


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
    return min(value, max_value) if max_value is not None else value


LOOKBACK_BARS = env_int("NEAR_HIGH_LOOKBACK_BARS", 1560, min_value=260)
THRESHOLD_PCT = env_float("NEAR_HIGH_THRESHOLD_PCT", 7.0, min_value=0.0, max_value=30.0)
MAX_AGE_DAYS = env_int("NEAR_HIGH_SKIPLIST_MAX_AGE_DAYS", 8, min_value=1)


def load_watch_symbols() -> set[str]:
    symbols: set[str] = {"VNINDEX"}
    portfolio = scan.json_load(DATA_DIR / "portfolio.json", [])
    if isinstance(portfolio, list):
        for item in portfolio:
            if isinstance(item, dict):
                symbol = str(item.get("symbol", "")).upper().strip()
                if symbol:
                    symbols.add(symbol)
    notes = scan.json_load(DATA_DIR / "notes.json", {})
    if isinstance(notes, dict):
        symbols.update(str(symbol).upper().strip() for symbol in notes if str(symbol).strip())
    elif isinstance(notes, list):
        for item in notes:
            if isinstance(item, dict):
                symbol = str(item.get("symbol", "")).upper().strip()
                if symbol:
                    symbols.add(symbol)
    return symbols


def build_universe() -> list[str]:
    symbols = list(scan.ALL_TICKERS)
    symbols.extend(load_watch_symbols())
    return sorted(set(symbol for symbol in symbols if symbol and symbol != "VNINDEX"))


def near_high_item(symbol: str, df: pd.DataFrame | None) -> NearHighItem | None:
    if df is None or len(df) < 260:
        return None
    data = df.tail(LOOKBACK_BARS).copy()
    close = float(data["close"].iloc[-1])
    high_6y = float(data["high"].max())
    if close <= 0 or high_6y <= 0:
        return None
    prev_peak = float(data["high"].iloc[:-1].max()) if len(data) > 1 else high_6y
    distance = (high_6y - close) / high_6y * 100
    over_high = close >= prev_peak
    if distance > THRESHOLD_PCT and not over_high:
        return None
    return NearHighItem(
        symbol=symbol,
        sector=scan.TICKER_TO_SECTOR.get(symbol, "Other"),
        close=round(close, 2),
        high_6y=round(high_6y, 2),
        distance_pct=round(distance, 2),
        over_high=bool(over_high),
        bars=len(data),
    )


def read_skip_symbols(max_age_days: int = MAX_AGE_DAYS) -> set[str]:
    data = scan.json_load(SKIPLIST_PATH, {})
    if not isinstance(data, dict):
        return set()
    try:
        updated = datetime.fromisoformat(str(data.get("updated_at"))).astimezone(VN_TZ).date()
    except Exception:
        return set()
    if (datetime.now(VN_TZ).date() - updated).days > max_age_days:
        return set()
    symbols = data.get("symbols", [])
    if not isinstance(symbols, list):
        return set()
    return {str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()}


def filter_symbols(symbols: list[str], protected: set[str] | None = None) -> tuple[list[str], list[str]]:
    if os.getenv("SCAN_SKIP_NEAR_HIGH_WEEKDAY", "1").strip().lower() in {"0", "false", "no"}:
        return symbols, []
    skip = read_skip_symbols()
    protected_symbols = {str(symbol).upper().strip() for symbol in (protected or set())}
    kept: list[str] = []
    removed: list[str] = []
    for symbol in symbols:
        normalized = str(symbol).upper().strip()
        if normalized in skip and normalized not in protected_symbols:
            removed.append(normalized)
        else:
            kept.append(symbol)
    if removed:
        logger.info("Skip near/vuot dinh 6 nam weekday: %s", ",".join(removed[:40]))
    return kept, removed


async def update_skiplist(mode: str) -> list[NearHighItem]:
    symbols = build_universe() if mode == "full" else ["VCB", "FPT", "HPG", "SSI", "VIX", "DIG", "KDH", "PVD"]
    random.shuffle(symbols)
    items: list[NearHighItem] = []
    for index, symbol in enumerate(symbols, start=1):
        logger.info("[%s/%s] Near-high scan %s", index, len(symbols), symbol)
        try:
            df = await asyncio.to_thread(scan_safe.fetch_ohlcv_safe, symbol, LOOKBACK_BARS, False)
            item = near_high_item(symbol, df)
            if item is not None:
                items.append(item)
        except Exception as exc:
            logger.warning("[%s] near-high failed: %s", symbol, exc)
    items = sorted(items, key=lambda item: (not item.over_high, item.distance_pct, item.symbol))
    now = datetime.now(VN_TZ).isoformat(timespec="seconds")
    scan.json_save(
        SKIPLIST_PATH,
        {
            "updated_at": now,
            "threshold_pct": THRESHOLD_PCT,
            "lookback_bars": LOOKBACK_BARS,
            "symbols": [item.symbol for item in items],
            "items": [asdict(item) for item in items],
        },
        pretty=False,
    )
    return items


def build_report(items: list[NearHighItem]) -> str:
    now = datetime.now(VN_TZ).strftime("%d/%m %H:%M")
    lines = [
        f"*THIEUCUBU NEAR HIGH 6Y* `{now}`",
        f"Gan/vuot dinh 6 nam <= {THRESHOLD_PCT:.0f}% se duoc bo qua trong broad scan ngay thuong, tru ma trong portfolio/note.",
    ]
    if not items:
        lines.append("Chua co ma nao gan/vuot dinh 6 nam.")
    else:
        for item in items[:40]:
            state = "vuot dinh" if item.over_high else f"cach dinh {item.distance_pct:.1f}%"
            lines.append(f"`{item.symbol}` {state} | close {item.close:.2f}/high6y {item.high_6y:.2f} | {item.sector}")
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=os.getenv("NEAR_HIGH_MODE", "full"))
    args = parser.parse_args()
    mode = str(args.mode).strip().lower()
    if mode not in {"full", "test"}:
        mode = "full"
    items = await update_skiplist(mode)
    await scan.send_chunks("*THIEUCUBU NEAR HIGH*", build_report(items))
    logger.info("Near-high skiplist updated: %s symbols", len(items))


if __name__ == "__main__":
    asyncio.run(main())
