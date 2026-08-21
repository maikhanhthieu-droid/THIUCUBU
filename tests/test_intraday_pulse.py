from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pandas as pd

import intraday_pulse as pulse
import market_strategy
import session_scan


def test_normalize_kbs_board_repairs_units_and_keeps_liquidity() -> None:
    frame = pd.DataFrame(
        [
            {
                "symbol": "NVL",
                "reference_price": 13_000,
                "open_price": 13_100,
                "high_price": 13_600,
                "low_price": 12_950,
                "close_price": 13_500,
                "volume_accumulated": 2_000_000,
                "total_value": 27_000_000_000,
                "bid_price_1": 13_450,
                "bid_vol_1": 120_000,
                "ask_price_1": 13_500,
                "ask_vol_1": 80_000,
            }
        ]
    )

    row = pulse.normalize_board(frame, "KBS")["NVL"]

    assert row["close"] == 13.5
    assert row["reference"] == 13.0
    assert row["value"] == 27_000_000_000
    assert row["bid_price_1"] == 13.45
    assert row["source"] == "KBS"


def test_compare_snapshots_detects_material_up_and_down_events() -> None:
    previous = {
        "AAA": {"close": 10.0, "reference": 10.0, "value": 5_000_000_000},
        "BBB": {"close": 20.0, "reference": 20.0, "value": 8_000_000_000},
        "CCC": {"close": 30.0, "reference": 30.0, "value": 1_000_000_000},
    }
    current = {
        "AAA": {
            "close": 10.3,
            "reference": 10.0,
            "high": 10.3,
            "low": 9.9,
            "value": 35_000_000_000,
            "bid_volume_1": 200_000,
            "ask_volume_1": 50_000,
            "source": "KBS",
        },
        "BBB": {
            "close": 19.6,
            "reference": 20.0,
            "high": 20.2,
            "low": 19.6,
            "value": 38_000_000_000,
            "bid_volume_1": 50_000,
            "ask_volume_1": 150_000,
            "source": "KBS",
        },
        "CCC": {
            "close": 30.03,
            "reference": 30.0,
            "high": 30.1,
            "low": 29.9,
            "value": 1_100_000_000,
            "source": "KBS",
        },
    }

    events = pulse.compare_snapshots(current, previous, elapsed_minutes=30)
    by_symbol = {item.symbol: item for item in events}

    assert by_symbol["AAA"].direction == "UP"
    assert by_symbol["AAA"].value_30m_billion == 30.0
    assert by_symbol["BBB"].direction == "DOWN"
    assert "CCC" not in by_symbol

    compact = pulse.compact_snapshot(current)
    assert set(compact["AAA"]) == {"close", "reference", "value"}
    assert "high" not in compact["AAA"]


def test_pulse_report_lists_symbols_first_and_portfolio_even_without_event() -> None:
    events = pulse.compare_snapshots(
        {
            "AAA": {
                "close": 10.3,
                "reference": 10.0,
                "high": 10.3,
                "low": 9.9,
                "value": 35_000_000_000,
                "source": "KBS",
            }
        },
        {"AAA": {"close": 10.0, "reference": 10.0, "value": 5_000_000_000}},
        elapsed_minutes=30,
    )
    report = pulse.build_report(
        generated_at=datetime(2026, 8, 21, 10, 0, tzinfo=pulse.VN_TZ),
        board={
            "NVL": {
                "close": 13.5,
                "reference": 13.0,
                "value": 10_000_000_000,
                "source": "KBS",
            }
        },
        events=events,
        market={"close": 1_300, "open": 1_295},
        portfolio_symbols=["NVL", "SMC"],
        fetch_meta={"requested": 3, "source_counts": {"KBS": 3}},
        elapsed_minutes=30,
    )

    assert "*MÃ ĐỘT BIẾN TÓM TẮT*" in report
    assert "Tăng: `AAA`" in report
    assert "*PORTFOLIO PULSE*" in report
    assert "`NVL` Giá 13.50" in report
    assert "`SMC` NO_DATA" in report


def test_second_source_mismatch_is_downgraded_not_used_as_directional_signal(monkeypatch) -> None:
    event = pulse.PulseEvent(
        symbol="AAA",
        event_type="BREAKOUT_PULSE",
        direction="UP",
        score=80,
        price=10.0,
        change_30m_pct=3.0,
        session_change_pct=4.0,
        value_30m_billion=30.0,
        total_value_billion=100.0,
        close_position=1.0,
        order_imbalance=2.0,
        source="KBS",
        verified=None,
        reasons=["giá 30p +3%"],
    )
    monkeypatch.setattr(pulse, "_create_client", lambda source: object())
    monkeypatch.setattr(
        pulse,
        "_fetch_batch",
        lambda client, source, symbols: {"AAA": {"close": 11.0}},
    )

    checked = pulse.verify_events([event])[0]

    assert checked.verified is False
    assert checked.event_type == "SOURCE_MISMATCH"
    assert checked.direction == "NEUTRAL"
    assert checked.score == 55


def test_pulse_window_covers_both_sessions_but_not_lunch() -> None:
    def at(hour: int, minute: int) -> datetime:
        return datetime(2026, 8, 21, hour, minute, tzinfo=pulse.VN_TZ)

    assert pulse.in_pulse_window(at(9, 0))
    assert pulse.in_pulse_window(at(11, 45))
    assert not pulse.in_pulse_window(at(12, 30))
    assert pulse.in_pulse_window(at(13, 0))
    assert pulse.in_pulse_window(at(15, 0))


def test_today_pulse_symbols_are_promoted_to_next_fixed_scan(tmp_path, monkeypatch) -> None:
    today = datetime(2026, 8, 21, 15, 5, tzinfo=session_scan.VN_TZ)
    payload = {
        "trading_date": "2026-08-21",
        "top_symbols": ["AAA", "NVL", "AAA", "BAD-SYMBOL"],
    }
    (tmp_path / "intraday_pulse_latest.json").write_text(json.dumps(payload), encoding="utf-8")
    state = {
        "trading_date": "2026-08-21",
        "events": [
            {
                "items": [
                    {"symbol": "SMC", "score": 81, "direction": "UP"},
                    {"symbol": "AAA", "score": 75, "direction": "DOWN"},
                ]
            }
        ],
    }
    (tmp_path / "intraday_pulse_state.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(session_scan, "DATA_DIR", tmp_path)

    assert session_scan.intraday_pulse_symbols(now=today) == ["SMC", "AAA", "NVL"]

    stale = today.replace(day=22)
    assert session_scan.intraday_pulse_symbols(now=stale) == []


def test_end_of_day_horizon_strategy_separates_trade_horizons() -> None:
    bull_market = SimpleNamespace(
        win_score=72,
        failed_break=False,
        market_state="OPPORTUNITY",
    )
    bull = market_strategy.horizon_strategy(bull_market, {"regime": "BULL"})
    assert bull["posture"] == "TÍCH CỰC CÓ CHỌN LỌC"
    assert "Luồng 2" in bull["scalp"]
    assert "từng phần" in bull["accumulate"]

    bear_market = SimpleNamespace(
        win_score=35,
        failed_break=True,
        market_state="DISTRIBUTION",
    )
    bear = market_strategy.horizon_strategy(bear_market, {"regime": "BEAR"})
    assert bear["posture"] == "PHÒNG THỦ / CẨN TRỌNG CAO"
    assert "vẫn quét" in bear["risk"]
