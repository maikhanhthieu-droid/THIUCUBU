from __future__ import annotations

import regime_gate
from scan import ScanResult


def make_result(**overrides):
    values = {
        "symbol": "AAA",
        "sector": "Test",
        "close": 10.0,
        "win_score": 80,
        "setup": "VCP",
        "discount_pct": 20.0,
        "target_discount_pct": 15.0,
        "discount_group": "G1",
        "trend_score": 0,
        "base_score": 0,
        "flow_score": 0,
        "break_score": 0,
        "risk_score": 0,
        "rsi": 50.0,
        "mfi": 50.0,
        "vol_ratio": 1.0,
        "obv_up": True,
        "near_break": True,
        "failed_break": False,
        "warning": "",
        "reason": "test",
    }
    values.update(overrides)
    return ScanResult(**values)


def test_signal_gate_handles_null_nested_metrics():
    result = make_result()

    gate = regime_gate.signal_gate(result, {"regime": None, "rs": None, "trade": None, "weekly": None})

    assert gate["regime"] == "UNKNOWN"
    assert gate["allowed"] is True
    assert gate["rs_score"] == 50


def test_failed_break_is_blocked():
    result = make_result(failed_break=True)

    gate = regime_gate.signal_gate(result, {"regime": {"regime": "BULL"}})

    assert gate == {"allowed": False, "reason": "FAILED_BREAK"}
