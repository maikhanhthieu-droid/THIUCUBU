from __future__ import annotations

import traceback

import pandas as pd
import pytest

import fetcher
import fiinquant_provider as fiin
import scan_safe
import weekend_opportunities as weekend


@pytest.fixture(autouse=True)
def clean_session(monkeypatch):
    monkeypatch.delenv("FIINQUANT_USERNAME", raising=False)
    monkeypatch.delenv("FIINQUANT_PASSWORD", raising=False)
    fiin.reset_session()
    yield
    fiin.reset_session()


def test_fiinquant_is_optional_and_removed_without_secrets() -> None:
    assert fiin.is_configured() is False
    assert fetcher.filter_sources(["FIINQUANT", "VCI", "DNSE"]) == ["VCI", "DNSE"]


def test_aliases_and_index_symbols_are_normalized(monkeypatch) -> None:
    monkeypatch.setenv("FIINQUANT_USERNAME", "user")
    monkeypatch.setenv("FIINQUANT_PASSWORD", "password")

    assert fetcher.normalize_source("fiinquantx") == "FIINQUANT"
    assert fetcher.filter_sources(["FQ", "VCI"], include_index_sources_only=True) == [
        "FIINQUANT",
        "VCI",
    ]
    assert fiin.canonical_symbol("HNXINDEX") == "HNXIndex"
    assert fiin.canonical_symbol("UPCOMINDEX") == "UpcomIndex"


def test_healthy_fiinquant_is_the_first_source(monkeypatch) -> None:
    monkeypatch.setattr(scan_safe, "API_SOURCES", ["FIINQUANT", "VCI", "KBS", "DNSE"])
    monkeypatch.setattr(scan_safe, "PREVIOUS_SOURCE_HEALTH", {"sources": {}})

    assert scan_safe.source_order_for_symbol("VCB")[0] == "FIINQUANT"


def test_history_reuses_one_session_and_never_requests_realtime(monkeypatch) -> None:
    monkeypatch.setenv("FIINQUANT_USERNAME", "user")
    monkeypatch.setenv("FIINQUANT_PASSWORD", "password")
    login_calls: list[tuple[str, str]] = []
    requests: list[dict] = []

    class FetchRequest:
        def get_data(self):
            return pd.DataFrame(
                {
                    "ticker": ["VCB", "VCB"],
                    "timestamp": ["2026-08-12", "2026-08-13"],
                    "open": [61.0, 61.2],
                    "high": [62.0, 62.2],
                    "low": [60.5, 60.7],
                    "close": [61.5, 61.8],
                    "volume": [1_000_000, 1_100_000],
                }
            )

    class Session:
        def Fetch_Trading_Data(self, **kwargs):
            requests.append(kwargs)
            return FetchRequest()

    class SessionType:
        def __init__(self, username, password):
            login_calls.append((username, password))

        def login(self):
            return Session()

    monkeypatch.setattr(fiin, "_load_session_type", lambda: SessionType)

    first = fetcher.fetch_source_history("FIINQUANT", "VCB", "2026-01-01", "2026-08-13")
    second = fetcher.fetch_source_history("FIINQUANT", "VCB", "2026-01-01", "2026-08-13")

    assert len(login_calls) == 1
    assert len(requests) == 2
    assert all(request["realtime"] is False for request in requests)
    assert all(request["by"] == "1d" for request in requests)
    assert first["close"].iloc[-1] == pytest.approx(61.8)
    assert second.attrs["price_unit"] == "thousand_vnd"


def test_fundamental_payload_is_normalized(monkeypatch) -> None:
    monkeypatch.setenv("FIINQUANT_USERNAME", "user")
    monkeypatch.setenv("FIINQUANT_PASSWORD", "password")

    class Fundamentals:
        def get_ratios(self, **kwargs):
            return [
                {
                    "ticker": "HPG",
                    "year": 2025,
                    "quarter": 4,
                    "ratios": {
                        "ProfitabilityRatio": {"ROA": 0.08, "ROE": 0.18, "NetProfitMargin": 0.12},
                        "SolvencyRatio": {"DebtToEquityRatio": 0.5},
                        "LiquidityRatio": {"CurrentRatio": 1.6},
                        "ValuationRatios": {
                            "PriceToEarning": 8.5,
                            "PriceToBook": 1.2,
                            "BasicEPS": 2500,
                        },
                    },
                }
            ]

    class Session:
        def FundamentalAnalysis(self):
            return Fundamentals()

    monkeypatch.setattr(fiin, "get_session", lambda: Session())

    values = fiin.fetch_fundamental("HPG")
    snapshot = weekend.snapshot_from_mapping("HPG", values or {})

    assert values is not None
    assert values["pe"] == pytest.approx(8.5)
    assert snapshot is not None
    assert snapshot.roe == pytest.approx(18.0)
    assert snapshot.source == "FiinQuantX"


def test_errors_do_not_echo_credentials(monkeypatch) -> None:
    monkeypatch.setenv("FIINQUANT_USERNAME", "private@example.com")
    monkeypatch.setenv("FIINQUANT_PASSWORD", "super-secret")

    class SessionType:
        def __init__(self, username, password):
            self.username = username
            self.password = password

        def login(self):
            raise RuntimeError(f"login failed for {self.username} with {self.password}")

    monkeypatch.setattr(fiin, "_load_session_type", lambda: SessionType)

    with pytest.raises(fiin.FiinQuantAuthenticationError) as error:
        fiin.get_session()

    assert "private@example.com" not in str(error.value)
    assert "super-secret" not in str(error.value)
    rendered = "".join(traceback.format_exception(error.value))
    assert "private@example.com" not in rendered
    assert "super-secret" not in rendered


def test_transient_login_failure_can_retry(monkeypatch) -> None:
    monkeypatch.setenv("FIINQUANT_USERNAME", "user")
    monkeypatch.setenv("FIINQUANT_PASSWORD", "password")
    calls = 0

    class SessionType:
        def __init__(self, username, password):
            pass

        def login(self):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("temporary network timeout")
            return object()

    monkeypatch.setattr(fiin, "_load_session_type", lambda: SessionType)

    with pytest.raises(fiin.FiinQuantError):
        fiin.get_session()

    assert fiin.get_session() is not None
    assert calls == 2
