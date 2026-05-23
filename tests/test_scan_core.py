from __future__ import annotations

import os
from datetime import datetime, timedelta

import pandas as pd

import scan
import fetcher


def test_cache_fresh_rejects_previous_vn_day(tmp_path):
    path = tmp_path / "cached.parquet"
    path.write_text("x", encoding="utf-8")
    yesterday = datetime.now(scan.VN_TZ) - timedelta(days=1)
    os.utime(path, (yesterday.timestamp(), yesterday.timestamp()))

    assert scan.is_cache_fresh(path, ttl_minutes=999_999) is False


def test_direct_fetch_filters_unsupported_tcbs(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_fetch_ohlcv(symbol: str, bars: int, sources: list[str]) -> pd.DataFrame | None:
        calls.append(sources)
        return None

    monkeypatch.setenv("SCAN_API_SOURCES", "TCBS")
    monkeypatch.setattr(scan, "cache_path", lambda symbol, bars: tmp_path / f"{symbol}_{bars}.parquet")
    monkeypatch.setattr(scan.fetcher, "fetch_ohlcv", fake_fetch_ohlcv)
    monkeypatch.setattr(scan.random, "shuffle", lambda values: None)

    assert scan.fetch_ohlcv("VCB", force_refresh=True) is None
    assert calls == [["VCI", "KBS", "DNSE"]]


def test_vietfin_source_aliases_to_dnse():
    assert fetcher.filter_sources(["VCI", "VIETFIN", "TCBS"]) == ["VCI", "DNSE"]


def test_analyze_index_does_not_apply_stock_discount_rules():
    dates = pd.date_range("2026-01-01", periods=120, freq="D")
    df = pd.DataFrame(
        {
            "time": dates,
            "open": range(1000, 1120),
            "high": range(1002, 1122),
            "low": range(998, 1118),
            "close": range(1001, 1121),
            "volume": [1_000_000 + i for i in range(120)],
        }
    )

    result = scan.analyze_index("VNINDEX", df)

    assert result is not None
    assert result.symbol == "VNINDEX"
    assert result.sector == "Index"
    assert result.setup == "INDEX"
    assert result.discount_group == "INDEX"
    assert result.failed_break is False
