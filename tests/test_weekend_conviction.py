from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime

import weekend_opportunities as weekend
from weekly_sniper import WeeklyStructure


def weekly_ready(score: int = 90) -> WeeklyStructure:
    return WeeklyStructure(
        score=score,
        timing_score=86,
        confidence=90,
        state="EARLY_MARKUP",
        trigger="RECLAIM",
        discount_104w_pct=32,
        risk_to_key_pct=10,
        risk_reward=2.5,
        rs_edge_13w_pct=6,
        turnover_13w_bn=200,
        base_weeks=13,
        buy_zone_low=20,
        buy_zone_high=21,
        breakout_price=23,
        invalidation_price=18.5,
        components={"discount": 90, "structure": 90, "base": 90, "flow": 90, "relative_strength": 90},
        flags=[],
    )


def packet(symbol: str) -> dict:
    fund = weekend.FundamentalSnapshot(
        symbol=symbol,
        pe=8,
        pb=1.1,
        roe=18,
        roa=8,
        debt_to_equity=0.5,
        current_ratio=1.5,
        profit_margin=15,
        eps=2,
        period="2026Q2",
        source="test",
    )
    tech = SimpleNamespace(
        failed_break=False,
        discount_pct=32,
        target_discount_pct=25,
        setup="DISCOUNT_BASE",
    )
    return {
        "symbol": symbol,
        "sector": "Test",
        "close": 21,
        "fundamental": fund,
        "tech": tech,
        "weekly": weekly_ready(),
        "as_of": datetime.now(weekend.VN_TZ).date().isoformat(),
        "cache_status": "live",
    }


def test_weekend_selects_at_most_two_convictions(monkeypatch) -> None:
    monkeypatch.setattr(weekend, "valuation_score", lambda packet, sector: (90, 30.0, 30.0))
    monkeypatch.setattr(weekend, "quality_score", lambda packet: 90)
    monkeypatch.setattr(weekend, "technical_score", lambda packet: 90)
    monkeypatch.setattr(weekend, "risk_score", lambda *args: (5, []))
    monkeypatch.setattr(weekend, "build_bull_case", lambda *args: "good value; early markup")
    sector = weekend.SectorSnapshot("Test", 85, 80, 10, 1.5, 15, 0, 4)

    results = weekend.build_opportunities(
        [packet("AAA"), packet("BBB"), packet("CCC"), packet("DDD")],
        {"Test": sector},
    )

    selected = [item for item in results if item.selected]
    assert len(selected) == 2
    assert all(item.action == "UU_TIEN_GOM" for item in selected)
    assert all(item.opportunity_score <= 97 for item in results)


def test_stale_price_data_cannot_be_selected(monkeypatch) -> None:
    monkeypatch.setattr(weekend, "valuation_score", lambda packet, sector: (95, 35.0, 35.0))
    monkeypatch.setattr(weekend, "quality_score", lambda packet: 95)
    monkeypatch.setattr(weekend, "technical_score", lambda packet: 95)
    monkeypatch.setattr(weekend, "risk_score", lambda *args: (0, []))
    monkeypatch.setattr(weekend, "build_bull_case", lambda *args: "great")
    item = packet("AAA")
    item["cache_status"] = "stale_cache"
    sector = weekend.SectorSnapshot("Test", 90, 90, 10, 1.5, 15, 0, 4)

    results = weekend.build_opportunities([item], {"Test": sector})

    assert not any(result.selected for result in results)
