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

    health = scan_safe.source_health_payload()
    assert health["symbol_provenance"]["AAA"]["data_source"] == "FIINQUANT"
    assert health["symbol_provenance"]["AAA"]["history_backfill_source"] == "VCI"


def test_safe_fetch_keeps_fiinquant_overlay_on_standard_backfill(monkeypatch, tmp_path) -> None:
    deep_dates = pd.bdate_range("2025-01-01", periods=300)
    recent_dates = deep_dates[-90:]

    def frame(dates, close):
        return pd.DataFrame(
            {
                "time": dates,
                "open": close,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1_000_000,
            }
        )

    deep = frame(deep_dates, 10.0)
    recent = frame(recent_dates, 10.2)
    recent.attrs["history_partial"] = True

    class Limiter:
        disabled = False

        def wait_turn(self, symbol):
            pass

        def record_success(self):
            pass

        def record_failure(self, **kwargs):
            pass

        def disable(self, reason):
            self.disabled = True

    monkeypatch.setattr(scan_safe, "FETCH_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(scan_safe, "API_LIMITERS", {"FIINQUANT": Limiter(), "VCI": Limiter()})
    monkeypatch.setattr(scan_safe, "source_order_for_symbol", lambda symbol: ["FIINQUANT", "VCI"])
    monkeypatch.setattr(
        scan_safe,
        "fetch_source_history",
        lambda source, symbol, start, end: recent.copy() if source == "FIINQUANT" else deep.copy(),
    )
    monkeypatch.setattr(scan_safe.scan, "cache_path", lambda symbol, bars: tmp_path / "AAA.parquet")
    monkeypatch.setattr(scan_safe.scan, "write_cache_frame", lambda path, value: None)
    monkeypatch.setattr(scan_safe.scan, "json_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(scan_safe.scan, "read_stale_cache", lambda path: None)

    result = scan_safe.fetch_ohlcv_safe("AAA", bars=260, force_refresh=True)

    assert result is not None
    assert len(result) == 260
    assert result.attrs["data_source"] == "FIINQUANT"
    assert result.attrs["history_backfill_source"] == "VCI"
    overlap = result[result["time"].isin(recent_dates)]
    assert not overlap.empty
    assert (overlap["close"] - 10.2).abs().max() < 1e-9


def test_transient_fiinquant_failure_falls_back_without_disabling_run(monkeypatch, tmp_path) -> None:
    dates = pd.bdate_range("2025-01-01", periods=300)
    deep = pd.DataFrame(
        {
            "time": dates,
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "volume": 1_000_000,
        }
    )

    class Limiter:
        def __init__(self) -> None:
            self.disabled = False
            self.failures = 0
            self.disable_calls = 0

        def wait_turn(self, symbol):
            pass

        def record_success(self):
            pass

        def record_failure(self, **kwargs):
            self.failures += 1

        def disable(self, reason):
            self.disable_calls += 1
            self.disabled = True

    fiinquant = Limiter()
    vci = Limiter()
    calls: list[tuple[str, str]] = []

    def fetch(source, symbol, start, end):
        calls.append((source, symbol))
        if source == "FIINQUANT" and symbol == "AAA":
            raise TimeoutError("504 Gateway Timeout")
        return deep.copy()

    monkeypatch.setattr(scan_safe, "FETCH_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(scan_safe, "API_LIMITERS", {"FIINQUANT": fiinquant, "VCI": vci})
    monkeypatch.setattr(scan_safe, "source_order_for_symbol", lambda symbol: ["FIINQUANT", "VCI"])
    monkeypatch.setattr(scan_safe, "fetch_source_history", fetch)
    monkeypatch.setattr(
        scan_safe.scan,
        "cache_path",
        lambda symbol, bars: tmp_path / f"{symbol}.parquet",
    )
    monkeypatch.setattr(scan_safe.scan, "write_cache_frame", lambda path, value: None)
    monkeypatch.setattr(scan_safe.scan, "json_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(scan_safe.scan, "read_stale_cache", lambda path: None)

    first = scan_safe.fetch_ohlcv_safe("AAA", bars=260, force_refresh=True)
    second = scan_safe.fetch_ohlcv_safe("BBB", bars=260, force_refresh=True)

    assert first is not None and first.attrs["data_source"] == "VCI"
    assert second is not None and second.attrs["data_source"] == "FIINQUANT"
    assert ("FIINQUANT", "BBB") in calls
    assert fiinquant.failures == 1
    assert fiinquant.disable_calls == 0
    assert fiinquant.disabled is False
