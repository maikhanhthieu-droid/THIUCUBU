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
