from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pandas as pd

import intraday_pulse as pulse
import market_strategy
import session_scan


def test_pulse_source_rate_is_hard_capped_at_seventy_percent(monkeypatch) -> None:
    monkeypatch.setenv("PULSE_SOURCE_LIMITS", "KBS=20,VCI=10")
    monkeypatch.setenv("PULSE_SOURCE_USAGE_RATIO", "0.95")

    assert pulse.pulse_source_effective_rpm("KBS") == 14.0
    assert pulse.pulse_source_effective_rpm("VCI") == 7.0


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


def test_pulse_report_is_compact_and_lists_filtered_symbols_first() -> None:
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

    assert "*TOP ĐỘT BIẾN CHẤT LƯỢNG" in report
    assert "Mã: `AAA`" in report
    assert "`AAA` [NO_DATA]" in report
    assert "*PORTFOLIO PULSE*" not in report
    assert "`SMC` NO_DATA" not in report


def test_cross_section_out_band_detects_only_the_true_tail() -> None:
    previous = {}
    current = {}
    for index in range(30):
        symbol = f"A{index:02d}"
        previous[symbol] = {"close": 10.0, "reference": 10.0, "value": 2_000_000_000}
        change = 0.001 * ((index % 5) - 2)
        current[symbol] = {
            "close": 10.0 * (1 + change),
            "reference": 10.0,
            "high": 10.1,
            "low": 9.9,
            "value": 2_100_000_000,
            "source": "KBS",
        }
    previous["HOT"] = {"close": 10.0, "reference": 10.0, "value": 2_000_000_000}
    current["HOT"] = {
        "close": 10.28,
        "reference": 10.0,
        "high": 10.28,
        "low": 9.95,
        "value": 8_000_000_000,
        "source": "KBS",
    }

    events = pulse.compare_snapshots(current, previous, elapsed_minutes=30)
    hot = next(item for item in events if item.symbol == "HOT")

    assert hot.out_of_band is True
    assert hot.event_type == "OUT_BAND_UP"
    assert hot.outlier_z is not None and hot.outlier_z > 3


def test_actionable_filter_keeps_quality_and_out_band_but_drops_weak_noise() -> None:
    def event(symbol: str, *, score: int, direction: str = "UP", out_band: bool = False) -> pulse.PulseEvent:
        return pulse.PulseEvent(
            symbol=symbol,
            event_type="OUT_BAND_UP" if out_band else "BUYING_SURGE",
            direction=direction,
            score=score,
            price=10,
            change_30m_pct=2.8 if out_band else 1.2,
            session_change_pct=3,
            value_30m_billion=8,
            total_value_billion=40,
            close_position=0.9,
            order_imbalance=2,
            source="KBS",
            verified=True,
            reasons=[],
            out_of_band=out_band,
            outlier_z=4.0 if out_band else None,
        )

    filtered = pulse.select_actionable_events(
        [
            event("GOOD", score=46),
            event("WEAK", score=70),
            event("TAIL", score=60, out_band=True),
            event("RISK", score=65, direction="DOWN", out_band=True),
        ],
        {
            "GOOD": {"eligible": True, "label": "TÍCH LŨY", "score": 72, "primary_stream": "early"},
            "WEAK": {"eligible": False, "label": "KHÔNG ĐẠT", "score": 30},
            "RISK": {"eligible": False, "label": "KHÔNG ĐẠT", "score": 20},
        },
        limit=5,
    )

    symbols = [item.symbol for item in filtered]
    assert "GOOD" in symbols
    assert "TAIL" in symbols
    assert "WEAK" not in symbols
    assert "RISK" not in symbols
    assert next(item for item in filtered if item.symbol == "GOOD").quality_state == "TÍCH LŨY"


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
