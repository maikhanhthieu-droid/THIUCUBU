"""Multi-timeframe market phase and breakout-quality diagnostics.

The scanner uses this module as a context layer.  A high setup score is not
enough on its own: 1D, 1W and 1M structure must agree, while failed breakouts
and distribution pressure can block a new entry.  The classifications are
descriptive and intentionally separate from return forecasts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


POSITIVE_PHASES = {"MARKUP", "REACCUMULATION"}
BASE_PHASES = {"ACCUMULATION", "REACCUMULATION"}
RISK_PHASES = {"DISTRIBUTION", "MARKDOWN"}
FAILED_BREAK_STATES = {"FAILED_BREAK_WATCH", "FAILED_BREAK_CONFIRMED"}
BREAKOUT_LOOKBACK_BARS = 25

PHASE_LABELS = {
    "MARKUP": "CƠ HỘI",
    "REACCUMULATION": "TÁI TÍCH LŨY",
    "ACCUMULATION": "TÍCH LŨY",
    "DISTRIBUTION": "PHÂN PHỐI",
    "MARKDOWN": "SUY YẾU",
    "TRANSITION": "CẨN THẬN",
    "NO_DATA": "CHƯA ĐỦ DỮ LIỆU",
}

OVERALL_LABELS = {
    "OPPORTUNITY": "CƠ HỘI",
    "CAUTION": "CẨN THẬN",
    "ACCUMULATION": "TÍCH LŨY",
    "DISTRIBUTION": "PHÂN PHỐI",
    "NO_DATA": "CHƯA ĐỦ DỮ LIỆU",
}

BREAKOUT_LABELS = {
    "BREAKOUT_CONFIRMED": "BREAK XÁC NHẬN",
    "BREAKOUT_UNCONFIRMED": "BREAK CHƯA XÁC NHẬN",
    "HEALTHY_RETEST": "RETEST LÀNH MẠNH",
    "RECLAIMED_BREAK": "LẤY LẠI NỀN",
    "REACCUMULATION": "NGHI TÁI TÍCH LŨY",
    "FAILED_BREAK_WATCH": "BREAK XỊT - CHỜ XÁC NHẬN",
    "FAILED_BREAK_CONFIRMED": "BREAK XỊT XÁC NHẬN",
    "NEAR_BREAK": "GẦN ĐIỂM BREAK",
    "NO_BREAKOUT": "CHƯA CÓ BREAK",
    "NO_DATA": "CHƯA ĐỦ DỮ LIỆU",
}


def _safe(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return default if not np.isfinite(number) else number
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _prepare(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    required = {"time", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        return None
    frame = df[list(required)].copy()
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna().sort_values("time").drop_duplicates("time", keep="last")
    frame = frame[(frame["close"] > 0) & (frame["volume"] >= 0)]
    return frame.reset_index(drop=True) if not frame.empty else None


def _resample(frame: pd.DataFrame, rule: str | pd.DateOffset) -> pd.DataFrame:
    return (
        frame.resample(rule, on="time")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )


@dataclass
class TimeframePhase:
    timeframe: str
    state: str
    label: str
    score: int
    confidence: int
    range_pct: float | None
    distribution_bars: int
    accumulation_bars: int
    trend: str
    flow: str
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BreakoutDiagnosis:
    state: str
    label: str
    risk_level: str
    breakout_level: float | None
    event_age_bars: int | None
    distance_to_level_pct: float | None
    invalidation_price: float | None
    failed_confirmed: bool
    reaccumulation: bool
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketStructure:
    overall_state: str
    label: str
    score: int
    confidence: int
    action: str
    timeframes: dict[str, TimeframePhase]
    breakout: BreakoutDiagnosis
    blockers: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timeframes"] = {key: value.to_dict() for key, value in self.timeframes.items()}
        data["breakout"] = self.breakout.to_dict()
        return data


def _no_phase(timeframe: str) -> TimeframePhase:
    return TimeframePhase(timeframe, "NO_DATA", PHASE_LABELS["NO_DATA"], 0, 0, None, 0, 0, "UNKNOWN", "UNKNOWN", ["INSUFFICIENT_HISTORY"])


def _timeframe_phase(
    frame: pd.DataFrame | None,
    timeframe: str,
    *,
    fast: int,
    medium: int,
    long: int,
    range_window: int,
    base_limit: float,
    minimum_bars: int,
) -> TimeframePhase:
    if frame is None or len(frame) < minimum_bars:
        return _no_phase(timeframe)

    data = frame.copy().reset_index(drop=True)
    close = data["close"]
    high = data["high"]
    low = data["low"]
    volume = data["volume"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_medium = close.ewm(span=medium, adjust=False).mean()
    ema_long = close.ewm(span=long, adjust=False).mean()
    previous = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous).abs(), (low - previous).abs()], axis=1
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / max(5, fast), adjust=False).mean()
    candle_range = (high - low).clip(lower=1e-9)
    close_position = (close - low) / candle_range
    volume_average = volume.shift(1).rolling(max(5, range_window)).mean()
    weak_bar = (
        (close < data["open"])
        & (close_position < 0.42)
        & (volume > volume_average * 1.25)
    )
    upthrust = (
        (high > high.shift(1).rolling(max(4, range_window // 2)).max())
        & (close_position < 0.38)
        & (volume > volume_average * 1.20)
    )
    accumulation_bar = (
        (close >= data["open"])
        & (close_position > 0.60)
        & (volume > volume_average * 1.08)
    )
    count_window = min(10, len(data))
    distribution_bars = int((weak_bar | upthrust).tail(count_window).sum())
    accumulation_bars = int(accumulation_bar.tail(count_window).sum())

    close_now = _safe(close.iloc[-1])
    medium_now = _safe(ema_medium.iloc[-1], close_now)
    long_now = _safe(ema_long.iloc[-1], close_now)
    slope_lag = min(3, len(data) - 1)
    medium_before = _safe(ema_medium.iloc[-1 - slope_lag], medium_now)
    medium_slope = (medium_now - medium_before) / max(abs(medium_before), 1e-9)
    fast_now = _safe(ema_fast.iloc[-1], close_now)
    recent = data.tail(range_window)
    range_pct = (_safe(recent["high"].max()) - _safe(recent["low"].min())) / max(close_now, 1e-9)
    atr_pct = _safe(atr.iloc[-1]) / max(close_now, 1e-9)
    atr_prior = _safe(atr.iloc[-1 - slope_lag], _safe(atr.iloc[-1])) / max(_safe(close.iloc[-1 - slope_lag], close_now), 1e-9)
    base = range_pct <= base_limit and atr_pct <= atr_prior * 1.08
    volume_dry = _safe(volume.tail(min(5, len(data))).mean()) <= _safe(volume.tail(range_window).mean(), 1.0) * 0.88
    direction = np.sign(close.diff()).fillna(0)
    obv = (direction * volume).cumsum()
    obv_lag = min(max(3, range_window // 2), len(data) - 1)
    obv_change = _safe(obv.iloc[-1]) - _safe(obv.iloc[-1 - obv_lag])
    recent_rows = data.tail(count_window)
    up_volume = _safe(recent_rows.loc[recent_rows["close"] >= recent_rows["open"], "volume"].sum())
    down_volume = _safe(recent_rows.loc[recent_rows["close"] < recent_rows["open"], "volume"].sum())
    up_share = up_volume / max(up_volume + down_volume, 1.0)
    flow_positive = obv_change >= 0 and up_share >= 0.51
    flow_negative = obv_change < 0 and up_share < 0.46
    trend_up = close_now > medium_now and fast_now >= medium_now and medium_slope > 0
    trend_down = close_now < medium_now and fast_now < medium_now and medium_slope < 0
    structural_up = medium_slope >= 0 and close_now >= long_now * 0.97

    if trend_down and close_now < long_now and (flow_negative or distribution_bars >= 2):
        state = "MARKDOWN"
    elif distribution_bars >= 2 and (flow_negative or close_now < fast_now):
        state = "DISTRIBUTION"
    elif base and structural_up and volume_dry and distribution_bars <= 1:
        state = "REACCUMULATION"
    elif base and not trend_down and distribution_bars <= 1:
        state = "ACCUMULATION"
    elif trend_up and not flow_negative and distribution_bars <= 1:
        state = "MARKUP"
    else:
        state = "TRANSITION"

    base_scores = {
        "MARKUP": 84,
        "REACCUMULATION": 78,
        "ACCUMULATION": 69,
        "TRANSITION": 50,
        "DISTRIBUTION": 30,
        "MARKDOWN": 18,
    }
    score = base_scores[state]
    score += 6 if flow_positive else -7 if flow_negative else 0
    score += 4 if state in BASE_PHASES and volume_dry else 0
    score -= min(distribution_bars, 3) * 4
    score = int(round(_clamp(score)))
    confidence = int(
        _clamp(
            45
            + min(len(data) / max(minimum_bars * 1.8, 1.0), 1.0) * 28
            + (8 if state != "TRANSITION" else 0)
            + (7 if flow_positive or flow_negative else 0),
            30,
            94,
        )
    )
    notes: list[str] = []
    if volume_dry:
        notes.append("VOL_CONTRACTION")
    if distribution_bars:
        notes.append(f"DISTRIBUTION_BARS_{distribution_bars}")
    if accumulation_bars:
        notes.append(f"ACCUMULATION_BARS_{accumulation_bars}")
    if base:
        notes.append("BASE")
    trend = "UP" if trend_up else "DOWN" if trend_down else "SIDEWAYS"
    flow = "POSITIVE" if flow_positive else "NEGATIVE" if flow_negative else "NEUTRAL"
    return TimeframePhase(
        timeframe=timeframe,
        state=state,
        label=PHASE_LABELS[state],
        score=score,
        confidence=confidence,
        range_pct=round(range_pct * 100.0, 2),
        distribution_bars=distribution_bars,
        accumulation_bars=accumulation_bars,
        trend=trend,
        flow=flow,
        notes=notes,
    )


def _no_breakout() -> BreakoutDiagnosis:
    return BreakoutDiagnosis("NO_DATA", BREAKOUT_LABELS["NO_DATA"], "UNKNOWN", None, None, None, None, False, False, ["INSUFFICIENT_HISTORY"])


def diagnose_breakout(frame: pd.DataFrame | None, daily_phase: TimeframePhase | None = None) -> BreakoutDiagnosis:
    """Classify a recent breakout using confirmation, retest and failure evidence."""

    if frame is None or len(frame) < 45:
        return _no_breakout()
    data = frame.copy().reset_index(drop=True)
    close = data["close"]
    high = data["high"]
    low = data["low"]
    volume = data["volume"]
    resistance = high.shift(1).rolling(20).max()
    vol_average = volume.shift(1).rolling(20).mean()
    candle_range = (high - low).clip(lower=1e-9)
    close_position = (close - low) / candle_range
    closed_break = (
        (close > resistance * 1.008)
        & (close.shift(1) <= resistance.shift(1) * 1.01)
        & (close > data["open"])
        & (close_position >= 0.58)
        & (volume >= vol_average * 0.95)
    )
    wick_attempt = (
        (high > resistance * 1.01)
        & (close < resistance)
        & (close_position < 0.45)
        & (volume > vol_average * 1.20)
    )
    recent_start = max(20, len(data) - BREAKOUT_LOOKBACK_BARS)
    events = np.flatnonzero((closed_break | wick_attempt).iloc[recent_start:].fillna(False).to_numpy())
    close_now = _safe(close.iloc[-1])
    current_resistance = _safe(resistance.iloc[-1], close_now)
    if len(events) == 0:
        distance = (close_now / max(current_resistance, 1e-9) - 1.0) * 100.0
        state = "NEAR_BREAK" if -5.0 <= distance <= 2.0 else "NO_BREAKOUT"
        return BreakoutDiagnosis(
            state=state,
            label=BREAKOUT_LABELS[state],
            risk_level="MEDIUM" if state == "NEAR_BREAK" else "LOW",
            breakout_level=round(current_resistance, 2),
            event_age_bars=None,
            distance_to_level_pct=round(distance, 2),
            invalidation_price=round(current_resistance * 0.96, 2),
            failed_confirmed=False,
            reaccumulation=False,
            notes=[],
        )

    event_index = recent_start + int(events[-1])
    level = _safe(resistance.iloc[event_index], current_resistance)
    age = len(data) - 1 - event_index
    segment = data.iloc[event_index:].copy()
    segment_close = segment["close"]
    distance = (close_now / max(level, 1e-9) - 1.0) * 100.0
    below_count = int((segment_close.tail(3) < level * 0.985).sum())
    above_count = int((segment_close.tail(4) > level * 1.002).sum())
    lost_after_break = bool((segment_close.iloc[1:] < level * 0.985).any()) if len(segment) > 1 else False
    weak_distribution = (
        (segment["close"] < segment["open"])
        & ((segment["close"] - segment["low"]) / (segment["high"] - segment["low"]).clip(lower=1e-9) < 0.45)
        & (segment["volume"] > _safe(vol_average.iloc[event_index], 1.0) * 1.15)
    )
    distribution_bars = int(weak_distribution.sum())
    recent_five = data.tail(min(5, len(data)))
    tight = (_safe(recent_five["high"].max()) - _safe(recent_five["low"].min())) / max(close_now, 1e-9) <= 0.09
    volume_dry = _safe(recent_five["volume"].mean()) <= _safe(volume.tail(20).mean(), 1.0) * 0.88
    retest_touch = bool(_safe(low.tail(min(5, len(data))).min(), level) <= level * 1.015)
    current_is_wick_failure = bool(wick_attempt.iloc[event_index])
    phase_state = daily_phase.state if daily_phase else "TRANSITION"

    notes: list[str] = []
    if volume_dry:
        notes.append("RETEST_VOLUME_DRY")
    if tight:
        notes.append("POST_BREAK_TIGHT")
    if distribution_bars:
        notes.append(f"POST_BREAK_DISTRIBUTION_{distribution_bars}")
    if lost_after_break:
        notes.append("LOST_BREAKOUT_LEVEL")

    if current_is_wick_failure and age == 0:
        state = "FAILED_BREAK_CONFIRMED"
    elif close_now >= level * 1.002:
        if lost_after_break:
            state = "RECLAIMED_BREAK"
        elif age >= 1 and retest_touch and volume_dry:
            state = "HEALTHY_RETEST"
        elif above_count >= 2:
            state = "BREAKOUT_CONFIRMED"
        else:
            state = "BREAKOUT_UNCONFIRMED"
    elif -3.0 <= distance < 0.2 and tight and volume_dry and distribution_bars <= 1 and phase_state in BASE_PHASES | {"TRANSITION"}:
        state = "REACCUMULATION"
    elif distance <= -3.0 or (below_count >= 2 and distribution_bars >= 1):
        state = "FAILED_BREAK_CONFIRMED"
    else:
        state = "FAILED_BREAK_WATCH"

    risk_level = {
        "FAILED_BREAK_CONFIRMED": "HIGH",
        "FAILED_BREAK_WATCH": "HIGH",
        "BREAKOUT_UNCONFIRMED": "MEDIUM",
        "REACCUMULATION": "MEDIUM",
        "RECLAIMED_BREAK": "MEDIUM",
        "HEALTHY_RETEST": "LOW",
        "BREAKOUT_CONFIRMED": "LOW",
    }[state]
    return BreakoutDiagnosis(
        state=state,
        label=BREAKOUT_LABELS[state],
        risk_level=risk_level,
        breakout_level=round(level, 2),
        event_age_bars=age,
        distance_to_level_pct=round(distance, 2),
        invalidation_price=round(min(level * 0.965, _safe(segment["low"].min(), level)), 2),
        failed_confirmed=state == "FAILED_BREAK_CONFIRMED",
        reaccumulation=state == "REACCUMULATION",
        notes=notes,
    )


def analyze_market_structure(df: pd.DataFrame | None) -> MarketStructure:
    daily = _prepare(df)
    if daily is None:
        phases = {key: _no_phase(key) for key in ("1D", "1W", "1M")}
        breakout = _no_breakout()
        return MarketStructure("NO_DATA", OVERALL_LABELS["NO_DATA"], 0, 0, "WAIT", phases, breakout, ["INSUFFICIENT_HISTORY"])

    weekly = _resample(daily, "W-FRI")
    monthly = _resample(daily, pd.offsets.MonthEnd())
    phases = {
        "1D": _timeframe_phase(daily, "1D", fast=8, medium=21, long=50, range_window=20, base_limit=0.18, minimum_bars=70),
        "1W": _timeframe_phase(weekly, "1W", fast=5, medium=13, long=26, range_window=13, base_limit=0.30, minimum_bars=26),
        "1M": _timeframe_phase(monthly, "1M", fast=3, medium=6, long=10, range_window=6, base_limit=0.42, minimum_bars=10),
    }
    breakout = diagnose_breakout(daily, phases["1D"])
    valid = [item for item in phases.values() if item.state != "NO_DATA"]
    if not valid:
        return MarketStructure("NO_DATA", OVERALL_LABELS["NO_DATA"], 0, 0, "WAIT", phases, breakout, ["INSUFFICIENT_HISTORY"])

    states = {key: item.state for key, item in phases.items()}
    risk_votes = sum(state in RISK_PHASES for state in states.values())
    positive_votes = sum(state in POSITIVE_PHASES for state in states.values())
    base_votes = sum(state in BASE_PHASES for state in states.values())
    blockers: list[str] = []
    if breakout.failed_confirmed:
        blockers.append("FAILED_BREAK_CONFIRMED")
    if states["1W"] in RISK_PHASES:
        blockers.append("WEEKLY_STRUCTURE_RISK")
    if states["1M"] == "DISTRIBUTION":
        blockers.append("MONTHLY_DISTRIBUTION")

    if risk_votes >= 2 or states["1M"] == "DISTRIBUTION" or (states["1W"] == "DISTRIBUTION" and states["1D"] in RISK_PHASES):
        overall = "DISTRIBUTION"
        action = "KHONG_MUA_MOI"
    elif breakout.state in FAILED_BREAK_STATES | {"BREAKOUT_UNCONFIRMED"} or states["1W"] in RISK_PHASES or states["1M"] == "MARKDOWN":
        overall = "CAUTION"
        action = "CHO_RECLAIM"
    elif breakout.state == "REACCUMULATION":
        overall = "ACCUMULATION"
        action = "CHO_RECLAIM_TAI_TICH_LUY"
    elif positive_votes >= 2 and states["1W"] in POSITIVE_PHASES and states["1M"] not in RISK_PHASES:
        overall = "OPPORTUNITY"
        action = "CANH_MUA_CO_XAC_NHAN"
    elif base_votes >= 2 or (states["1W"] in BASE_PHASES and states["1M"] not in RISK_PHASES):
        overall = "ACCUMULATION"
        action = "THEO_DOI_TICH_LUY"
    else:
        overall = "CAUTION"
        action = "THEO_DOI"

    weights = {"1D": 0.25, "1W": 0.40, "1M": 0.35}
    available_weight = sum(weights[key] for key, item in phases.items() if item.state != "NO_DATA")
    score = sum(item.score * weights[key] for key, item in phases.items() if item.state != "NO_DATA") / max(available_weight, 1e-9)
    if breakout.state == "FAILED_BREAK_CONFIRMED":
        score = min(score - 18, 42)
    elif breakout.state == "FAILED_BREAK_WATCH":
        score = min(score - 10, 58)
    elif breakout.state in {"HEALTHY_RETEST", "RECLAIMED_BREAK", "BREAKOUT_CONFIRMED"}:
        score += 5
    if overall == "DISTRIBUTION":
        score = min(score, 39)
    confidence = int(round(sum(item.confidence for item in valid) / len(valid)))
    if len(valid) < 3:
        confidence = min(confidence, 58)
    return MarketStructure(
        overall_state=overall,
        label=OVERALL_LABELS[overall],
        score=int(round(_clamp(score))),
        confidence=confidence,
        action=action,
        timeframes=phases,
        breakout=breakout,
        blockers=blockers,
    )


def compact_line(structure: dict[str, Any] | MarketStructure | None) -> str:
    if structure is None:
        return "Trạng thái: chưa đủ dữ liệu"
    data = structure.to_dict() if isinstance(structure, MarketStructure) else structure
    frames = data.get("timeframes") or {}
    labels = [f"{key} {frames.get(key, {}).get('label', 'N/A')}" for key in ("1D", "1W", "1M")]
    return (
        f"Pha {' | '.join(labels)} => {data.get('label', 'N/A')} "
        f"{int(data.get('score') or 0)}/100"
    )
