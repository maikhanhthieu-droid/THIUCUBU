#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any

import market_intel as intel
import scan
import scan_safe
import session_scan as sess
import state_manager
import telegram_format as tf

logger = logging.getLogger("thieucutoo.session_plus")
_STATE: dict[str, Any] = {"started_at": time.time(), "results": {}, "metrics": {}, "regime": {}, "rotation": []}
_old_save_session_outputs = sess.save_session_outputs


scan_safe.fetch_ohlcv_safe = intel.fetch_ohlcv_safe
scan.fetch_ohlcv = intel.fetch_ohlcv_safe


def adv_score(result: scan.ScanResult, metrics: dict[str, dict[str, Any]]) -> int:
    return int(metrics.get(result.symbol, {}).get("advanced_score", result.win_score))


def with_intel(card: str, metrics: dict[str, Any] | None) -> str:
    extra = intel.format_advanced_lines(metrics)
    if not extra:
        return card
    return card.rstrip() + "\n" + "\n".join(extra) + "\n"


def market_status(market: scan.ScanResult | None, regime: dict[str, Any] | None = None) -> str:
    if market is None:
        return tf.format_market_card(None, "")
    name = str((regime or {}).get("regime", ""))
    if name == "BEAR" or market.failed_break or market.win_score < 45:
        state = "RISK OFF"
    elif name == "BULL":
        state = "RISK ON / BULL"
    elif market.win_score >= 68 and market.obv_up and market.mfi >= 50:
        state = "RISK ON"
    elif market.win_score >= 55:
        state = "NEUTRAL / CHO XAC NHAN"
    else:
        state = "YEU / THAN TRONG"
    return tf.format_market_card(market, state)


def portfolio_lines(results: dict[str, scan.ScanResult], watch_items: dict[str, dict[str, Any]], metrics: dict[str, dict[str, Any]]) -> list[str]:
    if not watch_items:
        return ["Portfolio/note: chua co ma trong data/portfolio.json hoac data/notes.json."]
    lines: list[str] = []
    for symbol, item in watch_items.items():
        result = results.get(symbol)
        note = str(item.get("note", "")).strip()
        if result is None:
            lines.append(f"`{symbol}` NO_DATA | {note}")
            continue
        buy_more = int(item.get("buy_more_score", 78))
        sell = int(item.get("sell_score", 45))
        score = adv_score(result, metrics)
        if result.failed_break or result.win_score < sell:
            action = "BAT LOI / GIAM RUI RO"
        elif score >= buy_more and result.near_break and not result.failed_break:
            action = "TIN HIEU DEP / CANH MUA THEM"
        elif score >= buy_more:
            action = "TIN HIEU TOT / THEO DOI MUA THEM"
        elif score >= 62 and result.obv_up:
            action = "GIU / THEO DOI TICH CUC"
        else:
            action = "GIU / THEO DOI"
        lines.append(with_intel(tf.format_stock_card(result, action=action, note=note), metrics.get(symbol)))
    return lines


def projection_line(result: scan.ScanResult, mode: str, metrics: dict[str, dict[str, Any]]) -> str:
    score = adv_score(result, metrics)
    if result.failed_break:
        action = "NE FAILED BREAK"
    elif score >= 82 and result.near_break and result.obv_up:
        action = "CANH MUA NGAY NEU GIU NEN"
    elif score >= 74:
        action = "CANH MUA TUNG PHAN"
    elif result.near_break and score >= 62:
        action = "CANH BREAK"
    else:
        action = "WATCH"
    timing = "sau 14h uu tien du lieu moi" if sess.base_mode(mode) == "afternoon" else "cho xac nhan sau moc phien"
    return with_intel(tf.format_stock_card(result, action=action, timing=timing), metrics.get(result.symbol))


def build_session_report(mode: str, results: dict[str, scan.ScanResult], focus_symbols: list[str], watch_items: dict[str, dict[str, Any]]) -> str:
    window = sess.SESSION_WINDOWS[mode]
    metrics = _STATE.get("metrics", {})
    regime = _STATE.get("regime", {})
    rotation_alerts = _STATE.get("rotation", [])
    ordered = sorted(results.values(), key=lambda x: (adv_score(x, metrics), x.win_score, x.flow_score), reverse=True)
    market = results.get("VNINDEX")
    stocks = [x for x in ordered if x.symbol != "VNINDEX"]
    focus_set = set(focus_symbols)
    focus_results = [x for x in stocks if x.symbol in focus_set and not x.failed_break]
    strong = [x for x in stocks if adv_score(x, metrics) >= 72 and not x.failed_break][:10]
    break_watch = [x for x in stocks if adv_score(x, metrics) >= 62 and x.near_break and not x.failed_break][:14]
    failed = [x for x in stocks if x.failed_break][:10]
    sectors = scan.summarize_sector(stocks)[:8]
    now = datetime.now(sess.VN_TZ).strftime("%d/%m %H:%M")

    lines = [
        f"*THIEUCUTOO {window['title']}* `{now}`",
        f"{window['description']} Score 0-100, khong phai cam ket loi nhuan.",
        market_status(market, regime),
        intel.format_regime(regime),
        "",
        "*PORTFOLIO / NOTE BAT BUOC*",
    ]
    lines += portfolio_lines(results, watch_items, metrics)
    lines += ["", "*DU PHONG CO MANH CAN CHU Y*"]
    lines += [projection_line(x, mode, metrics) for x in (focus_results or strong)[:12]] or ["Chua co co manh du nguong."]
    lines += ["", "*CO MANH THI TRUONG*"]
    lines += [with_intel(tf.format_stock_card(x), metrics.get(x.symbol)) for x in strong] or ["Khong co ma dat nguong."]
    lines += ["", "*GAN BREAK / CO THE MUA TUNG PHAN*"]
    lines += [with_intel(tf.format_stock_card(x, action="CANH BREAK / MUA TUNG PHAN"), metrics.get(x.symbol)) for x in break_watch] or ["Khong co ma dat nguong."]
    lines += ["", "*NGANH LEAD / RISK*"]
    lines += [tf.format_sector_line(x) for x in sectors] or ["Chua du du lieu nganh."]
    if rotation_alerts:
        lines += ["", "*SECTOR ROTATION*"]
        lines += rotation_alerts[:8]
    if mode == "eod" or sess.base_mode(mode) == "afternoon":
        lines += ["", "*FAILED BREAK / CAN NE*"]
        lines += [with_intel(tf.format_stock_card(x, action="CAN NE / GIAM RUI RO"), metrics.get(x.symbol)) for x in failed] or ["Khong co failed-break dang chu y."]
    if mode == "eod":
        lines += ["", intel.build_performance_report()]
    return "\n".join(lines)


def save_session_outputs(mode: str, results: dict[str, scan.ScanResult], history_store: dict[str, Any], peak_store: dict[str, Any], focus_symbols: list[str], watch_items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    metrics, regime = intel.build_market_metrics(results, history_store)
    _, rotation_alerts = intel.update_sector_rotation([x for x in results.values() if x.symbol != "VNINDEX"])
    _STATE.update({"results": results, "metrics": metrics, "regime": regime, "rotation": rotation_alerts})
    failed_breaks = _old_save_session_outputs(mode, results, history_store, peak_store, focus_symbols, watch_items)
    new_signals = intel.update_signal_tracker(results, metrics, mode)
    if mode == "eod":
        intel.auto_update_portfolio_thresholds(results)
    memory_summary: dict[str, Any] = {}
    if mode == "test":
        memory_summary = state_manager.memory_summary()
    else:
        try:
            memory_state = state_manager.StateManager().update_from_results(results, mode, focus_symbols, watch_items, metrics)
            memory_summary = state_manager.memory_summary(memory_state)
        except Exception as exc:
            logger.warning("Cannot update memory_state.json: %s", exc)
    ordered = sorted(results.values(), key=lambda x: (adv_score(x, metrics), x.win_score, x.flow_score), reverse=True)
    scan.json_save(sess.DATA_DIR / "results_latest.json", [asdict(x) for x in ordered], pretty=False)
    latest = {
        "updated_at": datetime.now(sess.VN_TZ).isoformat(timespec="seconds"),
        "mode": mode,
        "focus_symbols": focus_symbols,
        "portfolio_symbols": list(watch_items.keys()),
        "market": asdict(results["VNINDEX"]) if "VNINDEX" in results else None,
        "market_regime": regime,
        "new_signals": new_signals,
        "memory": memory_summary,
        "advanced_top": {x.symbol: metrics.get(x.symbol, {}) for x in ordered[:20]},
        "top": [asdict(x) for x in ordered[:20]],
    }
    scan.json_save(sess.DATA_DIR / "session_alerts_latest.json", latest, pretty=False)
    return failed_breaks


sess.build_session_report = build_session_report
sess.save_session_outputs = save_session_outputs


async def main() -> None:
    _STATE["started_at"] = time.time()
    mode_hint = os.getenv("SCAN_MODE", "auto")
    try:
        intel.warn_uncovered_groups()
        await sess.main()
        results = _STATE.get("results", {})
        failed_symbols = sorted(getattr(sess, "SCAN_FAILED_SYMBOLS", set()))
        summary = intel.build_scan_completion_summary(len(results), failed_symbols, time.time() - float(_STATE.get("started_at", time.time())))
        await scan.send_chunks("*THIEUCUTOO SUMMARY*", summary)
    except Exception as exc:
        logger.exception("Fatal enhanced session scan error")
        await scan.send_telegram(f"*THIEUCUTOO ALERT* `{mode_hint}` FAILED\n`{str(exc)[:300]}`")
        raise


if __name__ == "__main__":
    asyncio.run(main())
