#!/usr/bin/env python3
import argparse
import asyncio
import logging
import os
import random
import sys
import time
from dataclasses import asdict
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any

import scan
import scan_safe
import telegram_format as tf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("thieucutoo.session")

VN_TZ = timezone(timedelta(hours=7))
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)


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


SESSION_RANDOM_START_MAX = env_int("SESSION_RANDOM_START_MAX_SEC", 45, min_value=0)
SESSION_FOCUS_LIMIT = env_int("SESSION_FOCUS_LIMIT", 28, min_value=5)


SESSION_WINDOWS = {
    "morning": {
        "title": "MORNING 12H30",
        "broad_after": dt_time(10, 30),
        "focus_after": dt_time(12, 30),
        "report_after": dt_time(12, 30),
        "description": "Lay data sau 10h30, quet lai note/co manh sau 12h30.",
    },
    "afternoon": {
        "title": "AFTERNOON 14H15",
        "broad_after": dt_time(13, 45),
        "focus_after": dt_time(14, 0),
        "report_after": dt_time(14, 15),
        "description": "Lay data sau 13h45, uu tien note/co manh sau 14h.",
    },
    "eod": {
        "title": "EOD 15H+",
        "broad_after": dt_time(15, 5),
        "focus_after": None,
        "report_after": dt_time(15, 5),
        "description": "Tong ket sau 15h, co trang thai VNINDEX.",
    },
    "test": {
        "title": "TEST",
        "broad_after": None,
        "focus_after": None,
        "report_after": None,
        "description": "Test mode voi tap ma mau.",
    },
}


def parse_mode() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=os.getenv("SCAN_MODE", "auto"))
    args = parser.parse_args()
    mode = str(args.mode).strip().lower()
    if mode == "auto":
        now_vn = datetime.now(VN_TZ)
        current = now_vn.time()
        if current < dt_time(13, 0):
            return "morning"
        if current < dt_time(15, 0):
            return "afternoon"
        return "eod"
    if mode not in SESSION_WINDOWS:
        logger.warning("Unknown mode=%r, falling back to auto", mode)
        return parse_mode_from_auto()
    return mode


def parse_mode_from_auto() -> str:
    now_vn = datetime.now(VN_TZ)
    if now_vn.time() < dt_time(13, 0):
        return "morning"
    if now_vn.time() < dt_time(15, 0):
        return "afternoon"
    return "eod"


def session_target_datetime(target: dt_time | None) -> datetime | None:
    if target is None:
        return None
    now = datetime.now(VN_TZ)
    return datetime.combine(now.date(), target, tzinfo=VN_TZ)


async def wait_until(target: dt_time | None, label: str) -> None:
    target_dt = session_target_datetime(target)
    if target_dt is None:
        return
    now = datetime.now(VN_TZ)
    if now >= target_dt:
        return
    sleep_for = (target_dt - now).total_seconds()
    logger.info("Wait %.0fs until %s (%s)", sleep_for, label, target_dt.strftime("%H:%M"))
    await asyncio.sleep(sleep_for)


def normalize_symbol(value: Any) -> str:
    return str(value or "").upper().strip()


def load_watch_items() -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}

    portfolio = scan.json_load(DATA_DIR / "portfolio.json", [])
    if isinstance(portfolio, list):
        for raw in portfolio:
            if not isinstance(raw, dict):
                continue
            symbol = normalize_symbol(raw.get("symbol"))
            if not symbol:
                continue
            items[symbol] = {
                "symbol": symbol,
                "note": str(raw.get("note", "")).strip(),
                "buy_more_score": int(raw.get("buy_more_score", 78)),
                "sell_score": int(raw.get("sell_score", 45)),
                "position": str(raw.get("position", "holding")).strip() or "holding",
                "source": "portfolio",
            }

    notes = scan.json_load(DATA_DIR / "notes.json", {})
    if isinstance(notes, dict):
        for key, value in notes.items():
            symbol = normalize_symbol(key)
            if not symbol:
                continue
            note_text = value.get("note", "") if isinstance(value, dict) else value
            current = items.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "note": "",
                    "buy_more_score": 78,
                    "sell_score": 45,
                    "position": "note",
                    "source": "notes",
                },
            )
            note = str(note_text or "").strip()
            if note:
                current["note"] = note
                current["source"] = "portfolio+notes" if current["source"] == "portfolio" else "notes"
    elif isinstance(notes, list):
        for raw in notes:
            if not isinstance(raw, dict):
                continue
            symbol = normalize_symbol(raw.get("symbol"))
            if not symbol:
                continue
            current = items.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "note": "",
                    "buy_more_score": 78,
                    "sell_score": 45,
                    "position": "note",
                    "source": "notes",
                },
            )
            note = str(raw.get("note", "")).strip()
            if note:
                current["note"] = note
    return items


def all_universe_symbols(mode: str, watch_items: dict[str, dict[str, Any]]) -> list[str]:
    if mode == "test":
        symbols = ["VCB", "FPT", "HPG", "TCB", "SSI", "DIG", "VIX", "VNM", "PVD", "KDH"]
    else:
        symbols = list(scan.ALL_TICKERS)
    for symbol in watch_items:
        if symbol not in symbols:
            symbols.append(symbol)
    return sorted(set(symbols))


def configure_safe_api() -> None:
    scan.fetch_ohlcv = scan_safe.fetch_ohlcv_safe
    scan.MAX_WORKERS = min(env_int("SCAN_MAX_WORKERS", len(scan_safe.API_SOURCES), min_value=1), max(1, len(scan_safe.API_SOURCES)))
    scan.REQUESTS_PER_MINUTE = max(1, int(scan_safe.effective_total_api_rpm()))
    logger.info(
        "Session safe API: sources=%s effective_rpm=%.1f workers=%s",
        ",".join(scan_safe.API_SOURCES),
        scan_safe.effective_total_api_rpm(),
        scan.MAX_WORKERS,
    )


async def scan_symbols(
    symbols: list[str],
    force_refresh: bool,
    history_store: dict[str, Any],
    peak_store: dict[str, Any],
    label: str,
) -> dict[str, scan.ScanResult]:
    results: dict[str, scan.ScanResult] = {}
    if not symbols:
        return results

    min_batch_seconds = max(1, int((scan.BATCH_SIZE / max(scan.REQUESTS_PER_MINUTE, 1)) * 60))
    for start in range(0, len(symbols), scan.BATCH_SIZE):
        batch_started = time.time()
        batch = symbols[start:start + scan.BATCH_SIZE]
        logger.info("%s batch %s-%s/%s: %s", label, start + 1, start + len(batch), len(symbols), ",".join(batch))
        semaphore = asyncio.Semaphore(min(scan.MAX_WORKERS, len(batch)))

        async def run_symbol(symbol: str) -> tuple[str, Any, scan.ScanResult | None]:
            async with semaphore:
                return await asyncio.to_thread(scan.process_symbol, symbol, force_refresh)

        for symbol, df, result in await asyncio.gather(*(run_symbol(symbol) for symbol in batch)):
            if df is not None and result:
                results[symbol] = result
                scan.save_history(symbol, df, history_store, peak_store)

        elapsed = time.time() - batch_started
        if start + scan.BATCH_SIZE < len(symbols):
            delay = max(0, min_batch_seconds - elapsed) + random.uniform(scan.DELAY_MIN, scan.DELAY_MAX)
            logger.info("%s sleep %.1fs before next batch", label, delay)
            await asyncio.sleep(delay)

    return results


def strong_candidates(results: dict[str, scan.ScanResult], watch_symbols: set[str]) -> list[str]:
    ranked = sorted(
        [
            r for r in results.values()
            if r.symbol != "VNINDEX"
            and r.symbol not in watch_symbols
            and not r.failed_break
            and (r.win_score >= 72 or (r.win_score >= 62 and r.near_break and r.obv_up))
        ],
        key=lambda item: (item.win_score, item.near_break, item.flow_score),
        reverse=True,
    )
    return [item.symbol for item in ranked[:SESSION_FOCUS_LIMIT]]


def market_status(market: scan.ScanResult | None) -> str:
    if market is None:
        return tf.format_market_card(None, "")
    if market.failed_break or market.win_score < 45:
        state = "RISK OFF"
    elif market.win_score >= 68 and market.obv_up and market.mfi >= 50:
        state = "RISK ON"
    elif market.win_score >= 55:
        state = "NEUTRAL / CHO XAC NHAN"
    else:
        state = "YEU / THAN TRONG"
    return tf.format_market_card(market, state)


def portfolio_alert_lines(results: dict[str, scan.ScanResult], watch_items: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    if not watch_items:
        return ["Portfolio/note: chua co ma trong data/portfolio.json hoac data/notes.json."]
    for symbol, item in watch_items.items():
        r = results.get(symbol)
        note = str(item.get("note", "")).strip()
        buy_more = int(item.get("buy_more_score", 78))
        sell = int(item.get("sell_score", 45))
        if r is None:
            lines.append(f"`{symbol}` NO_DATA | {note}")
            continue
        if r.failed_break or r.win_score <= sell:
            action = "BAT LOI / GIAM RUI RO"
        elif r.win_score >= buy_more and r.near_break and not r.failed_break:
            action = "TIN HIEU DEP / CANH MUA THEM"
        elif r.win_score >= buy_more:
            action = "TIN HIEU TOT / THEO DOI MUA THEM"
        elif r.win_score >= 62 and r.obv_up:
            action = "GIU / THEO DOI TICH CUC"
        else:
            action = "GIU / THEO DOI"
        lines.append(tf.format_stock_card(r, action=action, note=note))
    return lines


def projection_line(r: scan.ScanResult, mode: str) -> str:
    if r.failed_break:
        action = "NE FAILED BREAK"
    elif r.win_score >= 80 and r.near_break and r.obv_up:
        action = "CANH MUA NGAY NEU GIU NEN"
    elif r.win_score >= 74:
        action = "CANH MUA TUNG PHAN"
    elif r.near_break and r.win_score >= 62:
        action = "CANH BREAK"
    else:
        action = "WATCH"
    timing = "sau 14h uu tien du lieu moi" if mode == "afternoon" else "cho xac nhan sau moc phien"
    return tf.format_stock_card(r, action=action, timing=timing)


def build_session_report(
    mode: str,
    results: dict[str, scan.ScanResult],
    focus_symbols: list[str],
    watch_items: dict[str, dict[str, Any]],
) -> str:
    window = SESSION_WINDOWS[mode]
    ordered = sorted(results.values(), key=lambda item: item.win_score, reverse=True)
    market = results.get("VNINDEX")
    stocks = [r for r in ordered if r.symbol != "VNINDEX"]
    focus_set = set(focus_symbols)
    focus_results = [r for r in stocks if r.symbol in focus_set and not r.failed_break]
    strong = [r for r in stocks if r.win_score >= 72 and not r.failed_break][:10]
    break_watch = [r for r in stocks if r.win_score >= 62 and r.near_break and not r.failed_break][:14]
    failed = [r for r in stocks if r.failed_break][:10]
    sectors = scan.summarize_sector(stocks)[:8]
    now = datetime.now(VN_TZ).strftime("%d/%m %H:%M")

    lines = [
        f"*THIEUCUTOO {window['title']}* `{now}`",
        f"{window['description']} Score 0-100, khong phai cam ket loi nhuan.",
        market_status(market),
        "",
        "*PORTFOLIO / NOTE BAT BUOC*",
    ]
    lines += portfolio_alert_lines(results, watch_items)
    lines += ["", "*DU PHONG CO MANH CAN CHU Y*"]
    lines += [projection_line(r, mode) for r in (focus_results or strong)[:12]] or ["Chua co co manh du nguong."]
    lines += ["", "*CO MANH THI TRUONG*"]
    lines += [tf.format_stock_card(r) for r in strong] or ["Khong co ma dat nguong."]
    lines += ["", "*GAN BREAK / CO THE MUA TUNG PHAN*"]
    lines += [tf.format_stock_card(r, action="CANH BREAK / MUA TUNG PHAN") for r in break_watch] or ["Khong co ma dat nguong."]
    lines += ["", "*NGANH LEAD / RISK*"]
    lines += [tf.format_sector_line(line) for line in sectors] or ["Chua du du lieu nganh."]
    if mode in {"eod", "afternoon"}:
        lines += ["", "*FAILED BREAK / CAN NE*"]
        lines += [tf.format_stock_card(r, action="CAN NE / GIAM RUI RO") for r in failed] or ["Khong co failed-break dang chu y."]
    return "\n".join(lines)


def save_session_outputs(
    mode: str,
    results: dict[str, scan.ScanResult],
    history_store: dict[str, Any],
    peak_store: dict[str, Any],
    focus_symbols: list[str],
    watch_items: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered = sorted(results.values(), key=lambda item: item.win_score, reverse=True)
    failed_breaks = scan.update_failed_breaks(ordered)
    scan.json_save(DATA_DIR / "results_latest.json", [asdict(r) for r in ordered], pretty=False)
    scan.json_save(DATA_DIR / "history_data.json", history_store, pretty=False)
    scan.json_save(DATA_DIR / "historical_peaks.json", peak_store, pretty=False)

    alerts = {
        "updated_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        "mode": mode,
        "focus_symbols": focus_symbols,
        "portfolio_symbols": list(watch_items.keys()),
        "market": asdict(results["VNINDEX"]) if "VNINDEX" in results else None,
        "top": [asdict(r) for r in ordered[:20]],
    }
    scan.json_save(DATA_DIR / "session_alerts_latest.json", alerts, pretty=False)
    return failed_breaks


async def main() -> None:
    mode = parse_mode()
    window = SESSION_WINDOWS[mode]
    configure_safe_api()

    if os.getenv("GITHUB_ACTIONS") and mode != "test":
        delay = random.randint(0, max(SESSION_RANDOM_START_MAX, 0))
        logger.info("Session random start delay %ss", delay)
        await asyncio.sleep(delay)

    await wait_until(window["broad_after"], "session broad scan")

    watch_items = load_watch_items()
    watch_symbols = set(watch_items.keys())
    universe = all_universe_symbols(mode, watch_items)
    history_store: dict[str, Any] = {}
    peak_store: dict[str, Any] = scan.json_load(DATA_DIR / "historical_peaks.json", {})
    results: dict[str, scan.ScanResult] = {}

    index_result = await scan_symbols(["VNINDEX"], force_refresh=True, history_store=history_store, peak_store=peak_store, label="index")
    results.update(index_result)

    if mode == "eod":
        scan_list = universe
    else:
        scan_list = [symbol for symbol in universe if symbol not in watch_symbols]
    broad_results = await scan_symbols(scan_list, force_refresh=True, history_store=history_store, peak_store=peak_store, label=f"{mode}-broad")
    results.update(broad_results)

    focus_symbols = sorted(watch_symbols | set(strong_candidates(results, watch_symbols)))
    if mode != "eod":
        await wait_until(window["focus_after"], "focus scan")
        focus_results = await scan_symbols(focus_symbols, force_refresh=True, history_store=history_store, peak_store=peak_store, label=f"{mode}-focus")
        results.update(focus_results)

    await wait_until(window["report_after"], "report")
    failed_breaks = save_session_outputs(mode, results, history_store, peak_store, focus_symbols, watch_items)
    report = build_session_report(mode, results, focus_symbols, watch_items)
    await scan.send_chunks("*THIEUCUTOO SESSION*", report)

    if failed_breaks and mode != "eod":
        recent = failed_breaks[-10:]
        text = "*FAILED BREAK WATCH 25D*\n" + "\n".join(
            f"`{x['symbol']}` {x['date']} score {x.get('score')}: {x.get('reason','')}" for x in recent
        )
        await scan.send_chunks("*THIEUCUTOO RISK*", text)

    logger.info("Session %s completed: results=%s focus=%s", mode, len(results), len(focus_symbols))


if __name__ == "__main__":
    asyncio.run(main())
