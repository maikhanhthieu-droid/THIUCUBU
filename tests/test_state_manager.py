from __future__ import annotations

from types import SimpleNamespace

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
