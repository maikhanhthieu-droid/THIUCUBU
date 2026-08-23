from __future__ import annotations

import pandas as pd

import scan_safe


def test_with_provenance_records_source_date_and_cache_status() -> None:
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-07-23", "2026-07-24"]),
            "open": [10.0, 10.2],
            "high": [10.5, 10.6],
            "low": [9.9, 10.1],
            "close": [10.2, 10.4],
            "volume": [1000, 1200],
        }
    )
    result = scan_safe.with_provenance(
        "AAA", frame, source="VCI", cache_status="live"
    )
    assert result.attrs["as_of"] == "2026-07-24"
    assert result.attrs["data_source"] == "VCI"
    assert result.attrs["cache_status"] == "live"
    assert scan_safe.FETCH_PROVENANCE["AAA"]["data_source"] == "VCI"


def test_recent_fiinquant_data_overlays_deep_history() -> None:
    history = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-07-22", "2026-07-23"]),
            "open": [10.0, 10.1],
            "high": [10.4, 10.5],
            "low": [9.8, 9.9],
            "close": [10.1, 10.2],
            "volume": [1000, 1100],
        }
    )
    recent = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-07-23", "2026-07-24"]),
            "open": [10.2, 10.3],
            "high": [10.6, 10.7],
            "low": [10.0, 10.1],
            "close": [10.4, 10.5],
            "volume": [1200, 1300],
        }
    )
    recent.attrs["provider"] = "FiinQuantX"

    merged = scan_safe.merge_recent_history(history, recent)

    assert merged is not None
    assert len(merged) == 3
    assert merged.loc[merged["time"] == pd.Timestamp("2026-07-23"), "close"].iloc[0] == 10.4
    assert merged.attrs["provider"] == "FiinQuantX"


def test_provenance_records_hybrid_history_source() -> None:
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-07-23", "2026-07-24"]),
            "open": [10.0, 10.2],
            "high": [10.5, 10.6],
            "low": [9.9, 10.1],
            "close": [10.2, 10.4],
            "volume": [1000, 1200],
        }
    )

    result = scan_safe.with_provenance(
        "AAA",
        frame,
        source="FIINQUANT",
        cache_status="live",
        history_backfill_source="VCI",
    )

    assert result.attrs["data_source"] == "FIINQUANT"
    assert result.attrs["history_backfill_source"] == "VCI"
