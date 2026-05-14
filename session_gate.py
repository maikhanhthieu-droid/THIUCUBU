#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, time as dt_time
from typing import Any

import market_intel as intel
import regime_gate
import scan
import session_plus as plus
import telegram_format as tf

_old_update_signal_tracker = intel.update_signal_tracker

plus.sess.SESSION_WINDOWS["morning"].update(
    {
        "title": "MORNING 12H30",
        "broad_after": dt_time(10, 30),
        "focus_after": dt_time(12, 15),
        "report_after": dt_time(12, 30),
        "description": "Lay data sau 10h30, quet lai note/co manh tu 12h15 de tra report 12h30.",
    }
)
plus.sess.SESSION_WINDOWS["afternoon"].update(
    {
        "title": "AFTERNOON 14H15",
        "broad_after": dt_time(13, 45),
        "focus_after": dt_time(14, 0),
        "report_after": dt_time(14, 15),
        "description": "Lay data sau 13h45, uu tien note/co manh sau 14h, tra report 14h15.",
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
    if mode in {"eod", "afternoon"}:
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
plus.sess.build_session_report = build_session_report
plus.sess.save_session_outputs = save_session_outputs
plus.build_session_report = build_session_report


if __name__ == "__main__":
    asyncio.run(plus.main())
