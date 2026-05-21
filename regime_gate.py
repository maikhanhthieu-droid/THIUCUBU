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
    if require_near_break and not result.near_break:
        return {"allowed": False, "reason": "CHUA_GAN_BREAK"}

    score = adv_score(result, metrics)
    info = _dict_value(metrics)
    rs = _dict_value(info.get("rs"))
    trade = _dict_value(info.get("trade"))
    weekly = _dict_value(info.get("weekly"))
    regime_info = _dict_value(info.get("regime"))
    rs_score = _safe_int(rs.get("rs_score"), 50)
    rr = _safe_float(trade.get("risk_reward"), 0.0)
    regime = str(regime_info.get("regime") or "UNKNOWN").upper()
    deep_discount = result.discount_pct >= result.target_discount_pct + 8
    weekly_ok = bool(weekly.get("weekly_uptrend") or weekly.get("weekly_above_ema13"))

    if regime == "BULL":
        allowed = score >= min_score
        reason = "BULL full signal" if allowed else "BULL score chua du"
    elif regime == "RECOVERY":
        allowed = score >= max(68, min_score - 4) and rs_score >= 50
        reason = "RECOVERY co RS xac nhan" if allowed else "RECOVERY loc them"
    elif regime == "CHOPPY":
        allowed = score >= max(74, min_score) and rs_score >= 55 and (rr >= 1.5 or weekly_ok)
        reason = "CHOPPY chi nhan RS/RR tot" if allowed else "CHOPPY chan signal yeu"
    elif regime == "BEAR":
        allowed = deep_discount and score >= 76 and rs_score >= 60 and rr >= 1.5
        reason = "BEAR chi cho discount sau + RS manh" if allowed else "BEAR chan tin hieu mua"
    else:
        allowed = score >= min_score and rs_score >= 50
        reason = "UNKNOWN regime loc co ban" if allowed else "UNKNOWN regime chua du"

    return {
        "allowed": bool(allowed),
        "reason": reason,
        "regime": regime,
        "score": score,
        "rs_score": rs_score,
        "risk_reward": round(rr, 2) if rr else None,
        "deep_discount": deep_discount,
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
) -> list[str]:
    lines: list[str] = []
    for result in results:
        item = metrics.get(result.symbol, {})
        if adv_score(result, item) < min_score:
            continue
        gate = signal_gate(result, item, min_score=min_score)
        if gate["allowed"]:
            continue
        item["gate"] = gate
        lines.append(
            f"`{result.symbol}` Intel {adv_score(result, item)}/100 | "
            f"RS {gate['rs_score']} | {gate['reason']}"
        )
        if len(lines) >= limit:
            break
    return lines
