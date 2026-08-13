from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import signal_tracker


def trading_days(count: int) -> list[str]:
    current = date(2026, 1, 2)
    days: list[str] = []
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def history(symbol: str, count: int = 21) -> dict[str, list[dict[str, float | str]]]:
    days = trading_days(count)
    stock = []
    index = []
    for offset, day in enumerate(days):
        close = 100.0 + offset
        stock.append({"time": day, "open": close, "high": close + 2, "low": close - 3, "close": close, "volume": 1_000_000})
        index.append({"time": day, "open": 1000 + offset, "high": 1001 + offset, "low": 999 + offset, "close": 1000 + offset, "volume": 1_000_000})
    return {symbol: stock, "VNINDEX": index}


def test_episode_uses_exact_trading_sessions_and_path_metrics(tmp_path):
    days = trading_days(21)
    tracker = {
        "schema_version": signal_tracker.SCHEMA_VERSION,
        "updated_at": days[0],
        "episodes": [
            {
                "episode_id": f"AAA:{days[0]}",
                "eligible_for_metrics": True,
                "symbol": "AAA",
                "date_signal": days[0],
                "price_at_signal": 100.0,
                "stop_loss": 99.0,
                "take_profit": 106.0,
                "episode_status": "ACTIVE",
                "path_outcome": "OPEN",
            }
        ],
    }
    path = tmp_path / "tracker.json"
    path.write_text(json.dumps(tracker), encoding="utf-8")

    signal_tracker.update_tracker(
        path=path,
        results={},
        metrics_by_symbol={},
        history_store=history("AAA"),
        mode="eod",
    )
    episode = signal_tracker.load_tracker(path)["episodes"][0]

    assert episode["date_t5"] == days[5]
    assert episode["return_t5"] == pytest.approx(5.0)
    assert episode["benchmark_return_t5"] == pytest.approx(0.5)
    assert episode["excess_return_t5"] == pytest.approx(4.5)
    assert episode["mfe_t5_pct"] == pytest.approx(7.0)
    assert episode["mae_t5_pct"] == pytest.approx(-2.0)
    assert episode["return_t20"] == pytest.approx(20.0)
    assert episode["episode_status"] == "COMPLETED_T20"
    assert episode["path_outcome"] == "STOPPED"
    assert episode["outcome_session"] == 1


def test_only_one_active_episode_per_symbol(tmp_path):
    path = tmp_path / "tracker.json"
    rows = history("AAA", count=5)
    result = SimpleNamespace(
        failed_break=False,
        near_break=True,
        position_score=80,
        win_score=82,
        trade_score=84,
        confidence=75,
        price_unit="thousand_vnd",
        setup="VCP_BREAK",
        market_state="OPPORTUNITY",
        daily_phase="MARKUP",
        weekly_phase="ACCUMULATION",
        monthly_phase="ACCUMULATION",
        breakout_state="BREAKOUT_UNCONFIRMED",
    )
    metrics = {"AAA": {"advanced_score": 86, "trade": {}, "market_structure": {}}}

    first = signal_tracker.update_tracker(
        path=path,
        results={"AAA": result},
        metrics_by_symbol=metrics,
        history_store=rows,
        mode="morning_broad",
    )
    second = signal_tracker.update_tracker(
        path=path,
        results={"AAA": result},
        metrics_by_symbol=metrics,
        history_store=rows,
        mode="eod",
    )

    assert len(first) == 1
    assert second == []
    assert len(signal_tracker.load_tracker(path)["episodes"]) == 1


def test_legacy_tracker_is_audited_but_excluded(tmp_path):
    path = tmp_path / "tracker.json"
    path.write_text(
        json.dumps(
            [
                {"symbol": "AAA", "date_signal": "2026-01-02", "mode": "morning"},
                {"symbol": "AAA", "date_signal": "2026-01-02", "mode": "eod"},
                {"symbol": "BBB", "date_signal": "2026-01-03", "mode": "eod"},
            ]
        ),
        encoding="utf-8",
    )

    tracker = signal_tracker.load_tracker(path)

    assert tracker["episodes"] == []
    assert tracker["legacy_summary"]["record_count"] == 3
    assert tracker["legacy_summary"]["duplicate_records"] == 1
    assert tracker["legacy_summary"]["excluded_from_metrics"] is True
