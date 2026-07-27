"""Build a machine-readable raw-filter feed for downstream scanners.

This is intentionally a facts layer.  It does not forecast returns and it does
not remove symbols merely because a downstream consumer disagrees with the
advisory gate.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Mapping


SCHEMA_VERSION = "thieucubu.raw_filter.v1"


def _float(value: Any) -> float | None:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _fact(result: Any, metrics: Mapping[str, Any]) -> dict[str, Any]:
    raw = asdict(result) if hasattr(result, "__dataclass_fields__") else dict(result)
    intel = metrics.get(str(raw.get("symbol")), {})
    weekly = intel.get("weekly") if isinstance(intel.get("weekly"), Mapping) else {}
    volume = intel.get("volume") if isinstance(intel.get("volume"), Mapping) else {}
    rs = intel.get("rs") if isinstance(intel.get("rs"), Mapping) else {}
    trade = intel.get("trade") if isinstance(intel.get("trade"), Mapping) else {}
    gate = intel.get("gate") if isinstance(intel.get("gate"), Mapping) else {}
    as_of = raw.get("as_of") or raw.get("date") or raw.get("as_of_date")
    data_source = raw.get("data_source")
    cache_status = str(raw.get("cache_status") or "unknown")
    attributable = bool(as_of and data_source)
    current = cache_status in {"live", "fresh_cache"}
    return {
        "symbol": str(raw.get("symbol") or "").upper(),
        "as_of": as_of,
        "data_source": data_source,
        "cache_status": cache_status,
        "close": _float(raw.get("close")),
        "setup": raw.get("setup"),
        "base_score": _float(raw.get("base_score")),
        "flow_score": _float(raw.get("flow_score")),
        "break_score": _float(raw.get("break_score")),
        "risk_score": _float(raw.get("risk_score")),
        "win_score": _float(raw.get("win_score")),
        "near_break": bool(raw.get("near_break")),
        "failed_break": bool(raw.get("failed_break")),
        "weekly": {
            "uptrend": bool(weekly.get("weekly_uptrend")),
            "above_ema13": bool(weekly.get("weekly_above_ema13")),
            "ema13_slope": _float(weekly.get("weekly_ema13_slope")),
        },
        "volume": {
            "accumulation_ratio": _float(volume.get("accumulation_ratio")),
            "contraction": bool(volume.get("vol_contraction")),
            "expansion_up": bool(volume.get("vol_expansion_up")),
            "churning": bool(volume.get("churning")),
        },
        "relative_strength": {
            "20d": _float(rs.get("rs_20d")),
            "60d": _float(rs.get("rs_60d")),
            "120d": _float(rs.get("rs_120d")),
            "score": _float(rs.get("rs_score")),
        },
        "trade_context": {
            "support": _float(trade.get("support")),
            "resistance": _float(trade.get("resistance")),
            "risk_reward": _float(trade.get("risk_reward")),
        },
        "gate": {
            "allowed": gate.get("allowed"),
            "reason": gate.get("reason"),
            "regime": gate.get("regime"),
        },
        "data_quality": {
            "known_data_only": attributable,
            "status": (
                "current"
                if attributable and current
                else "stale"
                if attributable and cache_status == "stale_cache"
                else "unattributed"
            ),
            "failed_break_excluded": bool(raw.get("failed_break")),
        },
    }


def build_filter_feed(
    *,
    mode: str,
    updated_at: str,
    results: Mapping[str, Any] | list[Any],
    metrics: Mapping[str, Any],
    regime: Mapping[str, Any],
    source_health: Mapping[str, Any] | None = None,
    market_activity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = list(results.values()) if isinstance(results, Mapping) else list(results)
    facts = [_fact(row, metrics) for row in rows]
    facts.sort(key=lambda item: (-(item["win_score"] or 0), item["symbol"]))
    as_of_values = sorted(
        str(item["as_of"]) for item in facts if item.get("as_of")
    )
    feed_as_of = as_of_values[-1] if as_of_values else None
    return {
        "schema_version": SCHEMA_VERSION,
        "producer": "maikhanhthieu-droid/THIUCUBU",
        "kind": "raw_filter_feed",
        "generated_at": updated_at,
        "updated_at": updated_at,
        "as_of": feed_as_of,
        "status": "ok" if facts and feed_as_of else "degraded",
        "mode": mode,
        "market": {
            "regime": regime.get("regime", "UNKNOWN"),
            "risk_multiplier": _float(regime.get("risk_multiplier")),
            "above_ema50": regime.get("above_ema50"),
            "above_ema200": regime.get("above_ema200"),
            "ema50_slope_pct": _float(regime.get("ema50_slope_pct")),
        },
        "quality": {
            "source_health": dict(source_health or {}),
            "market_activity": dict(market_activity or {}),
            "facts_with_provenance": sum(
                1 for item in facts if item["data_quality"]["known_data_only"]
            ),
            "stale_facts": sum(
                1 for item in facts if item["data_quality"]["status"] == "stale"
            ),
            "warning": "Facts and advisory gates only; not a return forecast.",
        },
        "facts": facts,
    }
