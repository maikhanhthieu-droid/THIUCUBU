from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

import weekly_bottom_watch as watch


def weekly_frame(periods: int = 120) -> pd.DataFrame:
    close = np.linspace(16.0, 20.0, periods)
    volume = np.linspace(1_000_000, 1_600_000, periods)
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-05", periods=periods, freq="W-FRI"),
            "open": close * 0.985,
            "high": close * 1.02,
            "low": close * 0.96,
            "close": close,
            "volume": volume,
        }
    )


def strong_technical() -> dict:
    return {
        "bottom_count": 2,
        "bottom_quality_score": 84,
        "pre_label": "PRE-DIV-2",
        "stage": "FORMING_STRONG",
        "score": 82,
        "confidence": 82,
        "macd_bullish_divergence": True,
        "rsi_bullish_divergence": True,
        "macd_convergence": True,
        "macd_cross_bottom": False,
        "risk_dominant": False,
        "rsi": 43.0,
        "smi": -22.0,
        "macd_hist_pct": -0.08,
        "trigger_price": 20.8,
        "invalidation_price": 18.0,
        "pivot_dates": ["2025-04-04", "2026-02-06"],
        "pivot_prices": [18.2, 18.0],
        "bullish_signals": ["Cấu trúc 2 đáy đã xác nhận", "MACD phân kỳ tăng ở đáy"],
    }


def packet(*, discount: float = 35, flags: list[str] | None = None) -> dict:
    return {
        "symbol": "AAA",
        "sector": "Test",
        "df": pd.DataFrame({"placeholder": [1]}),
        "tech": SimpleNamespace(target_discount_pct=25, failed_break=False),
        "weekly": SimpleNamespace(
            state="PREP_BASE",
            score=76,
            confidence=82,
            flags=flags or [],
            discount_104w_pct=discount,
        ),
        "market_structure": None,
    }


def test_weekly_two_bottom_candidate_combines_discount_momentum_and_flow(monkeypatch) -> None:
    monkeypatch.setattr(watch.weekly_sniper, "to_weekly", lambda df: weekly_frame())
    monkeypatch.setattr(watch.technical_features, "analyze_technical_watch", lambda frame: strong_technical())

    candidate = watch.analyze_packet(packet())

    assert candidate is not None
    assert candidate.label == "W-PRE-DIV-2"
    assert candidate.bottom_count == 2
    assert candidate.discount_104w_pct == 35
    assert candidate.obv_state in {"TĂNG", "CẢI THIỆN"}
    assert candidate.probe_fraction in {0.15, 0.20}
    assert candidate.invalidation_price == 18.0
    assert "không bình quân" in candidate.risk_note


def test_weekly_watch_rejects_shallow_discount_and_broken_structure(monkeypatch) -> None:
    monkeypatch.setattr(watch.weekly_sniper, "to_weekly", lambda df: weekly_frame())
    monkeypatch.setattr(watch.technical_features, "analyze_technical_watch", lambda frame: strong_technical())

    assert watch.analyze_packet(packet(discount=10)) is None
    assert watch.analyze_packet(packet(flags=["BROKEN_STRUCTURE"])) is None


def test_weekly_watch_requires_two_confirmed_bottoms(monkeypatch) -> None:
    weak = strong_technical()
    weak.update({"bottom_count": 1, "pre_label": "PRE-MACD-CONVERGE"})
    monkeypatch.setattr(watch.weekly_sniper, "to_weekly", lambda df: weekly_frame())
    monkeypatch.setattr(watch.technical_features, "analyze_technical_watch", lambda frame: weak)

    assert watch.analyze_packet(packet()) is None


def test_weekly_watch_payload_is_explicitly_advisory(monkeypatch) -> None:
    monkeypatch.setattr(watch.weekly_sniper, "to_weekly", lambda df: weekly_frame())
    monkeypatch.setattr(watch.technical_features, "analyze_technical_watch", lambda frame: strong_technical())
    candidate = watch.analyze_packet(packet())
    assert candidate is not None

    payload = watch.payload([candidate], "2026-08-24T08:00:00+07:00")

    assert payload["policy"]["advisory_only"] is True
    assert payload["policy"]["never_average_below_invalidation"] is True
    assert payload["candidates"][0]["symbol"] == "AAA"
    assert "W-PRE-DIV-2" in watch.format_line(candidate)
