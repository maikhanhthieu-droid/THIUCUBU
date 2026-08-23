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


def oscillator(timeframe: str, bottoms: int = 2) -> dict:
    weekly = timeframe == "1W"
    dates = ["2024-10-04", "2025-04-04", "2026-02-06"]
    prices = [18.4, 18.2, 18.0]
    return {
        "oscillator_type": "SMI_ERGODIC_OSCILLATOR",
        "profile": {
            "short_period": 5 if weekly else 3,
            "long_period": 20 if weekly else 13,
            "signal_period": 5 if weekly else 3,
        },
        "smiio_bottom_count": bottoms,
        "smiio_state": "TURNING_UP_NEGATIVE" if weekly else "ZERO_CROSS_UP",
        "smiio_zone": "NEGATIVE" if weekly else "POSITIVE",
        "smiio_value": -2.2 if weekly else 0.4,
        "ergodic_value": -18.0,
        "ergodic_signal": -15.8,
        "smiio_pivot_indices": [78, 104] if bottoms == 2 else [55, 78, 104] if bottoms >= 3 else [104],
        "smiio_pivot_dates": dates[-bottoms:],
        "smiio_pivot_values": [-6.2, -4.8, -3.5][-bottoms:],
        "smiio_pivot_prices": prices[-bottoms:],
        "smiio_bullish_divergence": True,
        "smiio_divergence_state": "BULLISH_NEGATIVE",
        "macd_state": "PRE_CROSS_NEGATIVE",
        "macd_zone": "NEGATIVE",
        "macd_hist_pct": -0.08,
        "macd_bullish_divergence": True,
        "macd_divergence_state": "BULLISH_NEGATIVE",
        "rsi_bullish_divergence": True,
        "rsi": 43.0,
        "momentum_ready": True,
        "signals": [f"SMI {bottoms} đáy {timeframe}"],
    }


def patch_oscillators(monkeypatch, *, weekly_bottoms: int = 2, daily_bottoms: int = 2) -> None:
    monkeypatch.setattr(
        watch.technical_features,
        "analyze_smiio_bottoms",
        lambda frame, *, timeframe: oscillator(
            timeframe,
            weekly_bottoms if timeframe == "1W" else daily_bottoms,
        ),
    )


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
    patch_oscillators(monkeypatch)

    candidate = watch.analyze_packet(packet())

    assert candidate is not None
    assert candidate.label == "W-PRE-SMIIO-2"
    assert candidate.bottom_count == 2
    assert candidate.oscillator_type == "SMI_ERGODIC_OSCILLATOR"
    assert candidate.weekly_smiio_profile == {
        "short_period": 5,
        "long_period": 20,
        "signal_period": 5,
    }
    assert candidate.daily_smiio_profile == {
        "short_period": 3,
        "long_period": 13,
        "signal_period": 3,
    }
    assert candidate.daily_smi_bottom_count == 2
    assert candidate.score == 75
    assert candidate.score_components == {
        "weekly_smiio_bottoms": 40,
        "daily_smiio_bottoms": 10,
        "momentum_divergence": 15,
        "money_flow_divergence": 0,
        "discount_structure": 10,
    }
    assert candidate.score == sum(candidate.score_components.values())
    assert candidate.discount_104w_pct == 35
    assert candidate.obv_state in {"TĂNG", "CẢI THIỆN"}
    assert candidate.probe_fraction in {0.15, 0.20}
    assert candidate.invalidation_price == 18.0
    assert "không bình quân" in candidate.risk_note


def test_weekly_watch_rejects_shallow_discount_and_broken_structure(monkeypatch) -> None:
    monkeypatch.setattr(watch.weekly_sniper, "to_weekly", lambda df: weekly_frame())
    monkeypatch.setattr(watch.technical_features, "analyze_technical_watch", lambda frame: strong_technical())
    patch_oscillators(monkeypatch)

    assert watch.analyze_packet(packet(discount=10)) is None
    assert watch.analyze_packet(packet(flags=["BROKEN_STRUCTURE"])) is None


def test_weekly_watch_requires_two_confirmed_bottoms(monkeypatch) -> None:
    monkeypatch.setattr(watch.weekly_sniper, "to_weekly", lambda df: weekly_frame())
    monkeypatch.setattr(watch.technical_features, "analyze_technical_watch", lambda frame: strong_technical())
    patch_oscillators(monkeypatch, weekly_bottoms=1)

    assert watch.analyze_packet(packet()) is None


def test_weekly_watch_payload_is_explicitly_advisory(monkeypatch) -> None:
    monkeypatch.setattr(watch.weekly_sniper, "to_weekly", lambda df: weekly_frame())
    monkeypatch.setattr(watch.technical_features, "analyze_technical_watch", lambda frame: strong_technical())
    patch_oscillators(monkeypatch)
    candidate = watch.analyze_packet(packet())
    assert candidate is not None

    payload = watch.payload([candidate], "2026-08-24T08:00:00+07:00")

    assert payload["policy"]["advisory_only"] is True
    assert payload["policy"]["never_average_below_invalidation"] is True
    assert payload["schema_version"] == "thieucubu.weekly_bottom_watch.v3"
    assert payload["score_version"] == "thieucubu.weekly_bottom_watch.score.v3"
    assert payload["candidates"][0]["symbol"] == "AAA"
    assert "W-PRE-SMIIO-2" in watch.format_line(candidate)
    assert "/100" in watch.format_line(candidate)


def test_score_matrix_uses_exact_weekly_and_daily_bottom_weights() -> None:
    two_score, two = watch.calculate_watch_score(
        weekly_smiio_bottom_count=2,
        daily_smiio_bottom_count=1,
        momentum_points=15,
        flow_divergence_points=15,
        discount_structure_points=10,
    )
    three_score, three = watch.calculate_watch_score(
        weekly_smiio_bottom_count=3,
        daily_smiio_bottom_count=2,
        momentum_points=15,
        flow_divergence_points=15,
        discount_structure_points=10,
    )

    assert two["weekly_smiio_bottoms"] == 40
    assert two["daily_smiio_bottoms"] == 5
    assert two_score == 85
    assert three["weekly_smiio_bottoms"] == 50
    assert three["daily_smiio_bottoms"] == 10
    assert three_score == 100


def test_flow_divergence_rewards_price_lower_low_with_flat_or_rising_flow() -> None:
    frame = weekly_frame()
    frame.loc[104, "close"] = frame.loc[78, "close"] * 0.92
    frame["open"] = frame["close"] * 0.985
    frame["high"] = frame["close"] * 1.02
    frame["low"] = frame["close"] * 0.96

    flow = watch._weekly_flow(frame, [78, 104])

    assert flow["price_change_between_bottoms_pct"] < 0
    assert flow["divergence_score"] >= 5
    assert flow["divergence_signals"]
