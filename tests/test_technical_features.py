from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

import technical_features


def stable_frame() -> pd.DataFrame:
    close = pd.Series([15 - index * 0.03 for index in range(90)] + [12.25, 12.20, 12.22, 12.21, 12.24, 12.26, 12.25, 12.28, 12.30, 12.32])
    volume = [1_000_000.0] * 95 + [500_000.0] * 5
    return pd.DataFrame(
        {
            "open": close - 0.03,
            "high": close + 0.12,
            "low": close - 0.12,
            "close": close,
            "volume": volume,
        }
    )


def structure(weekly: str = "TRANSITION") -> dict:
    return {
        "overall_state": "ACCUMULATION",
        "timeframes": {
            "1D": {"state": "ACCUMULATION"},
            "1W": {"state": weekly},
            "1M": {"state": "ACCUMULATION"},
        },
        "breakout": {"state": "NO_BREAKOUT"},
    }


def test_early_accumulation_grades_stable_discount_but_respects_structure_blocker() -> None:
    frame = stable_frame()
    result = SimpleNamespace(discount_pct=35, target_discount_pct=30, failed_break=False)
    technical = {"watch": True, "score": 50, "signals": ["RSI phân kỳ đáy"]}

    healthy = technical_features.analyze_early_accumulation(
        frame,
        result,
        structure=structure(),
        technical=technical,
        relative_strength={"rs_score": 52},
    )
    blocked = technical_features.analyze_early_accumulation(
        frame,
        result,
        structure=structure("DISTRIBUTION"),
        technical=technical,
        relative_strength={"rs_score": 52},
    )

    assert healthy["eligible"] is True
    assert healthy["stage"] in {"E2", "E3"}
    assert healthy["score"] <= 97
    assert blocked["eligible"] is False
    assert blocked["stage"] == "NONE"


def test_flat_oscillators_do_not_create_bottom_watch_noise() -> None:
    close = pd.Series([10.0] * 100)
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": [1_000_000.0] * 100,
        }
    )

    result = technical_features.analyze_technical_watch(frame)

    assert result["watch"] is False
    assert result["stage"] == "NONE"


def test_confirmed_double_bottom_gets_pre_label_trigger_and_invalidation() -> None:
    segments = [
        np.linspace(18, 12, 65),
        np.linspace(12, 14, 10)[1:],
        np.linspace(14, 11.7, 12)[1:],
        np.linspace(11.7, 13.7, 10)[1:],
        np.linspace(13.7, 11.55, 22)[1:],
        np.linspace(11.55, 13.2, 12)[1:],
    ]
    close = pd.Series(np.concatenate(segments))
    frame = pd.DataFrame(
        {
            "time": pd.bdate_range("2026-01-02", periods=len(close)),
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 0.16,
            "low": close - 0.16,
            "close": close,
            "volume": np.linspace(1_000_000, 400_000, len(close)),
        }
    )

    result = technical_features.analyze_technical_watch(frame)

    assert result["watch"] is True
    assert result["bullish_watch"] is True
    assert result["pre_label"] == "PRE-DIV-2"
    assert result["bottom_count"] == 2
    assert result["confirmed_at_bar"] <= len(frame) - 3
    assert result["signal_age_bars"] <= 15
    assert result["trigger_price"] > result["invalidation_price"]


def test_weekly_oscillator_detector_counts_three_distinct_smi_bottoms(monkeypatch) -> None:
    periods = 120
    close = pd.Series(np.linspace(20.0, 13.0, periods))
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-05", periods=periods, freq="W-FRI"),
            "open": close + 0.05,
            "high": close + 0.25,
            "low": close - 0.25,
            "close": close,
            "volume": np.linspace(1_500_000, 800_000, periods),
        }
    )
    smi = pd.Series([20.0] * periods)
    smi.iloc[65] = -62.0
    smi.iloc[88] = -56.0
    smi.iloc[112] = -50.0
    smi.iloc[113:120] = [-43.0, -35.0, -27.0, -20.0, -15.0, -10.0, -5.0]
    signal = smi + 5.0
    signal.iloc[-3:] = smi.iloc[-3:] + pd.Series([10.0, 6.0, 3.0], index=signal.index[-3:])
    monkeypatch.setattr(technical_features, "_smi", lambda prepared: (smi, signal))

    result = technical_features.analyze_oscillator_bottoms(frame, timeframe="1W")

    assert result["smi_bottom_count"] == 3
    assert result["smi_state"] == "CURLING_UP_BELOW_SIGNAL"
    assert result["momentum_ready"] is True
    assert len(result["smi_pivot_dates"]) == 3


def test_flat_smi_is_not_counted_as_one_or_more_bottoms() -> None:
    close = pd.Series([10.0] * 100)
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": [1_000_000.0] * 100,
        }
    )

    result = technical_features.analyze_oscillator_bottoms(frame, timeframe="1D")

    assert result["smi_bottom_count"] == 0

