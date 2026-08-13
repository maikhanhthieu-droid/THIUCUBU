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


def test_unconfirmed_failed_break_and_distribution_are_blocked():
    result = make_result()
    failed_watch = regime_gate.signal_gate(
        result,
        {
            "market_structure": {
                "overall_state": "CAUTION",
                "breakout": {"state": "FAILED_BREAK_WATCH"},
                "timeframes": {"1W": {"state": "MARKUP"}, "1M": {"state": "MARKUP"}},
            }
        },
    )
    distribution = regime_gate.signal_gate(
        result,
        {
            "market_structure": {
                "overall_state": "DISTRIBUTION",
                "breakout": {"state": "NO_BREAKOUT"},
                "timeframes": {"1W": {"state": "DISTRIBUTION"}, "1M": {"state": "TRANSITION"}},
            }
        },
    )

    assert failed_watch["allowed"] is False
    assert failed_watch["reason"] == "BREAK_XIT_CHO_XAC_NHAN"
    assert distribution["allowed"] is False
    assert distribution["reason"] == "MTF_PHAN_PHOI"
