from __future__ import annotations

import json

import httpx
import pytest

import vimo_provider as vimo


@pytest.fixture(autouse=True)
def clean_vimo(monkeypatch, tmp_path):
    monkeypatch.delenv("VIMO_API_KEY", raising=False)
    monkeypatch.setattr(vimo, "CACHE_DIR", tmp_path)
    monkeypatch.setenv("VIMO_BUDGET_FILE", str(tmp_path / "request_budget.json"))
    monkeypatch.setattr(vimo, "_wait_turn", lambda: None)
    vimo.reset_health()


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)


def mcp_payload(data):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps({"data": data})}]},
    }


def install_client(monkeypatch, response):
    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, *args, **kwargs):
            return response

    monkeypatch.setattr(vimo.httpx, "Client", Client)


def test_vimo_is_optional_without_secret():
    assert vimo.is_configured() is False
    with pytest.raises(vimo.VimoConfigurationError):
        vimo.call_tool("get_ta_signals", {"symbol": "FPT"})


def test_ta_payload_is_reduced_and_cached(monkeypatch):
    monkeypatch.setenv("VIMO_API_KEY", "private-key")
    install_client(
        monkeypatch,
        DummyResponse(
            mcp_payload(
                {
                    "symbol": "FPT",
                    "signal": "SELL",
                    "score": -0.438,
                    "confidence": 63,
                    "price": 69.2,
                    "date": "2026-08-13",
                    "narrative": "must not be persisted",
                }
            )
        ),
    )

    result = vimo.fetch_ta_signal("FPT")
    cached = vimo.fetch_ta_signal("FPT")

    assert result["signal"] == "SELL"
    assert result["confidence"] == 63
    assert "narrative" not in result
    assert cached["cache_status"] == "cache"
    assert vimo.health_dict()["successes"] == 1


def test_bctc_payload_keeps_only_strategy_summary(monkeypatch):
    monkeypatch.setenv("VIMO_API_KEY", "private-key")
    install_client(
        monkeypatch,
        DummyResponse(
            mcp_payload(
                {
                    "symbol": "VCB",
                    "year": 2025,
                    "strategies": [
                        {"strategy": "graham", "score": 80, "grade": "Buy"},
                        {"strategy": "fisher", "score": 60, "grade": "Hold"},
                    ],
                    "summary": "must not be persisted",
                }
            )
        ),
    )

    result = vimo.fetch_bctc_support("VCB")

    assert result["strategy_average"] == 70
    assert result["buy_count"] == 1
    assert result["strategy_count"] == 2
    assert "summary" not in result


def test_authentication_error_does_not_expose_key(monkeypatch):
    monkeypatch.setenv("VIMO_API_KEY", "super-secret")
    install_client(monkeypatch, DummyResponse({}, status_code=401))

    with pytest.raises(vimo.VimoAuthenticationError) as error:
        vimo.call_tool("get_ta_signals", {"symbol": "FPT"})

    assert "super-secret" not in str(error.value)


def test_vimo_daily_budget_stops_at_seventy_percent_cap(monkeypatch):
    monkeypatch.setenv("VIMO_DAILY_REQUEST_BUDGET", "2")

    vimo._reserve_daily_request_unlocked()
    vimo._reserve_daily_request_unlocked()

    with pytest.raises(vimo.VimoRateLimitError, match="budget reached"):
        vimo._reserve_daily_request_unlocked()
    snapshot = vimo.daily_budget_snapshot()
    assert snapshot["used"] == 2
    assert snapshot["remaining"] == 0
