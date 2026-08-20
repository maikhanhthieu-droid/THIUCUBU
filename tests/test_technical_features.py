from __future__ import annotations

from types import SimpleNamespace

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

