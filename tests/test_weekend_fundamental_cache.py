from __future__ import annotations

import pandas as pd

import weekend_opportunities as weekend


def snapshot(symbol: str = "VCB") -> weekend.FundamentalSnapshot:
    return weekend.FundamentalSnapshot(
        symbol=symbol,
        pe=12.38,
        pb=2.33,
        roe=16.72,
        roa=1.60,
        debt_to_equity=None,
        current_ratio=None,
        profit_margin=None,
        eps=5008.22,
        period="2026-Q2",
        source="Fundamental.equity().ratio",
    )


def test_vnstock_ratios_use_trailing_values_without_percent_inflation() -> None:
    frame = pd.DataFrame(
        [
            {
                "period": "2026-Q2",
                "p_e": 12.38,
                "p_b": 2.33,
                "roe": 4.13,
                "roa": 0.39,
                "roe_trailling": 16.72,
                "roa_trailling": 1.60,
                "trailing_eps": 5008.22,
            }
        ]
    )

    result = weekend.snapshot_from_df("VCB", frame, "Fundamental.equity().ratio")

    assert result is not None
    assert result.roe == 16.72
    assert result.roa == 1.60
    assert result.eps == 5008.22
    assert result.period == "2026-Q2"


def test_fundamental_cache_avoids_repeated_provider_call(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(weekend, "FUNDAMENTAL_CACHE_PATH", tmp_path / "fundamental_latest.json")
    monkeypatch.setattr(weekend, "FUNDAMENTAL_CACHE_TTL_HOURS", 168)
    monkeypatch.setattr(weekend, "_FUNDAMENTAL_LIVE_CACHE", None)
    monkeypatch.setattr(weekend, "_FUNDAMENTAL_CACHE_HITS", 0)
    monkeypatch.setattr(weekend, "_FUNDAMENTAL_CACHE_MISSES", 0)
    weekend.save_fundamental_snapshot_cache(snapshot())

    def unexpected_fetch(symbol: str):
        raise AssertionError(f"provider should not be called for cached {symbol}")

    monkeypatch.setattr(weekend, "fetch_fundamental", unexpected_fetch)
    result = weekend.resolve_fundamental("VCB")

    assert result is not None
    assert result.roa == 1.60
    assert result.source.endswith("[cache]")
    assert weekend.fundamental_cache_stats()["hits"] == 1


def test_network_timeout_is_not_retried_with_equivalent_kwargs(monkeypatch) -> None:
    calls = 0

    class Limiter:
        def wait_turn(self, symbol: str) -> None:
            pass

        def record_failure(self, **kwargs) -> None:
            pass

        def record_success(self) -> None:
            pass

    def timeout_method(**kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("provider read timeout")

    monkeypatch.setattr(weekend, "FUNDAMENTAL_LIMITER", Limiter())
    result = weekend.call_ratio_method(
        timeout_method,
        (),
        "Fundamental.equity().ratio",
        "VCB",
    )

    assert result is None
    assert calls == 1
