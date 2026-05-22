from __future__ import annotations

from datetime import date

import market_calendar


def test_market_calendar_detects_configured_holiday(monkeypatch, tmp_path):
    path = tmp_path / "holidays.json"
    path.write_text('{"holidays":[{"date":"2026-02-16","name":"Tet"}]}', encoding="utf-8")
    monkeypatch.setenv("MARKET_CLOSED_POLICY", "skip")

    status = market_calendar.get_market_day_status(date(2026, 2, 16), path)

    assert status.closed is True
    assert status.reason == "Tet"
    assert market_calendar.should_skip_scan(status) is True


def test_market_calendar_scan_old_policy(monkeypatch, tmp_path):
    path = tmp_path / "holidays.json"
    path.write_text('{"holidays":{"2026-04-30":"Reunification Day"}}', encoding="utf-8")
    monkeypatch.setenv("MARKET_CLOSED_POLICY", "scan_old")

    status = market_calendar.get_market_day_status(date(2026, 4, 30), path)

    assert status.closed is True
    assert status.policy == "scan_old"
    assert market_calendar.should_scan_old_data(status) is True
    assert market_calendar.should_skip_scan(status) is False


def test_market_calendar_weekend_is_closed(monkeypatch):
    monkeypatch.setenv("MARKET_CLOSED_POLICY", "nghi")

    status = market_calendar.get_market_day_status(date(2026, 5, 23))

    assert status.closed is True
    assert status.reason == "Weekend"
    assert status.policy == "skip"
