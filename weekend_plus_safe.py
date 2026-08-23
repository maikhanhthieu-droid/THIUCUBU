#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import argparse
import os
import random
import time
from typing import Any

import run_journal
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


def build_report_compat(
    opportunities,
    sectors,
    mode,
    missing_fundamental=None,
    weekly_watch=None,
) -> str:
    report = _old_build_report(opportunities, sectors, mode, weekly_watch)
    if missing_fundamental:
        preview = ",".join(missing_fundamental[:12])
        suffix = "..." if len(missing_fundamental) > 12 else ""
        lines = report.splitlines()
        lines.insert(2 if len(lines) > 1 else 1, f"Thiếu fundamental: {len(missing_fundamental)} mã ({preview}{suffix})")
        report = "\n".join(lines)
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

    started = time.time()
    run_id = run_journal.start_run(f"weekend_{mode}", os.getenv("GITHUB_EVENT_NAME", ""))
    try:
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
        force_refresh = mode == "test"
        index_df = await asyncio.to_thread(
            plus.weekend.scan_safe.fetch_ohlcv_safe,
            "VNINDEX",
            plus.weekend.HISTORY_BARS,
            force_refresh,
        )
        plus.weekend.set_weekly_index(index_df)
        if index_df is None:
            plus.weekend.logger.warning("VNINDEX weekly context unavailable; RS confidence will be reduced")
        workers = int(os.getenv("WEEKEND_MAX_WORKERS", "3"))
        workers = max(1, min(workers, len(tickers) or 1))
        plus.weekend.logger.info("Weekend opportunity scan mode=%s tickers=%s workers=%s", mode, len(tickers), workers)

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

        if mode != "test":
            plus.weekend.save_fundamental_history(packets)
        sectors = plus.weekend.build_sector_snapshots(packets)
        opportunities = plus.weekend.build_opportunities(packets, sectors)
        weekly_watch = plus.weekend.weekly_bottom_watch.rank_packets(packets)
        plus.weekend.save_outputs(opportunities, sectors, weekly_watch)
        if mode != "test":
            plus.weekend.source_router.update_from_weekend(
                opportunities,
                universe=scan.ALL_TICKERS,
            )

        report = plus.weekend.build_report(
            opportunities,
            sectors,
            mode,
            missing_fundamental,
            weekly_watch,
        )
        telegram_sent = bool(await scan.send_chunks("*THIEUCUBU WEEKEND*", report)) and not scan.DRY_RUN
        fundamental_cache = plus.weekend.fundamental_cache_stats()
        scan_safe.save_source_health(extra={"fundamental_cache": fundamental_cache})
        run_journal.finish_run(
            run_id,
            f"weekend_{mode}",
            "success",
            success_count=len(packets),
            failed_symbols=[],
            elapsed_sec=time.time() - started,
            telegram_sent=telegram_sent,
        )
        plus.weekend.logger.info(
            "Weekend opportunities found: %s weekly_bottom_watch=%s fundamental_cache=%s",
            len(opportunities),
            len(weekly_watch),
            fundamental_cache,
        )
    except Exception as exc:
        scan_safe.save_source_health(extra={"fundamental_cache": plus.weekend.fundamental_cache_stats()})
        run_journal.fail_run(run_id, f"weekend_{mode}", exc, elapsed_sec=time.time() - started)
        raise


if __name__ == "__main__":
    asyncio.run(main())
