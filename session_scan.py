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
import market_calendar
import state_manager
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
SESSION_QUICK_LIMIT = env_int("SESSION_QUICK_LIMIT", 50, min_value=10)
SESSION_QUICK_SIGNAL_LIMIT = env_int("SESSION_QUICK_SIGNAL_LIMIT", 38, min_value=5)
SESSION_QUICK_NOTE_LIMIT = env_int("SESSION_QUICK_NOTE_LIMIT", 12, min_value=0)
SCAN_SYMBOL_TIMEOUT = env_int("SCAN_SYMBOL_TIMEOUT_SEC", 90, min_value=10)
SCAN_RETRY_FAILED_DELAY_MIN = env_int("SCAN_RETRY_FAILED_DELAY_MIN_SEC", 20, min_value=0)
SCAN_RETRY_FAILED_DELAY_MAX = env_int("SCAN_RETRY_FAILED_DELAY_MAX_SEC", 60, min_value=0)
SCAN_RETRY_FAILED_MAX_SYMBOLS = env_int("SCAN_RETRY_FAILED_MAX_SYMBOLS", 24, min_value=0)
SESSION_DEADLINE_BUFFER = env_int("SESSION_DEADLINE_BUFFER_SEC", 90, min_value=0)
SCAN_FAILED_SYMBOLS: set[str] = set()


SESSION_WINDOWS = {
    "morning": {
        "title": "MORNING 12H30",
        "broad_after": dt_time(10, 30),
        "focus_after": dt_time(12, 30),
        "report_after": dt_time(12, 30),
        "description": "Lay data sau 10h30, quet lai note/co manh sau 12h30.",
    },
    "morning_focus": {
        "title": "MORNING QUICK 10H31",
        "broad_after": dt_time(10, 31),
        "focus_after": None,
        "report_after": None,
        "description": "Quet nhanh note/co manh/gan break tu lan quet truoc, toi da 50 ma.",
    },
    "morning_broad": {
        "title": "MORNING BROAD 10H31",
        "broad_after": dt_time(10, 31),
        "focus_after": None,
        "report_after": None,
        "description": "Quet rong buoi sang sau 10h31, muc tieu tra report truoc 11h15.",
    },
    "afternoon": {
        "title": "AFTERNOON 14H15",
        "broad_after": dt_time(13, 45),
        "focus_after": dt_time(14, 0),
        "report_after": dt_time(14, 15),
        "description": "Lay data sau 13h45, uu tien note/co manh sau 14h.",
    },
    "afternoon_focus": {
        "title": "AFTERNOON QUICK 13H31",
        "broad_after": dt_time(13, 31),
        "focus_after": None,
        "report_after": None,
        "description": "Quet nhanh note/co manh/gan break dau phien chieu, toi da 50 ma.",
    },
    "afternoon_broad": {
        "title": "AFTERNOON BROAD 14H01",
        "broad_after": dt_time(14, 1),
        "focus_after": None,
        "report_after": None,
        "description": "Quet rong phien chieu sau 14h01, uu tien co co the mua ban kip.",
    },
    "afternoon_split": {
        "title": "AFTERNOON SPLIT 13H31/14H00",
        "broad_after": dt_time(13, 31),
        "focus_after": dt_time(14, 0),
        "report_after": None,
        "description": "13h31 quet cac ma chua uu tien, 14h00 quet lai note/co manh/ma phien sang, muc tieu tra truoc 14h15.",
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


def base_mode(mode: str) -> str:
    if mode.startswith("morning"):
        return "morning"
    if mode.startswith("afternoon"):
        return "afternoon"
    return mode


def is_quick_mode(mode: str) -> bool:
    return mode.endswith("_focus")


def is_broad_mode(mode: str) -> bool:
    return mode.endswith("_broad")


def session_target_datetime(target: dt_time | None) -> datetime | None:
    if target is None:
        return None
    now = datetime.now(VN_TZ)
    return datetime.combine(now.date(), target, tzinfo=VN_TZ)


def session_deadline(mode: str) -> dt_time | None:
    raw = os.getenv(f"SESSION_{mode.upper()}_DEADLINE", "").strip()
    if raw:
        try:
            hour, minute = raw.split(":", 1)
            return dt_time(int(hour), int(minute))
        except Exception:
            logger.warning("Invalid SESSION_%s_DEADLINE=%r", mode.upper(), raw)
    defaults = {
        "morning_broad": dt_time(11, 13),
        "afternoon_split": dt_time(14, 13),
    }
    return defaults.get(mode)


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


def add_symbol_once(target: list[str], seen: set[str], symbol: Any) -> None:
    normalized = normalize_symbol(symbol)
    if not normalized or normalized == "VNINDEX" or normalized in seen:
        return
    if len(normalized) < 3 or len(normalized) > 12:
        return
    target.append(normalized)
    seen.add(normalized)


def previous_focus_symbols(watch_items: dict[str, dict[str, Any]], limit: int | None = None) -> list[str]:
    limit = limit or SESSION_QUICK_LIMIT
    note_limit = min(SESSION_QUICK_NOTE_LIMIT, limit)
    signal_limit = min(SESSION_QUICK_SIGNAL_LIMIT, max(limit - note_limit, 0))
    signal_symbols: list[str] = []
    note_symbols: list[str] = []
    seen: set[str] = set()

    for symbol in state_manager.memory_focus_symbols(limit=signal_limit):
        add_symbol_once(signal_symbols, seen, symbol)
        if len(signal_symbols) >= signal_limit:
            break

    latest = scan.json_load(DATA_DIR / "session_alerts_latest.json", {})
    if isinstance(latest, dict):
        for symbol in latest.get("focus_symbols", []):
            add_symbol_once(signal_symbols, seen, symbol)
            if len(signal_symbols) >= signal_limit:
                break
        for item in latest.get("top", []):
            if isinstance(item, dict):
                add_symbol_once(signal_symbols, seen, item.get("symbol"))
            if len(signal_symbols) >= signal_limit:
                break

    results_latest = scan.json_load(DATA_DIR / "results_latest.json", [])
    if isinstance(results_latest, list):
        for item in results_latest:
            if not isinstance(item, dict):
                continue
            score = int(item.get("win_score") or 0)
            near_break = bool(item.get("near_break"))
            if score >= 72 or (score >= 62 and near_break):
                add_symbol_once(signal_symbols, seen, item.get("symbol"))
            if len(signal_symbols) >= signal_limit:
                break

    for symbol in watch_items:
        add_symbol_once(note_symbols, seen, symbol)
        if len(note_symbols) >= note_limit:
            break

    return (signal_symbols + note_symbols)[:limit]


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
    stop_at: dt_time | None = None,
    retry_failures: bool = True,
) -> dict[str, scan.ScanResult]:
    results: dict[str, scan.ScanResult] = {}
    if not symbols:
        return results

    stop_at_dt = session_target_datetime(stop_at)
    failed_symbols: list[str] = []
    min_batch_seconds = max(1, int((scan.BATCH_SIZE / max(scan.REQUESTS_PER_MINUTE, 1)) * 60))
    for start in range(0, len(symbols), scan.BATCH_SIZE):
        if stop_at_dt is not None and datetime.now(VN_TZ) >= stop_at_dt:
            logger.warning("%s reached stop time %s, keeping partial broad results", label, stop_at_dt.strftime("%H:%M"))
            break
        batch_started = time.time()
        batch = symbols[start:start + scan.BATCH_SIZE]
        logger.info("%s batch %s-%s/%s: %s", label, start + 1, start + len(batch), len(symbols), ",".join(batch))
        semaphore = asyncio.Semaphore(min(scan.MAX_WORKERS, len(batch)))

        async def run_symbol(symbol: str) -> tuple[str, Any, scan.ScanResult | None]:
            async with semaphore:
                try:
                    return await asyncio.wait_for(
                        asyncio.to_thread(scan.process_symbol, symbol, force_refresh),
                        timeout=SCAN_SYMBOL_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.warning("%s %s timed out after %ss, skipping symbol", label, symbol, SCAN_SYMBOL_TIMEOUT)
                    return symbol, None, None
                except Exception as exc:
                    logger.warning("%s %s failed unexpectedly: %s", label, symbol, exc)
                    return symbol, None, None

        for symbol, df, result in await asyncio.gather(*(run_symbol(symbol) for symbol in batch)):
            if df is not None and result:
                results[symbol] = result
                scan.save_history(symbol, df, history_store, peak_store)
            else:
                failed_symbols.append(symbol)

        elapsed = time.time() - batch_started
        if start + scan.BATCH_SIZE < len(symbols):
            delay = max(0, min_batch_seconds - elapsed) + random.uniform(scan.DELAY_MIN, scan.DELAY_MAX)
            if stop_at_dt is not None:
                remaining = (stop_at_dt - datetime.now(VN_TZ)).total_seconds()
                if remaining <= 0:
                    logger.warning("%s stop time hit after batch, skipping remaining symbols", label)
                    break
                delay = min(delay, max(0.0, remaining))
            logger.info("%s sleep %.1fs before next batch", label, delay)
            await asyncio.sleep(delay)

    failed_symbols = [symbol for symbol in dict.fromkeys(failed_symbols) if symbol not in results]
    retry_symbols: list[str] = []
    if retry_failures and failed_symbols and SCAN_RETRY_FAILED_MAX_SYMBOLS > 0:
        if stop_at_dt is not None and datetime.now(VN_TZ) >= stop_at_dt:
            logger.warning("%s reached stop time before retry pass, keeping %s failed symbol(s)", label, len(failed_symbols))
        else:
            retry_symbols = failed_symbols[:SCAN_RETRY_FAILED_MAX_SYMBOLS]

    if retry_symbols:
        delay_max = max(SCAN_RETRY_FAILED_DELAY_MIN, SCAN_RETRY_FAILED_DELAY_MAX)
        delay = random.uniform(SCAN_RETRY_FAILED_DELAY_MIN, delay_max)
        if stop_at_dt is not None:
            remaining = (stop_at_dt - datetime.now(VN_TZ)).total_seconds()
            if remaining <= 0:
                delay = 0
                retry_symbols = []
            else:
                delay = min(delay, max(0.0, remaining))

    if retry_symbols:
        logger.warning(
            "%s retry pass for %s failed symbol(s) after %.1fs: %s",
            label,
            len(retry_symbols),
            delay,
            ",".join(retry_symbols),
        )
        if delay > 0:
            await asyncio.sleep(delay)
        retry_results = await scan_symbols(
            retry_symbols,
            force_refresh=True,
            history_store=history_store,
            peak_store=peak_store,
            label=f"{label}-retry",
            stop_at=stop_at,
            retry_failures=False,
        )
        results.update(retry_results)
        failed_symbols = [symbol for symbol in failed_symbols if symbol not in results]

    for symbol in failed_symbols:
        if symbol != "VNINDEX":
            SCAN_FAILED_SYMBOLS.add(symbol)

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
        if r.failed_break or r.win_score < sell:
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
    timing = "sau 14h uu tien du lieu moi" if base_mode(mode) == "afternoon" else "cho xac nhan sau moc phien"
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
    if mode == "eod" or base_mode(mode) == "afternoon":
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
    failed_breaks = scan.update_failed_breaks([r for r in ordered if r.symbol != "VNINDEX"])
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
    SCAN_FAILED_SYMBOLS.clear()

    market_status = market_calendar.get_market_day_status()
    if mode != "test" and market_status.closed:
        if market_calendar.should_skip_scan(market_status):
            logger.info(
                "Market closed on %s (%s), policy=skip. Writing closed marker and stopping scan.",
                market_status.date,
                market_status.reason,
            )
            scan.json_save(DATA_DIR / "session_alerts_latest.json", market_calendar.closed_alert_payload(mode, market_status), pretty=False)
            await scan.send_chunks("*THIEUCUTOO SESSION*", market_calendar.closed_notice(mode, market_status))
            return
        logger.info(
            "Market closed on %s (%s), policy=scan_old. Scanner will continue using latest available data.",
            market_status.date,
            market_status.reason,
        )

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
    hard_stop = session_deadline(mode)

    index_result = await scan_symbols(["VNINDEX"], force_refresh=True, history_store=history_store, peak_store=peak_store, label="index")
    results.update(index_result)

    if is_quick_mode(mode):
        focus_symbols = previous_focus_symbols(watch_items, SESSION_QUICK_LIMIT)
        logger.info(
            "%s quick symbols=%s signal_cap=%s note_cap=%s: %s",
            mode,
            len(focus_symbols),
            SESSION_QUICK_SIGNAL_LIMIT,
            SESSION_QUICK_NOTE_LIMIT,
            ",".join(focus_symbols),
        )
        quick_results = await scan_symbols(focus_symbols, force_refresh=True, history_store=history_store, peak_store=peak_store, label=f"{mode}-quick")
        results.update(quick_results)
    elif mode == "eod" or is_broad_mode(mode):
        scan_list = universe
        broad_results = await scan_symbols(
            scan_list,
            force_refresh=True,
            history_store=history_store,
            peak_store=peak_store,
            label=f"{mode}-broad",
            stop_at=hard_stop,
        )
        results.update(broad_results)
        focus_symbols = sorted(set(watch_symbols) | set(strong_candidates(results, watch_symbols)))
    elif mode == "afternoon_split":
        focus_symbols = previous_focus_symbols(watch_items, SESSION_QUICK_LIMIT)
        focus_set = set(focus_symbols)
        scan_list = [symbol for symbol in universe if symbol not in focus_set]
        logger.info(
            "%s broad_nonfocus=%s focus_1403=%s: %s",
            mode,
            len(scan_list),
            len(focus_symbols),
            ",".join(focus_symbols),
        )
        broad_results = await scan_symbols(
            scan_list,
            force_refresh=True,
            history_store=history_store,
            peak_store=peak_store,
            label=f"{mode}-broad-nonfocus",
            stop_at=window["focus_after"],
            retry_failures=False,
        )
        results.update(broad_results)
        await wait_until(window["focus_after"], "afternoon 14:00 priority scan")
        focus_results = await scan_symbols(
            focus_symbols,
            force_refresh=True,
            history_store=history_store,
            peak_store=peak_store,
            label=f"{mode}-focus-1403",
            stop_at=hard_stop,
        )
        results.update(focus_results)
    else:
        scan_list = [symbol for symbol in universe if symbol not in watch_symbols]
        broad_results = await scan_symbols(scan_list, force_refresh=True, history_store=history_store, peak_store=peak_store, label=f"{mode}-broad")
        results.update(broad_results)
        focus_symbols = sorted(watch_symbols | set(strong_candidates(results, watch_symbols)))
        await wait_until(window["focus_after"], "focus scan")
        focus_results = await scan_symbols(
            focus_symbols,
            force_refresh=True,
            history_store=history_store,
            peak_store=peak_store,
            label=f"{mode}-focus",
            stop_at=hard_stop,
        )
        results.update(focus_results)

    await wait_until(window["report_after"], "report")
    failed_breaks = save_session_outputs(mode, results, history_store, peak_store, focus_symbols, watch_items)
    report = build_session_report(mode, results, focus_symbols, watch_items)
    await scan.send_chunks("*THIEUCUTOO SESSION*", report)

    today = datetime.now(VN_TZ).date().isoformat()
    recent = scan.latest_failed_breaks(failed_breaks, limit=10, only_date=today)
    if recent and mode not in {"eod", "test"}:
        text = "*FAILED BREAK WATCH 25D*\n" + "\n".join(
            f"`{x['symbol']}` {x['date']} score {x.get('score')}: {x.get('reason','')}" for x in recent
        )
        await scan.send_chunks("*THIEUCUTOO RISK*", text)

    logger.info("Session %s completed: results=%s focus=%s", mode, len(results), len(focus_symbols))


if __name__ == "__main__":
    asyncio.run(main())
