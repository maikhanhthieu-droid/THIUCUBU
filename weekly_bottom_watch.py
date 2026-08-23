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


SCHEMA_VERSION = "thieucubu.weekly_bottom_watch.v1"
MAX_SCORE = 97


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


def _weekly_flow(frame: pd.DataFrame) -> dict[str, Any]:
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
    return {
        "score": int(round(_clamp(score))),
        "obv_state": obv_state,
        "cmf20": round(cmf_now, 3),
        "mfi14": round(mfi_now, 1),
        "signals": signals[:4],
    }


def _discount_score(discount: float, target: float) -> int:
    minimum = max(18.0, min(target * 0.65, 32.0))
    if discount < minimum:
        return int(_clamp(discount / max(minimum, 1.0) * 52))
    if discount <= 42:
        return int(_clamp(62 + (discount - minimum) / max(42 - minimum, 1.0) * 35))
    # A very deep fall is not rewarded without limit.
    return int(_clamp(97 - (discount - 42) * 1.7, 45, 97))


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


@dataclass(frozen=True)
class WeeklyBottomCandidate:
    symbol: str
    sector: str
    close: float
    score: int
    confidence: int
    label: str
    stage: str
    action: str
    probe_fraction: float
    bottom_count: int
    discount_104w_pct: float
    target_discount_pct: float
    technical_score: int
    flow_score: int
    structure_score: int
    structure_state: str
    rsi: float | None
    macd_hist_pct: float | None
    smi: float | None
    obv_state: str
    cmf20: float
    mfi14: float
    trigger_price: float | None
    invalidation_price: float | None
    risk_to_invalidation_pct: float | None
    pivot_dates: list[str]
    pivot_prices: list[float]
    signals: list[str]
    risk_note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_packet(packet: Mapping[str, Any]) -> WeeklyBottomCandidate | None:
    """Return a weekly bottom candidate only when all early-watch gates pass."""

    symbol = str(packet.get("symbol") or "").upper().strip()
    weekly = weekly_sniper.to_weekly(packet.get("df"))
    if not symbol or weekly is None or len(weekly) < 80:
        return None
    technical = technical_features.analyze_technical_watch(weekly)
    bottom_count = int(technical.get("bottom_count") or 0)
    pre_label = str(technical.get("pre_label") or "NONE")
    momentum_ready = bool(
        technical.get("macd_bullish_divergence")
        or technical.get("rsi_bullish_divergence")
        or technical.get("macd_convergence")
        or technical.get("macd_cross_bottom")
        or str(technical.get("stage")) in {"FORMING_STRONG", "CONFIRMED"}
    )
    if bottom_count < 2 or pre_label == "NONE" or not momentum_ready or technical.get("risk_dominant"):
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

    flow = _weekly_flow(weekly)
    technical_score = int(technical.get("score") or 0)
    pattern_score = int(technical.get("bottom_quality_score") or technical_score)
    discount_score = _discount_score(discount, target)
    flow_score = int(flow["score"])
    score = int(round(_clamp(
        pattern_score * 0.32
        + technical_score * 0.20
        + discount_score * 0.18
        + flow_score * 0.17
        + structure_score * 0.13
        + (5 if bottom_count >= 3 else 0)
        + (4 if technical.get("rsi_bullish_divergence") else 0)
    )))
    if structure_state in {"NO_DATA", "NO_SETUP"}:
        score = min(score, 69)
    if flow_score < 35:
        score = min(score, 74)
    if score < 58:
        return None

    trigger = technical.get("trigger_price")
    invalidation = technical.get("invalidation_price")
    risk_pct = None
    if close > 0 and _safe(invalidation) > 0 and close > _safe(invalidation):
        risk_pct = (close - _safe(invalidation)) / close * 100.0
    if risk_pct is None or risk_pct > 22:
        score = min(score, 67)

    if (
        score >= 80
        and flow_score >= 58
        and risk_pct is not None
        and risk_pct <= 16
        and structure_state in {"EARLY_MARKUP", "READY_TO_ACCUMULATE", "PREP_BASE"}
    ):
        action = "CANH THĂM DÒ 1/5 KHI GIỮ ĐÁY"
        probe_fraction = 0.20
    elif score >= 68 and risk_pct is not None and risk_pct <= 20:
        action = "CANH THĂM DÒ 1/6 SAU XÁC NHẬN"
        probe_fraction = 0.15
    else:
        action = "CANH ME — CHƯA MUA"
        probe_fraction = 0.0

    confidence = int(_clamp(
        _safe(technical.get("confidence")) * 0.48
        + _safe(getattr(weekly_structure, "confidence", 0)) * 0.32
        + flow_score * 0.20,
        30,
        94,
    ))
    signals = [
        f"Chiết khấu 104W {discount:.1f}%",
        *[str(value) for value in technical.get("bullish_signals", technical.get("signals", []))[:3]],
        *[str(value) for value in flow.get("signals", [])[:2]],
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
        confidence=confidence,
        label=f"W-{pre_label}",
        stage=str(technical.get("stage") or "FORMING"),
        action=action,
        probe_fraction=probe_fraction,
        bottom_count=bottom_count,
        discount_104w_pct=round(discount, 2),
        target_discount_pct=round(target, 2),
        technical_score=technical_score,
        flow_score=flow_score,
        structure_score=structure_score,
        structure_state=structure_state,
        rsi=_safe(technical.get("rsi")) if technical.get("rsi") is not None else None,
        macd_hist_pct=_safe(technical.get("macd_hist_pct")) if technical.get("macd_hist_pct") is not None else None,
        smi=_safe(technical.get("smi")) if technical.get("smi") is not None else None,
        obv_state=str(flow["obv_state"]),
        cmf20=float(flow["cmf20"]),
        mfi14=float(flow["mfi14"]),
        trigger_price=round(_safe(trigger), 2) if trigger is not None else None,
        invalidation_price=round(_safe(invalidation), 2) if invalidation is not None else None,
        risk_to_invalidation_pct=round(risk_pct, 2) if risk_pct is not None else None,
        pivot_dates=[str(value) for value in technical.get("pivot_dates", []) if value],
        pivot_prices=[round(_safe(value), 2) for value in technical.get("pivot_prices", [])],
        signals=list(dict.fromkeys(signals))[:6],
        risk_note=risk_note,
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
        key=lambda item: (item.score, item.flow_score, item.confidence, item.discount_104w_pct),
        reverse=True,
    )
    return candidates[:maximum]


def format_line(item: WeeklyBottomCandidate) -> str:
    trigger = f"KT {item.trigger_price:.2f}" if item.trigger_price is not None else "KT chờ"
    invalidation = f"HV {item.invalidation_price:.2f}" if item.invalidation_price is not None else "HV chưa rõ"
    rsi = f"{item.rsi:.0f}" if item.rsi is not None else "-"
    smi = f"{item.smi:.0f}" if item.smi is not None else "-"
    macd = f"{item.macd_hist_pct:+.3f}%" if item.macd_hist_pct is not None else "-"
    return (
        f"`{item.symbol}` {item.label} · {item.score}/97 | {item.action} | Giá {item.close:.2f} | "
        f"DD104W {item.discount_104w_pct:.1f}/{item.target_discount_pct:.0f}% | "
        f"RSI {rsi} · SMI {smi} · MACDh {macd} | "
        f"OBV {item.obv_state} · CMF {item.cmf20:+.2f} · MFI {item.mfi14:.0f} | "
        f"{trigger} · {invalidation}"
    )


def payload(candidates: Iterable[WeeklyBottomCandidate], updated_at: str) -> dict[str, Any]:
    values = list(candidates)
    maximum = min(5, max(1, int(os.getenv("WEEKEND_BOTTOM_WATCH_LIMIT", "5"))))
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": updated_at,
        "policy": {
            "advisory_only": True,
            "max_candidates": maximum,
            "requires": ["weekly_2_or_3_bottoms", "deep_discount", "MACD_RSI_SMI_bottom_momentum"],
            "flow_is_bonus": ["OBV", "CMF", "MFI"],
            "never_average_below_invalidation": True,
        },
        "candidates": [item.to_dict() for item in values[:maximum]],
    }
