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


def test_primary_stream_change_is_reported_after_stream_state_has_been_seeded(tmp_path):
    path = tmp_path / "states.json"
    early_metrics = metrics(68, "CAUTION", "NO_BREAKOUT")
    early_metrics["AAA"]["primary_stream"] = "early"
    opportunity_metrics = metrics(82, "OPPORTUNITY", "BREAKOUT_CONFIRMED")
    opportunity_metrics["AAA"]["primary_stream"] = "opportunity"

    state_transition.update_transitions(
        path=path,
        results={"AAA": result("2026-01-02")},
        metrics_by_symbol=early_metrics,
    )
    events = state_transition.update_transitions(
        path=path,
        results={"AAA": result("2026-01-05", 82)},
        metrics_by_symbol=opportunity_metrics,
    )

    stream_events = [item for item in events if item["kind"] == "PRIMARY_STREAM"]
    assert len(stream_events) == 1
    assert stream_events[0]["from"] == "early"
    assert stream_events[0]["to"] == "opportunity"


def test_new_pre_label_is_reported_once(tmp_path):
    path = tmp_path / "states.json"
    before = metrics(68, "ACCUMULATION", "NO_BREAKOUT")
    before["AAA"]["technical_watch"] = {"pre_label": "NONE", "risk_label": "NONE"}
    after = metrics(68, "ACCUMULATION", "NO_BREAKOUT")
    after["AAA"]["technical_watch"] = {"pre_label": "PRE-DIV-3", "risk_label": "NONE"}

    state_transition.update_transitions(
        path=path,
        results={"AAA": result("2026-01-02")},
        metrics_by_symbol=before,
    )
    events = state_transition.update_transitions(
        path=path,
        results={"AAA": result("2026-01-05")},
        metrics_by_symbol=after,
    )
    repeated = state_transition.update_transitions(
        path=path,
        results={"AAA": result("2026-01-05")},
        metrics_by_symbol=after,
    )

    assert [item["kind"] for item in events] == ["PRE_SIGNAL"]
    assert repeated == []
