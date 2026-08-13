from __future__ import annotations

import numpy as np
import pandas as pd

import market_phase
import scan


def make_history(tail_close: list[float], tail_volume: list[float]) -> pd.DataFrame:
    length = 520
    prefix_length = length - len(tail_close)
    dates = pd.date_range("2024-01-01", periods=length, freq="B")
    prefix = [8.0 + index * 0.004 + ((index % 9) - 4) * 0.01 for index in range(prefix_length)]
    close = pd.Series(prefix + tail_close, dtype=float)
    volume = pd.Series([1_000_000.0] * prefix_length + tail_volume, dtype=float)
    frame = pd.DataFrame(
        {
            "time": dates,
            "open": close - 0.03,
            "high": close + 0.18,
            "low": close - 0.16,
            "close": close,
            "volume": volume,
        }
    )
    breakout_offset = int(np.argmax(tail_close))
    breakout_index = prefix_length + breakout_offset
    frame.loc[breakout_index, "open"] = frame.loc[breakout_index, "close"] - 0.45
    frame.loc[breakout_index, "high"] = frame.loc[breakout_index, "close"] + 0.08
    frame.loc[breakout_index, "low"] = frame.loc[breakout_index, "close"] - 0.55
    return frame


def test_failed_break_requires_loss_of_level_after_breakout() -> None:
    frame = make_history(
        [10.0, 10.05, 11.0, 10.10, 9.85, 9.70],
        [800_000, 800_000, 1_800_000, 1_700_000, 1_600_000, 1_500_000],
    )

    structure = market_phase.analyze_market_structure(frame)

    assert structure.breakout.state == "FAILED_BREAK_CONFIRMED"
    assert structure.breakout.failed_confirmed is True
    assert structure.overall_state == "CAUTION"
    assert structure.breakout.distance_to_level_pct < -3

    scan_result = scan.analyze_symbol("AAA", frame)
    assert scan_result is not None
    assert scan_result.failed_break is True
    assert scan_result.breakout_state == "FAILED_BREAK_CONFIRMED"
    assert scan_result.action == "AVOID"


def test_tight_low_volume_action_is_reaccumulation_not_failed_break() -> None:
    tight_tail = [10.16, 10.18, 10.17, 10.19, 10.18, 10.20, 10.19]
    frame = make_history(
        [10.0, 10.05, 11.0, *tight_tail],
        [800_000, 800_000, 1_800_000, *([600_000] * len(tight_tail))],
    )

    structure = market_phase.analyze_market_structure(frame)

    assert structure.breakout.state == "REACCUMULATION"
    assert structure.breakout.failed_confirmed is False
    assert structure.breakout.reaccumulation is True
    assert structure.overall_state == "ACCUMULATION"
    assert all(key in structure.timeframes for key in ("1D", "1W", "1M"))
    assert structure.timeframes["1M"].state != "NO_DATA"


def test_distribution_across_daily_and_weekly_dominates_score() -> None:
    length = 520
    dates = pd.date_range("2024-01-01", periods=length, freq="B")
    close = np.linspace(8.0, 14.0, length)
    open_price = close - 0.03
    high = close + 0.15
    low = close - 0.12
    volume = np.ones(length) * 1_000_000
    for offset, index in enumerate(range(length - 9, length)):
        close[index] = 13.8 - offset * 0.12
        open_price[index] = close[index] + 0.25
        high[index] = open_price[index] + 0.10
        low[index] = close[index] - 0.05
        volume[index] = 2_200_000
    frame = pd.DataFrame(
        {"time": dates, "open": open_price, "high": high, "low": low, "close": close, "volume": volume}
    )

    structure = market_phase.analyze_market_structure(frame)

    assert structure.overall_state == "DISTRIBUTION"
    assert structure.score <= 39
    assert structure.action == "KHONG_MUA_MOI"
    assert structure.timeframes["1D"].state in market_phase.RISK_PHASES
    assert structure.timeframes["1W"].state in market_phase.RISK_PHASES
