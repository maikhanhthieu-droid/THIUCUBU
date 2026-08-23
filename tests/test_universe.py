from __future__ import annotations

import itertools
import string
from pathlib import Path

import pandas as pd

import universe


def _symbols(count: int) -> list[str]:
    return [
        "".join(chars)
        for chars in itertools.islice(itertools.product(string.ascii_uppercase, repeat=3), count)
    ]


def test_rotating_universe_advances_without_repeating_core(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "universe_state.json"
    monkeypatch.setattr(universe, "discover_symbols", lambda: ["AAA", "BBB", "CCC", "DDD", "EEE"])

    first = universe.rotating_batch(["AAA"], limit=2, path=path)
    second = universe.rotating_batch(["AAA"], limit=2, path=path)

    assert first == ["BBB", "CCC"]
    assert second == ["DDD", "EEE"]
    assert "AAA" not in first + second


def test_discovery_uses_direct_provider_and_filters_symbols(monkeypatch) -> None:
    frame = pd.DataFrame(
        {
            "symbol": [*_symbols(300), "VNINDEX", "ZZZ", "ZZZ"],
            "organ_name": [""] * 303,
        }
    )
    calls: list[str] = []

    def listing_frame(source: str):
        calls.append(source)
        return frame

    monkeypatch.setattr(universe, "_listing_frame", listing_frame)

    symbols = universe.discover_symbols()

    assert calls == ["KBS"]
    assert len(symbols) == 301
    assert "ZZZ" in symbols
    assert "VNINDEX" not in symbols


def test_discovery_falls_back_from_kbs_to_vci(monkeypatch) -> None:
    frame = pd.DataFrame({"symbol": _symbols(300)})
    calls: list[str] = []

    def listing_frame(source: str):
        calls.append(source)
        if source == "KBS":
            raise ConnectionError("KBS unavailable")
        return frame

    monkeypatch.setattr(universe, "_listing_frame", listing_frame)

    assert len(universe.discover_symbols()) == 300
    assert calls == ["KBS", "VCI"]
