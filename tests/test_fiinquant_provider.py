from __future__ import annotations

import traceback
from types import SimpleNamespace

import pandas as pd
import pytest

import fetcher
import fiinquant_provider as fiin
import scan_safe
import source_router
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


def test_fiinquant_rejects_python_311_with_clear_error(monkeypatch) -> None:
    class Python311:
        major = 3
        minor = 11

        def __lt__(self, other):
            return (self.major, self.minor) < other

    monkeypatch.setattr(fiin.sys, "version_info", Python311())

    with pytest.raises(fiin.FiinQuantError, match=r"requires Python 3\.12\+"):
        fiin._load_session_type()


def test_fiinquant_fundamental_requests_receive_default_timeout(monkeypatch) -> None:
    calls: list[dict] = []

    class Requests:
        def get(self, *args, **kwargs):
            calls.append(kwargs)
            return object()

    module_name = "FiinQuantX.core.FundamentalAnalysis"
    fake_module = SimpleNamespace(requests=Requests())
    monkeypatch.setitem(fiin.sys.modules, module_name, fake_module)
    monkeypatch.setenv("FIINQUANT_HTTP_TIMEOUT_SEC", "17")

    fiin._configure_sdk_http_timeout()
    fake_module.requests.get("https://example.test/default")
    fake_module.requests.get("https://example.test/explicit", timeout=5)

    assert calls == [{"timeout": 17.0}, {"timeout": 5}]


def test_weekend_fiinquant_fundamental_only_runs_for_priority_symbols(monkeypatch) -> None:
    monkeypatch.setenv("FIINQUANT_USERNAME", "user")
    monkeypatch.setenv("FIINQUANT_PASSWORD", "password")
    monkeypatch.setattr(weekend, "VnFundamental", None)
    monkeypatch.setattr(weekend, "VnCompany", None)
    calls: list[str] = []

    def fetch(symbol: str):
        calls.append(symbol)
        return {"symbol": symbol, "pe": 9.0, "source": "FiinQuantX"}

    monkeypatch.setattr(fiin, "fetch_fundamental", fetch)
    monkeypatch.setattr(weekend.source_router, "is_priority", lambda symbol: symbol == "VCB")

    assert weekend.fetch_fundamental("HPG") is None
    snapshot = weekend.fetch_fundamental("VCB")

    assert calls == ["VCB"]
    assert snapshot is not None
    assert snapshot.source == "FiinQuantX"


def test_healthy_fiinquant_is_first_only_for_priority_symbols(monkeypatch) -> None:
    monkeypatch.setattr(scan_safe, "API_SOURCES", ["FIINQUANT", "VCI", "KBS", "DNSE"])
    monkeypatch.setattr(scan_safe, "PREVIOUS_SOURCE_HEALTH", {"sources": {}})
    routing = source_router.default_routing()
    routing["fiinquant_priority"] = [
        {"symbol": "VCB", "reasons": ["test"], "attention_score": 100, "consecutive_misses": 0}
    ]
    monkeypatch.setattr(source_router, "get_routing", lambda path=None: routing)

    assert scan_safe.source_order_for_symbol("VCB")[0] == "FIINQUANT"
    assert scan_safe.source_order_for_symbol("HPG")[-1] == "FIINQUANT"


def test_history_reuses_one_session_and_never_requests_realtime(monkeypatch) -> None:
    monkeypatch.setenv("FIINQUANT_USERNAME", "user")
    monkeypatch.setenv("FIINQUANT_PASSWORD", "password")
    monkeypatch.setenv("FIINQUANT_HISTORY_CHUNK_DAYS", "365")
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


def test_long_fiinquant_history_is_split_into_contiguous_windows(monkeypatch) -> None:
    monkeypatch.setenv("FIINQUANT_HISTORY_CHUNK_DAYS", "180")

    windows = fiin._history_windows("2026-01-01", "2026-12-31")

    assert windows == [
        ("2026-01-01", "2026-06-29"),
        ("2026-06-30", "2026-12-26"),
        ("2026-12-27", "2026-12-31"),
    ]


def test_history_retries_an_empty_chunk_and_deduplicates_boundaries(monkeypatch) -> None:
    monkeypatch.setenv("FIINQUANT_USERNAME", "user")
    monkeypatch.setenv("FIINQUANT_PASSWORD", "password")
    monkeypatch.setenv("FIINQUANT_HISTORY_CHUNK_DAYS", "180")
    monkeypatch.setattr(fiin.time, "sleep", lambda *_: None)
    requests: list[dict] = []

    class FetchRequest:
        def __init__(self, kwargs):
            self.kwargs = kwargs

        def get_data(self):
            requests.append(self.kwargs)
            if len(requests) == 1:
                return pd.DataFrame()
            timestamp = self.kwargs["from_date"]
            return pd.DataFrame(
                {
                    "ticker": ["VCB", "VCB"],
                    "timestamp": [timestamp, timestamp],
                    "open": [60.0, 60.0],
                    "high": [61.0, 61.0],
                    "low": [59.0, 59.0],
                    "close": [60.5, 60.5],
                    "volume": [1_000_000, 1_000_000],
                }
            )

    class Session:
        def Fetch_Trading_Data(self, **kwargs):
            return FetchRequest(kwargs)

    monkeypatch.setattr(fiin, "get_session", lambda: Session())

    frame = fiin.fetch_history("VCB", "2026-01-01", "2026-12-31")

    assert len(requests) == 4  # First window retries once, then two more windows.
    assert len(frame) == 3
    assert frame.attrs["history_chunks"] == 3


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
