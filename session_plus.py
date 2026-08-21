#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any

import filter_feed
import market_breadth
import market_intel as intel
import market_probe
import market_strategy
import near_high_filter
import report_streams
import run_journal
import scan
import scan_safe
import sector_rotation
import session_scan as sess
import source_router
import state_manager
import state_transition
import technical_features
import telegram_format as tf

logger = logging.getLogger("thieucutoo.session_plus")
_STATE: dict[str, Any] = {
    "started_at": time.time(),
    "results": {},
    "metrics": {},
    "regime": {},
    "breadth": {},
    "systemic": {},
    "sector_rotation": {},
    "rotation": [],
}
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


def opportunity_action(result: scan.ScanResult, metrics: dict[str, dict[str, Any]]) -> str:
    item = metrics.get(result.symbol, {})
    gate = item.get("gate", {})
    if gate and not gate.get("allowed", True):
        return f"CÓ CƠ HỘI NHƯNG CHƯA MUA / {tf.clean_text(gate.get('reason'))}"
    structure = item.get("market_structure", {})
    state = str(structure.get("overall_state") or result.market_state)
    if state == "OPPORTUNITY" and result.near_break:
        return "CƠ HỘI / CANH MUA TỪNG PHẦN"
    if state == "OPPORTUNITY":
        return "CƠ HỘI / CHỜ ĐIỂM MUA"
    return "TÍCH LŨY / ƯU TIÊN THEO DÕI"


def early_accumulation_card(result: scan.ScanResult, metrics: dict[str, dict[str, Any]]) -> str:
    early = metrics.get(result.symbol, {}).get("early_accumulation", {})
    stage = str(early.get("stage") or "E1")
    pre_label = str(early.get("pre_label") or "NONE")
    pre_text = f" | PRE: {pre_label}" if pre_label != "NONE" else ""
    score = int(early.get("score") or 0)
    confidence = int(early.get("confidence") or 0)
    signals = "; ".join(tf.clean_text(item) for item in early.get("signals", [])[:4]) or "đang thu thập bằng chứng"
    missing = "; ".join(tf.clean_text(item) for item in early.get("missing", [])[:3]) or "chờ duy trì cấu trúc"
    return "\n".join(
        [
            f"`{result.symbol}` *{stage} · {score}/97*  {tf.clean_text(early.get('label')).upper()}{pre_text}",
            f"Giá {tf.format_price(result.close)} | DD {result.discount_pct:.1f}% | Vol5/20 {float(early.get('vol_5_20') or 0):.2f}x | RSI {result.rsi:.0f} | Tin cậy {confidence}%",
            f"Có: {signals}",
            f"Còn thiếu: {missing} | Hành động: {tf.clean_text(early.get('action')).upper()}",
            f"Kích hoạt {tf.format_price(early.get('trigger_price'))} | Vô hiệu {tf.format_price(early.get('invalidation_price'))}",
        ]
    )


def technical_watch_line(result: scan.ScanResult, metrics: dict[str, dict[str, Any]]) -> str:
    technical = metrics.get(result.symbol, {}).get("technical_watch", {})
    score = int(technical.get("score") or 0)
    confidence = int(technical.get("confidence") or 0)
    stage = tf.clean_text(technical.get("stage")).upper()
    pre_label = str(technical.get("pre_label") or "NONE")
    risk_label = str(technical.get("risk_label") or "NONE")
    label = pre_label if pre_label != "NONE" else risk_label
    label_text = f" | PRE: {label}" if label != "NONE" else ""
    bottoms = int(technical.get("bottom_count") or 0)
    pattern_text = (
        f"{int(technical.get('top_count') or 0)} đỉnh"
        if technical.get("risk_dominant")
        else f"{bottoms} đáy"
    )
    signals = "; ".join(tf.clean_text(item) for item in technical.get("signals", [])[:4]) or "động lượng đáy đang hình thành"
    return (
        f"`{result.symbol}` *T · {score}/97* {stage}{label_text} | Giá {tf.format_price(result.close)} | "
        f"RSI {float(technical.get('rsi') or result.rsi):.0f} | "
        f"MACD Hist {float(technical.get('macd_hist_pct') or 0):+.3f}% | "
        f"SMI {float(technical.get('smi') or 0):+.0f} | {pattern_text} | Tin cậy {confidence}%\n"
        f"Tín hiệu: {signals}\n"
        f"Kích hoạt {tf.format_price(technical.get('trigger_price'))} | "
        f"Vô hiệu {tf.format_price(technical.get('invalidation_price'))} | "
        f"{tf.clean_text(technical.get('pre_action')).upper()} — chưa phải lệnh mua"
    )


def five_stream_summary(
    streams: dict[str, list[scan.ScanResult]],
    metrics: dict[str, dict[str, Any]],
    watch_items: dict[str, dict[str, Any]],
) -> list[str]:
    labels = {
        report_streams.PORTFOLIO: "1. DANH MỤC BẮT BUỘC",
        report_streams.OPPORTUNITY: "2. CƠ HỘI / TÍCH LŨY",
        report_streams.EARLY: "3. GOM SỚM E1-E3",
        report_streams.TECHNICAL: "4. RSI / MACD / SMI",
        report_streams.STRUCTURE: "5. BREAK / RETEST / TÁI TL",
    }
    lines = ["*TÓM TẮT 5 LUỒNG — MÃ TRƯỚC, NỘI DUNG SAU*"]
    for stream in report_streams.DISPLAY_ORDER:
        extras = list(watch_items) if stream == report_streams.PORTFOLIO else None
        summary = report_streams.symbol_summary(stream, streams[stream], metrics, extra_symbols=extras)
        lines.append(f"{labels[stream]}: {summary}")
    return lines


def pulse_day_summary(limit: int = 20) -> str:
    events = sess.intraday_pulse_day_events(limit=limit)
    if not events:
        return "Không có mã Pulse vượt ngưỡng trong ngày."
    labels: list[str] = []
    for item in events:
        direction = str(item.get("direction") or "NEUTRAL").upper()
        arrow = "↑" if direction == "UP" else "↓" if direction == "DOWN" else "•"
        labels.append(f"`{item['symbol']}` {arrow}{int(item.get('score') or 0)}")
    return ", ".join(labels)


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
    breadth = _STATE.get("breadth", {})
    systemic = _STATE.get("systemic", {})
    sector_states = _STATE.get("sector_rotation", {})
    rotation_alerts = _STATE.get("rotation", [])
    transitions = _STATE.get("transitions", [])
    market = results.get("VNINDEX")
    stocks = [x for x in results.values() if x.symbol != "VNINDEX"]
    streams = report_streams.classify_streams(results, metrics, watch_items)
    sectors = scan.summarize_sector(stocks)[:8]
    now = datetime.now(sess.VN_TZ).strftime("%d/%m %H:%M")

    lines = [
        f"*THIEUCUBU {window['title']}* `{now}`",
        f"{window['description']} Score v2 tối đa 97; tự xếp 5 luồng, không phải cam kết lợi nhuận.",
        market_status(market, regime),
        intel.format_regime(regime),
        market_breadth.format_breadth(breadth),
        market_breadth.format_systemic(systemic),
        "",
        *five_stream_summary(streams, metrics, watch_items),
    ]
    if mode == "eod":
        lines += [
            "",
            "*RADAR 30P TRONG NGÀY*: " + pulse_day_summary(),
            *market_strategy.format_lines(market_strategy.horizon_strategy(market, regime)),
        ]
    lines += [
        "",
        "*BẢN ĐỒ TRẠNG THÁI 1D / 1W / 1M*",
        *intel.structure_map_lines(stocks, metrics),
    ]
    if market_day and getattr(market_day, "closed", False):
        lines += [
            "",
            f"*CALENDAR*: {getattr(market_day, 'reason', 'Market closed')} `{getattr(market_day, 'date', '')}` | van quet data moi nhat theo policy `{getattr(market_day, 'policy', '')}`.",
        ]
    probe_note = market_probe.report_note(activity_probe)
    if probe_note:
        lines += ["", probe_note]
    lines += [
        "",
        "*LUỒNG 1 — PORTFOLIO / GHI CHÚ BẮT BUỘC*",
        "*MÃ:* " + report_streams.symbol_summary(
            report_streams.PORTFOLIO,
            streams[report_streams.PORTFOLIO],
            metrics,
            extra_symbols=list(watch_items),
        ),
    ]
    lines += portfolio_lines(results, watch_items, metrics)
    opportunity = streams[report_streams.OPPORTUNITY]
    lines += [
        "",
        "*LUỒNG 2 — CƠ HỘI / TÍCH LŨY*",
        "*MÃ:* " + report_streams.symbol_summary(report_streams.OPPORTUNITY, opportunity, metrics),
    ]
    lines += [
        with_intel(tf.format_stock_card(x, action=opportunity_action(x, metrics)), metrics.get(x.symbol))
        for x in opportunity[:15]
    ] or ["Không có mã đạt chuẩn cơ hội/tích lũy."]
    early = streams[report_streams.EARLY]
    lines += [
        "",
        "*LUỒNG 3 — EARLY ACCUMULATION / GOM SỚM*",
        "*MÃ:* " + report_streams.symbol_summary(report_streams.EARLY, early, metrics),
    ]
    lines += [early_accumulation_card(x, metrics) for x in early[:15]] or ["Không có mã E1/E2/E3 đạt điều kiện."]
    technical = streams[report_streams.TECHNICAL]
    lines += [
        "",
        "*LUỒNG 4 — KỸ THUẬT ĐÁY RSI / MACD / SMI*",
        "*MÃ:* " + report_streams.symbol_summary(report_streams.TECHNICAL, technical, metrics),
    ]
    lines += [technical_watch_line(x, metrics) for x in technical[:15]] or ["Không có tín hiệu kỹ thuật đáy đáng chú ý."]
    structure_watch = streams[report_streams.STRUCTURE]
    lines += [
        "",
        "*LUỒNG 5 — BREAK XỊT / RETEST / TÁI TÍCH LŨY*",
        "*MÃ:* " + report_streams.symbol_summary(report_streams.STRUCTURE, structure_watch, metrics),
    ]
    lines += [intel.format_breakout_watch(x, metrics.get(x.symbol)) for x in structure_watch[:20]] or ["Không có cấu trúc break cần chú ý."]
    if transitions:
        lines += ["", "*CHUYỂN PHA / ĐIỂM MỚI ĐÁNG CHÚ Ý*"]
        lines += [state_transition.format_transition(item) for item in transitions[:10]]
    lines += ["", "*HEATMAP LUÂN CHUYỂN NGÀNH 1W / 1M / 3M*"]
    lines += sector_rotation.format_heatmap(sector_states)
    lines += ["", "*NGÀNH DẪN DẮT / RỦI RO — ĐIỂM SCANNER*"]
    lines += [tf.format_sector_line(x) for x in sectors] or ["Chua du du lieu nganh."]
    if rotation_alerts:
        lines += ["", "*LUÂN CHUYỂN NGÀNH*"]
        lines += rotation_alerts[:8]
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
    stocks = [item for item in results.values() if item.symbol != "VNINDEX"]
    expected_symbols = {
        *scan.ALL_TICKERS,
        *focus_symbols,
        *watch_items.keys(),
        *(item.symbol for item in stocks),
    }
    breadth = market_breadth.calculate_snapshot(
        history_store,
        expected_universe_size=len(expected_symbols),
    )
    live_systemic = market_breadth.derive_systemic_regime(breadth, regime)
    if mode == "eod":
        _, systemic = market_breadth.persist_daily(breadth, live_systemic)
    elif mode == "test":
        systemic = live_systemic
    else:
        persisted_systemic = market_breadth.load_systemic_state()
        if int(persisted_systemic.get("confidence") or 0) > 0:
            systemic = dict(persisted_systemic)
            systemic["live_raw_state"] = live_systemic.get("raw_state")
            systemic["live_risk_score"] = live_systemic.get("risk_score")
        else:
            systemic = live_systemic

    if mode == "test":
        sector_states, rotation_alerts = sector_rotation.update_sector_rotation(
            stocks,
            history_store=history_store,
            index_frame=intel.frame_from_history(history_store, "VNINDEX"),
            persist=False,
            path=sess.DATA_DIR / ".sector_rotation_test.json",
        )
    else:
        sector_states, rotation_alerts = intel.update_sector_rotation(
            stocks,
            history_store=history_store,
            index_frame=intel.frame_from_history(history_store, "VNINDEX"),
            persist=mode == "eod",
        )
    for symbol, item in metrics.items():
        result = results.get(symbol)
        item["market_breadth_state"] = breadth.get("state")
        item["market_breadth_score"] = breadth.get("score")
        item["systemic_regime"] = systemic
        item["sector_rotation"] = (
            sector_states.get(str(result.sector or "Other"), {}) if result else {}
        )
        early, technical = technical_features.apply_market_context(
            item.get("early_accumulation"),
            item.get("technical_watch"),
            systemic,
            item.get("sector_rotation"),
        )
        item["early_accumulation"] = early
        item["technical_watch"] = technical
    streams = report_streams.classify_streams(results, metrics, watch_items)
    primary_streams = report_streams.primary_stream_map(streams)
    for symbol, item in metrics.items():
        item["primary_stream"] = primary_streams.get(symbol, "unclassified")
    stream_payload = report_streams.serialize_streams(
        streams,
        metrics,
        portfolio_symbols=list(watch_items),
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
            "breadth": breadth,
            "systemic": systemic,
            "sector_rotation": sector_states,
            "rotation": rotation_alerts,
            "transitions": transitions,
            "streams": streams,
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
        "market_breadth": breadth,
        "systemic_regime": systemic,
        "sector_rotation": sector_states,
        "market_horizon_strategy": market_strategy.horizon_strategy(results.get("VNINDEX"), regime),
        "intraday_pulse_day": sess.intraday_pulse_day_events(limit=30),
        "five_streams": stream_payload,
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
    feed["five_streams"] = stream_payload
    feed["market_breadth"] = breadth
    feed["systemic_regime"] = systemic
    feed["sector_rotation"] = sector_states
    feed["market_horizon_strategy"] = latest["market_horizon_strategy"]
    feed["intraday_pulse_day"] = latest["intraday_pulse_day"]
    for fact in feed.get("facts", []):
        symbol = str(fact.get("symbol") or "")
        fact.setdefault("classification", {})["primary_stream"] = primary_streams.get(
            symbol, "unclassified"
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
