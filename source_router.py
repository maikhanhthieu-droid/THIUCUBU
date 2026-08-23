#!/usr/bin/env python3
"""Persistent, state-aware routing between FiinQuant and fallback providers.

The routing file is intentionally human editable.  Only the generated route
sections are replaced; ``manual.force_fiinquant`` and
``manual.force_standard`` are preserved across scanner runs.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


logger = logging.getLogger("thieucutoo.source_router")
VN_TZ = timezone(timedelta(hours=7))
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
ROUTING_PATH = Path(os.getenv("SCAN_SOURCE_ROUTING_FILE", str(DATA_DIR / "source_routing.json")))
SCHEMA_VERSION = "thieucubu.source_routing.v1"

STANDARD_SOURCES = ("VCI", "KBS", "DNSE")
INDEX_SYMBOLS = {"VNINDEX", "^VNINDEX", "VN-INDEX", "VN30", "HNX30", "HNXINDEX", "UPCOMINDEX"}
IMPORTANT_BREAKOUT_STATES = {
    "FAILED_BREAK_CONFIRMED": 132,
    "FAILED_BREAK_WATCH": 128,
    "REACCUMULATION": 124,
    "HEALTHY_RETEST": 122,
    "RECLAIMED_BREAK": 120,
    "BREAKOUT_UNCONFIRMED": 116,
    "BREAKOUT_CONFIRMED": 112,
}
WEEKEND_ACTIONS = {"UU_TIEN_GOM", "UNG_VIEN_GOM", "CHO_DIEM_GOM"}

DEFAULT_POLICY: dict[str, Any] = {
    "priority_limit": 32,
    "demote_after_scans": 3,
    "strong_score_threshold": 72,
    "near_break_score_threshold": 62,
    "unhealthy_source_score": 40,
    "standard_fiinquant_emergency_fallback": True,
    "priority_source_order": ["FIINQUANT", "VCI", "KBS", "DNSE"],
    "standard_sources": list(STANDARD_SOURCES),
}

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None
_CACHE_PATH: Path | None = None
_CACHE_MTIME_NS: int | None = None


def now_vn() -> datetime:
    return datetime.now(VN_TZ)


def normalize_symbol(value: Any) -> str:
    return str(value or "").replace("`", "").upper().strip()


def _symbols(values: Iterable[Any] | None) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        symbol = normalize_symbol(value)
        if not symbol or symbol in INDEX_SYMBOLS or symbol in seen:
            continue
        if not (3 <= len(symbol) <= 12 and symbol.replace("-", "").isalnum()):
            continue
        output.append(symbol)
        seen.add(symbol)
    return output


def _get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def default_routing() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": None,
        "last_mode": None,
        "policy": dict(DEFAULT_POLICY),
        "manual": {
            "force_fiinquant": [],
            "force_standard": [],
            "note": (
                "Chỉ sửa hai danh sách này. force_standard được ưu tiên nếu một mã "
                "vô tình có mặt trong cả hai danh sách."
            ),
        },
        "fiinquant_priority": [],
        "standard_routes": {source: [] for source in STANDARD_SOURCES},
        "meta": {
            "priority_count": 0,
            "standard_count": 0,
            "description": (
                "Mã cần chú ý dùng FiinQuant trước. Mã thường được cân tải qua "
                "VCI/KBS/DNSE; FiinQuant chỉ cứu hộ khi cả ba nguồn thường lỗi."
            ),
        },
    }


def normalize_routing(raw: Any) -> dict[str, Any]:
    routing = default_routing()
    if not isinstance(raw, Mapping):
        return routing

    policy_raw = _mapping(raw.get("policy"))
    policy = dict(DEFAULT_POLICY)
    policy.update({key: policy_raw[key] for key in policy if key in policy_raw})
    # FiinQuant Free allows 33 historical symbols. Keep one spare slot for
    # diagnostics/index checks and never route more than 32 equities to it.
    policy["priority_limit"] = _integer(policy.get("priority_limit"), 32, 1, 32)
    policy["demote_after_scans"] = _integer(policy.get("demote_after_scans"), 3, 1, 10)
    policy["strong_score_threshold"] = _integer(policy.get("strong_score_threshold"), 72, 40, 97)
    policy["near_break_score_threshold"] = _integer(policy.get("near_break_score_threshold"), 62, 40, 97)
    policy["unhealthy_source_score"] = _integer(policy.get("unhealthy_source_score"), 40, 0, 100)
    policy["standard_fiinquant_emergency_fallback"] = bool(
        policy.get("standard_fiinquant_emergency_fallback", True)
    )
    routing["policy"] = policy

    manual_raw = _mapping(raw.get("manual"))
    routing["manual"] = {
        "force_fiinquant": _symbols(manual_raw.get("force_fiinquant")),
        "force_standard": _symbols(manual_raw.get("force_standard")),
        "note": str(manual_raw.get("note") or routing["manual"]["note"]),
    }

    priority: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in raw.get("fiinquant_priority", []) if isinstance(raw.get("fiinquant_priority"), list) else []:
        if not isinstance(value, Mapping):
            continue
        symbol = normalize_symbol(value.get("symbol"))
        if not symbol or symbol in seen or symbol in INDEX_SYMBOLS:
            continue
        entry = dict(value)
        entry["symbol"] = symbol
        entry["reasons"] = [str(item) for item in value.get("reasons", []) if str(item).strip()]
        entry["attention_score"] = _integer(value.get("attention_score"), 0, 0, 1000)
        entry["consecutive_misses"] = _integer(value.get("consecutive_misses"), 0, 0, 100)
        priority.append(entry)
        seen.add(symbol)
    routing["fiinquant_priority"] = priority

    standard_raw = _mapping(raw.get("standard_routes"))
    routing["standard_routes"] = {
        source: _symbols(standard_raw.get(source)) for source in STANDARD_SOURCES
    }
    routing["updated_at"] = raw.get("updated_at")
    routing["last_mode"] = raw.get("last_mode")
    meta = _mapping(raw.get("meta"))
    routing["meta"].update(meta)
    return routing


def load_routing(path: Path = ROUTING_PATH) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return default_routing()
    try:
        return normalize_routing(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        logger.warning("Cannot read source routing %s: %s", path, exc)
        return default_routing()


def save_routing(routing: Mapping[str, Any], path: Path = ROUTING_PATH) -> dict[str, Any]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_routing(routing)
    text = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    clear_cache()
    return normalized


def clear_cache() -> None:
    global _CACHE, _CACHE_PATH, _CACHE_MTIME_NS
    with _CACHE_LOCK:
        _CACHE = None
        _CACHE_PATH = None
        _CACHE_MTIME_NS = None


def get_routing(path: Path | None = None) -> dict[str, Any]:
    global _CACHE, _CACHE_PATH, _CACHE_MTIME_NS
    selected = Path(path or ROUTING_PATH)
    try:
        mtime_ns = selected.stat().st_mtime_ns
    except OSError:
        mtime_ns = None
    with _CACHE_LOCK:
        if _CACHE is not None and _CACHE_PATH == selected and _CACHE_MTIME_NS == mtime_ns:
            return _CACHE
        _CACHE = load_routing(selected)
        _CACHE_PATH = selected
        _CACHE_MTIME_NS = mtime_ns
        return _CACHE


def priority_symbols(routing: Mapping[str, Any] | None = None) -> set[str]:
    item = normalize_routing(routing) if routing is not None else get_routing()
    forced_standard = set(item["manual"]["force_standard"])
    values = {entry["symbol"] for entry in item["fiinquant_priority"]}
    values.update(item["manual"]["force_fiinquant"])
    return values - forced_standard


def is_priority(symbol: str, routing: Mapping[str, Any] | None = None) -> bool:
    return normalize_symbol(symbol) in priority_symbols(routing)


def standard_primary(symbol: str, sources: Iterable[str] = STANDARD_SOURCES) -> str | None:
    available = [str(source).upper() for source in sources if str(source).upper() in STANDARD_SOURCES]
    if not available:
        return None
    bucket = zlib.crc32(normalize_symbol(symbol).encode("utf-8")) % len(available)
    return available[bucket]


def _rotated_standard_sources(symbol: str, available: list[str]) -> list[str]:
    standard = [source for source in STANDARD_SOURCES if source in available]
    primary = standard_primary(symbol, standard)
    if primary is None:
        return []
    start = standard.index(primary)
    return standard[start:] + standard[:start]


def _health_score(source: str, previous_health: Mapping[str, Any] | None) -> int:
    sources = _mapping(_mapping(previous_health).get("sources"))
    return _integer(_mapping(sources.get(source)).get("health_score"), 100, 0, 100)


def source_order(
    symbol: str,
    available_sources: Iterable[str],
    *,
    index_capable_sources: Iterable[str] | None = None,
    previous_health: Mapping[str, Any] | None = None,
    routing: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return a tier-aware fallback order for one symbol."""

    symbol = normalize_symbol(symbol)
    available: list[str] = []
    for value in available_sources:
        source = str(value).upper().strip()
        if source and source not in available:
            available.append(source)
    if symbol in INDEX_SYMBOLS or symbol.startswith("^"):
        capable = {str(value).upper() for value in (index_capable_sources or available)}
        available = [source for source in available if source in capable]
    if not available:
        return []

    item = normalize_routing(routing) if routing is not None else get_routing()
    policy = item["policy"]
    standard = _rotated_standard_sources(symbol, available)
    fiinquant = ["FIINQUANT"] if "FIINQUANT" in available else []

    if symbol not in INDEX_SYMBOLS and is_priority(symbol, item):
        ordered = fiinquant + standard
    else:
        ordered = standard
        if policy["standard_fiinquant_emergency_fallback"]:
            ordered += fiinquant

    # Unknown future providers stay usable without silently moving ahead of the
    # configured tiers.
    ordered += [source for source in available if source not in ordered]
    if not ordered:
        ordered = available

    unhealthy_below = int(policy["unhealthy_source_score"])
    return sorted(
        ordered,
        key=lambda source: _health_score(source, previous_health) < unhealthy_below,
    )


def _result_map(results: Mapping[str, Any] | Iterable[Any] | None) -> dict[str, Any]:
    values = results.values() if isinstance(results, Mapping) else (results or [])
    output: dict[str, Any] = {}
    for item in values:
        symbol = normalize_symbol(_get(item, "symbol"))
        if symbol and symbol not in INDEX_SYMBOLS:
            output[symbol] = item
    return output


def _metric_for(symbol: str, metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    return _mapping(_mapping(metrics).get(symbol))


def _attention_snapshot(
    symbol: str,
    result: Any,
    metric: Mapping[str, Any] | None,
    policy: Mapping[str, Any],
) -> dict[str, Any] | None:
    metric = _mapping(metric)
    structure = _mapping(metric.get("market_structure"))
    if not structure:
        structure = _mapping(_get(result, "market_structure", {}))
    breakout = _mapping(structure.get("breakout"))
    score = _number(
        metric.get(
            "advanced_score",
            _get(result, "opportunity_score", _get(result, "win_score", 0)),
        )
    )
    win_score = _number(_get(result, "win_score", _get(result, "opportunity_score", score)))
    market_state = str(
        structure.get("overall_state") or _get(result, "market_state", "NO_DATA") or "NO_DATA"
    ).upper()
    breakout_state = str(
        breakout.get("state") or _get(result, "breakout_state", "NO_DATA") or "NO_DATA"
    ).upper()
    action = str(_get(result, "action", "") or "").upper()
    near_break = bool(_get(result, "near_break", False))
    failed_break = bool(_get(result, "failed_break", False))
    reasons: list[str] = []
    attention = 0

    def flag(reason: str, value: int) -> None:
        nonlocal attention
        if reason not in reasons:
            reasons.append(reason)
        attention = max(attention, value)

    if failed_break:
        flag("failed_break", 134)
    if breakout_state in IMPORTANT_BREAKOUT_STATES:
        flag(f"breakout:{breakout_state}", IMPORTANT_BREAKOUT_STATES[breakout_state])
    if market_state == "OPPORTUNITY":
        flag("state:OPPORTUNITY", 121)
    elif market_state == "ACCUMULATION" and max(score, win_score) >= 55:
        flag("state:ACCUMULATION", 110)
    if near_break and max(score, win_score) >= int(policy["near_break_score_threshold"]):
        flag("near_break", 116)
    if max(score, win_score) >= int(policy["strong_score_threshold"]):
        flag(f"score:{int(round(max(score, win_score)))}", 108)
    if action in WEEKEND_ACTIONS:
        flag(f"weekend:{action}", 126 if action == "UU_TIEN_GOM" else 118)

    if not reasons:
        return None
    return {
        "symbol": symbol,
        "attention_score": attention,
        "reasons": reasons,
        "win_score": int(round(win_score)),
        "advanced_score": int(round(score)),
        "market_state": market_state,
        "breakout_state": breakout_state,
        "last_price": _number(_get(result, "close"), 0.0) or None,
        "as_of": _get(result, "as_of"),
    }


def _merge_candidate(candidates: dict[str, dict[str, Any]], symbol: Any, reason: str, score: int) -> None:
    normalized = normalize_symbol(symbol)
    if not normalized or normalized in INDEX_SYMBOLS:
        return
    item = candidates.setdefault(
        normalized,
        {
            "symbol": normalized,
            "attention_score": 0,
            "reasons": [],
            "win_score": 0,
            "advanced_score": 0,
            "market_state": "NO_DATA",
            "breakout_state": "NO_DATA",
            "last_price": None,
            "as_of": None,
        },
    )
    item["attention_score"] = max(int(item.get("attention_score") or 0), score)
    if reason not in item["reasons"]:
        item["reasons"].append(reason)


def _memory_symbols(memory_state: Mapping[str, Any] | None) -> tuple[list[str], list[str]]:
    memory = _mapping(memory_state)
    strong = _symbols(_get(item, "symbol") for item in memory.get("strong_stocks", []))
    watch = _symbols(_get(item, "symbol") for item in memory.get("watchlist", []))
    return strong, watch


def _transition_symbols(transitions: Iterable[Any] | None) -> list[str]:
    return _symbols(_get(item, "symbol") for item in (transitions or []))


def update_routing(
    results: Mapping[str, Any] | Iterable[Any] | None,
    *,
    metrics: Mapping[str, Any] | None = None,
    memory_state: Mapping[str, Any] | None = None,
    watch_items: Mapping[str, Any] | None = None,
    transitions: Iterable[Any] | None = None,
    universe: Iterable[Any] | None = None,
    mode: str = "auto",
    path: Path = ROUTING_PATH,
) -> dict[str, Any]:
    """Rebuild generated routes while preserving manual overrides and hysteresis."""

    path = Path(path)
    old = load_routing(path)
    policy = old["policy"]
    manual_fiinquant = set(old["manual"]["force_fiinquant"])
    manual_standard = set(old["manual"]["force_standard"])
    old_priority = {entry["symbol"]: dict(entry) for entry in old["fiinquant_priority"]}
    results_by_symbol = _result_map(results)
    candidates: dict[str, dict[str, Any]] = {}

    for symbol, result in results_by_symbol.items():
        snapshot = _attention_snapshot(symbol, result, _metric_for(symbol, metrics), policy)
        if snapshot:
            candidates[symbol] = snapshot

    strong_memory, watch_memory = _memory_symbols(memory_state)
    for symbol in strong_memory:
        _merge_candidate(candidates, symbol, "memory:strong", 125)
    for symbol in watch_memory:
        _merge_candidate(candidates, symbol, "memory:watch", 117)
    for symbol in _symbols((watch_items or {}).keys()):
        _merge_candidate(candidates, symbol, "portfolio_or_note", 145)
    for symbol in _transition_symbols(transitions):
        _merge_candidate(candidates, symbol, "state_transition", 130)
    for symbol in manual_fiinquant:
        _merge_candidate(candidates, symbol, "manual:force_fiinquant", 1000)

    evaluated_at = now_vn().isoformat(timespec="seconds")
    demote_after = int(policy["demote_after_scans"])
    retained: dict[str, dict[str, Any]] = {}

    for symbol, entry in old_priority.items():
        if symbol in manual_standard:
            continue
        if symbol in candidates:
            continue
        if symbol not in results_by_symbol:
            retained[symbol] = entry
            continue
        misses = int(entry.get("consecutive_misses") or 0) + 1
        if misses < demote_after:
            kept = dict(entry)
            kept["consecutive_misses"] = misses
            kept["reasons"] = [
                reason for reason in kept.get("reasons", []) if not str(reason).startswith("hysteresis:")
            ] + [f"hysteresis:{misses}/{demote_after}"]
            kept["last_evaluated_at"] = evaluated_at
            retained[symbol] = kept

    selected_entries: list[dict[str, Any]] = []
    for symbol, item in candidates.items():
        if symbol in manual_standard:
            continue
        old_entry = old_priority.get(symbol, {})
        entry = {
            "symbol": symbol,
            "tier": "FIINQUANT_PRIORITY",
            "primary_source": "FIINQUANT",
            "fallback_sources": list(STANDARD_SOURCES),
            "reasons": list(item.get("reasons", [])),
            "attention_score": int(item.get("attention_score") or 0),
            "win_score": int(item.get("win_score") or 0),
            "advanced_score": int(item.get("advanced_score") or 0),
            "market_state": str(item.get("market_state") or "NO_DATA"),
            "breakout_state": str(item.get("breakout_state") or "NO_DATA"),
            "last_price": item.get("last_price"),
            "as_of": item.get("as_of"),
            "first_promoted_at": old_entry.get("first_promoted_at") or evaluated_at,
            "last_evaluated_at": evaluated_at,
            "consecutive_misses": 0,
        }
        selected_entries.append(entry)
    selected_entries.extend(retained.values())

    def sort_key(entry: Mapping[str, Any]) -> tuple[int, int, int, str]:
        symbol = str(entry.get("symbol") or "")
        return (
            int(symbol in manual_fiinquant),
            int(entry.get("attention_score") or 0),
            -int(entry.get("consecutive_misses") or 0),
            symbol,
        )

    selected_entries.sort(key=sort_key, reverse=True)
    forced_entries = [entry for entry in selected_entries if entry["symbol"] in manual_fiinquant]
    automatic_entries = [entry for entry in selected_entries if entry["symbol"] not in manual_fiinquant]
    limit = int(policy["priority_limit"])
    if len(forced_entries) > limit:
        logger.warning(
            "Manual FiinQuant list has %s symbols; only the first %s fit the safety cap",
            len(forced_entries),
            limit,
        )
        forced_entries = forced_entries[:limit]
    slots = max(0, limit - len(forced_entries))
    selected_entries = forced_entries + automatic_entries[:slots]
    selected_entries.sort(key=sort_key, reverse=True)
    priority_set = {entry["symbol"] for entry in selected_entries}

    all_symbols: set[str] = set(_symbols(universe))
    all_symbols.update(results_by_symbol)
    all_symbols.update(manual_fiinquant)
    all_symbols.update(manual_standard)
    all_symbols.update(strong_memory)
    all_symbols.update(watch_memory)
    for values in old["standard_routes"].values():
        all_symbols.update(_symbols(values))
    all_symbols -= priority_set
    all_symbols -= INDEX_SYMBOLS

    standard_routes = {source: [] for source in STANDARD_SOURCES}
    for symbol in sorted(all_symbols):
        primary = standard_primary(symbol)
        if primary:
            standard_routes[primary].append(symbol)

    routing = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": evaluated_at,
        "last_mode": mode,
        "policy": policy,
        "manual": old["manual"],
        "fiinquant_priority": selected_entries,
        "standard_routes": standard_routes,
        "meta": {
            "priority_count": len(selected_entries),
            "standard_count": sum(len(values) for values in standard_routes.values()),
            "standard_counts_by_source": {
                source: len(values) for source, values in standard_routes.items()
            },
            "evaluated_symbols": len(results_by_symbol),
            "demote_after_scans": demote_after,
            "description": (
                "Mã cần chú ý dùng FiinQuant trước. Mã thường được cân tải qua "
                "VCI/KBS/DNSE; FiinQuant chỉ cứu hộ khi cả ba nguồn thường lỗi."
            ),
        },
    }
    saved = save_routing(routing, path)
    logger.info(
        "Source routing updated: priority=%s standard=%s mode=%s",
        saved["meta"]["priority_count"],
        saved["meta"]["standard_count"],
        mode,
    )
    return saved


def update_from_weekend(
    opportunities: Iterable[Any],
    *,
    universe: Iterable[Any] | None = None,
    path: Path = ROUTING_PATH,
) -> dict[str, Any]:
    candidates = [
        item
        for item in opportunities
        if bool(_get(item, "selected", False))
        or str(_get(item, "action", "")).upper() in WEEKEND_ACTIONS
    ]
    return update_routing(candidates, universe=universe, mode="weekend", path=path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def refresh_from_files(path: Path = ROUTING_PATH) -> dict[str, Any]:
    """Manual/bootstrap refresh using the latest committed scanner outputs."""

    results = _read_json(DATA_DIR / "results_latest.json", [])
    memory = _read_json(DATA_DIR / "memory_state.json", {})
    alerts = _read_json(DATA_DIR / "session_alerts_latest.json", {})
    feed = _read_json(DATA_DIR / "filter_feed_latest.json", {})
    portfolio = _read_json(DATA_DIR / "portfolio.json", [])
    notes = _read_json(DATA_DIR / "notes.json", {})
    metrics: dict[str, Any] = {}
    for fact in feed.get("facts", []) if isinstance(feed, Mapping) else []:
        if not isinstance(fact, Mapping):
            continue
        symbol = normalize_symbol(fact.get("symbol"))
        scores = _mapping(fact.get("scores"))
        if symbol:
            metrics[symbol] = {
                "advanced_score": scores.get("advanced", fact.get("win_score", 0)),
                "market_structure": fact.get("market_structure", {}),
            }
    watch_items: dict[str, Any] = {}
    for item in portfolio if isinstance(portfolio, list) else []:
        symbol = normalize_symbol(_get(item, "symbol"))
        if symbol:
            watch_items[symbol] = item
    if isinstance(notes, Mapping):
        watch_items.update({normalize_symbol(symbol): item for symbol, item in notes.items()})
    universe: list[str] = []
    try:
        import scan  # Local import avoids a scanner import cycle.

        universe = list(scan.ALL_TICKERS)
    except Exception:
        pass
    mode = str(alerts.get("mode") or "bootstrap") if isinstance(alerts, Mapping) else "bootstrap"
    transitions = alerts.get("state_transitions", []) if isinstance(alerts, Mapping) else []
    return update_routing(
        results,
        metrics=metrics,
        memory_state=memory,
        watch_items=watch_items,
        transitions=transitions,
        universe=universe,
        mode=mode,
        path=path,
    )


if __name__ == "__main__":
    item = refresh_from_files()
    print(
        f"source routing: FiinQuant={item['meta']['priority_count']} "
        f"standard={item['meta']['standard_count']}"
    )
