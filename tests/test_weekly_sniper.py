from __future__ import annotations

import numpy as np
import pandas as pd

import weekly_sniper


def make_daily(periods: int = 780) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-02", periods=periods)
    trend = np.linspace(30.0, 20.0, periods)
    recovery_periods = min(140, periods)
    recovery = np.linspace(20.0, 25.0, recovery_periods)
    trend[-recovery_periods:] = recovery
    wave = np.sin(np.arange(periods) / 8.0) * np.linspace(1.8, 0.35, periods)
    close = trend + wave
    volume = np.linspace(2_000_000, 850_000, periods)
    volume[-5:] *= 0.75
    return pd.DataFrame(
        {
            "time": dates,
            "open": close * 0.995,
            "high": close * 1.025,
            "low": close * 0.975,
            "close": close,
            "volume": volume,
        }
    )


def test_weekly_structure_returns_machine_readable_levels() -> None:
    stock = make_daily()
    index = make_daily()
    result = weekly_sniper.analyze_weekly_structure(stock, index, min_turnover_bn=0)

    assert 0 <= result.score <= 97
    assert 0 <= result.timing_score <= 100
    assert result.breakout_price is not None
    assert result.invalidation_price is not None
    assert set(result.components) == {"discount", "structure", "base", "flow", "relative_strength"}


def test_weekly_structure_handles_short_history() -> None:
    result = weekly_sniper.analyze_weekly_structure(make_daily(100), None)

    assert result.state == "NO_DATA"
    assert "INSUFFICIENT_HISTORY" in result.flags
