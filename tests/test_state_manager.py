from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import state_manager


def make_result(symbol: str, score: int, near_break: bool = True, obv_up: bool = True):
    return SimpleNamespace(
        symbol=symbol,
        sector="Test",
        close=10.0 + score,
        win_score=score,
        setup="VCP",
        near_break=near_break,
        obv_up=obv_up,
        failed_break=False,
        rsi=55,
        mfi=60,
        vol_ratio=1.2,
        reason="unit test",
    )


def test_update_memory_state_caps_buckets(tmp_path):
    path = tmp_path / "memory_state.json"
    results = {f"S{i:02d}": make_result(f"S{i:02d}", 90 - i) for i in range(30)}

    state = state_manager.update_memory_state(results, "test", path=path)

    assert path.exists()
    assert len(state["strong_stocks"]) == state_manager.STRONG_LIMIT
    assert len(state["watchlist"]) <= state_manager.WATCHLIST_LIMIT
    assert len(state["session_focus"]) <= state_manager.SESSION_FOCUS_LIMIT
    assert state["strong_stocks"][0]["symbol"] == "S00"


def test_memory_focus_symbols_uses_strong_then_watchlist():
    state = {
        "strong_stocks": [{"symbol": "TCB"}, {"symbol": "VCB"}],
        "watchlist": [{"symbol": "FPT"}, {"symbol": "HPG"}],
        "session_focus": ["DIG", "TCB"],
    }

    assert state_manager.memory_focus_symbols(state, limit=5) == ["TCB", "VCB", "FPT", "HPG", "DIG"]


def test_state_manager_class_updates_and_prunes(tmp_path):
    path = tmp_path / "memory_state.json"
    manager = state_manager.StateManager(path)
    stale_day = (datetime.now(timezone(timedelta(hours=7))) - timedelta(days=45)).date().isoformat()
    manager.save(
        {
            "strong_stocks": [{"symbol": "OLD", "last_seen": stale_day, "score": 99}],
            "watchlist": [{"symbol": "FPT", "last_seen": stale_day, "score": 70}],
            "session_focus": ["OLD", "FPT"],
        },
        mode="seed",
    )

    state = manager.update_strong_stocks({"TCB": make_result("TCB", 95)}, mode="unit")

    assert path.exists()
    assert state["last_mode"] == "unit"
    assert [item["symbol"] for item in state["strong_stocks"]] == ["TCB"]
    assert "OLD" not in state["session_focus"]
