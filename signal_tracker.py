"""Auditable signal episodes measured on trading sessions, not calendar days."""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "thieucubu.signal_tracker.v2"
HORIZONS = (5, 10, 20)
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
        high = _safe_float(raw.get("high"))
        low = _safe_float(raw.get("low"))
        by_date[day] = {
            "date": day,
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


def _update_path_outcome(episode: dict[str, Any], forward: list[dict[str, Any]]) -> None:
    if episode.get("path_outcome") in {"STOPPED", "TARGET_HIT"}:
        return
    stop = _safe_float(episode.get("stop_loss"))
    target = _safe_float(episode.get("take_profit"))
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
                }
            )
            return
        if hit_stop:
            episode.update(
                {
                    "path_outcome": "STOPPED",
                    "outcome_date": row["date"],
                    "outcome_session": session_number,
                }
            )
            return
        if hit_target:
            episode.update(
                {
                    "path_outcome": "TARGET_HIT",
                    "outcome_date": row["date"],
                    "outcome_session": session_number,
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
    signal_date = latest["date"]
    score = int(metrics.get("advanced_score", getattr(result, "win_score", 0)) or 0)
    regime = metrics.get("regime") if isinstance(metrics.get("regime"), Mapping) else {}
    return {
        "episode_id": f"{symbol}:{signal_date}",
        "tracker_version": SCHEMA_VERSION,
        "eligible_for_metrics": True,
        "symbol": symbol,
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
        "horizons": ["T+5", "T+10", "T+20"],
        "clock": "trading_sessions",
        "benchmark": "VNINDEX",
        "same_bar_stop_target": "STOP_FIRST_CONSERVATIVE",
    }
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
    lines.append("Chỉ tính tracker v2; dữ liệu v1 bị loại vì trùng tín hiệu và sai đồng hồ đo.")
    return "\n".join(lines)
