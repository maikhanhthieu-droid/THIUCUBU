from __future__ import annotations

import json
from pathlib import Path


def test_published_schemas_are_valid_json() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in (
        "raw_filter.v2.schema.json",
        "candidate_book.v1.schema.json",
        "signal_tracker.v2.schema.json",
        "market_state_history.v1.schema.json",
        "market_breadth.v1.schema.json",
        "systemic_regime.v1.schema.json",
        "sector_rotation.v2.schema.json",
        "intraday_pulse.v2.schema.json",
        "weekly_bottom_watch.v1.schema.json",
        "weekly_bottom_watch.v2.schema.json",
        "weekly_bottom_watch.v3.schema.json",
    ):
        payload = json.loads((root / "schemas" / name).read_text(encoding="utf-8"))
        assert payload["$schema"].endswith("2020-12/schema")
