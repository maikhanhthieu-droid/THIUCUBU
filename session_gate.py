#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, time as dt_time, timedelta
import os
import random
from typing import Any

import market_intel as intel
import regime_gate
import scan
import session_plus as plus
import telegram_format as tf

_old_update_signal_tracker = intel.update_signal_tracker

plus.sess.SESSION_WINDOWS["morning"].update(
    {
        "title": "MORNING TRUOC 12H30",
        "broad_after": dt_time(10, 35),
        "focus_after": dt_time(11, 31),
        "report_after": dt_time(12, 25),
        "description": "Lay data sau 10h35 co jitter 0-4p, quet lai note/co manh tu 11h31, tra report truoc 12h30.",
    }
)
plus.sess.SESSION_WINDOWS["morning_focus"].update(
    {
        "title": "MORNING QUICK 10H31",
        "broad_after": dt_time(10, 31),
        "focus_after": None,
        "report_after": None,
        "description": "Quet nhanh note/co manh/gan break tu lan truoc, toi da 40 ma; qua 20p thi huy job.",
    }
)
plus.sess.SESSION_WINDOWS["morning_broad"].update(
    {
        "title": "MORNING BROAD 11H16",
        "broad_after": dt_time(11, 16),
        "focus_after": None,
        "report_after": None,
        "description": "Quet rong buoi sang sau 11h16, cap nhat co manh va gan break.",
    }
)
plus.sess.SESSION_WINDOWS["afternoon"].update(
    {
        "title": "AFTERNOON TRUOC 14H17",
        "broad_after": dt_time(13, 35),
        "focus_after": dt_time(14, 0),
        "report_after": dt_time(14, 10),
        "description": "Lay data sau 13h35 co jitter 0-4p, uu tien note/co manh sau 14h, tra report truoc 14h17.",
    }
)
plus.sess.SESSION_WINDOWS["afternoon_focus"].update(
    {
        "title": "AFTERNOON QUICK 13H31",
        "broad_after": dt_time(13, 31),
        "focus_after": None,
        "report_after": None,
        "description": "Quet nhanh note/co manh/gan break dau phien chieu, toi da 40 ma; qua 20p thi huy job.",
    }
)
plus.sess.SESSION_WINDOWS["afternoon_broad"].update(
    {
        "title": "AFTERNOON BROAD 14H01",
        "broad_after": dt_time(14, 1),
        "focus_after": None,
        "report_after": None,
        "description": "Quet rong phien chieu sau 14h01, uu tien co co the mua ban kip.",
    }
)
plus.sess.SESSION_WINDOWS["eod"].update(
    {
        "title": "EOD 15H+",
        "broad_after": dt_time(15, 5),
        "focus_after": None,
        "report_after": dt_time(15, 5),
        "description": "Tong ket sau 15h, co trang thai VNINDEX.",
    }
)

_old_wait_until = plus.sess.wait_until
_JITTER_TARGETS = {dt_time(10, 35), dt_time(13, 35)}


async def wait_until_with_data_jitter(target: dt_time | None, label: str) -> None:
    target_dt = plus.sess.session_target_datetime(target)
    await _old_wait_until(target, label)
    if label != "session broad scan" or target not in _JITTER_TARGETS or target_dt is None:
        return
    max_jitter = max(0, int(os.getenv("SESSION_DATA_JITTER_MAX_SEC", "240")))
    late_sec = max(0.0, (datetime.now(plus.sess.VN_TZ) - target_dt).total_seconds())
    remaining_jitter = max(0, max_jitter - int(late_sec))
    if remaining_jitter <= 0:
        return
    delay = random.randint(0, remaining_jitter)
    if delay > 0:
        plus.sess.logger.info("Data window jitter %ss after %s", delay, target_dt.strftime("%H:%M"))
        await asyncio.sleep(delay)


def update_signal_tracker_gated(
    results: dict[str, scan.ScanResult],
    metrics_by_symbol: dict[str, dict[str, Any]],
    mode: str,
    min_score: int = 72,
) -> list[dict[str, Any]]:
    allowed = {
        symbol: result
        for symbol, result in results.items()
        if symbol == "VNINDEX"
        or regime_gate.signal_allowed(result, metrics_by_symbol.get(symbol), min_score=min_score)
    }
    return _old_update_signal_tracker(allowed, metrics_by_symbol, mode, min_score=min_score)


def build_session_report(
    mode: str,
    results: dict[str, scan.ScanResult],
    focus_symbols: list[str],
    watch_items: dict[str, dict[str, Any]],
) -> str:
    window = plus.sess.SESSION_WINDOWS[mode]
    metrics = plus._STATE.get("metrics", {})
    regime = plus._STATE.get("regime", {})
    rotation_alerts = plus._STATE.get("rotation", [])
    ordered = sorted(results.values(), key=lambda x: (regime_gate.adv_score(x, metrics.get(x.symbol)), x.win_score, x.flow_score), reverse=True)
    market = results.get("VNINDEX")
    stocks = [x for x in ordered if x.symbol != "VNINDEX"]
    focus_set = set(focus_symbols)
    focus_results = [x for x in stocks if x.symbol in focus_set and regime_gate.signal_allowed(x, metrics.get(x.symbol), min_score=62)]
    strong = regime_gate.filter_results(stocks, metrics, min_score=72)[:10]
    break_watch = regime_gate.filter_results(stocks, metrics, min_score=62, require_near_break=True)[:14]
    suppressed = regime_gate.suppressed_lines(stocks, metrics, min_score=72)
    failed = [x for x in stocks if x.failed_break][:10]
    sectors = scan.summarize_sector(stocks)[:8]
    now = datetime.now(plus.sess.VN_TZ).strftime("%d/%m %H:%M")

    lines = [
        f"*THIEUCUTOO {window['title']}* `{now}`",
        f"{window['description']} Score 0-100, khong phai cam ket loi nhuan.",
        plus.market_status(market, regime),
        intel.format_regime(regime),
        "",
        "*PORTFOLIO / NOTE BAT BUOC*",
    ]
    lines += plus.portfolio_lines(results, watch_items, metrics)
    lines += ["", "*DU PHONG CO MANH CAN CHU Y*"]
    lines += [plus.projection_line(x, mode, metrics) for x in (focus_results or strong)[:12]] or ["Chua co co manh du nguong sau khi loc market regime."]
    lines += ["", "*CO MANH THI TRUONG*"]
    lines += [plus.with_intel(tf.format_stock_card(x), metrics.get(x.symbol)) for x in strong] or ["Market regime dang chan bot signal mua."]
    lines += ["", "*GAN BREAK / CO THE MUA TUNG PHAN*"]
    lines += [plus.with_intel(tf.format_stock_card(x, action="CANH BREAK / MUA TUNG PHAN"), metrics.get(x.symbol)) for x in break_watch] or ["Khong co ma dat nguong sau khi loc regime."]
    if suppressed:
        lines += ["", "*BI MARKET REGIME LOC BOT*"]
        lines += suppressed
    lines += ["", "*NGANH LEAD / RISK*"]
    lines += [tf.format_sector_line(x) for x in sectors] or ["Chua du du lieu nganh."]
    if rotation_alerts:
        lines += ["", "*SECTOR ROTATION*"]
        lines += rotation_alerts[:8]
    if mode == "eod" or plus.sess.base_mode(mode) == "afternoon":
        lines += ["", "*FAILED BREAK / CAN NE*"]
        lines += [plus.with_intel(tf.format_stock_card(x, action="CAN NE / GIAM RUI RO"), metrics.get(x.symbol)) for x in failed] or ["Khong co failed-break dang chu y."]
    if mode == "eod":
        lines += ["", intel.build_performance_report()]
    return "\n".join(lines)


def save_session_outputs(
    mode: str,
    results: dict[str, scan.ScanResult],
    history_store: dict[str, Any],
    peak_store: dict[str, Any],
    focus_symbols: list[str],
    watch_items: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    failed_breaks = plus.save_session_outputs(mode, results, history_store, peak_store, focus_symbols, watch_items)
    metrics = plus._STATE.get("metrics", {})
    ordered = sorted(results.values(), key=lambda x: (regime_gate.adv_score(x, metrics.get(x.symbol)), x.win_score, x.flow_score), reverse=True)
    latest_path = plus.sess.DATA_DIR / "session_alerts_latest.json"
    latest = scan.json_load(latest_path, {})
    latest["regime_gate"] = {
        "applied": True,
        "allowed_top": [x.symbol for x in regime_gate.filter_results([r for r in ordered if r.symbol != "VNINDEX"], metrics, min_score=72)[:20]],
        "suppressed": regime_gate.suppressed_lines([r for r in ordered if r.symbol != "VNINDEX"], metrics, min_score=72, limit=20),
    }
    latest["top"] = [asdict(x) for x in ordered[:20]]
    scan.json_save(latest_path, latest, pretty=False)
    return failed_breaks


intel.update_signal_tracker = update_signal_tracker_gated
plus.sess.wait_until = wait_until_with_data_jitter
plus.sess.build_session_report = build_session_report
plus.sess.save_session_outputs = save_session_outputs
plus.build_session_report = build_session_report


if __name__ == "__main__":
    asyncio.run(plus.main())
