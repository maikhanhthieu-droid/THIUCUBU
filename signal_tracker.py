"""Auditable signal episodes measured on trading sessions, not calendar days."""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "thieucubu.signal_tracker.v2"
HORIZONS = (1, 3, 5, 10, 20)
VN_TZ = timezone(timedelta(hours=7))


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _history_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    by_date: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        day = _date_text(raw.get("time") or raw.get("date"))
        close = _safe_float(raw.get("close"))
        if not day or close is None or close <= 0:
            continue
        open_price = _safe_float(raw.get("open"))
        high = _safe_float(raw.get("high"))
        low = _safe_float(raw.get("low"))
        by_date[day] = {
            "date": day,
            "open": open_price if open_price is not None and open_price > 0 else close,
            "close": close,
            "high": high if high is not None else close,
            "low": low if low is not None else close,
        }
    return [by_date[day] for day in sorted(by_date)]


def _empty_tracker() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        "episodes": [],
    }


def _legacy_summary(records: list[Any]) -> dict[str, Any]:
    valid = [item for item in records if isinstance(item, Mapping)]
    keys = {
        (str(item.get("symbol", "")).upper(), str(item.get("date_signal", "")))
        for item in valid
        if item.get("symbol") and item.get("date_signal")
    }
    return {
        "schema_version": "legacy.v1",
        "record_count": len(valid),
        "unique_symbol_dates": len(keys),
        "duplicate_records": max(0, len(valid) - len(keys)),
        "excluded_from_metrics": True,
        "reason": "calendar-day snapshots and mixed historical price units are not comparable",
    }


def load_tracker(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_tracker()
    if isinstance(raw, list):
        tracker = _empty_tracker()
        tracker["legacy_summary"] = _legacy_summary(raw)
        return tracker
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return _empty_tracker()
    tracker = dict(raw)
    tracker["episodes"] = [item for item in raw.get("episodes", []) if isinstance(item, dict)]
    return tracker


def _save_tracker(path: Path, tracker: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(tracker, ensure_ascii=False, separators=(",", ":"))
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _return_pct(price: float, entry: float) -> float:
    return round((price / entry - 1.0) * 100.0, 2)


def _benchmark_return(
    benchmark: list[dict[str, Any]], signal_date: str, target_date: str
) -> float | None:
    prices = {row["date"]: row["close"] for row in benchmark}
    start = prices.get(signal_date)
    end = prices.get(target_date)
    if start is None or end is None or start <= 0:
        return None
    return _return_pct(end, start)


def _initial_risk_pct(entry: float, stop: float | None) -> float | None:
    if stop is None or stop <= 0 or entry <= 0 or stop >= entry:
        return None
    risk_pct = (entry - stop) / entry * 100.0
    return round(risk_pct, 2) if risk_pct >= 0.25 else None


def _update_path_outcome(episode: dict[str, Any], forward: list[dict[str, Any]]) -> None:
    if episode.get("path_outcome") in {"STOPPED", "TARGET_HIT"}:
        return
    entry = _safe_float(episode.get("price_at_signal"))
    stop = _safe_float(episode.get("stop_loss"))
    target = _safe_float(episode.get("take_profit"))
    risk_pct = _initial_risk_pct(entry, stop) if entry else None
    for session_number, row in enumerate(forward[:20], start=1):
        hit_stop = stop is not None and row["low"] <= stop
        hit_target = target is not None and row["high"] >= target
        if hit_stop and hit_target:
            episode.update(
                {
                    "path_outcome": "STOPPED",
                    "outcome_date": row["date"],
                    "outcome_session": session_number,
                    "same_bar_conflict": "STOP_FIRST_CONSERVATIVE",
                    "realized_r": -1.0 if risk_pct is not None else None,
                }
            )
            return
        if hit_stop:
            episode.update(
                {
                    "path_outcome": "STOPPED",
                    "outcome_date": row["date"],
                    "outcome_session": session_number,
                    "realized_r": -1.0 if risk_pct is not None else None,
                }
            )
            return
        if hit_target:
            realized_r = None
            if entry and target and risk_pct:
                realized_r = round(((target / entry - 1.0) * 100.0) / risk_pct, 2)
            episode.update(
                {
                    "path_outcome": "TARGET_HIT",
                    "outcome_date": row["date"],
                    "outcome_session": session_number,
                    "realized_r": realized_r,
                }
            )
            return


def update_episode(
    episode: dict[str, Any],
    rows: list[dict[str, Any]],
    benchmark: list[dict[str, Any]],
) -> None:
    signal_date = _date_text(episode.get("date_signal"))
    entry = _safe_float(episode.get("price_at_signal"))
    if not signal_date or entry is None or entry <= 0:
        return
    positions = {row["date"]: index for index, row in enumerate(rows)}
    start_index = positions.get(signal_date)
    if start_index is None:
        return
    forward = rows[start_index + 1 :]
    episode["sessions_observed"] = min(len(forward), 20)
    stop = _safe_float(episode.get("stop_loss"))
    risk_pct = _initial_risk_pct(entry, stop)
    if risk_pct is not None:
        episode["initial_risk_pct"] = risk_pct

    next_open: float | None = None
    if forward:
        raw_next_open = _safe_float(forward[0].get("open"))
        if raw_next_open is not None and raw_next_open > 0:
            next_open = raw_next_open
            episode["entry_next_open"] = round(next_open, 4)
            episode["overnight_gap_pct"] = _return_pct(next_open, entry)

    _update_path_outcome(episode, forward)
    for horizon in HORIZONS:
        if len(forward) < horizon:
            continue
        key = f"t{horizon}"
        window = forward[:horizon]
        target_row = window[-1]
        result_return = _return_pct(target_row["close"], entry)
        benchmark_return = _benchmark_return(benchmark, signal_date, target_row["date"])
        episode[f"date_{key}"] = target_row["date"]
        episode[f"price_{key}"] = round(target_row["close"], 4)
        episode[f"return_{key}"] = result_return
        episode[f"mfe_{key}_pct"] = round((max(row["high"] for row in window) / entry - 1) * 100, 2)
        episode[f"mae_{key}_pct"] = round((min(row["low"] for row in window) / entry - 1) * 100, 2)
        if next_open is not None and next_open > 0:
            episode[f"return_open_{key}"] = _return_pct(target_row["close"], next_open)
        if risk_pct is not None and risk_pct > 0:
            episode[f"r_multiple_{key}"] = round(result_return / risk_pct, 2)
        if benchmark_return is not None:
            episode[f"benchmark_return_{key}"] = benchmark_return
            episode[f"excess_return_{key}"] = round(result_return - benchmark_return, 2)
    episode["episode_status"] = "COMPLETED_T20" if episode.get("return_t20") is not None else "ACTIVE"


def _eligible(result: Any, metrics: Mapping[str, Any], min_score: int) -> bool:
    if bool(getattr(result, "failed_break", False)):
        return False
    structure = metrics.get("market_structure") if isinstance(metrics.get("market_structure"), Mapping) else {}
    breakout = structure.get("breakout") if isinstance(structure.get("breakout"), Mapping) else {}
    if breakout.get("state") in {"FAILED_BREAK_CONFIRMED", "FAILED_BREAK_WATCH"}:
        return False
    score = int(metrics.get("advanced_score", getattr(result, "win_score", 0)) or 0)
    if score < min_score:
        return False
    return bool(
        getattr(result, "near_break", False)
        or int(getattr(result, "position_score", 0) or 0) >= 72
        or int(getattr(result, "win_score", 0) or 0) >= 78
    )


def _new_episode(
    symbol: str,
    result: Any,
    metrics: Mapping[str, Any],
    mode: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    latest = rows[-1]
    structure = metrics.get("market_structure") if isinstance(metrics.get("market_structure"), Mapping) else {}
    breakout = structure.get("breakout") if isinstance(structure.get("breakout"), Mapping) else {}
    trade = metrics.get("trade") if isinstance(metrics.get("trade"), Mapping) else {}
    systemic = metrics.get("systemic_regime") if isinstance(metrics.get("systemic_regime"), Mapping) else {}
    signal_date = latest["date"]
    score = int(metrics.get("advanced_score", getattr(result, "win_score", 0)) or 0)
    regime = metrics.get("regime") if isinstance(metrics.get("regime"), Mapping) else {}
    primary_stream = str(metrics.get("primary_stream") or getattr(result, "primary_stream", "unclassified") or "unclassified")
    return {
        "episode_id": f"{symbol}:{signal_date}",
        "tracker_version": SCHEMA_VERSION,
        "eligible_for_metrics": True,
        "symbol": symbol,
        "sector": str(getattr(result, "sector", "") or ""),
        "primary_stream": primary_stream,
        "date_signal": signal_date,
        "mode": mode,
        "episode_status": "ACTIVE",
        "path_outcome": "OPEN",
        "sessions_observed": 0,
        "score_at_signal": int(getattr(result, "win_score", 0) or 0),
        "advanced_score": score,
        "trade_score": int(getattr(result, "trade_score", 0) or 0),
        "position_score": int(getattr(result, "position_score", 0) or 0),
        "confidence": int(getattr(result, "confidence", 0) or 0),
        "price_at_signal": round(float(latest["close"]), 4),
        "price_unit": str(getattr(result, "price_unit", "unknown")),
        "setup": str(getattr(result, "setup", "")),
        "regime": str(regime.get("regime", "UNKNOWN")),
        "systemic_state": str(systemic.get("state") or "UNKNOWN"),
        "market_state": str(structure.get("overall_state") or getattr(result, "market_state", "NO_DATA")),
        "daily_phase": str(getattr(result, "daily_phase", "NO_DATA")),
        "weekly_phase": str(getattr(result, "weekly_phase", "NO_DATA")),
        "monthly_phase": str(getattr(result, "monthly_phase", "NO_DATA")),
        "breakout_state": str(breakout.get("state") or getattr(result, "breakout_state", "NO_DATA")),
        "breakout_level": _safe_float(breakout.get("breakout_level")),
        "stop_loss": _safe_float(trade.get("stop_loss")),
        "take_profit": _safe_float(trade.get("take_profit")),
        "risk_reward": _safe_float(trade.get("risk_reward")),
    }


def _bucket_stats(episodes: Iterable[Mapping[str, Any]], horizon_key: str = "t10") -> dict[str, Any]:
    ret_field = f"return_{horizon_key}"
    exc_field = f"excess_return_{horizon_key}"
    r_field = f"r_multiple_{horizon_key}"
    valid = [item for item in episodes if _safe_float(item.get(ret_field)) is not None]
    if not valid:
        return {"count": 0, "win_rate_pct": 0.0, "avg_return": 0.0, "median_return": 0.0, "avg_excess": None, "avg_r": None}
    returns = [float(item[ret_field]) for item in valid]
    excess = [float(item[exc_field]) for item in valid if _safe_float(item.get(exc_field)) is not None]
    r_mults = [float(item[r_field]) for item in valid if _safe_float(item.get(r_field)) is not None]
    wins = sum(value > 3.0 for value in returns)
    return {
        "count": len(valid),
        "win_rate_pct": round(wins / len(returns) * 100.0, 1),
        "avg_return": round(statistics.fmean(returns), 2),
        "median_return": round(statistics.median(returns), 2),
        "avg_excess": round(statistics.fmean(excess), 2) if excess else None,
        "avg_r": round(statistics.fmean(r_mults), 2) if r_mults else None,
    }


def summarize_tracker_analytics(tracker: Mapping[str, Any]) -> dict[str, Any]:
    """Build multi-dimensional performance, calibration, and path-outcome analytics."""
    raw_episodes = [
        item
        for item in tracker.get("episodes", [])
        if isinstance(item, Mapping) and item.get("eligible_for_metrics")
    ]
    completed_t10 = [item for item in raw_episodes if _safe_float(item.get("return_t10")) is not None]

    # 1. Score Calibration buckets (<78, 78-84, 85+)
    buckets: dict[str, list[Mapping[str, Any]]] = {"score_lt_78": [], "score_78_84": [], "score_85_plus": []}
    for item in completed_t10:
        score = int(_safe_float(item.get("advanced_score")) or _safe_float(item.get("score_at_signal")) or 0)
        if score >= 85:
            buckets["score_85_plus"].append(item)
        elif score >= 78:
            buckets["score_78_84"].append(item)
        else:
            buckets["score_lt_78"].append(item)
    calibration = {name: _bucket_stats(rows, "t10") for name, rows in buckets.items()}
    low_avg = calibration["score_lt_78"]["avg_return"] if calibration["score_lt_78"]["count"] > 0 else None
    high_avg = calibration["score_85_plus"]["avg_return"] if calibration["score_85_plus"]["count"] > 0 else None
    calibration_monotonic = (
        bool(high_avg >= low_avg) if (low_avg is not None and high_avg is not None) else None
    )

    # 2. Group by setup, primary_stream, regime
    def group_breakdown(field: str, default_key: str) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for item in completed_t10:
            key = str(item.get(field) or default_key).strip() or default_key
            grouped.setdefault(key, []).append(item)
        return {
            key: _bucket_stats(rows, "t10")
            for key, rows in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        }

    by_setup = group_breakdown("setup", "UNKNOWN")
    by_stream = group_breakdown("primary_stream", "unclassified")
    by_regime = group_breakdown("regime", "UNKNOWN")

    # 3. Path outcomes & realistic entry slippage
    target_hits = sum(1 for item in raw_episodes if item.get("path_outcome") == "TARGET_HIT")
    stopped = sum(1 for item in raw_episodes if item.get("path_outcome") == "STOPPED")
    open_outcomes = sum(1 for item in raw_episodes if item.get("path_outcome") not in {"TARGET_HIT", "STOPPED"})
    open_returns_t10 = [
        float(item["return_open_t10"])
        for item in completed_t10
        if _safe_float(item.get("return_open_t10")) is not None
    ]
    r_multiples_t10 = [
        float(item["r_multiple_t10"])
        for item in completed_t10
        if _safe_float(item.get("r_multiple_t10")) is not None
    ]
    return {
        "total_eligible_episodes": len(raw_episodes),
        "completed_t10_count": len(completed_t10),
        "horizons": {f"t{h}": _bucket_stats(raw_episodes, f"t{h}") for h in HORIZONS},
        "calibration": calibration,
        "calibration_monotonic": calibration_monotonic,
        "by_setup": by_setup,
        "by_stream": by_stream,
        "by_regime": by_regime,
        "path_outcomes": {
            "target_hit": target_hits,
            "stopped": stopped,
            "open": open_outcomes,
            "avg_r_multiple_t10": round(statistics.fmean(r_multiples_t10), 2) if r_multiples_t10 else None,
            "avg_return_open_t10": round(statistics.fmean(open_returns_t10), 2) if open_returns_t10 else None,
        },
    }


def update_tracker(
    *,
    path: Path,
    results: Mapping[str, Any],
    metrics_by_symbol: Mapping[str, Mapping[str, Any]],
    history_store: Mapping[str, Any],
    mode: str,
    min_score: int = 72,
    persist: bool = True,
) -> list[dict[str, Any]]:
    tracker = load_tracker(path)
    episodes = tracker["episodes"]
    normalized_history = {symbol: _history_rows(rows) for symbol, rows in history_store.items()}
    benchmark = normalized_history.get("VNINDEX", [])
    for episode in episodes:
        symbol = str(episode.get("symbol", "")).upper()
        update_episode(episode, normalized_history.get(symbol, []), benchmark)

    active_symbols = {
        str(item.get("symbol", "")).upper()
        for item in episodes
        if item.get("episode_status") != "COMPLETED_T20"
    }
    existing_ids = {str(item.get("episode_id", "")) for item in episodes}
    added: list[dict[str, Any]] = []
    if mode != "test":
        for symbol, result in results.items():
            normalized_symbol = str(symbol).upper()
            if normalized_symbol == "VNINDEX" or normalized_symbol in active_symbols:
                continue
            rows = normalized_history.get(normalized_symbol, [])
            metrics = metrics_by_symbol.get(normalized_symbol, {})
            if not rows or not _eligible(result, metrics, min_score):
                continue
            episode = _new_episode(normalized_symbol, result, metrics, mode, rows)
            if episode["episode_id"] in existing_ids:
                continue
            episodes.append(episode)
            added.append(episode)
            existing_ids.add(episode["episode_id"])

    active = [item for item in episodes if item.get("episode_status") != "COMPLETED_T20"]
    completed = sorted(
        (item for item in episodes if item.get("episode_status") == "COMPLETED_T20"),
        key=lambda item: str(item.get("date_signal", "")),
    )[-500:]
    tracker["episodes"] = completed + active
    tracker["updated_at"] = datetime.now(VN_TZ).isoformat(timespec="seconds")
    tracker["measurement"] = {
        "horizons": [f"T+{h}" for h in HORIZONS],
        "clock": "trading_sessions",
        "benchmark": "VNINDEX",
        "same_bar_stop_target": "STOP_FIRST_CONSERVATIVE",
        "next_open_slippage_tracked": True,
    }
    tracker["analytics"] = summarize_tracker_analytics(tracker)
    if persist and mode != "test":
        _save_tracker(path, tracker)
    return added


def build_performance_report(path: Path) -> str:
    tracker = load_tracker(path)
    completed = [
        item
        for item in tracker.get("episodes", [])
        if item.get("eligible_for_metrics") and item.get("return_t10") is not None
    ]
    if not completed:
        return "*TRACK RECORD V2*\nChưa đủ tín hiệu sạch để đo T+10 theo phiên."
    returns = [float(item["return_t10"]) for item in completed]
    excess = [float(item["excess_return_t10"]) for item in completed if item.get("excess_return_t10") is not None]
    mfe = [float(item["mfe_t10_pct"]) for item in completed if item.get("mfe_t10_pct") is not None]
    mae = [float(item["mae_t10_pct"]) for item in completed if item.get("mae_t10_pct") is not None]
    wins = sum(value > 3 for value in returns)
    outperformed = sum(value > 0 for value in excess)
    lines = [
        f"*TRACK RECORD V2* ({len(completed)} episodes độc lập)",
        f"T+10 >3%: {wins / len(returns) * 100:.0f}% | TB {statistics.fmean(returns):+.1f}% | Trung vị {statistics.median(returns):+.1f}%",
    ]
    if excess:
        lines.append(
            f"Vượt VNINDEX: {outperformed / len(excess) * 100:.0f}% | Excess TB {statistics.fmean(excess):+.1f}%"
        )
    if mfe and mae:
        lines.append(f"MFE/MAE T+10 trung vị: {statistics.median(mfe):+.1f}% / {statistics.median(mae):+.1f}%")

    analytics = summarize_tracker_analytics(tracker)
    path_outcomes = analytics.get("path_outcomes", {})
    avg_r = path_outcomes.get("avg_r_multiple_t10")
    avg_open_ret = path_outcomes.get("avg_return_open_t10")
    outcome_bits = [
        f"TP {int(path_outcomes.get('target_hit') or 0)}",
        f"SL {int(path_outcomes.get('stopped') or 0)}",
        f"Open {int(path_outcomes.get('open') or 0)}",
    ]
    if avg_r is not None:
        outcome_bits.append(f"R-mult TB {float(avg_r):+.2f}R")
    if avg_open_ret is not None:
        outcome_bits.append(f"Entry Open T+1: {float(avg_open_ret):+.1f}%")
    lines.append("Path 20P: " + " | ".join(outcome_bits))

    cal = analytics.get("calibration", {})
    cal_bits = []
    for label, key in (("<78", "score_lt_78"), ("78-84", "score_78_84"), ("85+", "score_85_plus")):
        bucket = cal.get(key, {})
        if int(bucket.get("count") or 0) > 0:
            cal_bits.append(
                f"{label}: {float(bucket['avg_return']):+.1f}% ({float(bucket['win_rate_pct']):.0f}%W, n={bucket['count']})"
            )
    if cal_bits:
        lines.append("Calibration T+10: " + " | ".join(cal_bits))

    lines.append("Chỉ tính tracker v2; dữ liệu v1 bị loại vì trùng tín hiệu và sai đồng hồ đo.")
    return "\n".join(lines)
