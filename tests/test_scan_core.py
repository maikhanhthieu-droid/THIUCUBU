from __future__ import annotations

import os
from datetime import datetime, timedelta

import pandas as pd

import scan


def test_cache_fresh_rejects_previous_vn_day(tmp_path):
    path = tmp_path / "cached.parquet"
    path.write_text("x", encoding="utf-8")
    yesterday = datetime.now(scan.VN_TZ) - timedelta(days=1)
    os.utime(path, (yesterday.timestamp(), yesterday.timestamp()))

    assert scan.is_cache_fresh(path, ttl_minutes=999_999) is False


def test_direct_fetch_filters_unsupported_tcbs(monkeypatch, tmp_path):
    calls: list[str] = []

    class FakeQuote:
        def __init__(self, symbol: str, source: str) -> None:
            calls.append(source)

        def history(self, start: str, end: str, interval: str) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setenv("SCAN_API_SOURCES", "TCBS")
    monkeypatch.setattr(scan, "cache_path", lambda symbol, bars: tmp_path / f"{symbol}_{bars}.parquet")
    monkeypatch.setattr(scan, "Quote", FakeQuote)
    monkeypatch.setattr(scan.random, "shuffle", lambda values: None)

    assert scan.fetch_ohlcv("VCB", force_refresh=True) is None
    assert calls == ["vci", "kbs"]
