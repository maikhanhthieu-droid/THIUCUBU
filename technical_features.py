"""Technical-watch and early-accumulation evidence for the five-stream report.

These signals are deliberately advisory.  A bottoming oscillator or a deep
discount can promote a symbol to a watch stream, but neither one bypasses the
market-structure risk gate or becomes an automatic buy instruction.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
import pandas as pd


MAX_SIGNAL_SCORE = 97


def _safe(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return default if not math.isfinite(number) else number
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = MAX_SIGNAL_SCORE) -> float:
    return max(low, min(high, value))


def _prepare(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        return None
    frame = df.copy()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=list(required))
    frame = frame[(frame["close"] > 0) & (frame["volume"] >= 0)]
    return frame.reset_index(drop=True) if len(frame) >= 40 else None


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    result = 100 - 100 / (1 + rs)
    return result.fillna(50.0).clip(0, 100)


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = line.ewm(span=9, adjust=False).mean()
    return line, signal, line - signal


def _smi(frame: pd.DataFrame, period: int = 14, smooth: int = 3) -> tuple[pd.Series, pd.Series]:
    highest = frame["high"].rolling(period).max()
    lowest = frame["low"].rolling(period).min()
    midpoint = (highest + lowest) / 2
    half_range = (highest - lowest) / 2
    distance = frame["close"] - midpoint
    distance_smooth = distance.ewm(span=smooth, adjust=False).mean().ewm(
        span=smooth, adjust=False
    ).mean()
    range_smooth = half_range.ewm(span=smooth, adjust=False).mean().ewm(
        span=smooth, adjust=False
    ).mean()
    smi = (100 * distance_smooth / range_smooth.replace(0, np.nan)).fillna(0.0)
    return smi.clip(-100, 100), smi.ewm(span=3, adjust=False).mean().clip(-100, 100)


def _pivot_lows(series: pd.Series, *, lookback: int = 65, left: int = 3, right: int = 2) -> list[int]:
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    start = max(left, len(values) - lookback)
    pivots: list[int] = []
    for index in range(start, len(values) - right):
        value = values[index]
        if not np.isfinite(value):
            continue
        window = values[index - left : index + right + 1]
        if np.isfinite(window).all() and value <= float(np.min(window)):
            if not pivots or index - pivots[-1] >= 4:
                pivots.append(index)
            elif value < values[pivots[-1]]:
                pivots[-1] = index
    return pivots


def _bullish_divergence(
    price: pd.Series,
    indicator: pd.Series,
    *,
    indicator_floor: float,
) -> bool:
    """Use price pivots so price and oscillator lows refer to the same bars."""

    pivots = _pivot_lows(price)
    if len(pivots) < 2:
        return False
    first, second = pivots[-2], pivots[-1]
    if second - first > 40:
        return False
    p1, p2 = _safe(price.iloc[first]), _safe(price.iloc[second])
    i1, i2 = _safe(indicator.iloc[first]), _safe(indicator.iloc[second])
    scale = max(_safe(indicator.tail(65).std()), abs(i1) * 0.08, indicator_floor)
    return p2 <= p1 * 0.995 and i2 >= i1 + scale


def analyze_technical_watch(df: pd.DataFrame | None) -> dict[str, Any]:
    """Detect bottom-area convergence/divergence without issuing a buy signal."""

    empty = {
        "watch": False,
        "score": 0,
        "stage": "NONE",
        "confidence": 0,
        "signals": [],
        "rsi": None,
        "macd_hist_pct": None,
        "smi": None,
    }
    frame = _prepare(df)
    if frame is None or len(frame) < 80:
        return empty

    close = frame["close"]
    rsi = _rsi(close)
    macd, signal, histogram = _macd(close)
    smi, smi_signal = _smi(frame)
    close_now = max(_safe(close.iloc[-1]), 1e-9)
    hist_pct = histogram / close.replace(0, np.nan) * 100

    rsi_div = _bullish_divergence(close, rsi, indicator_floor=2.0)
    macd_div = _bullish_divergence(close, histogram, indicator_floor=close_now * 0.0008)
    hist_rising = bool(
        histogram.iloc[-1] > histogram.iloc[-2]
        and histogram.iloc[-2] >= histogram.iloc[-3]
    )
    macd_cross_bottom = bool(
        histogram.iloc[-1] >= 0
        and histogram.iloc[-2] < 0
        and macd.iloc[-1] <= close_now * 0.01
    )
    macd_near_cross = bool(
        histogram.iloc[-1] < 0
        and -0.65 <= _safe(hist_pct.iloc[-1]) < 0
        and hist_rising
    )
    rsi_turn = bool(rsi.tail(6).min() <= 40 and rsi.iloc[-1] > rsi.iloc[-2] and rsi.iloc[-1] <= 52)
    smi_cross_bottom = bool(
        smi.iloc[-1] > smi_signal.iloc[-1]
        and smi.iloc[-2] <= smi_signal.iloc[-2]
        and smi.iloc[-1] <= 25
    )
    smi_turn = bool(
        smi.iloc[-1] <= 20
        and smi.iloc[-1] > smi.iloc[-2]
        and (smi.iloc[-1] - smi_signal.iloc[-1]) > (smi.iloc[-2] - smi_signal.iloc[-2])
    )
    near_recent_low = bool(close_now <= _safe(close.tail(60).max(), close_now) * 0.88)
    bottom_context = bool(
        near_recent_low
        or rsi.iloc[-1] <= 48
        or macd.iloc[-1] < 0
        or smi.iloc[-1] <= 0
    )

    score = 0
    signals: list[str] = []
    if rsi_div:
        score += 30
        signals.append("RSI phân kỳ đáy")
    if macd_div:
        score += 25
        signals.append("MACD phân kỳ đáy")
    if macd_cross_bottom:
        score += 22
        signals.append("MACD vừa giao cắt lên vùng đáy")
    elif macd_near_cross:
        score += 17
        signals.append("MACD hội tụ, gần giao cắt")
    if smi_cross_bottom:
        score += 15
        signals.append("SMI giao cắt lên vùng đáy")
    elif smi_turn:
        score += 10
        signals.append("SMI quay lên vùng đáy")
    if rsi_turn:
        score += 10
        signals.append("RSI hồi phục từ vùng thấp")
    if sum((rsi_div or rsi_turn, macd_div or macd_cross_bottom or macd_near_cross, smi_cross_bottom or smi_turn)) >= 2:
        score += 8
        signals.append("Động lượng đáy đồng thuận")
    score = int(round(_clamp(score)))

    watch = bool(bottom_context and score >= 25)
    if not watch:
        stage = "NONE"
    elif (rsi_div or macd_div) and (macd_cross_bottom or smi_cross_bottom):
        stage = "CONFIRMED"
    elif rsi_div or macd_div or macd_cross_bottom:
        stage = "FORMING_STRONG"
    else:
        stage = "FORMING"
    confidence = int(_clamp(42 + score * 0.45 + (8 if len(frame) >= 160 else 0), 0, 92))
    return {
        "watch": watch,
        "score": score,
        "stage": stage,
        "confidence": confidence,
        "signals": signals[:5],
        "rsi": round(_safe(rsi.iloc[-1]), 1),
        "macd_hist_pct": round(_safe(hist_pct.iloc[-1]), 3),
        "smi": round(_safe(smi.iloc[-1]), 1),
    }


def analyze_early_accumulation(
    df: pd.DataFrame | None,
    result: Any,
    *,
    structure: Mapping[str, Any] | None,
    technical: Mapping[str, Any] | None,
    relative_strength: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Score E1/E2/E3 stabilization while hard-blocking severe structure risk."""

    empty = {
        "eligible": False,
        "score": 0,
        "stage": "NONE",
        "label": "CHƯA ĐỦ ĐIỀU KIỆN",
        "confidence": 0,
        "action": "THEO DÕI",
        "signals": [],
        "missing": [],
        "vol_5_20": None,
    }
    frame = _prepare(df)
    if frame is None or len(frame) < 80:
        return empty

    structure = structure or {}
    technical = technical or {}
    relative_strength = relative_strength or {}
    frames = structure.get("timeframes") if isinstance(structure.get("timeframes"), Mapping) else {}
    breakout = structure.get("breakout") if isinstance(structure.get("breakout"), Mapping) else {}
    overall = str(structure.get("overall_state") or "NO_DATA")
    daily = str((frames.get("1D") or {}).get("state") or "NO_DATA")
    weekly = str((frames.get("1W") or {}).get("state") or "NO_DATA")
    monthly = str((frames.get("1M") or {}).get("state") or "NO_DATA")
    breakout_state = str(breakout.get("state") or "NO_DATA")

    severe_risk = bool(
        getattr(result, "failed_break", False)
        or breakout_state == "FAILED_BREAK_CONFIRMED"
        or overall == "DISTRIBUTION"
        or weekly == "DISTRIBUTION"
        or monthly == "DISTRIBUTION"
        or (daily == "MARKDOWN" and weekly == "MARKDOWN")
    )
    close = frame["close"]
    volume = frame["volume"]
    close_now = max(_safe(close.iloc[-1]), 1e-9)
    vol20 = max(_safe(volume.tail(20).mean()), 1.0)
    vol_5_20 = _safe(volume.tail(5).mean()) / vol20
    range10 = (_safe(frame["high"].tail(10).max()) - _safe(frame["low"].tail(10).min())) / close_now
    return5 = close_now / max(_safe(close.iloc[-6], close_now), 1e-9) - 1 if len(close) >= 6 else 0.0
    low20 = max(_safe(frame["low"].tail(20).min(), close_now), 1e-9)
    distance_low20 = close_now / low20 - 1
    stable = bool(range10 <= 0.13 and return5 >= -0.06 and distance_low20 <= 0.12)
    selling_exhaustion = bool(vol_5_20 <= 0.82 and return5 >= -0.06)

    discount = max(_safe(getattr(result, "discount_pct", 0.0)), 0.0)
    target = max(_safe(getattr(result, "target_discount_pct", 25.0), 25.0), 10.0)
    discount_context = bool(discount >= max(10.0, target * 0.65))
    discount_points = min(discount / max(target, 1.0), 1.25) * 20
    structure_points = {
        "ACCUMULATION": 20,
        "CAUTION": 13,
        "OPPORTUNITY": 10,
        "NO_DATA": 4,
    }.get(overall, 2)
    if daily in {"ACCUMULATION", "REACCUMULATION", "TRANSITION"}:
        structure_points += 4
    flow_points = 16 if selling_exhaustion else 9 if vol_5_20 <= 1.0 else 2
    stability_points = 18 if stable else 7 if return5 >= -0.08 else 0
    technical_points = min(_safe(technical.get("score")) * 0.25, 24)
    rs_score = _safe(relative_strength.get("rs_score"), 50.0)
    rs_points = 10 if rs_score >= 55 else 7 if rs_score >= 45 else 3
    score = int(round(_clamp(discount_points + structure_points + flow_points + stability_points + technical_points + rs_points)))

    signals: list[str] = []
    missing: list[str] = []
    if discount_context:
        signals.append(f"Chiết khấu {discount:.1f}%")
    else:
        missing.append("chưa vào vùng chiết khấu")
    if selling_exhaustion:
        signals.append(f"Cung bán cạn Vol5/20 {vol_5_20:.2f}x")
    else:
        missing.append("volume bán chưa cạn")
    if stable:
        signals.append("Giá bắt đầu ổn định")
    else:
        missing.append("giá chưa ổn định")
    signals.extend(str(item) for item in list(technical.get("signals") or [])[:2])
    if not technical.get("watch"):
        missing.append("động lượng đáy chưa xác nhận")
    if daily == "MARKDOWN":
        missing.append("1D còn suy yếu")
    if weekly in {"MARKDOWN", "DISTRIBUTION"}:
        missing.append("1W còn rủi ro")

    eligible = bool(not severe_risk and discount_context and (selling_exhaustion or technical.get("watch")))
    if not eligible or score < 45:
        stage, label, action = "NONE", "CHƯA ĐỦ ĐIỀU KIỆN", "THEO DÕI"
        eligible = False
    elif score >= 75 and stable and selling_exhaustion and technical.get("score", 0) >= 35 and daily != "MARKDOWN":
        stage, label, action = "E3", "GOM SỚM XÁC NHẬN", "CÓ THỂ THĂM DÒ NHỎ"
    elif score >= 60 and stable:
        stage, label, action = "E2", "ĐANG TẠO ĐÁY", "CHỜ XÁC NHẬN / THĂM DÒ RẤT NHỎ"
    else:
        stage, label, action = "E1", "CẠN KIỆT BÁN", "CHỈ THEO DÕI"
    confidence = int(_clamp(38 + score * 0.42 + (7 if len(frame) >= 160 else 0), 0, 90))
    return {
        "eligible": eligible,
        "score": score,
        "stage": stage,
        "label": label,
        "confidence": confidence,
        "action": action,
        "signals": signals[:5],
        "missing": missing[:4],
        "vol_5_20": round(vol_5_20, 2),
    }
