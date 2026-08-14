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
import market_probe
import near_high_filter
import run_journal
import scan
import scan_safe
import session_scan as sess
import source_router
import state_manager
import state_transition
import telegram_format as tf
import filter_feed

logger = logging.getLogger("thieucutoo.session_plus")
_STATE: dict[str, Any] = {"started_at": time.time(), "results": {}, "metrics": {}, "regime": {}, "rotation": []}
_old_save_session_outputs = sess.save_session_outputs


scan_safe.fetch_ohlcv_safe = intel.fetch_ohlcv_safe
scan.fetch_ohlcv = intel.fetch_ohlcv_safe


def adv_score(result: scan.ScanResult, metrics: dict[str, dict[str, Any]]) -> int:
    return int(metrics.get(result.symbol, {}).get("advanced_score", result.win_score))


def unique_results(
    rows: list[scan.ScanResult], seen: set[str], limit: int
) -> list[scan.ScanResult]:
    selected: list[scan.ScanResult] = []
    for row in rows:
        if row.symbol in seen:
            continue
        selected.append(row)
        seen.add(row.symbol)
        if len(selected) >= limit:
            break
    return selected


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
        structure = metrics.get(symbol, {}).get("market_structure", {})
        break_state = structure.get("breakout", {}).get("state")
        market_state = structure.get("overall_state")
        if result.failed_break or break_state == "FAILED_BREAK_CONFIRMED" or market_state == "DISTRIBUTION" or result.win_score < sell:
            action = "BAT LOI / GIAM RUI RO"
        elif break_state == "FAILED_BREAK_WATCH":
            action = "BREAK XIT / CHO XAC NHAN"
        elif break_state == "REACCUMULATION":
            action = "NGHI TAI TICH LUY / CHO RECLAIM"
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


def build_session_report(
    mode: str,
    results: dict[str, scan.ScanResult],
    focus_symbols: list[str],
    watch_items: dict[str, dict[str, Any]],
    activity_probe: Any | None = None,
    market_day: Any | None = None,
    **_: Any,
) -> str:
    near_high_filter.annotate_results(results)
    window = sess.SESSION_WINDOWS[mode]
    metrics = _STATE.get("metrics", {})
    regime = _STATE.get("regime", {})
    rotation_alerts = _STATE.get("rotation", [])
    transitions = _STATE.get("transitions", [])
    ordered = sorted(results.values(), key=lambda x: (adv_score(x, metrics), x.win_score, x.flow_score), reverse=True)
    market = results.get("VNINDEX")
    stocks = [x for x in ordered if x.symbol != "VNINDEX"]
    focus_set = set(focus_symbols)
    focus_candidates = [x for x in stocks if x.symbol in focus_set and not x.failed_break]
    strong_candidates = [x for x in stocks if adv_score(x, metrics) >= 72 and not x.failed_break]
    break_candidates = [x for x in stocks if adv_score(x, metrics) >= 62 and x.near_break and not x.failed_break]
    failed_candidates = [x for x in stocks if x.failed_break]
    structure_watch_states = {
        "FAILED_BREAK_CONFIRMED", "FAILED_BREAK_WATCH", "REACCUMULATION",
        "HEALTHY_RETEST", "RECLAIMED_BREAK", "BREAKOUT_UNCONFIRMED",
    }
    structure_candidates = [
        x for x in stocks
        if metrics.get(x.symbol, {}).get("market_structure", {}).get("breakout", {}).get("state") in structure_watch_states
    ]
    seen_symbols = set(watch_items)
    show_failed = mode == "eod" or sess.base_mode(mode) == "afternoon"
    failed = unique_results(failed_candidates, seen_symbols, 10) if show_failed else []
    structure_watch = unique_results(structure_candidates, seen_symbols, 12)
    focus_results = unique_results(focus_candidates, seen_symbols, 12)
    strong = unique_results(strong_candidates, seen_symbols, 10)
    break_watch = unique_results(break_candidates, seen_symbols, 14)
    sectors = scan.summarize_sector(stocks)[:8]
    now = datetime.now(sess.VN_TZ).strftime("%d/%m %H:%M")

    lines = [
        f"*THIEUCUBU {window['title']}* `{now}`",
        f"{window['description']} Score v2 tối đa 97; báo lại đầy đủ mỗi phiên, không chỉ mã mới. Không phải cam kết lợi nhuận.",
        market_status(market, regime),
        intel.format_regime(regime),
        "*BẢN ĐỒ TRẠNG THÁI 1D / 1W / 1M*",
        *intel.structure_map_lines(stocks, metrics),
    ]
    if transitions:
        lines += ["", "*CHUYỂN PHA / ĐIỂM MỚI ĐÁNG CHÚ Ý*"]
        lines += [state_transition.format_transition(item) for item in transitions[:10]]
    if market_day and getattr(market_day, "closed", False):
        lines += [
            "",
            f"*CALENDAR*: {getattr(market_day, 'reason', 'Market closed')} `{getattr(market_day, 'date', '')}` | van quet data moi nhat theo policy `{getattr(market_day, 'policy', '')}`.",
        ]
    probe_note = market_probe.report_note(activity_probe)
    if probe_note:
        lines += ["", probe_note]
    lines += ["", "*PORTFOLIO / GHI CHÚ BẮT BUỘC*"]
    lines += portfolio_lines(results, watch_items, metrics)
    lines += ["", "*DỰ PHÓNG CỔ MẠNH CẦN CHÚ Ý*"]
    lines += [projection_line(x, mode, metrics) for x in focus_results] or ["Không có mã focus riêng ngoài các nhóm bên dưới."]
    lines += ["", "*CỔ MẠNH THỊ TRƯỜNG*"]
    lines += [with_intel(tf.format_stock_card(x), metrics.get(x.symbol)) for x in strong] or ["Khong co ma dat nguong."]
    lines += ["", "*GẦN BREAK / CÓ THỂ MUA TỪNG PHẦN*"]
    lines += [with_intel(tf.format_stock_card(x, action="CANH BREAK / MUA TUNG PHAN"), metrics.get(x.symbol)) for x in break_watch] or ["Khong co ma dat nguong."]
    lines += ["", "*BREAK XỊT / RETEST / TÁI TÍCH LŨY*"]
    lines += [intel.format_breakout_watch(x, metrics.get(x.symbol)) for x in structure_watch] or ["Không có cấu trúc break cần chú ý."]
    lines += ["", "*NGÀNH DẪN DẮT / RỦI RO*"]
    lines += [tf.format_sector_line(x) for x in sectors] or ["Chua du du lieu nganh."]
    if rotation_alerts:
        lines += ["", "*LUÂN CHUYỂN NGÀNH*"]
        lines += rotation_alerts[:8]
    if show_failed:
        lines += ["", "*FAILED-BREAK / CẦN TRÁNH*"]
        lines += [with_intel(tf.format_stock_card(x, action="CAN NE / GIAM RUI RO"), metrics.get(x.symbol)) for x in failed] or ["Khong co failed-break dang chu y."]
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
    activity_probe: Any | None = None,
    market_day: Any | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    near_high_filter.annotate_results(results)
    metrics, regime = intel.build_market_metrics(results, history_store)
    if mode == "test":
        rotation_alerts: list[str] = []
    else:
        _, rotation_alerts = intel.update_sector_rotation(
            [x for x in results.values() if x.symbol != "VNINDEX"]
        )
    transitions = [] if mode == "test" else state_transition.update_transitions(
        path=sess.DATA_DIR / "market_state_history.json",
        results=results,
        metrics_by_symbol=metrics,
    )
    _STATE.update(
        {
            "mode": mode,
            "results": results,
            "metrics": metrics,
            "regime": regime,
            "rotation": rotation_alerts,
            "transitions": transitions,
        }
    )
    failed_breaks = _old_save_session_outputs(
        mode,
        results,
        history_store,
        peak_store,
        focus_symbols,
        watch_items,
        activity_probe=activity_probe,
        market_day=market_day,
    )
    new_signals = intel.update_signal_tracker(results, metrics, mode, history_store=history_store)
    if mode == "eod":
        intel.auto_update_portfolio_thresholds(results)
    memory_summary: dict[str, Any] = {}
    memory_state: dict[str, Any] = {}
    if mode == "test":
        memory_summary = state_manager.memory_summary()
    else:
        try:
            memory_state = state_manager.StateManager().update_from_results(results, mode, focus_symbols, watch_items, metrics)
            memory_summary = state_manager.memory_summary(memory_state)
        except Exception as exc:
            logger.warning("Cannot update memory_state.json: %s", exc)
        try:
            source_router.update_routing(
                results,
                metrics=metrics,
                memory_state=memory_state,
                watch_items=watch_items,
                transitions=transitions,
                universe=[*scan.ALL_TICKERS, *focus_symbols, *watch_items.keys()],
                mode=mode,
            )
        except Exception as exc:
            logger.warning("Cannot update source_routing.json: %s", exc)
    ordered = sorted(results.values(), key=lambda x: (adv_score(x, metrics), x.win_score, x.flow_score), reverse=True)
    scan.json_save(sess.DATA_DIR / "results_latest.json", [asdict(x) for x in ordered], pretty=False)
    latest = {
        "updated_at": datetime.now(sess.VN_TZ).isoformat(timespec="seconds"),
        "mode": mode,
        "focus_symbols": focus_symbols,
        "portfolio_symbols": list(watch_items.keys()),
        "market": asdict(results["VNINDEX"]) if "VNINDEX" in results else None,
        "market_day": asdict(market_day) if market_day else None,
        "market_activity": asdict(activity_probe) if activity_probe else None,
        "market_regime": regime,
        "new_signals": new_signals,
        "state_transitions": transitions,
        "memory": memory_summary,
        "market_structure_map": {
            x.symbol: metrics.get(x.symbol, {}).get("market_structure", {})
            for x in ordered
            if x.symbol != "VNINDEX"
        },
        "advanced_top": {x.symbol: metrics.get(x.symbol, {}) for x in ordered[:20]},
        "top": [asdict(x) for x in ordered[:20]],
    }
    scan.json_save(sess.DATA_DIR / "session_alerts_latest.json", latest, pretty=False)
    feed = filter_feed.build_filter_feed(
            mode=mode,
            updated_at=latest["updated_at"],
            results=results,
            metrics=metrics,
            regime=regime,
            source_health=scan_safe.source_health_payload(),
            market_activity=asdict(activity_probe) if activity_probe else None,
    )
    scan.json_save(sess.DATA_DIR / "filter_feed_latest.json", feed, pretty=False)
    scan.json_save(sess.DATA_DIR / "stock_features_latest.json", feed, pretty=False)
    return failed_breaks


sess.build_session_report = build_session_report
sess.save_session_outputs = save_session_outputs


def actual_mode(default: str) -> str:
    mode = str(_STATE.get("mode") or "").strip()
    if mode:
        return mode
    latest = scan.json_load(sess.DATA_DIR / "session_alerts_latest.json", {})
    if isinstance(latest, dict) and latest.get("mode"):
        return str(latest.get("mode"))
    return default


def build_fallback_report(mode: str, error: BaseException) -> str:
    latest = scan.json_load(sess.DATA_DIR / "session_alerts_latest.json", {})
    memory = state_manager.memory_summary()
    source_health = scan_safe.source_health_payload()
    latest_at = latest.get("updated_at") if isinstance(latest, dict) else None
    latest_mode = latest.get("mode") if isinstance(latest, dict) else None
    source_bits = []
    for source, item in (source_health.get("sources") or {}).items():
        source_bits.append(
            f"{source} score {item.get('health_score')} ok {item.get('successes')}/{item.get('attempts')} "
            f"rl {item.get('rate_limit_failures')} err {item.get('transient_failures')}"
        )
    focus = ", ".join(memory.get("session_focus", [])[:12]) if isinstance(memory, dict) else ""
    lines = [
        f"*THIEUCUBU FALLBACK* `{mode}`",
        "Scanner gap loi giua phien, da ghi journal/source health de run sau tu hoi phuc.",
        f"Loi: `{str(error)[:260]}`",
        f"Latest report: {latest_mode or 'n/a'} | {latest_at or 'n/a'}",
        f"Memory: strong {memory.get('strong_count', 0)} | watch {memory.get('watchlist_count', 0)}",
    ]
    if focus:
        lines.append("Focus gan nhat: " + focus)
    if source_bits:
        lines.append("Sources: " + " | ".join(source_bits))
    lines.append("Watchdog se tu dispatch lai neu chua co report moi dung phien.")
    return "\n".join(lines)


async def main() -> None:
    _STATE["started_at"] = time.time()
    mode_hint = os.getenv("SCAN_MODE", "auto")
    run_id = run_journal.start_run(mode_hint, os.getenv("SCAN_EVENT_NAME", ""))
    summary_sent = False
    try:
        intel.warn_uncovered_groups()
        await sess.main()
        results = _STATE.get("results", {})
        failed_symbols = sorted(getattr(sess, "SCAN_FAILED_SYMBOLS", set()))
        summary = intel.build_scan_completion_summary(len(results), failed_symbols, time.time() - float(_STATE.get("started_at", time.time())))
        try:
            summary_sent = bool(await scan.send_chunks("*THIEUCUBU SUMMARY*", summary)) and not scan.DRY_RUN
        except Exception as exc:
            logger.warning("Cannot send completion summary, main report may already be sent: %s", exc)
        scan_safe.save_source_health()
        run_journal.finish_run(
            run_id,
            actual_mode(mode_hint),
            "success",
            success_count=len(results),
            failed_symbols=failed_symbols,
            elapsed_sec=time.time() - float(_STATE.get("started_at", time.time())),
            telegram_sent=summary_sent,
        )
    except Exception as exc:
        logger.exception("Fatal enhanced session scan error")
        fallback_sent = False
        try:
            fallback_sent = bool(
                await scan.send_chunks("*THIEUCUBU FALLBACK*", build_fallback_report(mode_hint, exc))
            ) and not scan.DRY_RUN
        except Exception as fallback_exc:
            logger.warning("Cannot send fallback report: %s", fallback_exc)
            try:
                await scan.send_telegram(f"*THIEUCUBU ALERT* `{mode_hint}` FAILED\n`{str(exc)[:300]}`")
                fallback_sent = not scan.DRY_RUN
            except Exception:
                logger.exception("Cannot send fatal Telegram alert")
        scan_safe.save_source_health()
        run_journal.fail_run(
            run_id,
            actual_mode(mode_hint),
            exc,
            elapsed_sec=time.time() - float(_STATE.get("started_at", time.time())),
            fallback_sent=fallback_sent,
        )
        raise


if __name__ == "__main__":
    asyncio.run(main())
