#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import argparse
import os
import random
from typing import Any

import scan
import scan_safe

_ORIGINAL_SAFE_FETCH = scan_safe.fetch_ohlcv_safe


def fetch_ohlcv_no_exit(*args: Any, **kwargs: Any):
    try:
        return _ORIGINAL_SAFE_FETCH(*args, **kwargs)
    except SystemExit as exc:
        scan_safe.logger.warning("fetch_ohlcv stopped by vnstock quota: %s", str(exc).splitlines()[0])
        return None


_SAFE_FETCH = fetch_ohlcv_no_exit

import weekend_plus as plus

scan_safe.fetch_ohlcv_safe = _SAFE_FETCH
plus.weekend.scan_safe.fetch_ohlcv_safe = _SAFE_FETCH

_old_fetch_fundamental = plus.weekend.fetch_fundamental
_old_build_report = plus.weekend.build_report


def patch_scan_metadata() -> None:
    sector_default_group = getattr(
        scan,
        "SECTOR_DEFAULT_GROUP",
        {
            "Bank": "G2",
            "Chung khoan": "G4",
            "Bao hiem": "G3",
            "BDS dan cu": "G5",
            "BDS KCN": "G3",
            "Xay dung dau tu cong": "G6",
            "Thep": "G4",
            "Da xi mang nhua duong": "G4",
            "Go cao su": "G3",
            "Hoa chat phan bon": "G4",
            "Cao su nhua": "G4",
            "Dau khi": "G4",
            "Dien tien ich": "G3",
            "Ban le": "G2",
            "Thuc pham do uong": "G2",
            "Det may san xuat": "G6",
            "Thuy san": "G6",
            "Nong nghiep chan nuoi": "G6",
            "Cong nghe vien thong": "G2",
            "Logistics cang bien": "G4",
        },
    )
    scan.TICKER_GROUP["VNINDEX"] = "G1"
    for ticker in scan.ALL_TICKERS:
        scan.TICKER_GROUP.setdefault(ticker, sector_default_group.get(scan.TICKER_TO_SECTOR.get(ticker, ""), "G4"))


def fetch_fundamental_safe(symbol: str) -> Any:
    try:
        return _old_fetch_fundamental(symbol)
    except SystemExit as exc:
        plus.weekend.logger.warning("Fundamental %s stopped by vnstock quota: %s", symbol, str(exc).splitlines()[0])
        return None


def build_report_compat(opportunities, sectors, mode, near_high=None, missing_fundamental=None) -> str:
    report = None
    for args in (
        (opportunities, sectors, mode, near_high, missing_fundamental),
        (opportunities, sectors, mode, near_high),
        (opportunities, sectors, mode),
    ):
        try:
            report = _old_build_report(*args)
            break
        except TypeError:
            continue
    if report is None:
        raise TypeError("No compatible weekend build_report signature")
    if missing_fundamental:
        preview = ",".join(missing_fundamental[:12])
        suffix = "..." if len(missing_fundamental) > 12 else ""
        report = report.replace(
            "Quet PE/PB + chiet khau gia + chat luong + nganh + risk. Khong phai khuyen nghi mua ban.",
            "Quet PE/PB + chiet khau gia + chat luong + nganh + risk. Khong phai khuyen nghi mua ban.\n"
            f"Fundamental missing: {len(missing_fundamental)} ma ({preview}{suffix})",
        )
    return report


patch_scan_metadata()
plus.weekend.fetch_fundamental = fetch_fundamental_safe
plus.weekend.build_report = build_report_compat


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=os.getenv("WEEKEND_MODE", "full"))
    args = parser.parse_args()
    mode = str(args.mode).strip().lower()
    if mode not in {"full", "test"}:
        plus.weekend.logger.warning("Unknown mode=%r, using full", mode)
        mode = "full"

    if os.getenv("GITHUB_ACTIONS") and mode != "test":
        delay = random.randint(0, max(plus.weekend.RANDOM_START_MAX, 0))
        plus.weekend.logger.info("Weekend random start delay %ss", delay)
        await asyncio.sleep(delay)

    tickers = plus.weekend.build_universe(mode)
    valid_tickers = [s for s in tickers if 3 <= len(str(s).strip()) <= 12]
    dropped = sorted(set(tickers) - set(valid_tickers))
    if dropped:
        plus.weekend.logger.warning("Dropping invalid ticker(s): %s", ",".join(dropped))
    tickers = valid_tickers
    random.shuffle(tickers)
    workers = int(os.getenv("WEEKEND_MAX_WORKERS", "3"))
    workers = max(1, min(workers, len(tickers) or 1))
    plus.weekend.logger.info("Weekend opportunity scan mode=%s tickers=%s workers=%s", mode, len(tickers), workers)

    force_refresh = mode == "test"
    semaphore = asyncio.Semaphore(workers)

    async def analyze_one(index: int, symbol: str):
        async with semaphore:
            plus.weekend.logger.info("[%s/%s] Analyze %s", index, len(tickers), symbol)
            try:
                return await asyncio.to_thread(plus.weekend.fetch_symbol_packet, symbol, force_refresh)
            except SystemExit as exc:
                plus.weekend.logger.warning("[%s] stopped by quota: %s", symbol, str(exc).splitlines()[0])
                return None
            except Exception as exc:
                plus.weekend.logger.exception("[%s] weekend scan failed: %s", symbol, exc)
                return None

    packets = [
        packet
        for packet in await asyncio.gather(*(analyze_one(index, symbol) for index, symbol in enumerate(tickers, start=1)))
        if packet is not None
    ]
    missing_fundamental = [str(packet["symbol"]) for packet in packets if packet.get("fundamental") is None]
    if missing_fundamental:
        plus.weekend.logger.warning(
            "Weekend fundamental missing for %s/%s symbols: %s",
            len(missing_fundamental),
            len(packets),
            ",".join(missing_fundamental[:30]),
        )

    sectors = plus.weekend.build_sector_snapshots(packets)
    opportunities = plus.weekend.build_opportunities(packets, sectors)
    near_high = []
    build_near_high = getattr(plus.weekend, "build_near_high_snapshots", None)
    if callable(build_near_high) and plus.weekend.UPDATE_NEAR_HIGH and mode != "test":
        near_high = build_near_high(packets)
    try:
        plus.weekend.save_outputs(opportunities, sectors, near_high)
    except TypeError:
        plus.weekend.save_outputs(opportunities, sectors)

    report = plus.weekend.build_report(opportunities, sectors, mode, near_high, missing_fundamental)
    await scan.send_chunks("*THIEUCUTOO WEEKEND*", report)
    plus.weekend.logger.info("Weekend opportunities found: %s near_high_skip=%s", len(opportunities), len(near_high))


if __name__ == "__main__":
    asyncio.run(main())
