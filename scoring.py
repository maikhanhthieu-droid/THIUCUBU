"""Shared, bounded scoring for daily and weekend scanners.

The old scanners added many binary bonuses and then clipped at 100.  That made
very different setups look identical.  Score v2 rewards agreement between
independent evidence groups, penalises a weak link, and reserves the top band
for genuinely rare setups.  ``win_score`` remains available for compatibility,
but no actionable stock score can be 100.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


SCORE_VERSION = "thieucubu.score.v2"
MAX_SCORE = 97


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def grade(score: int) -> str:
    if score >= 94:
        return "S"
    if score >= 88:
        return "A+"
    if score >= 82:
        return "A"
    if score >= 75:
        return "B+"
    if score >= 68:
        return "B"
    if score >= 55:
        return "C"
    return "D"


def _balanced_score(components: Mapping[str, tuple[float, float, float]], penalty: float = 0.0) -> int:
    """Return a 0..97 score from value/max/weight triples.

    A blend of weighted arithmetic and geometric means keeps one excellent
    component from hiding a weak component.  A small agreement bonus is only
    available when most independent groups are strong.
    """

    rows: list[tuple[float, float]] = []
    for value, maximum, weight in components.values():
        if maximum <= 0 or weight <= 0:
            continue
        rows.append((clamp(float(value) / float(maximum), 0.0, 1.0), float(weight)))
    if not rows:
        return 0

    weight_sum = sum(weight for _, weight in rows)
    arithmetic = sum(value * weight for value, weight in rows) / weight_sum
    # A 0.05 floor avoids making one unavailable/zero indicator erase all
    # other evidence, while still strongly penalising weak links.
    geometric = math.exp(
        sum(weight * math.log(max(value, 0.05)) for value, weight in rows) / weight_sum
    )
    strong_share = sum(weight for value, weight in rows if value >= 0.72) / weight_sum
    agreement_bonus = 4.0 if strong_share >= 0.80 else 2.0 if strong_share >= 0.60 else 0.0
    raw = (arithmetic * 0.68 + geometric * 0.32) * 93.0 + agreement_bonus - penalty
    return int(round(clamp(raw, 0.0, float(MAX_SCORE))))


def daily_scores(
    *,
    trend: float,
    base: float,
    flow: float,
    timing: float,
    discount: float,
    risk_penalty: float,
    near_break: bool,
    failed_break: bool,
) -> dict[str, Any]:
    trade = _balanced_score(
        {
            "trend": (trend, 35.0, 0.27),
            "base": (base, 36.0, 0.25),
            "flow": (flow, 36.0, 0.28),
            "timing": (timing, 38.0, 0.20),
        },
        penalty=risk_penalty,
    )
    position = _balanced_score(
        {
            "trend": (trend, 35.0, 0.25),
            "base": (base, 36.0, 0.30),
            "flow": (flow, 36.0, 0.27),
            "discount": (discount, 35.0, 0.18),
        },
        penalty=risk_penalty * 0.85,
    )
    if not near_break:
        trade = min(trade, 86)
    if flow < 8:
        trade = min(trade, 64)
        position = min(position, 64)
    if failed_break:
        trade = min(trade, 38)
        position = min(position, 42)

    score = max(trade, position)
    horizon = "SWING" if trade >= position + 4 else "POSITION" if position >= trade + 4 else "SWING+POSITION"
    confidence = int(
        clamp(
            48
            + min(trend / 35.0, 1.0) * 12
            + min(base / 36.0, 1.0) * 12
            + min(flow / 36.0, 1.0) * 14
            + (8 if near_break else 3)
            - min(risk_penalty, 30) * 0.8,
            20,
            96,
        )
    )
    if failed_break:
        action = "AVOID"
    elif trade >= 82 and near_break:
        action = "CANH_MUA"
    elif position >= 78:
        action = "CANH_GOM"
    elif score >= 68:
        action = "THEO_DOI"
    else:
        action = "CHUA_DAT"
    return {
        "score": score,
        "trade_score": trade,
        "position_score": position,
        "grade": grade(score),
        "confidence": confidence,
        "horizon": horizon,
        "action": action,
        "score_version": SCORE_VERSION,
    }


def enhanced_daily_score(
    *,
    base_score: int,
    weekly_score: float,
    volume_score: float,
    rs_score: float,
    rr_score: float,
    regime: str,
    churning: bool = False,
) -> int:
    penalty = {"BULL": 0.0, "RECOVERY": 3.0, "CHOPPY": 8.0, "BEAR": 16.0}.get(
        str(regime).upper(), 6.0
    )
    if churning:
        penalty += 10.0
    return _balanced_score(
        {
            "daily": (base_score, MAX_SCORE, 0.48),
            "weekly": (weekly_score, 100.0, 0.17),
            "volume": (volume_score, 100.0, 0.14),
            "relative_strength": (rs_score, 100.0, 0.14),
            "risk_reward": (rr_score, 100.0, 0.07),
        },
        penalty=penalty,
    )


def weekend_score(
    *,
    valuation: float,
    quality: float,
    structure: float,
    timing: float,
    sector: float,
    risk: float,
) -> int:
    return _balanced_score(
        {
            "valuation": (valuation, 100.0, 0.27),
            "quality": (quality, 100.0, 0.22),
            "weekly_structure": (structure, 100.0, 0.25),
            "timing": (timing, 100.0, 0.16),
            "sector": (sector, 100.0, 0.10),
        },
        penalty=clamp(risk, 0.0, 100.0) * 0.26,
    )
