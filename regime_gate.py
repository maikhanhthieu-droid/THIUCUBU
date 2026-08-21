#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

import scan


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def adv_score(result: scan.ScanResult, metrics: dict[str, Any] | None) -> int:
    if not metrics:
        return int(result.win_score)
    return _safe_int(metrics.get("advanced_score"), int(result.win_score))


def signal_allowed(
    result: scan.ScanResult,
    metrics: dict[str, Any] | None,
    min_score: int = 72,
    require_near_break: bool = False,
) -> bool:
    gate = signal_gate(result, metrics, min_score=min_score, require_near_break=require_near_break)
    if metrics is not None:
        metrics["gate"] = gate
    return bool(gate["allowed"])


def signal_gate(
    result: scan.ScanResult,
    metrics: dict[str, Any] | None,
    min_score: int = 72,
    require_near_break: bool = False,
) -> dict[str, Any]:
    if result.failed_break:
        return {"allowed": False, "reason": "FAILED_BREAK"}
    info = _dict_value(metrics)
    structure = _dict_value(info.get("market_structure"))
    breakout = _dict_value(structure.get("breakout"))
    timeframes = _dict_value(structure.get("timeframes"))
    weekly_phase = str(_dict_value(timeframes.get("1W")).get("state") or "NO_DATA")
    monthly_phase = str(_dict_value(timeframes.get("1M")).get("state") or "NO_DATA")
    overall_state = str(structure.get("overall_state") or getattr(result, "market_state", "NO_DATA"))
    breakout_state = str(breakout.get("state") or getattr(result, "breakout_state", "NO_DATA"))
    if breakout_state == "FAILED_BREAK_CONFIRMED":
        return {"allowed": False, "reason": "FAILED_BREAK_CONFIRMED", "market_state": overall_state, "breakout_state": breakout_state}
    if breakout_state == "FAILED_BREAK_WATCH":
        return {"allowed": False, "reason": "BREAK_XIT_CHO_XAC_NHAN", "market_state": overall_state, "breakout_state": breakout_state}
    if overall_state == "DISTRIBUTION" or weekly_phase == "DISTRIBUTION" or monthly_phase == "DISTRIBUTION":
        return {"allowed": False, "reason": "MTF_PHAN_PHOI", "market_state": overall_state, "breakout_state": breakout_state}
    if weekly_phase == "MARKDOWN" and monthly_phase in {"MARKDOWN", "DISTRIBUTION"}:
        return {"allowed": False, "reason": "W_M_SUY_YEU", "market_state": overall_state, "breakout_state": breakout_state}
    if breakout_state == "REACCUMULATION":
        return {"allowed": False, "reason": "CHO_RECLAIM_TAI_TICH_LUY", "market_state": overall_state, "breakout_state": breakout_state}
    if breakout_state == "BREAKOUT_UNCONFIRMED":
        return {"allowed": False, "reason": "CHO_BREAK_GIU_NEN", "market_state": overall_state, "breakout_state": breakout_state}
    technical = _dict_value(info.get("technical_watch"))
    if technical.get("bearish_top_divergence") and technical.get("risk_dominant", True):
        return {
            "allowed": False,
            "reason": "PRE_RISK_DIV_TOP_CHO_XAC_NHAN",
            "market_state": overall_state,
            "breakout_state": breakout_state,
            "risk_label": technical.get("risk_label"),
        }

    systemic = _dict_value(info.get("systemic_regime"))
    sector = _dict_value(info.get("sector_rotation"))
    systemic_state = str(systemic.get("state") or "NEUTRAL").upper()
    sector_state = str(sector.get("state") or "NO_DATA").upper()
    systemic_adjustment = max(0, _safe_int(systemic.get("min_score_adjustment"), 0))
    sector_adjustment = 4 if sector_state == "LAGGING" else 2 if sector_state == "EXITING" else 0
    if systemic_state == "SYSTEMIC_RISK" and bool(systemic.get("hard_lock_new_accumulation")):
        return {
            "allowed": False,
            "reason": "SYSTEMIC_RISK_KHOA_MUA_MOI",
            "systemic_state": systemic_state,
            "sector_state": sector_state,
            "market_state": overall_state,
            "breakout_state": breakout_state,
            "position_size_multiplier": _safe_float(
                systemic.get("position_size_multiplier"), 0.2
            ),
        }
    if require_near_break and not result.near_break:
        return {"allowed": False, "reason": "CHUA_GAN_BREAK"}

    score = adv_score(result, metrics)
    rs = _dict_value(info.get("rs"))
    trade = _dict_value(info.get("trade"))
    weekly = _dict_value(info.get("weekly"))
    regime_info = _dict_value(info.get("regime"))
    rs_score = _safe_int(rs.get("rs_score"), 50)
    rr = _safe_float(trade.get("risk_reward"), 0.0)
    regime = str(regime_info.get("regime") or "UNKNOWN").upper()
    deep_discount = result.discount_pct >= result.target_discount_pct + 8
    weekly_ok = bool(weekly.get("weekly_uptrend") or weekly.get("weekly_above_ema13"))

    structure_threshold = 4 if overall_state == "CAUTION" else 0
    effective_min_score = (
        min_score + structure_threshold + systemic_adjustment + sector_adjustment
    )
    if regime == "BULL":
        allowed = score >= effective_min_score
        reason = "BULL full signal" if allowed else "BULL score chua du"
    elif regime == "RECOVERY":
        allowed = score >= max(68, effective_min_score - 4) and rs_score >= 50
        reason = "RECOVERY co RS xac nhan" if allowed else "RECOVERY loc them"
    elif regime == "CHOPPY":
        allowed = score >= max(74, effective_min_score) and rs_score >= 55 and (rr >= 1.5 or weekly_ok)
        reason = "CHOPPY chi nhan RS/RR tot" if allowed else "CHOPPY chan signal yeu"
    elif regime == "BEAR":
        allowed = deep_discount and score >= max(76, effective_min_score) and rs_score >= 60 and rr >= 1.5
        reason = "BEAR chi cho discount sau + RS manh" if allowed else "BEAR chan tin hieu mua"
    else:
        allowed = score >= effective_min_score and rs_score >= 50
        reason = "UNKNOWN regime loc co ban" if allowed else "UNKNOWN regime chua du"

    return {
        "allowed": bool(allowed),
        "reason": reason,
        "regime": regime,
        "score": score,
        "rs_score": rs_score,
        "risk_reward": round(rr, 2) if rr else None,
        "deep_discount": deep_discount,
        "effective_min_score": effective_min_score,
        "systemic_state": systemic_state,
        "sector_state": sector_state,
        "position_size_multiplier": _safe_float(
            systemic.get("position_size_multiplier"), 0.75
        ),
        "market_state": overall_state,
        "breakout_state": breakout_state,
    }


def filter_results(
    results: list[scan.ScanResult],
    metrics: dict[str, dict[str, Any]],
    min_score: int = 72,
    require_near_break: bool = False,
) -> list[scan.ScanResult]:
    return [
        result for result in results
        if adv_score(result, metrics.get(result.symbol)) >= min_score
        and signal_allowed(result, metrics.get(result.symbol), min_score=min_score, require_near_break=require_near_break)
    ]


def suppressed_lines(
    results: list[scan.ScanResult],
    metrics: dict[str, dict[str, Any]],
    min_score: int = 72,
    limit: int = 8,
    exclude_symbols: set[str] | None = None,
) -> list[str]:
    lines: list[str] = []
    excluded = exclude_symbols or set()
    for result in results:
        if result.symbol in excluded:
            continue
        item = metrics.get(result.symbol, {})
        if adv_score(result, item) < min_score:
            continue
        gate = signal_gate(result, item, min_score=min_score)
        if gate["allowed"]:
            continue
        item["gate"] = gate
        lines.append(
            f"`{result.symbol}` Intel {adv_score(result, item)}/97 | "
            f"RS {gate.get('rs_score', 50)} | {gate['reason']}"
        )
        if len(lines) >= limit:
            break
    return lines
