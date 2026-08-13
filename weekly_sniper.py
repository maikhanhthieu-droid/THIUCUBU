"""Weekly accumulation structure scanner inspired by the companion Pine script.

This module deliberately scores *price/volume structure*, not intrinsic value.
The weekend engine combines it with separately sourced valuation and business
quality data before it can promote a stock to a high-conviction candidate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


MIN_WEEKLY_BARS = 104


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _safe(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return default if not np.isfinite(number) else number
    except (TypeError, ValueError):
        return default


def to_weekly(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    required = {"time", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        return None
    data = df[list(required)].copy()
    data["time"] = pd.to_datetime(data["time"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.dropna().sort_values("time")
    if data.empty:
        return None
    weekly = (
        data.resample("W-FRI", on="time")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    return weekly if not weekly.empty else None


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _true_range(frame: pd.DataFrame) -> pd.Series:
    previous = frame["close"].shift(1)
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)


@dataclass
class WeeklyStructure:
    score: int
    timing_score: int
    confidence: int
    state: str
    trigger: str
    discount_104w_pct: float
    risk_to_key_pct: float | None
    risk_reward: float | None
    rs_edge_13w_pct: float | None
    turnover_13w_bn: float
    base_weeks: int
    buy_zone_low: float | None
    buy_zone_high: float | None
    breakout_price: float | None
    invalidation_price: float | None
    components: dict[str, int]
    flags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def empty_structure(reason: str = "INSUFFICIENT_HISTORY") -> WeeklyStructure:
    return WeeklyStructure(
        score=0,
        timing_score=0,
        confidence=0,
        state="NO_DATA",
        trigger="WAIT",
        discount_104w_pct=0.0,
        risk_to_key_pct=None,
        risk_reward=None,
        rs_edge_13w_pct=None,
        turnover_13w_bn=0.0,
        base_weeks=0,
        buy_zone_low=None,
        buy_zone_high=None,
        breakout_price=None,
        invalidation_price=None,
        components={"discount": 0, "structure": 0, "base": 0, "flow": 0, "relative_strength": 0},
        flags=[reason],
    )


def analyze_weekly_structure(
    daily: pd.DataFrame | None,
    index_daily: pd.DataFrame | None = None,
    *,
    min_turnover_bn: float = 50.0,
) -> WeeklyStructure:
    weekly = to_weekly(daily)
    index_weekly = to_weekly(index_daily)
    if weekly is None or len(weekly) < MIN_WEEKLY_BARS:
        return empty_structure()

    frame = weekly.copy().reset_index(drop=True)
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    volume = frame["volume"]
    ema8 = close.ewm(span=8, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema40 = close.ewm(span=40, adjust=False).mean()
    atr14 = _true_range(frame).ewm(alpha=1 / 14, adjust=False).mean()
    rsi14 = _rsi(close)
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    macd_hist = macd - macd.ewm(span=9, adjust=False).mean()

    bar_range = (high - low).clip(lower=1e-9)
    close_pos = (close - low) / bar_range
    lower_wick = (pd.concat([frame["open"], close], axis=1).min(axis=1) - low) / bar_range
    mf_multiplier = ((close - low) - (high - close)) / bar_range
    cmf20 = (mf_multiplier * volume).rolling(20).sum() / volume.rolling(20).sum().replace(0, np.nan)
    obv = (np.sign(close.diff()).fillna(0) * volume).cumsum()

    last = len(frame) - 1
    close_now = _safe(close.iloc[last])
    high104 = _safe(high.tail(104).max(), close_now)
    low104 = _safe(low.tail(104).min(), close_now)
    discount = (high104 - close_now) / max(high104, 1e-9) * 100.0
    discount_fit = 0.0
    if 15.0 <= discount <= 62.0:
        # Best zone is deep enough to offer asymmetry, but not so deep that a
        # structural decline is rewarded automatically.
        discount_fit = 100.0 - min(abs(discount - 35.0) / 27.0 * 58.0, 58.0)

    low8 = _safe(low.tail(8).min(), close_now)
    low20 = _safe(low.tail(20).min(), close_now)
    high4_prev = _safe(high.shift(1).tail(4).max(), close_now)
    high13_prev = _safe(high.shift(1).tail(13).max(), high104)
    low10_prev = _safe(low.shift(1).tail(10).min(), low20)
    range20 = (_safe(high.tail(20).max()) - low20) / max(close_now, 1e-9)
    range8 = (_safe(high.tail(8).max()) - low8) / max(close_now, 1e-9)
    prior_range = (
        _safe(high.iloc[-20:-8].max()) - _safe(low.iloc[-20:-8].min())
    ) / max(_safe(close.iloc[-9], close_now), 1e-9)
    atr_pct = _safe(atr14.iloc[-1]) / max(close_now, 1e-9)
    atr_prev = _safe(atr14.iloc[-5], _safe(atr14.iloc[-1])) / max(_safe(close.iloc[-5], close_now), 1e-9)

    ema8_rising = _safe(ema8.iloc[-1]) > _safe(ema8.iloc[-2]) > _safe(ema8.iloc[-3])
    ema21_flat = _safe(ema21.iloc[-1]) >= _safe(ema21.iloc[-5]) * 0.96
    early_transition = close_now > _safe(ema8.iloc[-1]) and _safe(ema8.iloc[-1]) >= _safe(ema21.iloc[-1]) * 0.96
    not_broken = close_now >= _safe(ema40.iloc[-1]) * 0.76 and low8 >= low20 * 0.94
    not_extended = close_now <= _safe(ema21.iloc[-1]) * 1.12 and _safe(rsi14.iloc[-1]) <= 70
    base_contracting = range8 <= prior_range * 0.82 and range20 <= 0.32
    atr_contracting = atr_pct <= atr_prev * 1.02
    higher_low = _safe(low.tail(4).min()) > _safe(low.iloc[-8:-4].min())

    vol13 = _safe(volume.tail(13).mean(), 1.0)
    vol26 = _safe(volume.tail(26).mean(), vol13)
    volume_dry = vol13 <= vol26 * 0.92 or _safe(volume.tail(5).mean()) <= vol13 * 0.82
    volume_pulse = _safe(volume.iloc[-1]) >= vol13 * 1.15 and _safe(close_pos.iloc[-1]) >= 0.60 and close.iloc[-1] > frame["open"].iloc[-1]
    up_volume = _safe(volume.tail(13)[close.tail(13) >= frame["open"].tail(13)].sum())
    down_volume = _safe(volume.tail(13)[close.tail(13) < frame["open"].tail(13)].sum())
    up_share = up_volume / max(up_volume + down_volume, 1.0)
    cmf_now = _safe(cmf20.iloc[-1])
    cmf_rising = cmf_now > _safe(cmf20.iloc[-4], cmf_now)
    obv_up = _safe(obv.iloc[-1]) > _safe(obv.iloc[-6]) >= _safe(obv.iloc[-11])
    flow_ok = cmf_now >= -0.04 and up_share >= 0.51 and (cmf_rising or obv_up)

    rs_edge: float | None = None
    rs_rising = False
    if index_weekly is not None and len(index_weekly) >= 20:
        aligned = pd.merge(
            frame[["time", "close"]],
            index_weekly[["time", "close"]],
            on="time",
            how="inner",
            suffixes=("_stock", "_index"),
        )
        if len(aligned) >= 17:
            rs_series = (
                aligned["close_stock"] / aligned["close_stock"].shift(13)
                - aligned["close_index"] / aligned["close_index"].shift(13)
            ) * 100.0
            rs_edge = _safe(rs_series.iloc[-1])
            rs_rising = rs_edge > _safe(rs_series.iloc[-4], rs_edge)

    spring = low.iloc[-1] < low10_prev and close_now > low10_prev and _safe(close_pos.iloc[-1]) >= 0.62 and _safe(lower_wick.iloc[-1]) >= 0.20
    reclaim = close.iloc[-1] > ema8.iloc[-1] and close.iloc[-2] <= ema8.iloc[-2] and _safe(rsi14.iloc[-1]) >= 44
    early_break = close_now > high4_prev and close.iloc[-2] <= _safe(high.shift(2).tail(4).max(), high4_prev)
    pocket_pivot = volume_pulse and close_now > ema8.iloc[-1] and close_now < high13_prev
    momentum_kick = _safe(rsi14.iloc[-1]) > _safe(rsi14.iloc[-2]) and _safe(macd_hist.iloc[-1]) > _safe(macd_hist.iloc[-2])
    trigger = "SPRING" if spring else "RECLAIM" if reclaim else "EARLY_BREAK" if early_break else "POCKET_PIVOT" if pocket_pivot else "WAIT"

    price_unit = 1000.0 if close_now < 1000 else 1.0
    turnover13 = _safe((close.tail(13) * price_unit * volume.tail(13) / 1_000_000_000).mean())
    liquid = turnover13 >= min_turnover_bn and close_now >= 5.0
    protect_key = low8 - _safe(atr14.iloc[-1]) * 0.25
    risk_pct = (close_now - protect_key) / close_now * 100.0 if close_now > protect_key > 0 else None
    resistance = high13_prev if high13_prev > close_now * 1.03 else high104
    rr = None
    if risk_pct and risk_pct > 0:
        rr = (resistance - close_now) / max(close_now - protect_key, 1e-9)

    structure_component = int(_clamp(
        (26 if early_transition else 0)
        + (22 if ema8_rising else 0)
        + (18 if ema21_flat else 0)
        + (18 if not_broken else 0)
        + (16 if not_extended else 0)
    ))
    base_component = int(_clamp(
        (30 if base_contracting else 0)
        + (22 if atr_contracting else 0)
        + (20 if higher_low else 0)
        + (18 if volume_dry else 0)
        + (10 if range20 <= 0.24 else 0)
    ))
    flow_component = int(_clamp(
        (28 if cmf_now >= 0 else 16 if cmf_now >= -0.04 else 0)
        + (24 if cmf_rising else 0)
        + (24 if obv_up else 0)
        + (24 if up_share >= 0.57 else 12 if up_share >= 0.51 else 0)
    ))
    rs_component = 50 if rs_edge is None else int(_clamp(50 + rs_edge * 2.2 + (16 if rs_rising else -6)))
    timing = int(_clamp(
        (35 if trigger != "WAIT" else 8)
        + (25 if momentum_kick else 0)
        + (18 if risk_pct is not None and risk_pct <= 16 else 7 if risk_pct is not None and risk_pct <= 21 else 0)
        + (14 if rr is not None and rr >= 2.2 else 6 if rr is not None and rr >= 1.5 else 0)
        + (8 if not_extended else 0)
    ))
    components = {
        "discount": int(_clamp(discount_fit)),
        "structure": structure_component,
        "base": base_component,
        "flow": flow_component,
        "relative_strength": rs_component,
    }
    score = int(round(
        components["discount"] * 0.15
        + structure_component * 0.25
        + base_component * 0.22
        + flow_component * 0.20
        + rs_component * 0.10
        + timing * 0.08
    ))

    flags: list[str] = []
    if not liquid:
        flags.append("LOW_LIQUIDITY")
        score = min(score, 54)
    if discount > 62:
        flags.append("TOO_DEEP_FALL")
        score = min(score, 58)
    if not not_broken:
        flags.append("BROKEN_STRUCTURE")
        score = min(score, 49)
    if not flow_ok:
        flags.append("FLOW_UNCONFIRMED")
        score = min(score, 69)
    if risk_pct is None or risk_pct > 21:
        flags.append("RISK_TOO_WIDE")
        score = min(score, 64)
    if rr is None or rr < 1.5:
        flags.append("RR_WEAK")
        score = min(score, 69)
    if rs_edge is not None and (rs_edge < -6 or not rs_rising):
        flags.append("RS_WEAK")
        score = min(score, 72)
    score = int(_clamp(score, 0, 97))

    qualified_structure = early_transition and ema8_rising and ema21_flat and not_broken and base_contracting
    if qualified_structure and flow_ok and trigger != "WAIT" and momentum_kick and score >= 76:
        state = "EARLY_MARKUP"
    elif qualified_structure and flow_ok and score >= 70:
        state = "READY_TO_ACCUMULATE"
    elif base_contracting and (flow_ok or volume_dry):
        state = "PREP_BASE"
    elif discount_fit >= 60:
        state = "DISCOUNT_WATCH"
    else:
        state = "NO_SETUP"

    confidence = int(_clamp(
        45
        + min(len(frame), 156) / 156 * 16
        + (10 if index_weekly is not None and rs_edge is not None else 0)
        + (10 if liquid else 0)
        + (10 if flow_ok else 0)
        + (8 if trigger != "WAIT" else 2)
        - len(flags) * 5,
        20,
        96,
    ))
    base_weeks = 8 if range8 <= 0.18 else 13 if range20 <= 0.28 else 20
    buy_low = max(protect_key * 1.03, _safe(ema8.iloc[-1]) * 0.98)
    buy_high = min(close_now * 1.03, _safe(ema21.iloc[-1]) * 1.06)
    if buy_low > buy_high:
        buy_low, buy_high = min(buy_low, close_now), max(buy_high, close_now)

    return WeeklyStructure(
        score=score,
        timing_score=timing,
        confidence=confidence,
        state=state,
        trigger=trigger,
        discount_104w_pct=round(discount, 2),
        risk_to_key_pct=round(risk_pct, 2) if risk_pct is not None else None,
        risk_reward=round(rr, 2) if rr is not None else None,
        rs_edge_13w_pct=round(rs_edge, 2) if rs_edge is not None else None,
        turnover_13w_bn=round(turnover13, 2),
        base_weeks=base_weeks,
        buy_zone_low=round(buy_low, 2),
        buy_zone_high=round(buy_high, 2),
        breakout_price=round(high4_prev, 2),
        invalidation_price=round(protect_key, 2),
        components=components,
        flags=flags,
    )
