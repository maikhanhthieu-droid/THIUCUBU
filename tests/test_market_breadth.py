from __future__ import annotations

import numpy as np
import pandas as pd

import market_breadth
import scan


def rows(values: np.ndarray, *, volume: float = 1_000_000) -> list[dict]:
    dates = pd.bdate_range("2025-01-02", periods=len(values))
    return [
        {
            "time": date.isoformat(),
            "open": float(value * 0.995),
            "high": float(value * 1.01),
            "low": float(value * 0.99),
            "close": float(value),
            "volume": float(volume),
        }
        for date, value in zip(dates, values)
    ]


def test_breadth_uses_same_date_dynamic_universe(tmp_path) -> None:
    index = np.linspace(1_000, 1_250, 220)
    history = {"VNINDEX": rows(index)}
    for number in range(60):
        history[f"A{number:02d}"] = rows(np.linspace(10 + number, 18 + number, 220))
    stale = rows(np.linspace(10, 12, 219))
    history["ZZZ"] = stale

    snapshot = market_breadth.calculate_snapshot(
        history,
        expected_universe_size=61,
        history_path=tmp_path / "breadth.json",
    )

    assert snapshot["valid_symbols"] == 60
    assert snapshot["stale_or_invalid_symbols"] == 1
    assert snapshot["pct_above_ma20"] == 100
    assert snapshot["pct_above_ma50"] == 100
    assert snapshot["state"] == "STRONG"
    assert snapshot["confidence"] >= 60


def test_thin_breadth_sample_can_never_hard_lock_market(tmp_path) -> None:
    index = np.linspace(1_250, 900, 220)
    history = {"VNINDEX": rows(index)}
    for number in range(6):
        history[f"B{number:02d}"] = rows(np.linspace(20 + number, 10 + number, 220))

    snapshot = market_breadth.calculate_snapshot(
        history,
        expected_universe_size=150,
        history_path=tmp_path / "breadth.json",
    )
    systemic = market_breadth.derive_systemic_regime(
        snapshot,
        {"regime": "BEAR"},
    )

    assert snapshot["state"] == "NO_DATA"
    assert snapshot["confidence"] < 45
    assert systemic["state"] == "NEUTRAL"
    assert systemic["hard_lock_new_accumulation"] is False


def test_systemic_transition_counts_distinct_eod_dates_not_reruns(tmp_path) -> None:
    history_path = tmp_path / "breadth.json"
    systemic_path = tmp_path / "systemic.json"
    scan.json_save(
        systemic_path,
        {
            "state": "NEUTRAL",
            "trading_date": "2026-08-20",
            "pending_state": None,
            "pending_count": 0,
            "transitions": [],
        },
    )
    breadth = {"trading_date": "2026-08-21", "updated_at": "2026-08-21T15:05:00"}
    candidate = {
        "schema_version": market_breadth.SYSTEMIC_SCHEMA_VERSION,
        "updated_at": "2026-08-21T15:05:00",
        "trading_date": "2026-08-21",
        "state": "HIGH_RISK",
        "raw_state": "HIGH_RISK",
        "risk_score": 55,
        "confidence": 80,
    }

    _, first = market_breadth.persist_daily(
        breadth,
        candidate,
        history_path=history_path,
        systemic_path=systemic_path,
    )
    _, rerun = market_breadth.persist_daily(
        breadth,
        candidate,
        history_path=history_path,
        systemic_path=systemic_path,
    )
    next_breadth = {"trading_date": "2026-08-24", "updated_at": "2026-08-24T15:05:00"}
    next_candidate = dict(candidate, trading_date="2026-08-24", updated_at="2026-08-24T15:05:00")
    _, confirmed = market_breadth.persist_daily(
        next_breadth,
        next_candidate,
        history_path=history_path,
        systemic_path=systemic_path,
    )

    assert first["state"] == "NEUTRAL"
    assert first["pending_count"] == 1
    assert rerun["pending_count"] == 1
    assert confirmed["state"] == "HIGH_RISK"
