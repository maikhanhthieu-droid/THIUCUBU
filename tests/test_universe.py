from __future__ import annotations

from pathlib import Path

import universe


def test_rotating_universe_advances_without_repeating_core(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "universe_state.json"
    monkeypatch.setattr(universe, "discover_symbols", lambda: ["AAA", "BBB", "CCC", "DDD", "EEE"])

    first = universe.rotating_batch(["AAA"], limit=2, path=path)
    second = universe.rotating_batch(["AAA"], limit=2, path=path)

    assert first == ["BBB", "CCC"]
    assert second == ["DDD", "EEE"]
    assert "AAA" not in first + second
