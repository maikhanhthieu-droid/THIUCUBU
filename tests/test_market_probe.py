from __future__ import annotations

from datetime import date

import market_probe


def snap(symbol: str, day: str = "2026-05-22", close: float = 10.0, volume: float = 1000.0):
    return market_probe.ProbeSnapshot(symbol=symbol, latest_date=day, close=close, volume=volume)


def many_snapshots(prefix: str, count: int, day: str, volume: float = 1000.0):
    return {f"{prefix}{idx:02d}": snap(f"{prefix}{idx:02d}", day=day, close=10 + idx, volume=volume) for idx in range(count)}


def test_probe_marks_old_dates_inactive(monkeypatch):
    monkeypatch.setenv("MARKET_PROBE_MIN_CHECKED", "10")
    snapshots = many_snapshots("A", 12, "2026-05-21")

    result = market_probe.evaluate_activity(snapshots, today=date(2026, 5, 22), policy="skip")

    assert result.inactive is True
    assert market_probe.should_stop_for_inactive(result) is False
    assert "data_date_cu" in result.reason


def test_probe_can_be_configured_to_stop(monkeypatch):
    monkeypatch.setenv("MARKET_PROBE_MIN_CHECKED", "10")
    snapshots = many_snapshots("A", 12, "2026-05-21")

    result = market_probe.evaluate_activity(
        snapshots,
        today=date(2026, 5, 22),
        policy="skip",
        action="skip",
    )

    assert result.inactive is True
    assert market_probe.should_stop_for_inactive(result) is True


def test_probe_marks_unchanged_sample_inactive(monkeypatch):
    monkeypatch.setenv("MARKET_PROBE_MIN_CHECKED", "10")
    current = many_snapshots("B", 12, "2026-05-22")
    previous = many_snapshots("B", 12, "2026-05-22")

    result = market_probe.evaluate_activity(current, previous, today=date(2026, 5, 22), policy="skip")

    assert result.inactive is True
    assert result.unchanged == 12
    assert "khong_doi" in result.reason


def test_probe_current_first_run_continues(monkeypatch):
    monkeypatch.setenv("MARKET_PROBE_MIN_CHECKED", "10")
    snapshots = many_snapshots("C", 12, "2026-05-22")

    result = market_probe.evaluate_activity(snapshots, today=date(2026, 5, 22), policy="skip")

    assert result.inactive is False
    assert market_probe.should_stop_for_inactive(result) is False
    assert "lan_dau" in result.reason


def test_probe_scan_old_policy_does_not_stop(monkeypatch):
    monkeypatch.setenv("MARKET_PROBE_MIN_CHECKED", "10")
    snapshots = many_snapshots("D", 12, "2026-05-21")

    result = market_probe.evaluate_activity(snapshots, today=date(2026, 5, 22), policy="scan_old")

    assert result.inactive is True
    assert market_probe.should_stop_for_inactive(result) is False
