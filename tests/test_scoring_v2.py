from __future__ import annotations

import scoring


def test_daily_score_reserves_top_band_and_never_reaches_100() -> None:
    result = scoring.daily_scores(
        trend=35,
        base=36,
        flow=36,
        timing=38,
        discount=35,
        risk_penalty=0,
        near_break=True,
        failed_break=False,
    )

    assert result["score"] == 97
    assert result["trade_score"] <= 97
    assert result["position_score"] <= 97
    assert result["grade"] == "S"


def test_daily_score_penalises_a_weak_evidence_group() -> None:
    result = scoring.daily_scores(
        trend=35,
        base=36,
        flow=0,
        timing=38,
        discount=35,
        risk_penalty=0,
        near_break=True,
        failed_break=False,
    )

    assert result["score"] <= 64
    assert result["action"] == "CHUA_DAT"


def test_failed_break_cannot_be_actionable() -> None:
    result = scoring.daily_scores(
        trend=35,
        base=36,
        flow=36,
        timing=38,
        discount=35,
        risk_penalty=28,
        near_break=True,
        failed_break=True,
    )

    assert result["score"] <= 42
    assert result["action"] == "AVOID"


def test_weekend_score_is_bounded() -> None:
    assert scoring.weekend_score(
        valuation=100,
        quality=100,
        structure=100,
        timing=100,
        sector=100,
        risk=0,
    ) == 97
