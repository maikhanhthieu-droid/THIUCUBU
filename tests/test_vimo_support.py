from __future__ import annotations

import json

import vimo_support


def test_daily_candidate_selection_uses_advanced_score(monkeypatch, tmp_path):
    payload = {
        "top": [
            {"symbol": "VNINDEX", "win_score": 60},
            {"symbol": "FPT", "win_score": 70, "close": 69.2},
        ],
        "advanced_top": {
            "FPT": {
                "advanced_score": 81,
                "market_structure": {"overall_state": "OPPORTUNITY"},
            }
        },
    }
    (tmp_path / "session_alerts_latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(vimo_support, "DATA_DIR", tmp_path)

    assert vimo_support._daily_candidates(8) == [
        {
            "symbol": "FPT",
            "local_score": 81,
            "local_price": 69.2,
            "failed_break": False,
            "market_state": "OPPORTUNITY",
        }
    ]


def test_daily_verdict_support_conflict_and_price_mismatch():
    positive = {
        "symbol": "FPT",
        "local_score": 80,
        "local_price": 70,
        "failed_break": False,
        "market_state": "OPPORTUNITY",
    }
    support = vimo_support._daily_verdict(
        positive,
        {"signal": "BUY", "confidence": 70, "price": 70, "date": ""},
    )
    conflict = vimo_support._daily_verdict(
        positive,
        {"signal": "SELL", "confidence": 70, "price": 70, "date": ""},
    )
    mismatch = vimo_support._daily_verdict(
        positive,
        {"signal": "BUY", "confidence": 70, "price": 55, "date": ""},
    )

    assert support["status"] == "SUPPORT"
    assert conflict["status"] == "CONFLICT"
    assert mismatch["status"] == "DATA_MISMATCH"


def test_change_filter_only_emits_material_updates():
    previous = {"status": "SUPPORT", "signal": "BUY", "local_score": 78}

    assert not vimo_support._changed(
        {"status": "SUPPORT", "signal": "BUY", "local_score": 82}, previous
    )
    assert vimo_support._changed(
        {"status": "CONFLICT", "signal": "SELL", "local_score": 82}, previous
    )
    assert vimo_support._changed(
        {"status": "SUPPORT", "signal": "BUY", "local_score": 85}, previous
    )

