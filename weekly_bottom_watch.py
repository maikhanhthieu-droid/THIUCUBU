"""Advisory weekly two/three-bottom radar for the weekend engine.

The radar is intentionally earlier and more tolerant than the weekend
conviction gate.  It may suggest a small probe, but it never marks a symbol as
high conviction and never overrides a broken weekly structure or failed break.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import os
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

import technical_features
import weekly_sniper


SCHEMA_VERSION = "thieucubu.weekly_bottom_watch.v3"
SCORE_VERSION = "thieucubu.weekly_bottom_watch.score.v3"
MAX_SCORE = 100


def _safe(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return default if not math.isfinite(number) else number
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = MAX_SCORE) -> float:
    return max(low, min(high, value))


def _mfi(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    raw_flow = typical * frame["volume"]
    direction = typical.diff()
    positive = raw_flow.where(direction > 0, 0.0).rolling(period).sum()
    negative = raw_flow.where(direction < 0, 0.0).rolling(period).sum().abs()
    ratio = positive / negative.replace(0, np.nan)
    return (100 - 100 / (1 + ratio)).fillna(50.0).clip(0, 100)


def _weekly_flow(
    frame: pd.DataFrame,
    pivot_indices: Iterable[int] | None = None,
) -> dict[str, Any]:
    close = frame["close"]
    volume = frame["volume"]
    spread = (frame["high"] - frame["low"]).replace(0, np.nan)
    multiplier = ((close - frame["low"]) - (frame["high"] - close)) / spread
    cmf = (multiplier.fillna(0.0) * volume).rolling(20).sum() / volume.rolling(20).sum().replace(0, np.nan)
    obv = (np.sign(close.diff()).fillna(0.0) * volume).cumsum()
    mfi = _mfi(frame)

    cmf_now = _safe(cmf.iloc[-1])
    cmf_prev = _safe(cmf.iloc[-5], cmf_now)
    mfi_now = _safe(mfi.iloc[-1], 50.0)
    mfi_prev = _safe(mfi.iloc[-5], mfi_now)
    obv_now = _safe(obv.iloc[-1])
    obv_8 = _safe(obv.iloc[-9], obv_now)
    obv_16 = _safe(obv.iloc[-17], obv_8)
    obv_up = obv_now > obv_8 >= obv_16
    obv_improving = obv_now > obv_8
    cmf_rising = cmf_now > cmf_prev + 0.015
    mfi_rising = mfi_now > mfi_prev + 2

    score = 0
    signals: list[str] = []
    if obv_up:
        score += 30
        signals.append("OBV tuần tăng bền")
        obv_state = "TĂNG"
    elif obv_improving:
        score += 18
        signals.append("OBV tuần đang cải thiện")
        obv_state = "CẢI THIỆN"
    else:
        obv_state = "YẾU"
    if cmf_now >= 0.08:
        score += 28
        signals.append(f"CMF tuần dương {cmf_now:+.2f}")
    elif cmf_now >= 0:
        score += 20
        signals.append(f"CMF tuần giữ dương {cmf_now:+.2f}")
    elif cmf_now >= -0.05:
        score += 8
    if cmf_rising:
        score += 10
        signals.append("CMF tuần đi lên")
    if 45 <= mfi_now <= 78:
        score += 20
        signals.append(f"MFI tuần khỏe {mfi_now:.0f}")
    elif 35 <= mfi_now < 45:
        score += 10
    if mfi_rising:
        score += 9
        signals.append("MFI tuần cải thiện")
    divergence_score = 0
    divergence_signals: list[str] = []
    price_change_pct = None
    anchors = [
        int(index)
        for index in (pivot_indices or [])
        if 0 <= int(index) < len(frame)
    ]
    if len(anchors) >= 2:
        first, second = anchors[-2], anchors[-1]
        first_price = max(_safe(close.iloc[first]), 1e-9)
        price_change = _safe(close.iloc[second]) / first_price - 1.0
        price_change_pct = price_change * 100.0
        # Only call this divergence when price actually made a lower low.
        if price_change <= -0.008:
            traded = max(_safe(volume.iloc[first + 1 : second + 1].sum()), 1.0)
            obv_pressure = (_safe(obv.iloc[second]) - _safe(obv.iloc[first])) / traded
            first_cmf = _safe(cmf.iloc[max(0, first - 1) : first + 2].mean())
            second_cmf = _safe(cmf.iloc[max(0, second - 1) : second + 2].mean())
            first_mfi = _safe(mfi.iloc[max(0, first - 1) : first + 2].mean(), 50.0)
            second_mfi = _safe(mfi.iloc[max(0, second - 1) : second + 2].mean(), 50.0)
            if obv_pressure >= -0.08:
                divergence_score += 5
                divergence_signals.append("Giá tạo đáy thấp hơn nhưng OBV đi ngang/tăng")
            if second_cmf >= first_cmf - 0.025:
                divergence_score += 5
                divergence_signals.append("Giá giảm nhưng CMF không giảm theo")
            if second_mfi >= first_mfi - 2.5:
                divergence_score += 5
                divergence_signals.append("Giá giảm nhưng MFI giữ ngang/tăng")
    return {
        "score": int(round(_clamp(score, 0, 97))),
        "obv_state": obv_state,
        "cmf20": round(cmf_now, 3),
        "mfi14": round(mfi_now, 1),
        "signals": signals[:4],
        "divergence_score": divergence_score,
        "divergence_signals": divergence_signals,
        "price_change_between_bottoms_pct": (
            round(price_change_pct, 2) if price_change_pct is not None else None
        ),
    }


def _weekly_structure_state(packet: Mapping[str, Any]) -> tuple[Any, str, int, list[str]]:
    structure = packet.get("weekly")
    if structure is None:
        structure = weekly_sniper.analyze_weekly_structure(packet.get("df"), None)
    state = str(getattr(structure, "state", "NO_DATA"))
    score = int(getattr(structure, "score", 0) or 0)
    flags = list(getattr(structure, "flags", []) or [])
    return structure, state, score, flags


def _one_week_state(packet: Mapping[str, Any]) -> str:
    structure = packet.get("market_structure")
    timeframes = getattr(structure, "timeframes", None)
    if isinstance(timeframes, Mapping):
        weekly = timeframes.get("1W")
        return str(getattr(weekly, "state", "NO_DATA")).upper()
    if isinstance(structure, Mapping):
        frames = structure.get("timeframes") if isinstance(structure.get("timeframes"), Mapping) else {}
        weekly = frames.get("1W") if isinstance(frames.get("1W"), Mapping) else {}
        return str(weekly.get("state") or "NO_DATA").upper()
    return "NO_DATA"


def calculate_watch_score(
    *,
    weekly_smiio_bottom_count: int,
    daily_smiio_bottom_count: int,
    momentum_points: int,
    flow_divergence_points: int,
    discount_structure_points: int,
) -> tuple[int, dict[str, int]]:
    """Return the explicit 100-point early-bottom score and its components."""

    weekly_points = 50 if weekly_smiio_bottom_count >= 3 else 40 if weekly_smiio_bottom_count == 2 else 0
    daily_points = 10 if daily_smiio_bottom_count >= 2 else 5 if daily_smiio_bottom_count == 1 else 0
    components = {
        "weekly_smiio_bottoms": weekly_points,
        "daily_smiio_bottoms": daily_points,
        "momentum_divergence": int(_clamp(momentum_points, 0, 15)),
        "money_flow_divergence": int(_clamp(flow_divergence_points, 0, 15)),
        "discount_structure": int(_clamp(discount_structure_points, 0, 10)),
    }
    return int(_clamp(sum(components.values()))), components


def _momentum_score(
    weekly: Mapping[str, Any],
    daily: Mapping[str, Any],
) -> tuple[int, list[str]]:
    score = 0
    signals: list[str] = []
    macd_state = str(weekly.get("macd_state") or "NO_DATA")
    smiio_state = str(weekly.get("smiio_state") or "NO_DATA")
    macd_points = {
        "BULL_CROSS_NEGATIVE": 6,
        "PRE_CROSS_NEGATIVE": 5,
        "EARLY_TURN_NEGATIVE": 4,
        "RECOVERING_NEGATIVE": 5,
        "BULL_CROSS_POSITIVE": 3,
        "PRE_CROSS_POSITIVE": 3,
        "IMPROVING_NEGATIVE": 2,
    }.get(macd_state, 0)
    smiio_points = {
        "ZERO_CROSS_UP": 4,
        "TURNING_UP_NEGATIVE": 5,
        "EARLY_TURN_NEGATIVE": 4,
        "ACCELERATING_POSITIVE": 2,
    }.get(smiio_state, 0)
    score += macd_points + smiio_points
    if macd_points:
        signals.append(f"MACD tuần {macd_state}")
    if smiio_points:
        signals.append(f"SMIIO tuần {smiio_state}")
    if weekly.get("smiio_bullish_divergence"):
        score += 3
        signals.append("SMIIO tuần phân kỳ tăng")
    if weekly.get("macd_bullish_divergence"):
        score += 3
        signals.append(f"MACD phân kỳ tăng {weekly.get('macd_zone', 'NO_DATA')}")
    if weekly.get("rsi_bullish_divergence"):
        score += 2
        signals.append("RSI tuần phân kỳ tăng")
    daily_state = str(daily.get("macd_state") or "NO_DATA")
    if int(daily.get("smiio_bottom_count") or 0) > 0 and daily_state in {
        "BULL_CROSS_NEGATIVE",
        "PRE_CROSS_NEGATIVE",
        "EARLY_TURN_NEGATIVE",
        "RECOVERING_NEGATIVE",
    }:
        score += 1
        signals.append("MACD ngày xác nhận timing")
    return int(_clamp(score, 0, 15)), signals[:5]


def _discount_structure_score(
    discount: float,
    target: float,
    structure_state: str,
) -> int:
    minimum = max(18.0, min(target * 0.65, 32.0))
    if discount >= target:
        discount_points = 5
    elif discount >= max(minimum, target * 0.85):
        discount_points = 4
    elif discount >= minimum:
        discount_points = 3
    else:
        discount_points = 0
    structure_points = {
        "EARLY_MARKUP": 5,
        "READY_TO_ACCUMULATE": 5,
        "PREP_BASE": 5,
        "NO_SETUP": 3,
        "NO_DATA": 2,
    }.get(structure_state, 2)
    return int(_clamp(discount_points + structure_points, 0, 10))


def _oscillator_levels(
    frame: pd.DataFrame,
    pivot_indices: Iterable[int],
) -> tuple[float | None, float | None]:
    indices = [int(index) for index in pivot_indices if 0 <= int(index) < len(frame)]
    if not indices:
        return None, None
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = max(_safe(true_range.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]), 0.0)
    floor = min(
        _safe(frame["low"].iloc[max(0, index - 1) : min(len(frame), index + 2)].min())
        for index in indices[-3:]
    )
    invalidation = floor - max(atr * 0.65, floor * 0.012)
    latest = indices[-1]
    trigger_start = max(latest, len(frame) - 6)
    trigger = _safe(frame["high"].iloc[trigger_start:].max()) * 1.002
    return round(trigger, 2), round(invalidation, 2)


@dataclass(frozen=True)
class WeeklyBottomCandidate:
    symbol: str
    sector: str
    close: float
    score: int
    score_version: str
    score_components: dict[str, int]
    confidence: int
    label: str
    stage: str
    action: str
    probe_fraction: float
    bottom_count: int
    oscillator_type: str
    weekly_smiio_bottom_count: int
    daily_smiio_bottom_count: int
    weekly_smiio_profile: dict[str, int]
    daily_smiio_profile: dict[str, int]
    weekly_smi_bottom_count: int
    daily_smi_bottom_count: int
    price_bottom_count: int
    discount_104w_pct: float
    target_discount_pct: float
    technical_score: int
    flow_score: int
    flow_divergence_score: int
    structure_score: int
    structure_state: str
    rsi: float | None
    macd_hist_pct: float | None
    smi: float | None
    smiio: float | None
    ergodic: float | None
    ergodic_signal: float | None
    weekly_smiio_state: str
    daily_smiio_state: str
    weekly_smi_state: str
    daily_smi_state: str
    weekly_macd_state: str
    daily_macd_state: str
    macd_zone: str
    macd_divergence_state: str
    smiio_zone: str
    smiio_divergence_state: str
    obv_state: str
    cmf20: float
    mfi14: float
    trigger_price: float | None
    invalidation_price: float | None
    risk_to_invalidation_pct: float | None
    pivot_dates: list[str]
    pivot_prices: list[float]
    weekly_smiio_pivot_dates: list[str]
    weekly_smiio_pivot_values: list[float]
    daily_smiio_pivot_dates: list[str]
    weekly_smi_pivot_dates: list[str]
    weekly_smi_pivot_values: list[float]
    daily_smi_pivot_dates: list[str]
    flow_divergence_signals: list[str]
    signals: list[str]
    risk_note: str
    price_data_source: str | None = None
    history_backfill_source: str | None = None
    cache_status: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_packet(packet: Mapping[str, Any]) -> WeeklyBottomCandidate | None:
    """Return a 1W-primary, 1D-confirmed early-bottom candidate."""

    symbol = str(packet.get("symbol") or "").upper().strip()
    daily_frame = packet.get("df")
    weekly = weekly_sniper.to_weekly(daily_frame)
    if not symbol or weekly is None or len(weekly) < 80:
        return None
    technical = technical_features.analyze_technical_watch(weekly)
    weekly_osc = technical_features.analyze_smiio_bottoms(
        weekly,
        timeframe="1W",
    )
    daily_osc = technical_features.analyze_smiio_bottoms(
        daily_frame,
        timeframe="1D",
    )
    bottom_count = min(3, int(weekly_osc.get("smiio_bottom_count") or 0))
    daily_bottom_count = min(3, int(daily_osc.get("smiio_bottom_count") or 0))
    if (
        bottom_count < 2
        or not weekly_osc.get("momentum_ready")
        or technical.get("risk_dominant")
    ):
        return None

    weekly_structure, structure_state, structure_score, flags = _weekly_structure_state(packet)
    one_week_state = _one_week_state(packet)
    tech_result = packet.get("tech")
    if bool(getattr(tech_result, "failed_break", False)):
        return None
    if one_week_state in {"DISTRIBUTION", "MARKDOWN"}:
        return None
    if {"BROKEN_STRUCTURE", "TOO_DEEP_FALL", "LOW_LIQUIDITY"}.intersection(flags):
        return None

    close = _safe(weekly["close"].iloc[-1])
    discount = _safe(getattr(weekly_structure, "discount_104w_pct", 0.0))
    if discount <= 0:
        high104 = _safe(weekly["high"].tail(104).max(), close)
        discount = (high104 - close) / max(high104, 1e-9) * 100.0
    target = max(_safe(getattr(tech_result, "target_discount_pct", 25.0), 25.0), 10.0)
    minimum_discount = max(18.0, min(target * 0.65, 32.0))
    if discount < minimum_discount:
        return None

    smiio_indices = list(weekly_osc.get("smiio_pivot_indices") or [])
    flow = _weekly_flow(weekly, smiio_indices)
    technical_score = int(technical.get("score") or 0)
    flow_score = int(flow["score"])
    flow_divergence_score = int(flow.get("divergence_score") or 0)
    momentum_score, momentum_signals = _momentum_score(weekly_osc, daily_osc)
    discount_structure_score = _discount_structure_score(
        discount,
        target,
        structure_state,
    )
    score, score_components = calculate_watch_score(
        weekly_smiio_bottom_count=bottom_count,
        daily_smiio_bottom_count=daily_bottom_count,
        momentum_points=momentum_score,
        flow_divergence_points=flow_divergence_score,
        discount_structure_points=discount_structure_score,
    )
    if score < 58:
        return None

    smiio_trigger, smiio_invalidation = _oscillator_levels(weekly, smiio_indices)
    trigger = technical.get("trigger_price") or smiio_trigger
    invalidation = technical.get("invalidation_price") or smiio_invalidation
    risk_pct = None
    if close > 0 and _safe(invalidation) > 0 and close > _safe(invalidation):
        risk_pct = (close - _safe(invalidation)) / close * 100.0
    if (
        score >= 82
        and daily_bottom_count >= 1
        and (flow_divergence_score >= 5 or flow_score >= 58)
        and risk_pct is not None
        and risk_pct <= 16
        and structure_state in {"EARLY_MARKUP", "READY_TO_ACCUMULATE", "PREP_BASE"}
    ):
        action = "CANH THĂM DÒ 1/5 KHI GIỮ ĐÁY"
        probe_fraction = 0.20
    elif (
        score >= 70
        and daily_bottom_count >= 1
        and risk_pct is not None
        and risk_pct <= 20
    ):
        action = "CANH THĂM DÒ 1/6 SAU XÁC NHẬN"
        probe_fraction = 0.15
    else:
        action = "CANH ME — CHƯA MUA"
        probe_fraction = 0.0

    confidence = int(
        _clamp(
            score * 0.45
            + _safe(technical.get("confidence")) * 0.20
            + _safe(getattr(weekly_structure, "confidence", 0)) * 0.25
            + (10 if daily_bottom_count >= 1 else 4),
            30,
            94,
        )
    )
    signals = [
        f"Chiết khấu 104W {discount:.1f}%",
        *[str(value) for value in weekly_osc.get("signals", [])[:3]],
        *[str(value) for value in daily_osc.get("signals", [])[:2]],
        *momentum_signals[:2],
        *[str(value) for value in flow.get("divergence_signals", [])[:3]],
    ]
    risk_note = (
        f"Thủng {float(invalidation):.2f}: hủy kịch bản; không bình quân giá xuống"
        if invalidation is not None
        else "Chưa có mức vô hiệu rõ: chỉ theo dõi"
    )
    return WeeklyBottomCandidate(
        symbol=symbol,
        sector=str(packet.get("sector") or "Other"),
        close=round(close, 2),
        score=score,
        score_version=SCORE_VERSION,
        score_components=score_components,
        confidence=confidence,
        label=f"W-PRE-SMIIO-{bottom_count}",
        stage=(
            "CONFIRMED"
            if str(weekly_osc.get("smiio_state")) == "ZERO_CROSS_UP"
            and str(weekly_osc.get("macd_state"))
            in {"BULL_CROSS_NEGATIVE", "RECOVERING_NEGATIVE"}
            and daily_bottom_count >= 1
            else "FORMING_STRONG"
            if bottom_count >= 3
            or weekly_osc.get("smiio_bullish_divergence")
            or weekly_osc.get("macd_bullish_divergence")
            or weekly_osc.get("rsi_bullish_divergence")
            else "FORMING"
        ),
        action=action,
        probe_fraction=probe_fraction,
        bottom_count=bottom_count,
        oscillator_type="SMI_ERGODIC_OSCILLATOR",
        weekly_smiio_bottom_count=bottom_count,
        daily_smiio_bottom_count=daily_bottom_count,
        weekly_smiio_profile={
            str(key): int(value)
            for key, value in dict(weekly_osc.get("profile") or {}).items()
        },
        daily_smiio_profile={
            str(key): int(value)
            for key, value in dict(daily_osc.get("profile") or {}).items()
        },
        # Compatibility aliases retained for downstream readers of v2.
        weekly_smi_bottom_count=bottom_count,
        daily_smi_bottom_count=daily_bottom_count,
        price_bottom_count=int(technical.get("bottom_count") or 0),
        discount_104w_pct=round(discount, 2),
        target_discount_pct=round(target, 2),
        technical_score=technical_score,
        flow_score=flow_score,
        flow_divergence_score=flow_divergence_score,
        structure_score=structure_score,
        structure_state=structure_state,
        rsi=_safe(weekly_osc.get("rsi")) if weekly_osc.get("rsi") is not None else None,
        macd_hist_pct=(
            _safe(weekly_osc.get("macd_hist_pct"))
            if weekly_osc.get("macd_hist_pct") is not None
            else None
        ),
        smi=(
            _safe(technical.get("smi"))
            if technical.get("smi") is not None
            else None
        ),
        smiio=(
            _safe(weekly_osc.get("smiio_value"))
            if weekly_osc.get("smiio_value") is not None
            else None
        ),
        ergodic=(
            _safe(weekly_osc.get("ergodic_value"))
            if weekly_osc.get("ergodic_value") is not None
            else None
        ),
        ergodic_signal=(
            _safe(weekly_osc.get("ergodic_signal"))
            if weekly_osc.get("ergodic_signal") is not None
            else None
        ),
        weekly_smiio_state=str(weekly_osc.get("smiio_state") or "NO_DATA"),
        daily_smiio_state=str(daily_osc.get("smiio_state") or "NO_DATA"),
        weekly_smi_state=str(weekly_osc.get("smiio_state") or "NO_DATA"),
        daily_smi_state=str(daily_osc.get("smiio_state") or "NO_DATA"),
        weekly_macd_state=str(weekly_osc.get("macd_state") or "NO_DATA"),
        daily_macd_state=str(daily_osc.get("macd_state") or "NO_DATA"),
        macd_zone=str(weekly_osc.get("macd_zone") or "NO_DATA"),
        macd_divergence_state=str(
            weekly_osc.get("macd_divergence_state") or "NONE"
        ),
        smiio_zone=str(weekly_osc.get("smiio_zone") or "NO_DATA"),
        smiio_divergence_state=str(
            weekly_osc.get("smiio_divergence_state") or "NONE"
        ),
        obv_state=str(flow["obv_state"]),
        cmf20=float(flow["cmf20"]),
        mfi14=float(flow["mfi14"]),
        trigger_price=round(_safe(trigger), 2) if trigger is not None else None,
        invalidation_price=round(_safe(invalidation), 2) if invalidation is not None else None,
        risk_to_invalidation_pct=round(risk_pct, 2) if risk_pct is not None else None,
        pivot_dates=[
            str(value) for value in weekly_osc.get("smiio_pivot_dates", []) if value
        ],
        pivot_prices=[
            round(_safe(value), 2)
            for value in weekly_osc.get("smiio_pivot_prices", [])
        ],
        weekly_smiio_pivot_dates=[
            str(value)
            for value in weekly_osc.get("smiio_pivot_dates", [])
            if value
        ],
        weekly_smiio_pivot_values=[
            round(_safe(value), 3)
            for value in weekly_osc.get("smiio_pivot_values", [])
        ],
        daily_smiio_pivot_dates=[
            str(value)
            for value in daily_osc.get("smiio_pivot_dates", [])
            if value
        ],
        weekly_smi_pivot_dates=[
            str(value)
            for value in weekly_osc.get("smiio_pivot_dates", [])
            if value
        ],
        weekly_smi_pivot_values=[
            round(_safe(value), 3)
            for value in weekly_osc.get("smiio_pivot_values", [])
        ],
        daily_smi_pivot_dates=[
            str(value)
            for value in daily_osc.get("smiio_pivot_dates", [])
            if value
        ],
        flow_divergence_signals=[
            str(value) for value in flow.get("divergence_signals", [])
        ],
        signals=list(dict.fromkeys(signals))[:8],
        risk_note=risk_note,
        price_data_source=(
            str(packet.get("price_data_source") or "") or None
        ),
        history_backfill_source=(
            str(packet.get("history_backfill_source") or "") or None
        ),
        cache_status=str(packet.get("cache_status") or "unknown"),
    )


def rank_packets(
    packets: Iterable[Mapping[str, Any]],
    *,
    limit: int | None = None,
) -> list[WeeklyBottomCandidate]:
    maximum = limit if limit is not None else int(os.getenv("WEEKEND_BOTTOM_WATCH_LIMIT", "5"))
    maximum = min(5, max(1, maximum))
    candidates = [candidate for packet in packets if (candidate := analyze_packet(packet)) is not None]
    candidates.sort(
        key=lambda item: (
            item.score,
            item.flow_divergence_score,
            item.daily_smiio_bottom_count,
            item.confidence,
            item.discount_104w_pct,
        ),
        reverse=True,
    )
    return candidates[:maximum]


_STATE_LABELS = {
    "BULL_CROSS_NEGATIVE": "âm vừa cắt lên",
    "BULL_CROSS_POSITIVE": "dương vừa cắt lên",
    "CONVERGING_NEGATIVE": "âm đang hội tụ",
    "PRE_CROSS_NEGATIVE": "âm, gap co — sắp cắt",
    "PRE_CROSS_POSITIVE": "dương, gap co — sắp cắt",
    "EARLY_TURN_NEGATIVE": "âm vừa ngóc lên",
    "RECOVERING_NEGATIVE": "âm đã cắt, đang hồi",
    "CURLING_UP_BELOW_SIGNAL": "dưới signal, cong lên",
    "RISING_NEGATIVE": "âm đang hướng lên",
    "RISING_POSITIVE": "dương đang tăng",
    "RISING_UNCONFIRMED": "quay lên, chưa xác nhận",
    "IMPROVING_NEGATIVE": "âm đang cải thiện",
    "BULLISH_POSITIVE": "dương còn khỏe",
    "BEAR_CROSS_NEGATIVE": "âm vừa cắt xuống",
    "BEAR_CROSS_POSITIVE": "dương vừa cắt xuống",
    "BEAR_CROSS": "vừa cắt xuống",
    "WEAKENING_NEGATIVE": "âm còn yếu",
    "WEAKENING_POSITIVE": "dương đang yếu",
    "FALLING": "đang giảm",
    "ZERO_CROSS_UP": "vừa cắt lên 0",
    "TURNING_UP_NEGATIVE": "âm, cong lên 2 nhịp",
    "ACCELERATING_POSITIVE": "dương đang tăng tốc",
    "FADING_POSITIVE": "dương đang chậm lại",
    "ZERO_CROSS_DOWN": "vừa cắt xuống 0",
    "FALLING_NEGATIVE": "âm còn giảm",
    "NO_DATA": "chưa đủ dữ liệu",
}


def _state_label(value: str) -> str:
    return _STATE_LABELS.get(value, value.replace("_", " ").lower())


def format_line(item: WeeklyBottomCandidate) -> str:
    trigger = f"KT {item.trigger_price:.2f}" if item.trigger_price is not None else "KT chờ"
    invalidation = f"HV {item.invalidation_price:.2f}" if item.invalidation_price is not None else "HV chưa rõ"
    rsi = f"{item.rsi:.0f}" if item.rsi is not None else "-"
    smiio = f"{item.smiio:+.3f}" if item.smiio is not None else "-"
    macd = f"{item.macd_hist_pct:+.3f}%" if item.macd_hist_pct is not None else "-"
    divergence = {
        "BULLISH_NEGATIVE": " · PK MACD tăng vùng âm",
        "BULLISH_POSITIVE": " · PK MACD tăng vùng dương",
    }.get(item.macd_divergence_state, "")
    smiio_divergence = {
        "BULLISH_NEGATIVE": " · PK SMIIO tăng vùng âm",
        "BULLISH_POSITIVE": " · PK SMIIO tăng vùng dương",
    }.get(item.smiio_divergence_state, "")
    parts = item.score_components
    breakdown = (
        f"W-SMIIO {parts.get('weekly_smiio_bottoms', 0)}/50 · "
        f"D-SMIIO {parts.get('daily_smiio_bottoms', 0)}/10 · "
        f"ĐL {parts.get('momentum_divergence', 0)}/15 · "
        f"DT {parts.get('money_flow_divergence', 0)}/15 · "
        f"CK/CT {parts.get('discount_structure', 0)}/10"
    )
    return (
        f"`{item.symbol}` {item.label} · {item.score}/100 | {item.action} | Giá {item.close:.2f}\n"
        f"{breakdown} | MACD W {_state_label(item.weekly_macd_state)} / "
        f"D {_state_label(item.daily_macd_state)}{divergence} · "
        f"SMIIO W {_state_label(item.weekly_smiio_state)} / "
        f"D {_state_label(item.daily_smiio_state)}{smiio_divergence}\n"
        f"DD104W {item.discount_104w_pct:.1f}/{item.target_discount_pct:.0f}% | "
        f"RSI {rsi} · SMIIO {smiio} · MACDh {macd} | "
        f"OBV {item.obv_state} · CMF {item.cmf20:+.2f} · MFI {item.mfi14:.0f} | "
        f"{trigger} · {invalidation}"
    )


def payload(candidates: Iterable[WeeklyBottomCandidate], updated_at: str) -> dict[str, Any]:
    values = list(candidates)
    maximum = min(5, max(1, int(os.getenv("WEEKEND_BOTTOM_WATCH_LIMIT", "5"))))
    return {
        "schema_version": SCHEMA_VERSION,
        "score_version": SCORE_VERSION,
        "updated_at": updated_at,
        "policy": {
            "advisory_only": True,
            "max_candidates": maximum,
            "requires": [
                "weekly_SMIIO_2_or_3_bottoms",
                "deep_discount",
                "weekly_momentum_turning_or_divergence",
            ],
            "score_100": {
                "weekly_SMIIO_bottoms": "2=40, 3=50",
                "daily_SMIIO_bottoms": "1=5, 2_or_3=10",
                "momentum_divergence": 15,
                "money_flow_divergence": 15,
                "discount_structure": 10,
            },
            "smiio_profiles": {
                "1W": "5/20/5_standard",
                "1D": "3/13/3_sensitive",
            },
            "money_flow_divergence": "price_lower_low_with_OBV_CMF_MFI_flat_or_rising",
            "never_average_below_invalidation": True,
        },
        "candidates": [item.to_dict() for item in values[:maximum]],
    }
