from __future__ import annotations

import numpy as np
import pandas as pd

import scan
import sector_rotation
from scan import ScanResult


def make_result(symbol: str, sector: str, score: int) -> ScanResult:
    return ScanResult(
        symbol=symbol,
        sector=sector,
        close=20,
        win_score=score,
        setup="BASE",
        discount_pct=20,
        target_discount_pct=20,
        discount_group="G1",
        trend_score=score,
        base_score=score,
        flow_score=score,
        break_score=0,
        risk_score=0,
        rsi=55,
        mfi=60,
        vol_ratio=1,
        obv_up=score >= 50,
        near_break=score >= 60,
        failed_break=False,
        warning="",
        reason="test",
        as_of="2026-08-21",
    )


def history(values: np.ndarray) -> list[dict]:
    dates = pd.bdate_range("2026-05-01", periods=len(values))
    return [
        {"time": date.isoformat(), "close": float(value), "volume": 1_000_000}
        for date, value in zip(dates, values)
    ]


def test_sector_rotation_ranks_relative_strength_and_assigns_four_states(tmp_path) -> None:
    index = np.linspace(1_000, 1_080, 80)
    results = [
        make_result("AAA", "Lead", 85),
        make_result("AAB", "Lead", 82),
        make_result("AAC", "Lead", 80),
        make_result("BBB", "Lag", 28),
        make_result("BBC", "Lag", 32),
        make_result("BBD", "Lag", 30),
    ]
    store = {
        "VNINDEX": history(index),
        "AAA": history(np.linspace(10, 16, 80)),
        "AAB": history(np.linspace(15, 23, 80)),
        "AAC": history(np.linspace(12, 19, 80)),
        "BBB": history(np.linspace(20, 13, 80)),
        "BBC": history(np.linspace(18, 12, 80)),
        "BBD": history(np.linspace(16, 11, 80)),
    }

    current, alerts = sector_rotation.update_sector_rotation(
        results,
        history_store=store,
        persist=True,
        path=tmp_path / "sector.json",
    )

    assert alerts == []
    assert current["Lead"]["rank"] == 1
    assert current["Lead"]["rs_1m_pct"] > 0
    assert current["Lead"]["state"] in {"LEADING", "ENTERING"}
    assert current["Lag"]["state"] == "LAGGING"
    assert current["Lag"]["rs_1m_pct"] < 0


def test_legacy_sector_history_is_migrated_without_losing_snapshots(tmp_path) -> None:
    path = tmp_path / "sector.json"
    scan.json_save(
        path,
        [
            {
                "updated_at": "2026-08-01T15:05:00+07:00",
                "sectors": [{"sector": "Legacy", "score": 44, "count": 5}],
            }
        ],
    )
    result = make_result("AAA", "Lead", 80)
    store = {
        "VNINDEX": history(np.linspace(1_000, 1_080, 80)),
        "AAA": history(np.linspace(10, 16, 80)),
    }

    sector_rotation.update_sector_rotation(
        [result],
        history_store=store,
        persist=True,
        path=path,
    )
    payload = scan.json_load(path, {})

    assert payload["schema_version"] == sector_rotation.SCHEMA_VERSION
    assert any(item["trading_date"] == "2026-08-01" for item in payload["history"])
    assert "Lead" in payload["current"]
