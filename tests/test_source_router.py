from __future__ import annotations

from pathlib import Path

import source_router


SOURCES = ["FIINQUANT", "VCI", "KBS", "DNSE"]


def routing_with_priority(*symbols: str) -> dict:
    routing = source_router.default_routing()
    routing["fiinquant_priority"] = [
        {
            "symbol": symbol,
            "reasons": ["test"],
            "attention_score": 100,
            "consecutive_misses": 0,
        }
        for symbol in symbols
    ]
    return routing


def test_priority_uses_fiinquant_first_and_standard_uses_it_only_as_emergency() -> None:
    routing = routing_with_priority("VNM")

    priority = source_router.source_order("VNM", SOURCES, routing=routing)
    standard = source_router.source_order("HPG", SOURCES, routing=routing)

    assert priority[0] == "FIINQUANT"
    assert priority[1:] == [source for source in priority if source != "FIINQUANT"]
    assert standard[-1] == "FIINQUANT"
    assert set(standard[:3]) == {"VCI", "KBS", "DNSE"}


def test_unhealthy_fiinquant_yields_to_fallback_sources() -> None:
    order = source_router.source_order(
        "VNM",
        SOURCES,
        routing=routing_with_priority("VNM"),
        previous_health={"sources": {"FIINQUANT": {"health_score": 20}}},
    )

    assert order[-1] == "FIINQUANT"


def test_manual_standard_override_wins() -> None:
    routing = routing_with_priority("VNM")
    routing["manual"]["force_fiinquant"] = ["VNM"]
    routing["manual"]["force_standard"] = ["VNM"]

    order = source_router.source_order("VNM", SOURCES, routing=routing)

    assert order[-1] == "FIINQUANT"


def test_update_promotes_attention_and_balances_remaining_symbols(tmp_path: Path) -> None:
    path = tmp_path / "source_routing.json"
    results = [
        {
            "symbol": "VNM",
            "win_score": 84,
            "close": 61.9,
            "market_state": "OPPORTUNITY",
            "breakout_state": "HEALTHY_RETEST",
        },
        {
            "symbol": "HPG",
            "win_score": 48,
            "market_state": "CAUTION",
            "breakout_state": "NO_BREAKOUT",
        },
    ]

    routing = source_router.update_routing(
        results,
        universe=["VNM", "HPG", "FPT", "VCB"],
        mode="eod",
        path=path,
    )

    assert "VNM" in source_router.priority_symbols(routing)
    assert "HPG" not in source_router.priority_symbols(routing)
    standard = set().union(*routing["standard_routes"].values())
    assert standard == {"HPG", "FPT", "VCB"}
    assert routing["meta"]["priority_count"] == 1


def test_priority_demotes_only_after_three_evaluated_misses(tmp_path: Path) -> None:
    path = tmp_path / "source_routing.json"
    strong = {
        "symbol": "VNM",
        "win_score": 84,
        "market_state": "OPPORTUNITY",
        "breakout_state": "HEALTHY_RETEST",
    }
    weak = {
        "symbol": "VNM",
        "win_score": 35,
        "market_state": "CAUTION",
        "breakout_state": "NO_BREAKOUT",
    }
    source_router.update_routing([strong], universe=["VNM"], path=path)

    first = source_router.update_routing([weak], universe=["VNM"], path=path)
    second = source_router.update_routing([weak], universe=["VNM"], path=path)
    third = source_router.update_routing([weak], universe=["VNM"], path=path)

    assert first["fiinquant_priority"][0]["consecutive_misses"] == 1
    assert second["fiinquant_priority"][0]["consecutive_misses"] == 2
    assert "VNM" not in source_router.priority_symbols(third)
    assert "VNM" in set().union(*third["standard_routes"].values())


def test_manual_lists_survive_automatic_updates(tmp_path: Path) -> None:
    path = tmp_path / "source_routing.json"
    routing = source_router.default_routing()
    routing["manual"]["force_fiinquant"] = ["FPT"]
    routing["manual"]["force_standard"] = ["VNM"]
    source_router.save_routing(routing, path)

    updated = source_router.update_routing(
        [{"symbol": "VNM", "win_score": 90, "market_state": "OPPORTUNITY"}],
        universe=["FPT", "VNM"],
        path=path,
    )

    assert updated["manual"]["force_fiinquant"] == ["FPT"]
    assert updated["manual"]["force_standard"] == ["VNM"]
    assert source_router.priority_symbols(updated) == {"FPT"}


def test_committed_routing_has_disjoint_complete_groups() -> None:
    routing = source_router.load_routing(Path("data/source_routing.json"))
    priority = source_router.priority_symbols(routing)
    standard_lists = list(routing["standard_routes"].values())
    standard = [symbol for values in standard_lists for symbol in values]

    assert routing["schema_version"] == source_router.SCHEMA_VERSION
    assert len(standard) == len(set(standard))
    assert priority.isdisjoint(standard)
    assert routing["meta"]["priority_count"] == len(priority)
    assert routing["meta"]["standard_count"] == len(standard)


def test_manual_priority_cannot_exceed_fiinquant_free_plan_cap(tmp_path: Path) -> None:
    path = tmp_path / "source_routing.json"
    symbols = [f"A{index:02d}" for index in range(35)]
    routing = source_router.default_routing()
    routing["manual"]["force_fiinquant"] = symbols
    source_router.save_routing(routing, path)

    updated = source_router.update_routing([], universe=symbols, path=path)

    assert len(updated["fiinquant_priority"]) == 32
    assert updated["manual"]["force_fiinquant"] == symbols
