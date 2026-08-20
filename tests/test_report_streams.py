from __future__ import annotations

from types import SimpleNamespace

import report_streams


def row(symbol: str, score: int = 70):
    return SimpleNamespace(
        symbol=symbol,
        win_score=score,
        position_score=score,
        trade_score=score,
        market_state="CAUTION",
        breakout_state="NO_BREAKOUT",
        failed_break=False,
    )


def metric(
    *,
    score: int = 70,
    market: str = "CAUTION",
    breakout: str = "NO_BREAKOUT",
    early: dict | None = None,
    technical: dict | None = None,
):
    return {
        "advanced_score": score,
        "market_structure": {
            "overall_state": market,
            "breakout": {"state": breakout},
        },
        "early_accumulation": early or {},
        "technical_watch": technical or {},
    }


def test_five_stream_assignment_is_exclusive_and_portfolio_wins() -> None:
    rows = {symbol: row(symbol) for symbol in ["PFL", "OPP", "EAR", "TEC", "BRK", "OUT"]}
    metrics = {
        "PFL": metric(market="OPPORTUNITY", breakout="REACCUMULATION"),
        "OPP": metric(score=82, market="OPPORTUNITY"),
        "EAR": metric(early={"eligible": True, "score": 73, "stage": "E2"}),
        "TEC": metric(technical={"watch": True, "score": 61}),
        "BRK": metric(breakout="FAILED_BREAK_WATCH"),
        "OUT": metric(score=30),
    }

    streams = report_streams.classify_streams(rows, metrics, {"PFL": {"note": "hold"}})

    assert [item.symbol for item in streams[report_streams.PORTFOLIO]] == ["PFL"]
    assert [item.symbol for item in streams[report_streams.OPPORTUNITY]] == ["OPP"]
    assert [item.symbol for item in streams[report_streams.EARLY]] == ["EAR"]
    assert [item.symbol for item in streams[report_streams.TECHNICAL]] == ["TEC"]
    assert [item.symbol for item in streams[report_streams.STRUCTURE]] == ["BRK"]
    displayed = [item.symbol for stream in streams.values() for item in stream]
    assert len(displayed) == len(set(displayed))
    assert "OUT" not in displayed


def test_symbol_summary_shows_codes_before_detail_and_keeps_no_data_portfolio() -> None:
    result = row("AAA")
    summary = report_streams.symbol_summary(
        report_streams.PORTFOLIO,
        [result],
        {"AAA": metric(score=81)},
        extra_symbols=["AAA", "BBB"],
    )

    assert summary.startswith("`AAA` 81")
    assert "`BBB` NO_DATA" in summary

    payload = report_streams.serialize_streams(
        {key: [result] if key == report_streams.PORTFOLIO else [] for key in report_streams.DISPLAY_ORDER},
        {"AAA": metric(score=81)},
        portfolio_symbols=["AAA", "BBB"],
    )
    assert payload["schema_version"] == "thieucubu.five_streams.v1"
    assert payload["streams"]["portfolio"][1]["symbol"] == "BBB"
    assert payload["streams"]["portfolio"][1]["data_status"] == "NO_DATA"
