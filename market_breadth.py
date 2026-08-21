"""Daily market breadth, correlation/dispersion and systemic-risk overlay.

The engine deliberately uses only symbols whose latest OHLCV row matches the
VNINDEX as-of date.  A thin/stale sample lowers confidence and is never allowed
to create a hard market lock.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

import scan


DATA_DIR = scan.DATA_DIR
HISTORY_PATH = DATA_DIR / "market_breadth_history.json"
SYSTEMIC_PATH = DATA_DIR / "market_systemic_state.json"
SCHEMA_VERSION = "thieucubu.market_breadth.v1"
SYSTEMIC_SCHEMA_VERSION = "thieucubu.systemic_regime.v1"
STATE_ORDER = {"FAVORABLE": 0, "NEUTRAL": 1, "HIGH_RISK": 2, "SYSTEMIC_RISK": 3}


def _safe(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return default if not math.isfinite(number) else number
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _frame(rows: Any) -> pd.DataFrame | None:
    if not isinstance(rows, list) or not rows:
        return None
    try:
        frame = pd.DataFrame(rows)
    except Exception:
        return None
    required = {"time", "close", "volume"}
    if not required.issubset(frame.columns):
        return None
    frame = frame.copy()
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce").dt.tz_localize(None)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame = frame.dropna(subset=["time", "close", "volume"])
    frame = frame[(frame["close"] > 0) & (frame["volume"] >= 0)]
    frame = frame.sort_values("time").drop_duplicates("time", keep="last")
    return frame.reset_index(drop=True) if len(frame) >= 2 else None


def _history_snapshots(path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    raw = scan.json_load(path, {})
    if isinstance(raw, dict):
        rows = raw.get("snapshots", [])
    else:  # defensive migration if an early prototype used a list
        rows = raw
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def _pct(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100.0, 2) if denominator > 0 else None


def _mean(values: list[float]) -> float | None:
    clean = [value for value in values if math.isfinite(value)]
    return round(float(np.mean(clean)), 4) if clean else None


def _correlation_metrics(
    frames: Mapping[str, pd.DataFrame],
    index_frame: pd.DataFrame,
) -> dict[str, Any]:
    index_returns = index_frame.set_index(index_frame["time"].dt.normalize())["close"].pct_change()
    corr20: list[float] = []
    corr60: list[float] = []
    downside: list[float] = []
    daily_returns: list[float] = []
    current_index_return = _safe(index_frame["close"].iloc[-1] / index_frame["close"].iloc[-2] - 1.0)
    same_direction = 0
    for frame in frames.values():
        returns = frame.set_index(frame["time"].dt.normalize())["close"].pct_change()
        joined = pd.concat([returns.rename("stock"), index_returns.rename("index")], axis=1).dropna()
        if len(joined) >= 15:
            value = joined.tail(20)["stock"].corr(joined.tail(20)["index"])
            if pd.notna(value):
                corr20.append(float(value))
        if len(joined) >= 35:
            value = joined.tail(60)["stock"].corr(joined.tail(60)["index"])
            if pd.notna(value):
                corr60.append(float(value))
            down_rows = joined.tail(60)[joined.tail(60)["index"] < 0]
            if len(down_rows) >= 6:
                down_value = down_rows["stock"].corr(down_rows["index"])
                if pd.notna(down_value):
                    downside.append(float(down_value))
        stock_return = _safe(frame["close"].iloc[-1] / frame["close"].iloc[-2] - 1.0)
        daily_returns.append(stock_return * 100.0)
        if stock_return == 0 or current_index_return == 0 or np.sign(stock_return) == np.sign(current_index_return):
            same_direction += 1
    average20 = _mean(corr20)
    average60 = _mean(corr60)
    downside_average = _mean(downside)
    return {
        "average_correlation_20d": round(average20, 3) if average20 is not None else None,
        "average_correlation_60d": round(average60, 3) if average60 is not None else None,
        "downside_correlation_60d": round(downside_average, 3) if downside_average is not None else None,
        "high_correlation_pct_20d": _pct(sum(value >= 0.70 for value in corr20), len(corr20)),
        "co_movement_pct_1d": _pct(same_direction, len(daily_returns)),
        "dispersion_1d_pct": round(float(np.std(daily_returns)), 3) if len(daily_returns) >= 2 else None,
        "sample_20d": len(corr20),
        "sample_60d": len(corr60),
        "index_return_1d_pct": round(current_index_return * 100.0, 3),
    }


def _divergence(
    snapshot: Mapping[str, Any],
    history: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    recent = [item for item in history if item.get("trading_date") != snapshot.get("trading_date")][-25:]
    if len(recent) < 5:
        return "NONE", []
    index_now = _safe(snapshot.get("index_close"))
    breadth_now = _safe(snapshot.get("pct_above_ma50"), 50.0)
    prior_high = max(_safe(item.get("index_close")) for item in recent)
    prior_low = min(_safe(item.get("index_close"), index_now) for item in recent)
    breadth_high = max(_safe(item.get("pct_above_ma50"), 50.0) for item in recent)
    breadth_low = min(_safe(item.get("pct_above_ma20"), 50.0) for item in recent)
    reasons: list[str] = []
    if prior_high > 0 and index_now >= prior_high * 0.997 and breadth_now <= breadth_high - 8:
        reasons.append(f"VNINDEX sát/vượt đỉnh nhưng % trên MA50 hụt {breadth_high - breadth_now:.1f}đ")
        return "BEARISH", reasons
    breadth20 = _safe(snapshot.get("pct_above_ma20"), 50.0)
    if prior_low > 0 and index_now <= prior_low * 1.003 and breadth20 >= breadth_low + 8:
        reasons.append(f"VNINDEX sát đáy nhưng % trên MA20 cải thiện {breadth20 - breadth_low:.1f}đ")
        return "BULLISH", reasons
    return "NONE", reasons


def calculate_snapshot(
    history_store: Mapping[str, Any],
    *,
    expected_universe_size: int | None = None,
    history_path: Path = HISTORY_PATH,
) -> dict[str, Any]:
    """Calculate a same-date equal-weight breadth snapshot from scanned symbols."""

    index_frame = _frame(history_store.get("VNINDEX"))
    if index_frame is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "NO_INDEX_DATA",
            "state": "NO_DATA",
            "score": 0,
            "confidence": 0,
            "valid_symbols": 0,
        }
    target_date = index_frame["time"].iloc[-1].date().isoformat()
    frames: dict[str, pd.DataFrame] = {}
    stale_symbols = 0
    for raw_symbol, rows in history_store.items():
        symbol = str(raw_symbol or "").upper()
        if symbol == "VNINDEX" or not (len(symbol) == 3 and symbol.isalnum()):
            continue
        frame = _frame(rows)
        if frame is None or frame["time"].iloc[-1].date().isoformat() != target_date:
            stale_symbols += 1
            continue
        frames[symbol] = frame

    advances = declines = unchanged = 0
    above20 = eligible20 = above50 = eligible50 = above200 = eligible200 = 0
    volume_above20 = volume_eligible = 0
    new_highs = new_lows = high_low_eligible = 0
    up_volume = down_volume = up_turnover = down_turnover = 0.0
    for frame in frames.values():
        close = frame["close"]
        volume = frame["volume"]
        change = _safe(close.iloc[-1] / close.iloc[-2] - 1.0)
        if change > 0.0005:
            advances += 1
            up_volume += _safe(volume.iloc[-1])
            up_turnover += _safe(volume.iloc[-1] * close.iloc[-1])
        elif change < -0.0005:
            declines += 1
            down_volume += _safe(volume.iloc[-1])
            down_turnover += _safe(volume.iloc[-1] * close.iloc[-1])
        else:
            unchanged += 1
        for period, above_name in ((20, "ma20"), (50, "ma50"), (200, "ma200")):
            if len(close) < period:
                continue
            is_above = bool(close.iloc[-1] > close.tail(period).mean())
            if above_name == "ma20":
                eligible20 += 1
                above20 += int(is_above)
            elif above_name == "ma50":
                eligible50 += 1
                above50 += int(is_above)
            else:
                eligible200 += 1
                above200 += int(is_above)
        if len(volume) >= 21:
            volume_eligible += 1
            volume_above20 += int(volume.iloc[-1] > volume.iloc[-21:-1].mean())
        if len(close) >= 120:
            high_low_eligible += 1
            prior = close.iloc[:-1].tail(252)
            new_highs += int(close.iloc[-1] >= prior.max() * 0.998)
            new_lows += int(close.iloc[-1] <= prior.min() * 1.002)

    valid = len(frames)
    ad_ratio = advances / max(declines, 1)
    volume_ratio = up_volume / max(down_volume, 1.0)
    turnover_ratio = up_turnover / max(down_turnover, 1.0)
    history = _history_snapshots(history_path)
    prior_rows = [item for item in history if item.get("trading_date") != target_date]
    prior_ad_line = _safe(prior_rows[-1].get("advance_decline_line")) if prior_rows else 0.0
    ad_line = prior_ad_line + advances - declines
    pct20 = _pct(above20, eligible20)
    pct50 = _pct(above50, eligible50)
    pct200 = _pct(above200, eligible200)
    pct_volume = _pct(volume_above20, volume_eligible)
    high_balance = new_highs / max(new_highs + new_lows, 1) * 100.0
    ad_component = ad_ratio / (1.0 + ad_ratio) * 100.0
    score = (
        _safe(pct20, 50.0) * 0.24
        + _safe(pct50, 50.0) * 0.28
        + _safe(pct200, 50.0) * 0.14
        + ad_component * 0.14
        + _safe(pct_volume, 50.0) * 0.10
        + high_balance * 0.10
    )
    expected = max(int(expected_universe_size or valid), 1)
    coverage = valid / expected * 100.0
    confidence = int(_clamp(35 + min(valid / 150.0, 1.0) * 35 + min(coverage / 80.0, 1.0) * 25, 0, 95))
    status = "OK" if valid >= 50 else "THIN_SAMPLE" if valid >= 20 else "INSUFFICIENT_SAMPLE"
    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": datetime.now(scan.VN_TZ).isoformat(timespec="seconds"),
        "trading_date": target_date,
        "status": status,
        "universe_size": expected,
        "valid_symbols": valid,
        "coverage_pct": round(min(coverage, 100.0), 2),
        "stale_or_invalid_symbols": stale_symbols,
        "index_close": round(_safe(index_frame["close"].iloc[-1]), 2),
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "advance_decline_ratio": round(ad_ratio, 3),
        "advance_decline_line": round(ad_line, 2),
        "pct_above_ma20": pct20,
        "pct_above_ma50": pct50,
        "pct_above_ma200": pct200,
        "pct_volume_above_ma20": pct_volume,
        "up_down_volume_ratio": round(volume_ratio, 3),
        "up_down_turnover_ratio": round(turnover_ratio, 3),
        "new_highs_52w": new_highs,
        "new_lows_52w": new_lows,
        "new_high_low_sample": high_low_eligible,
        "score": int(round(_clamp(score, 0, 97))),
        "confidence": confidence,
    }
    snapshot["correlation"] = _correlation_metrics(frames, index_frame)
    divergence, reasons = _divergence(snapshot, history)
    snapshot["divergence"] = divergence
    snapshot["divergence_reasons"] = reasons
    if divergence == "BEARISH":
        snapshot["score"] = max(0, int(snapshot["score"]) - 10)
    elif divergence == "BULLISH":
        snapshot["score"] = min(97, int(snapshot["score"]) + 6)
    if status == "INSUFFICIENT_SAMPLE":
        state = "NO_DATA"
    elif snapshot["score"] >= 65 and ad_ratio >= 1.0 and _safe(pct50) >= 52:
        state = "STRONG"
    elif snapshot["score"] < 35 or (_safe(pct50, 50) < 30 and ad_ratio < 0.70):
        state = "RISK_OFF"
    elif snapshot["score"] < 48:
        state = "WEAK"
    else:
        state = "NEUTRAL"
    snapshot["state"] = state
    return snapshot


def derive_systemic_regime(
    breadth: Mapping[str, Any],
    index_regime: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Combine breadth, index structure, liquidity and downside correlation."""

    index_name = str((index_regime or {}).get("regime") or "UNKNOWN").upper()
    breadth_state = str(breadth.get("state") or "NO_DATA").upper()
    confidence = int(_safe(breadth.get("confidence")))
    correlation = breadth.get("correlation") if isinstance(breadth.get("correlation"), Mapping) else {}
    risk = 0
    reasons: list[str] = []
    if breadth_state == "RISK_OFF":
        risk += 38
        reasons.append("breadth RISK OFF")
    elif breadth_state == "WEAK":
        risk += 23
        reasons.append("breadth suy yếu")
    elif breadth_state == "STRONG":
        risk -= 12
        reasons.append("breadth mở rộng")
    if index_name == "BEAR":
        risk += 28
        reasons.append("VNINDEX dưới cấu trúc dài hạn")
    elif index_name == "CHOPPY":
        risk += 10
        reasons.append("VNINDEX choppy")
    elif index_name == "BULL":
        risk -= 8
    divergence = str(breadth.get("divergence") or "NONE")
    if divergence == "BEARISH":
        risk += 18
        reasons.append("phân kỳ breadth âm")
    elif divergence == "BULLISH":
        risk -= 6
        reasons.append("phân kỳ breadth dương")
    if _safe(breadth.get("advance_decline_ratio"), 1.0) < 0.65:
        risk += 10
        reasons.append("A/D ratio thấp")
    if _safe(breadth.get("up_down_turnover_ratio"), 1.0) < 0.70:
        risk += 9
        reasons.append("giá trị giao dịch nghiêng về bên giảm")
    downside_corr = _safe(correlation.get("downside_correlation_60d"), 0.0)
    index_return = _safe(correlation.get("index_return_1d_pct"), 0.0)
    if downside_corr >= 0.65 and index_return < 0:
        risk += 14
        reasons.append("tương quan giảm hệ thống cao")
    risk = int(round(_clamp(risk, 0, 97)))
    if confidence < 45 or breadth_state == "NO_DATA":
        raw_state = "NEUTRAL"
        reasons.append("độ phủ breadth chưa đủ để khóa cứng")
    elif risk >= 65:
        raw_state = "SYSTEMIC_RISK"
    elif risk >= 42:
        raw_state = "HIGH_RISK"
    elif risk <= 20 and breadth_state == "STRONG" and index_name in {"BULL", "RECOVERY"}:
        raw_state = "FAVORABLE"
    else:
        raw_state = "NEUTRAL"
    adjustments = {
        "FAVORABLE": (0, 1.0, False),
        "NEUTRAL": (2, 0.75, False),
        "HIGH_RISK": (6, 0.40, False),
        "SYSTEMIC_RISK": (10, 0.20, True),
    }
    score_adjustment, size_multiplier, hard_lock = adjustments[raw_state]
    return {
        "schema_version": SYSTEMIC_SCHEMA_VERSION,
        "updated_at": datetime.now(scan.VN_TZ).isoformat(timespec="seconds"),
        "trading_date": breadth.get("trading_date"),
        "state": raw_state,
        "raw_state": raw_state,
        "risk_score": risk,
        "confidence": confidence,
        "breadth_state": breadth_state,
        "index_regime": index_name,
        "min_score_adjustment": score_adjustment,
        "position_size_multiplier": size_multiplier,
        "hard_lock_new_accumulation": hard_lock,
        "reasons": reasons[:6],
    }


def _resolve_state(
    previous: Mapping[str, Any],
    current: dict[str, Any],
    *,
    new_trading_day: bool,
) -> dict[str, Any]:
    """Use two daily confirmations except when systemic risk is already severe."""

    previous_state = str(previous.get("state") or current["raw_state"])
    raw_state = str(current["raw_state"])
    pending_state = str(previous.get("pending_state") or "")
    pending_count = int(_safe(previous.get("pending_count")))
    if raw_state == previous_state:
        resolved, pending_state, pending_count = raw_state, "", 0
    elif raw_state == "SYSTEMIC_RISK" and int(current.get("risk_score") or 0) >= 75:
        resolved, pending_state, pending_count = raw_state, "", 0
    else:
        if pending_state == raw_state and new_trading_day:
            pending_count += 1
        elif pending_state != raw_state:
            pending_state, pending_count = raw_state, 1
        resolved = raw_state if pending_count >= 2 else previous_state
        if resolved == raw_state:
            pending_state, pending_count = "", 0
    current["state"] = resolved
    current["pending_state"] = pending_state or None
    current["pending_count"] = pending_count
    adjustment, size, hard_lock = {
        "FAVORABLE": (0, 1.0, False),
        "NEUTRAL": (2, 0.75, False),
        "HIGH_RISK": (6, 0.40, False),
        "SYSTEMIC_RISK": (10, 0.20, True),
    }.get(resolved, (2, 0.75, False))
    current["min_score_adjustment"] = adjustment
    current["position_size_multiplier"] = size
    current["hard_lock_new_accumulation"] = bool(hard_lock and int(current.get("confidence") or 0) >= 55)
    return current


def persist_daily(
    breadth: dict[str, Any],
    systemic: dict[str, Any],
    *,
    history_path: Path = HISTORY_PATH,
    systemic_path: Path = SYSTEMIC_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshots = _history_snapshots(history_path)
    snapshots = [item for item in snapshots if item.get("trading_date") != breadth.get("trading_date")]
    snapshots.append(dict(breadth))
    history_payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": breadth.get("updated_at"),
        "snapshots": snapshots[-320:],
    }
    scan.json_save(history_path, history_payload, pretty=False)

    previous = scan.json_load(systemic_path, {})
    previous = previous if isinstance(previous, dict) else {}
    prior_state = str(previous.get("state") or "")
    new_trading_day = bool(
        previous.get("trading_date")
        and previous.get("trading_date") != systemic.get("trading_date")
    )
    resolved = _resolve_state(
        previous,
        dict(systemic),
        new_trading_day=new_trading_day,
    )
    transitions = previous.get("transitions", []) if isinstance(previous.get("transitions"), list) else []
    if prior_state and resolved["state"] != prior_state:
        transitions.append(
            {
                "updated_at": resolved["updated_at"],
                "trading_date": resolved.get("trading_date"),
                "from": prior_state,
                "to": resolved["state"],
                "risk_score": resolved["risk_score"],
            }
        )
    resolved["transitions"] = transitions[-120:]
    scan.json_save(systemic_path, resolved, pretty=False)
    return history_payload, resolved


def load_systemic_state(path: Path = SYSTEMIC_PATH) -> dict[str, Any]:
    raw = scan.json_load(path, {})
    if not isinstance(raw, dict) or not raw.get("state"):
        return {
            "state": "NEUTRAL",
            "risk_score": 50,
            "confidence": 0,
            "min_score_adjustment": 2,
            "position_size_multiplier": 0.75,
            "hard_lock_new_accumulation": False,
            "reasons": ["chưa có breadth EOD"],
        }
    return raw


def format_breadth(snapshot: Mapping[str, Any] | None) -> str:
    if not snapshot or snapshot.get("state") == "NO_DATA":
        return "*MARKET BREADTH* chưa đủ dữ liệu cùng ngày."
    corr = snapshot.get("correlation") if isinstance(snapshot.get("correlation"), Mapping) else {}
    divergence = str(snapshot.get("divergence") or "NONE")
    return (
        f"*MARKET BREADTH* `{snapshot.get('state')}` {int(snapshot.get('score') or 0)}/97 | "
        f"MA20 { _safe(snapshot.get('pct_above_ma20')):.0f}% · MA50 {_safe(snapshot.get('pct_above_ma50')):.0f}% · "
        f"MA200 {_safe(snapshot.get('pct_above_ma200')):.0f}% | A/D {_safe(snapshot.get('advance_decline_ratio')):.2f} | "
        f"Vol>MA20 {_safe(snapshot.get('pct_volume_above_ma20')):.0f}% | NH/NL {int(snapshot.get('new_highs_52w') or 0)}/{int(snapshot.get('new_lows_52w') or 0)}\n"
        f"Độ phủ {int(snapshot.get('valid_symbols') or 0)}/{int(snapshot.get('universe_size') or 0)} · tin cậy {int(snapshot.get('confidence') or 0)}% | "
        f"Corr giảm {_safe(corr.get('downside_correlation_60d')):+.2f} · dispersion {_safe(corr.get('dispersion_1d_pct')):.2f}% | divergence {divergence}"
    )


def format_systemic(state: Mapping[str, Any] | None) -> str:
    if not state:
        return "*SYSTEMIC GATE* chưa có dữ liệu."
    reason = "; ".join(str(item) for item in state.get("reasons", [])[:3])
    lock = "KHÓA GOM MỚI" if state.get("hard_lock_new_accumulation") else f"siết +{int(state.get('min_score_adjustment') or 0)} điểm"
    return (
        f"*SYSTEMIC GATE* `{state.get('state', 'NEUTRAL')}` | risk {int(state.get('risk_score') or 0)}/97 | "
        f"{lock} | size x{_safe(state.get('position_size_multiplier'), 0.75):.2f} | {reason}"
    )
