from __future__ import annotations

from dataclasses import dataclass

from filter_feed import build_filter_feed


@dataclass
class Result:
    symbol: str
    close: float
    win_score: int
    setup: str = "VCP_BREAK"
    failed_break: bool = False
    near_break: bool = True
    as_of: str | None = "2026-07-24"
    data_source: str | None = "VCI"
    cache_status: str = "live"


def test_filter_feed_is_facts_only_and_sorted() -> None:
    report = build_filter_feed(
        mode="eod",
        updated_at="2026-07-27T15:10:00+07:00",
        results={"BBB": Result("BBB", 20.0, 70), "AAA": Result("AAA", 10.0, 90)},
        metrics={
            "AAA": {
                "weekly": {"weekly_uptrend": True, "weekly_above_ema13": True},
                "rs": {"rs_score": 80, "rs_20d": 4},
                "gate": {"allowed": True, "reason": "BULL"},
            }
        },
        regime={"regime": "BULL", "risk_multiplier": 1.0},
    )
    assert report["schema_version"] == "thieucubu.raw_filter.v2"
    assert report["producer"] == "maikhanhthieu-droid/THIUCUBU"
    assert report["as_of"] == "2026-07-24"
    assert report["status"] == "ok"
    assert [row["symbol"] for row in report["facts"]] == ["AAA", "BBB"]
    assert report["facts"][0]["relative_strength"]["score"] == 80.0
    assert report["facts"][0]["as_of"] == "2026-07-24"
    assert report["facts"][0]["data_source"] == "VCI"
    assert report["facts"][0]["data_quality"]["status"] == "current"
    assert "scores" in report["facts"][0]
    assert report["quality"]["facts_with_provenance"] == 2
    assert "forecast" not in report["facts"][0]
    assert "target" not in report["facts"][0]


def test_filter_feed_marks_unknown_and_stale_provenance() -> None:
    unknown = Result("AAA", 10.0, 90, as_of=None, data_source=None)
    stale = Result("BBB", 20.0, 70, cache_status="stale_cache")
    report = build_filter_feed(
        mode="eod",
        updated_at="2026-07-27T15:10:00+07:00",
        results=[unknown, stale],
        metrics={},
        regime={},
    )
    by_symbol = {item["symbol"]: item for item in report["facts"]}
    assert by_symbol["AAA"]["data_quality"]["status"] == "unattributed"
    assert by_symbol["AAA"]["data_quality"]["known_data_only"] is False
    assert by_symbol["BBB"]["data_quality"]["status"] == "stale"
    assert report["quality"]["stale_facts"] == 1


def test_filter_feed_marks_old_live_symbol_stale_relative_to_feed() -> None:
    old = Result("AAA", 10.0, 80, as_of="2025-01-01", cache_status="live")
    current = Result("BBB", 20.0, 70, as_of="2026-07-24", cache_status="live")

    report = build_filter_feed(
        mode="eod",
        updated_at="2026-07-24T15:10:00+07:00",
        results=[old, current],
        metrics={},
        regime={},
    )

    by_symbol = {item["symbol"]: item for item in report["facts"]}
    assert by_symbol["AAA"]["data_quality"]["status"] == "stale"
    assert by_symbol["BBB"]["data_quality"]["status"] == "current"
