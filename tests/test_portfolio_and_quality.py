from __future__ import annotations

import pandas as pd
import pytest

import market_intel as intel
import scan


def _sample_df(periods: int = 130, *, flat_tail: int = 0, jump: bool = False) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=periods, freq="B")
    closes = [20.0 + i * 0.1 for i in range(periods)]
    if jump and periods > 20:
        closes[-15] = closes[-16] * 1.25
    if flat_tail > 0:
        for idx in range(1, flat_tail + 1):
            closes[-idx] = closes[-(flat_tail + 1)]
    close_s = pd.Series(closes)
    return pd.DataFrame(
        {
            "time": dates,
            "open": close_s - 0.1,
            "high": close_s + 0.3,
            "low": close_s - 0.3,
            "close": close_s,
            "volume": [1_000_000 + i * 1000 for i in range(periods)],
        }
    )


def test_assess_ohlcv_quality_detects_clean_and_anomalous_series():
    clean_df = _sample_df(130)
    clean_q = intel.assess_ohlcv_quality(clean_df, "VCB")
    assert clean_q["quality_score"] == 100
    assert clean_q["anomalies"] == []

    anom_df = _sample_df(90, flat_tail=6, jump=True)
    anom_q = intel.assess_ohlcv_quality(anom_df, "AAA")
    assert anom_q["quality_score"] < 70
    assert any(item.startswith("FLAT_CLOSE_") for item in anom_q["anomalies"])
    assert any(item.startswith("EXTREME_JUMP_") for item in anom_q["anomalies"])
    assert "SHORT_HISTORY" in anom_q["anomalies"]


def test_compute_trade_levels_includes_atr_chandelier_and_weight():
    df = _sample_df(60)
    close_now = float(df["close"].iloc[-1])
    res = scan.ScanResult(
        symbol="VCB",
        sector="Bank",
        close=close_now,
        win_score=84,
        setup="VCP_BREAK",
        discount_pct=15.0,
        target_discount_pct=15.0,
        discount_group="G1",
        trend_score=30,
        base_score=20,
        flow_score=20,
        break_score=14,
        risk_score=0,
        rsi=58.0,
        mfi=62.0,
        vol_ratio=1.2,
        obv_up=True,
        near_break=True,
        failed_break=False,
        warning="",
        reason="ok",
    )
    levels = intel.find_key_levels(df)
    trade = intel.compute_trade_levels(res, levels, df=df, regime="BULL")
    assert trade["atr_14"] is not None and trade["atr_14"] > 0
    assert trade["chandelier_stop"] is not None
    assert trade["ema20"] is not None
    assert trade["trailing_state"] == "SAFE"
    assert trade["suggested_weight_pct"] > 0


def test_analyze_portfolio_risk_detects_concentration_correlation_and_trailing():
    df_base = _sample_df(80)
    history_store = {
        "VCB": df_base.assign(time=df_base["time"].dt.strftime("%Y-%m-%d")).to_dict(orient="records"),
        "BID": df_base.assign(time=df_base["time"].dt.strftime("%Y-%m-%d")).to_dict(orient="records"),
        "CTG": df_base.assign(time=df_base["time"].dt.strftime("%Y-%m-%d")).to_dict(orient="records"),
        "FPT": df_base.assign(
            time=df_base["time"].dt.strftime("%Y-%m-%d"),
            close=[30.0 + ((-1) ** i) * 0.5 for i in range(len(df_base))],
        ).to_dict(orient="records"),
    }
    results = {
        sym: scan.ScanResult(
            symbol=sym,
            sector="Bank" if sym in {"VCB", "BID", "CTG"} else "Cong nghe vien thong",
            close=25.0,
            win_score=80,
            setup="BASE",
            discount_pct=15.0,
            target_discount_pct=20.0,
            discount_group="G2",
            trend_score=25,
            base_score=20,
            flow_score=20,
            break_score=15,
            risk_score=0,
            rsi=55.0,
            mfi=55.0,
            vol_ratio=1.0,
            obv_up=True,
            near_break=False,
            failed_break=(sym == "CTG"),
            warning="",
            reason="test",
        )
        for sym in ("VCB", "BID", "CTG", "FPT")
    }
    watch_items = {sym: {"note": "hold"} for sym in ("VCB", "BID", "CTG", "FPT")}
    metrics = {
        "VCB": {"trade": {"trailing_state": "SAFE", "chandelier_stop": 23.5, "ema20": 24.0}},
        "BID": {"trade": {"trailing_state": "TIGHTEN", "chandelier_stop": 24.5, "ema20": 25.2}},
        "CTG": {"trade": {"trailing_state": "BROKEN", "chandelier_stop": 25.5, "ema20": 25.8}},
        "FPT": {"trade": {"trailing_state": "SAFE", "chandelier_stop": 28.0, "ema20": 28.5}},
    }

    risk = intel.analyze_portfolio_risk(results, watch_items, metrics, history_store)
    assert risk["active_count"] == 4
    assert len(risk["concentration_warnings"]) == 1
    assert risk["concentration_warnings"][0]["sector"] == "Bank"
    assert risk["concentration_warnings"][0]["share_pct"] == pytest.approx(75.0)
    assert any(item["pair"] == "BID-VCB" for item in risk["high_correlations"])
    assert len(risk["trailing_alerts"]) == 2

    summary_lines = intel.format_portfolio_risk_summary(risk)
    assert len(summary_lines) >= 3
    assert any("Tập trung ngành cao" in line for line in summary_lines)
    assert any("Tương quan 60P cao" in line for line in summary_lines)



def test_analyze_breakout_acceleration_detects_squeeze_ignition_and_pocket_pivot():
    import technical_features as tf

    periods = 130
    dates = pd.date_range("2026-01-01", periods=periods, freq="B")
    closes = []
    opens = []
    highs = []
    lows = []
    volumes = []

    price = 20.0
    for i in range(periods):
        if i < 105:
            # Steady uptrend to build SEPA trend alignment
            price += 0.08
            o = price - 0.06
            c = price
            h = price + 0.12
            l = price - 0.18
            v = 1_000_000
        elif i < 128:
            # Tight volatility coil (narrow range, light down-volume)
            delta = 0.03 if i % 2 == 0 else -0.02
            price += delta
            o = price - 0.02
            c = price
            h = price + 0.05
            l = price - 0.05
            v = 700_000 if delta < 0 else 950_000
        else:
            # Explosive Pocket Pivot & Squeeze breakout bars at the end
            price += 0.55
            o = price - 0.48
            c = price
            h = price + 0.05
            l = price - 0.52
            v = 2_400_000
        opens.append(o)
        closes.append(c)
        highs.append(h)
        lows.append(l)
        volumes.append(v)

    df = pd.DataFrame(
        {
            "time": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )
    index_df = pd.DataFrame(
        {
            "time": dates,
            "open": [1200.0 + i * 0.3 for i in range(periods)],
            "high": [1202.0 + i * 0.3 for i in range(periods)],
            "low": [1198.0 + i * 0.3 for i in range(periods)],
            "close": [1200.0 + i * 0.3 for i in range(periods)],
            "volume": [500_000_000 for _ in range(periods)],
        }
    )

    res = scan.ScanResult(
        symbol="FPT",
        sector="Cong nghe vien thong",
        close=closes[-1],
        win_score=86,
        setup="VCP_BREAK",
        discount_pct=12.0,
        target_discount_pct=15.0,
        discount_group="G1",
        trend_score=32,
        base_score=22,
        flow_score=20,
        break_score=12,
        risk_score=0,
        rsi=65.0,
        mfi=68.0,
        vol_ratio=2.4,
        obv_up=True,
        near_break=True,
        failed_break=False,
        warning="",
        reason="Squeeze breakout",
    )

    acc = tf.analyze_breakout_acceleration(df, res, index_df)
    assert acc["strict_conviction"] is True
    assert acc["strict_gates_passed"] >= 5
    assert acc["acceleration_score"] >= 78
    assert acc["pocket_pivot"] is True
    assert acc["cmf_strong"] is True
    assert acc["rs_line_new_high_60d"] is True
    assert acc["sepa_trend_aligned"] is True
    assert acc["tier"] in {"SQUEEZE_IGNITION", "POCKET_PIVOT_CONVICTION", "MOMENTUM_SURGE"}

    # Verify market_intel strict pick selector and formatter
    metrics = {
        "FPT": {
            "quality": {"quality_score": 100, "anomalies": []},
            "trade": {
                "entry_zone": "29.00-29.50",
                "stop_loss": 27.8,
                "chandelier_stop": 28.1,
                "target_1": 32.5,
                "rr_t1": 2.4,
                "suggested_weight_pct": 12.0,
            },
            "advanced": {
                "signal_score": 88,
                "confidence": 85,
                "breakout_acceleration": acc,
            },
        }
    }
    picks = intel.select_strict_acceleration_picks({"FPT": res}, metrics)
    assert len(picks) == 1
    assert picks[0]["symbol"] == "FPT"
    section_text = "\n".join(intel.format_strict_acceleration_section(picks))
    assert "LỌC GẮT 6 CỔNG" in section_text
    assert "`FPT`" in section_text
