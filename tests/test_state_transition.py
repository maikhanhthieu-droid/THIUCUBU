from __future__ import annotations

from types import SimpleNamespace

import state_transition


def result(as_of: str, score: int = 68):
    return SimpleNamespace(
        as_of=as_of,
        win_score=score,
        market_state="ACCUMULATION",
        breakout_state="NO_BREAKOUT",
        daily_phase="ACCUMULATION",
        weekly_phase="ACCUMULATION",
        monthly_phase="ACCUMULATION",
    )


def metrics(score: int, market: str, breakout: str):
    return {
        "AAA": {
            "advanced_score": score,
            "market_structure": {
                "overall_state": market,
                "breakout": {"state": breakout},
            },
        }
    }


def test_first_snapshot_seeds_without_noise_then_reports_material_change(tmp_path):
    path = tmp_path / "states.json"
    first = state_transition.update_transitions(
        path=path,
        results={"AAA": result("2026-01-02")},
        metrics_by_symbol=metrics(68, "ACCUMULATION", "NO_BREAKOUT"),
    )
    second = state_transition.update_transitions(
        path=path,
        results={"AAA": result("2026-01-05", 84)},
        metrics_by_symbol=metrics(84, "OPPORTUNITY", "BREAKOUT_CONFIRMED"),
    )
    repeated = state_transition.update_transitions(
        path=path,
        results={"AAA": result("2026-01-05", 84)},
        metrics_by_symbol=metrics(84, "OPPORTUNITY", "BREAKOUT_CONFIRMED"),
    )

    assert first == []
    assert {item["kind"] for item in second} == {"BREAKOUT_STATE", "MARKET_STATE", "SCORE_UP"}
    assert repeated == []


def test_partial_scan_preserves_unseen_symbol_state(tmp_path):
    path = tmp_path / "states.json"
    state_transition.update_transitions(
        path=path,
        results={"AAA": result("2026-01-02"), "BBB": result("2026-01-02")},
        metrics_by_symbol={
            **metrics(68, "ACCUMULATION", "NO_BREAKOUT"),
            "BBB": metrics(68, "ACCUMULATION", "NO_BREAKOUT")["AAA"],
        },
    )
    state_transition.update_transitions(
        path=path,
        results={"AAA": result("2026-01-05")},
        metrics_by_symbol=metrics(68, "ACCUMULATION", "NO_BREAKOUT"),
    )

    stored = state_transition._load(path)
    assert "BBB" in stored["states"]
