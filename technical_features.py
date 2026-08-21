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
    if "time" in frame.columns:
        frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
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


def _pivot_highs(
    series: pd.Series,
    *,
    lookback: int = 65,
    left: int = 3,
    right: int = 2,
) -> list[int]:
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    start = max(left, len(values) - lookback)
    pivots: list[int] = []
    for index in range(start, len(values) - right):
        value = values[index]
        if not np.isfinite(value):
            continue
        window = values[index - left : index + right + 1]
        if np.isfinite(window).all() and value >= float(np.max(window)):
            if not pivots or index - pivots[-1] >= 4:
                pivots.append(index)
            elif value > values[pivots[-1]]:
                pivots[-1] = index
    return pivots


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def _pivot_date(frame: pd.DataFrame, index: int) -> str | None:
    if "time" not in frame.columns or index < 0 or index >= len(frame):
        return None
    value = frame["time"].iloc[index]
    return value.date().isoformat() if pd.notna(value) else None


def _bottom_pattern(
    frame: pd.DataFrame,
    rsi: pd.Series,
    macd: pd.Series,
    histogram: pd.Series,
    *,
    macd_cross_bottom: bool,
    macd_near_cross: bool,
) -> dict[str, Any]:
    """Find confirmed two/three-bottom evidence without using future bars."""

    empty = {
        "bottom_count": 0,
        "bottom_quality_score": 0,
        "bullish_divergence_type": "NONE",
        "macd_bullish_divergence": False,
        "rsi_bullish_divergence": False,
        "neckline": None,
        "neckline_distance_pct": None,
        "neckline_reclaimed": False,
        "trigger_price": None,
        "invalidation_price": None,
        "pivot_indices": [],
        "pivot_dates": [],
        "pivot_prices": [],
        "signal_age_bars": None,
        "confirmed_at_bar": None,
        "volume_drying": False,
        "pre_label": "NONE",
        "pre_action": "CHỈ THEO DÕI",
        "signals": [],
    }
    close = frame["close"]
    pivots = _pivot_lows(close, lookback=100, left=3, right=2)
    if len(pivots) < 2:
        if macd_cross_bottom:
            return {
                **empty,
                "bottom_quality_score": 28,
                "pre_label": "PRE-MACD-CROSS",
                "pre_action": "CHỜ GIÁ XÁC NHẬN CẤU TRÚC ĐÁY",
                "signals": ["MACD giao cắt lên nhưng chưa đủ 2 đáy"],
            }
        if macd_near_cross:
            return {
                **empty,
                "bottom_quality_score": 26,
                "pre_label": "PRE-MACD-CONVERGE",
                "pre_action": "CANH MACD GIAO CẮT VÀ GIÁ ỔN ĐỊNH",
                "signals": ["MACD hội tụ nhưng chưa đủ 2 đáy"],
            }
        return empty

    atr = _atr(frame)
    close_now = max(_safe(close.iloc[-1]), 1e-9)
    atr_now = max(_safe(atr.iloc[-1]), close_now * 0.005)
    tolerance = _clamp(max(0.03, atr_now / close_now * 1.5), 0.03, 0.08)
    recent = pivots[-3:]
    pair = recent[-2:]
    pair_gap = pair[1] - pair[0]
    pair_prices = [_safe(close.iloc[index]) for index in pair]
    pair_range = abs(pair_prices[1] / max(pair_prices[0], 1e-9) - 1.0)
    if not (5 <= pair_gap <= 50 and pair_range <= tolerance):
        return empty

    selected = pair
    if len(recent) == 3:
        triple_prices = [_safe(close.iloc[index]) for index in recent]
        triple_range = (max(triple_prices) - min(triple_prices)) / max(
            float(np.median(triple_prices)), 1e-9
        )
        gaps = [recent[1] - recent[0], recent[2] - recent[1]]
        if all(5 <= gap <= 50 for gap in gaps) and triple_range <= tolerance:
            selected = recent

    rebound_requirement = max(0.018, min(0.05, atr_now / close_now * 0.9))
    visible_rebounds = []
    for first, second in zip(selected, selected[1:]):
        middle_high = _safe(frame["high"].iloc[first : second + 1].max())
        base = min(_safe(close.iloc[first]), _safe(close.iloc[second]))
        visible_rebounds.append(middle_high >= base * (1 + rebound_requirement))
    if not visible_rebounds or not all(visible_rebounds):
        return empty

    first, latest = selected[0], selected[-1]
    previous = selected[-2]
    p1, p2 = _safe(close.iloc[previous]), _safe(close.iloc[latest])
    price_change = p2 / max(p1, 1e-9) - 1.0
    hist_scale = max(_safe(histogram.tail(100).std()) * 0.18, close_now * 0.00035)
    line_scale = max(_safe(macd.tail(100).std()) * 0.15, close_now * 0.00035)
    rsi_scale = max(_safe(rsi.tail(100).std()) * 0.18, 1.5)
    macd_improves = bool(
        _safe(histogram.iloc[latest]) >= _safe(histogram.iloc[previous]) + hist_scale
        or _safe(macd.iloc[latest]) >= _safe(macd.iloc[previous]) + line_scale
    )
    macd_weakens = bool(
        _safe(histogram.iloc[latest]) <= _safe(histogram.iloc[previous]) - hist_scale
        or _safe(macd.iloc[latest]) <= _safe(macd.iloc[previous]) - line_scale
    )
    rsi_improves = bool(
        _safe(rsi.iloc[latest]) >= _safe(rsi.iloc[previous]) + rsi_scale
    )
    regular = bool(price_change <= -0.003 and macd_improves)
    equal_low = bool(abs(price_change) <= 0.018 and macd_improves)
    hidden = bool(price_change >= 0.004 and macd_weakens)
    rsi_divergence = bool(price_change <= 0.002 and rsi_improves)
    divergence_type = (
        "REGULAR"
        if regular
        else "EQUAL_LOW_STRENGTH"
        if equal_low
        else "HIDDEN"
        if hidden
        else "NONE"
    )

    pivot_prices = [_safe(close.iloc[index]) for index in selected]
    neckline = _safe(frame["high"].iloc[first : latest + 1].max())
    neckline_distance = (neckline / close_now - 1.0) * 100.0
    neckline_reclaimed = bool(close_now >= neckline * 1.001)
    pivot_volume = [
        _safe(frame["volume"].iloc[max(0, index - 1) : index + 2].mean())
        for index in selected
    ]
    volume20 = max(_safe(frame["volume"].tail(20).mean()), 1.0)
    recent_volume_ratio = _safe(frame["volume"].tail(5).mean()) / volume20
    volume_drying = bool(
        recent_volume_ratio <= 0.85
        or pivot_volume[-1] <= max(pivot_volume[0], 1.0) * 0.88
    )
    confirmation_bar = latest + 2
    signal_age = max(0, len(frame) - 1 - confirmation_bar)
    stale = signal_age > 15
    floor = min(pivot_prices)
    invalidation = floor - max(atr_now * 0.65, floor * 0.012)
    breakdown = close_now < invalidation
    moved_away = close_now > neckline * 1.06
    momentum_active = bool(
        macd_cross_bottom
        or macd_near_cross
        or (
            histogram.iloc[-1] > histogram.iloc[-2]
            and histogram.iloc[-2] >= histogram.iloc[-3]
        )
    )

    quality = 18 + (12 if len(selected) == 3 else 0)
    quality += 9 if pair_range <= max(0.035, tolerance * 0.65) else 4
    quality += 24 if regular or equal_low else 13 if hidden else 0
    quality += 13 if rsi_divergence else 0
    quality += 10 if volume_drying else 0
    quality += 10 if macd_cross_bottom else 7 if macd_near_cross else 0
    quality += 10 if neckline_reclaimed else 5 if neckline_distance <= 4.0 else 0
    if stale:
        quality -= 16
    if moved_away:
        quality -= 18
    if breakdown:
        quality = 0
    quality = int(round(_clamp(quality)))

    count = len(selected)
    if breakdown or stale or moved_away:
        label = "NONE"
    elif divergence_type in {"REGULAR", "EQUAL_LOW_STRENGTH"} and momentum_active:
        label = f"PRE-DIV-{count}"
    elif divergence_type == "HIDDEN" and momentum_active:
        label = f"PRE-HIDDEN-{count}"
    elif macd_cross_bottom:
        label = f"PRE-BASE-{count}-CROSS"
    elif macd_near_cross:
        label = f"PRE-BASE-{count}"
    else:
        label = "NONE"

    signals: list[str] = [f"Cấu trúc {count} đáy đã xác nhận"]
    if regular:
        signals.append("MACD phân kỳ tăng ở đáy")
    elif equal_low:
        signals.append("MACD mạnh dần tại đáy ngang")
    elif hidden:
        signals.append("MACD phân kỳ ẩn tăng")
    if rsi_divergence:
        signals.append("RSI xác nhận phân kỳ đáy")
    if volume_drying:
        signals.append("Khối lượng co tại đáy sau")
    if neckline_reclaimed:
        signals.append("Giá đã reclaim neckline")
    elif neckline_distance <= 4.0:
        signals.append("Giá đang sát neckline")
    action = (
        "CHỜ RETEST GIỮ NECKLINE / XÁC NHẬN MTF"
        if neckline_reclaimed
        else "CANH RECLAIM NECKLINE, KHÔNG BẮT ĐÁY MÙ"
        if neckline_distance <= 6.0
        else "CHỈ THEO DÕI, CHỜ GIÁ XÁC NHẬN"
    )
    if label == "NONE":
        action = "CHỈ THEO DÕI — PRE CHƯA ĐỦ HOẶC ĐÃ HẾT HIỆU LỰC"
    return {
        **empty,
        "bottom_count": count,
        "bottom_quality_score": quality if label != "NONE" else 0,
        "bullish_divergence_type": divergence_type,
        "macd_bullish_divergence": bool(
            label != "NONE" and (regular or equal_low or hidden)
        ),
        "rsi_bullish_divergence": bool(label != "NONE" and rsi_divergence),
        "neckline": round(neckline, 2),
        "neckline_distance_pct": round(neckline_distance, 2),
        "neckline_reclaimed": neckline_reclaimed,
        "trigger_price": round(neckline * 1.002, 2),
        "invalidation_price": round(invalidation, 2),
        "pivot_indices": selected,
        "pivot_dates": [_pivot_date(frame, index) for index in selected],
        "pivot_prices": [round(value, 2) for value in pivot_prices],
        "signal_age_bars": signal_age,
        "confirmed_at_bar": confirmation_bar,
        "volume_drying": volume_drying,
        "pre_label": label,
        "pre_action": action,
        "signals": signals[:6] if label != "NONE" else [],
    }


def _bearish_top_pattern(
    frame: pd.DataFrame,
    rsi: pd.Series,
    macd: pd.Series,
    histogram: pd.Series,
) -> dict[str, Any]:
    close = frame["close"]
    pivots = _pivot_highs(close, lookback=90, left=3, right=2)
    empty = {
        "bearish_top_divergence": False,
        "top_count": 0,
        "risk_label": "NONE",
        "top_signal_age_bars": None,
    }
    if len(pivots) < 2:
        return empty
    first, second = pivots[-2], pivots[-1]
    if not 5 <= second - first <= 50:
        return empty
    close_now = max(_safe(close.iloc[-1]), 1e-9)
    price_change = _safe(close.iloc[second]) / max(_safe(close.iloc[first]), 1e-9) - 1
    hist_scale = max(_safe(histogram.tail(90).std()) * 0.18, close_now * 0.00035)
    line_scale = max(_safe(macd.tail(90).std()) * 0.15, close_now * 0.00035)
    rsi_scale = max(_safe(rsi.tail(90).std()) * 0.18, 1.5)
    momentum_lower = bool(
        _safe(histogram.iloc[second]) <= _safe(histogram.iloc[first]) - hist_scale
        or _safe(macd.iloc[second]) <= _safe(macd.iloc[first]) - line_scale
        or _safe(rsi.iloc[second]) <= _safe(rsi.iloc[first]) - rsi_scale
    )
    age = max(0, len(frame) - 1 - (second + 2))
    bearish = bool(price_change >= -0.002 and momentum_lower and age <= 20)
    return {
        "bearish_top_divergence": bearish,
        "top_count": 2 if bearish else 0,
        "risk_label": "PRE-RISK-DIV-TOP" if bearish else "NONE",
        "top_signal_age_bars": age if bearish else None,
    }


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
        "bullish_watch": False,
        "risk_watch": False,
        "score": 0,
        "stage": "NONE",
        "confidence": 0,
        "signals": [],
        "bullish_signals": [],
        "rsi": None,
        "macd_hist_pct": None,
        "smi": None,
        "pre_label": "NONE",
        "pre_action": "CHỈ THEO DÕI",
        "bottom_count": 0,
        "bottom_quality_score": 0,
        "bullish_divergence_type": "NONE",
        "macd_bullish_divergence": False,
        "rsi_bullish_divergence": False,
        "neckline": None,
        "trigger_price": None,
        "invalidation_price": None,
        "signal_age_bars": None,
        "bearish_top_divergence": False,
        "top_count": 0,
        "risk_label": "NONE",
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
    bottom = _bottom_pattern(
        frame,
        rsi,
        macd,
        histogram,
        macd_cross_bottom=macd_cross_bottom,
        macd_near_cross=macd_near_cross,
    )
    top = _bearish_top_pattern(frame, rsi, macd, histogram)
    risk_dominant = bool(
        top.get("bearish_top_divergence")
        and (
            bottom.get("signal_age_bars") is None
            or int(top.get("top_signal_age_bars") or 0)
            <= int(bottom.get("signal_age_bars") or 0)
        )
    )
    if risk_dominant:
        bottom["pre_label"] = "NONE"
        bottom["pre_action"] = "KHÔNG MUA MỚI — CHỜ HỦY PHÂN KỲ ĐỈNH"
    rsi_div = bool(rsi_div or bottom.get("rsi_bullish_divergence"))
    macd_div = bool(macd_div or bottom.get("macd_bullish_divergence"))

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
    oscillator_score = int(round(_clamp(score)))
    pattern_score = int(bottom.get("bottom_quality_score") or 0)
    score = max(oscillator_score, pattern_score)
    if oscillator_score >= 25 and bottom.get("pre_label") != "NONE":
        score = min(97, score + 5)
    bullish_signals = list(dict.fromkeys([*bottom.get("signals", []), *signals]))[:7]
    risk_watch = bool(top.get("bearish_top_divergence"))
    if risk_watch:
        score = max(score, 44)
    bullish_watch = bool(
        not risk_dominant
        and
        (bottom_context or bottom.get("pre_label") != "NONE")
        and score >= 25
        and (
            oscillator_score >= 25
            or bottom.get("pre_label") != "NONE"
        )
    )
    watch = bool(bullish_watch or risk_watch)
    all_signals = list(bullish_signals)
    if risk_watch:
        all_signals.insert(0, "MACD/RSI phân kỳ đỉnh — cảnh báo suy yếu")

    if not watch:
        stage = "NONE"
    elif risk_watch and not bullish_watch:
        stage = "RISK_TOP"
    elif bottom.get("neckline_reclaimed") and (rsi_div or macd_div):
        stage = "CONFIRMED"
    elif rsi_div or macd_div or macd_cross_bottom or int(bottom.get("bottom_count") or 0) >= 3:
        stage = "FORMING_STRONG"
    else:
        stage = "FORMING"
    confidence = int(
        _clamp(
            38
            + score * 0.45
            + (8 if len(frame) >= 160 else 0)
            + (5 if int(bottom.get("bottom_count") or 0) >= 3 else 0),
            0,
            92,
        )
    )
    return {
        **bottom,
        **top,
        "watch": watch,
        "bullish_watch": bullish_watch,
        "risk_watch": risk_watch,
        "risk_dominant": risk_dominant,
        "score": score,
        "stage": stage,
        "confidence": confidence,
        "signals": all_signals[:7],
        "bullish_signals": bullish_signals,
        "rsi": round(_safe(rsi.iloc[-1]), 1),
        "macd_hist_pct": round(_safe(hist_pct.iloc[-1]), 3),
        "smi": round(_safe(smi.iloc[-1]), 1),
        "macd_convergence": macd_near_cross,
        "macd_cross_bottom": macd_cross_bottom,
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
    bullish_technical = bool(
        technical.get("bullish_watch", technical.get("watch", False))
    )
    bearish_top = bool(
        technical.get("bearish_top_divergence")
        and technical.get("risk_dominant", True)
    )
    technical_points = (
        min(_safe(technical.get("score")) * 0.25, 24)
        if bullish_technical and not bearish_top
        else 0
    )
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
    signals.extend(
        str(item)
        for item in list(
            technical.get("bullish_signals") or technical.get("signals") or []
        )[:2]
    )
    if not bullish_technical:
        missing.append("động lượng đáy chưa xác nhận")
    if bearish_top:
        missing.append("có phân kỳ đỉnh, chưa phù hợp gom sớm")
    if daily == "MARKDOWN":
        missing.append("1D còn suy yếu")
    if weekly in {"MARKDOWN", "DISTRIBUTION"}:
        missing.append("1W còn rủi ro")

    eligible = bool(
        not severe_risk
        and not bearish_top
        and discount_context
        and (selling_exhaustion or bullish_technical)
    )
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
        "pre_label": technical.get("pre_label", "NONE"),
        "bottom_count": int(technical.get("bottom_count") or 0),
        "trigger_price": technical.get("trigger_price"),
        "invalidation_price": technical.get("invalidation_price"),
    }


def apply_market_context(
    early: Mapping[str, Any] | None,
    technical: Mapping[str, Any] | None,
    systemic: Mapping[str, Any] | None,
    sector: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply market/sector risk after symbol-level evidence is calculated.

    Technical PRE evidence remains visible even when the market gate blocks a
    new position.  Only the early-accumulation action is suppressed or capped.
    """

    early_out = dict(early or {})
    technical_out = dict(technical or {})
    systemic = systemic or {}
    sector = sector or {}
    systemic_state = str(systemic.get("state") or "NEUTRAL").upper()
    sector_state = str(sector.get("state") or "NO_DATA").upper()
    hard_lock = bool(systemic.get("hard_lock_new_accumulation"))
    context = {
        "systemic_state": systemic_state,
        "sector_state": sector_state,
        "position_size_multiplier": _safe(
            systemic.get("position_size_multiplier"), 0.75
        ),
    }
    early_out["market_context"] = context
    technical_out["market_context"] = context

    if hard_lock:
        early_out["eligible"] = False
        early_out["suppressed_by_market"] = True
        early_out["action"] = "CHỈ THEO DÕI — RỦI RO HỆ THỐNG KHÓA GOM MỚI"
        missing = list(early_out.get("missing") or [])
        missing.insert(0, "systemic risk khóa gom mới")
        early_out["missing"] = missing
    elif systemic_state == "HIGH_RISK" or sector_state in {"LAGGING", "EXITING"}:
        if early_out.get("stage") == "E3":
            early_out["stage"] = "E2"
            early_out["label"] = "ĐANG TẠO ĐÁY — CHỜ THỊ TRƯỜNG"
            early_out["score"] = min(int(early_out.get("score") or 0), 74)
        if early_out.get("eligible"):
            early_out["action"] = "CHỜ XÁC NHẬN, CHƯA THĂM DÒ"
        early_out["risk_capped"] = True
    elif systemic_state == "FAVORABLE" and sector_state in {"LEADING", "ENTERING"}:
        early_out["confidence"] = min(
            92, int(early_out.get("confidence") or 0) + 3
        )
        technical_out["confidence"] = min(
            94, int(technical_out.get("confidence") or 0) + 2
        )

    early_out["missing"] = list(early_out.get("missing") or [])[:5]
    return early_out, technical_out
