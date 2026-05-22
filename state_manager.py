#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("thieucutoo.state")

VN_TZ = timezone(timedelta(hours=7))
DATA_DIR = Path("data")
MEMORY_FILE = DATA_DIR / "memory_state.json"
MEMORY_VERSION = 1

STRONG_LIMIT = 7
WATCHLIST_LIMIT = 15
SESSION_FOCUS_LIMIT = 40
STALE_KEEP_DAYS = 21
RETIRED_LIMIT = 50


def now_vn() -> datetime:
    return datetime.now(VN_TZ)


def normalize_symbol(value: Any) -> str:
    return str(value or "").replace("`", "").upper().strip()


def default_state() -> dict[str, Any]:
    return {
        "version": MEMORY_VERSION,
        "last_updated": None,
        "last_mode": None,
        "strong_stocks": [],
        "watchlist": [],
        "session_focus": [],
        "retired": [],
        "meta": {
            "strong_limit": STRONG_LIMIT,
            "watchlist_limit": WATCHLIST_LIMIT,
            "session_focus_limit": SESSION_FOCUS_LIMIT,
            "stale_keep_days": STALE_KEEP_DAYS,
            "retired_limit": RETIRED_LIMIT,
            "description": "Bot memory for strong stocks, VCP/VSA watchlist, and priority focus symbols.",
        },
    }


class StateManager:
    """Small persistent memory layer for stateless GitHub Actions runners."""

    def __init__(self, path: Path = MEMORY_FILE) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        return load_state(self.path)

    def save(self, state: dict[str, Any], mode: str | None = None) -> dict[str, Any]:
        current = now_vn()
        normalized = self.prune(state)
        normalized["version"] = MEMORY_VERSION
        normalized["last_updated"] = current.isoformat(timespec="seconds")
        if mode:
            normalized["last_mode"] = mode
        save_state(normalized, self.path)
        return normalized

    def prune(self, state: dict[str, Any] | None = None) -> dict[str, Any]:
        current = now_vn()
        normalized = normalize_state(state or self.load())
        normalized["strong_stocks"] = _unique_entries(
            _drop_stale_entries(normalized.get("strong_stocks", []), current),
            STRONG_LIMIT,
        )
        normalized["watchlist"] = _unique_entries(
            _drop_stale_entries(normalized.get("watchlist", []), current),
            WATCHLIST_LIMIT,
        )
        normalized["retired"] = _unique_entries(
            _drop_stale_entries(normalized.get("retired", []), current),
            RETIRED_LIMIT,
        )
        normalized["session_focus"] = _clean_symbols(
            [item["symbol"] for item in normalized["strong_stocks"]]
            + [item["symbol"] for item in normalized["watchlist"]]
        )[:SESSION_FOCUS_LIMIT]
        normalized["meta"].update(
            {
                "strong_count": len(normalized["strong_stocks"]),
                "watchlist_count": len(normalized["watchlist"]),
                "session_focus_count": len(normalized["session_focus"]),
                "retired_count": len(normalized["retired"]),
            }
        )
        return normalized

    def update_from_results(
        self,
        results: dict[str, Any] | Iterable[Any],
        mode: str,
        focus_symbols: Iterable[Any] | None = None,
        watch_items: dict[str, dict[str, Any]] | None = None,
        metrics_by_symbol: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return _update_memory_state_impl(results, mode, focus_symbols, watch_items, metrics_by_symbol, self.path)

    def update_strong_stocks(
        self,
        results: dict[str, Any] | Iterable[Any],
        mode: str = "manual",
        metrics_by_symbol: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._update_bucket(results, mode, "strong", metrics_by_symbol)

    def update_watchlist(
        self,
        results: dict[str, Any] | Iterable[Any],
        mode: str = "manual",
        metrics_by_symbol: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._update_bucket(results, mode, "watchlist", metrics_by_symbol)

    def _update_bucket(
        self,
        results: dict[str, Any] | Iterable[Any],
        mode: str,
        category: str,
        metrics_by_symbol: dict[str, dict[str, Any]] | None,
    ) -> dict[str, Any]:
        state = self.load()
        updated_at = now_vn()
        values = results.values() if isinstance(results, dict) else results
        bucket_name = "strong_stocks" if category == "strong" else "watchlist"
        limit = STRONG_LIMIT if category == "strong" else WATCHLIST_LIMIT
        old_entries = {item["symbol"]: item for item in _clean_entries(state.get(bucket_name, []))}
        merged = list(old_entries.values())
        for result in values:
            symbol = normalize_symbol(_get(result, "symbol"))
            if not symbol or symbol == "VNINDEX":
                continue
            metrics = (metrics_by_symbol or {}).get(symbol, {})
            merged.append(_entry_from_result(result, old_entries.get(symbol), metrics, mode, category, updated_at))
        state[bucket_name] = _unique_entries(merged, limit)
        state["session_focus"] = _clean_symbols(
            [item["symbol"] for item in state.get("strong_stocks", [])]
            + [item["symbol"] for item in state.get("watchlist", [])]
            + state.get("session_focus", [])
        )[:SESSION_FOCUS_LIMIT]
        return self.save(state, mode)


def load_state(path: Path = MEMORY_FILE) -> dict[str, Any]:
    if not path.exists():
        return default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Cannot read memory state %s: %s", path, exc)
        return default_state()
    return normalize_state(raw)


def save_state(state: dict[str, Any], path: Path = MEMORY_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_state(state)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_state(raw: Any) -> dict[str, Any]:
    state = default_state()
    if not isinstance(raw, dict):
        return state
    state.update({key: raw.get(key, state[key]) for key in ("last_updated", "last_mode")})
    state["version"] = int(raw.get("version") or MEMORY_VERSION)
    for key in ("strong_stocks", "watchlist", "session_focus", "retired"):
        value = raw.get(key, [])
        state[key] = value if isinstance(value, list) else []
    meta = raw.get("meta")
    if isinstance(meta, dict):
        state["meta"].update(meta)
    state["strong_stocks"] = _clean_entries(state["strong_stocks"])[:STRONG_LIMIT]
    state["watchlist"] = _clean_entries(state["watchlist"])[:WATCHLIST_LIMIT]
    state["session_focus"] = _clean_symbols(state["session_focus"])[:SESSION_FOCUS_LIMIT]
    state["retired"] = _clean_entries(state["retired"])[:50]
    return state


def _clean_symbols(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        symbol = normalize_symbol(value)
        if not symbol or symbol == "VNINDEX" or symbol in seen:
            continue
        if len(symbol) < 3 or len(symbol) > 12:
            continue
        result.append(symbol)
        seen.add(symbol)
    return result


def _clean_entries(values: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        symbol = normalize_symbol(value.get("symbol"))
        if not symbol or symbol == "VNINDEX" or symbol in seen:
            continue
        item = dict(value)
        item["symbol"] = symbol
        result.append(item)
        seen.add(symbol)
    return result


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _score(result: Any, metrics: dict[str, Any] | None) -> int:
    if isinstance(metrics, dict):
        if "advanced_score" in metrics:
            return _int(metrics.get("advanced_score"), _int(_get(result, "win_score"), 0))
        gate = metrics.get("gate")
        if isinstance(gate, dict) and "score" in gate:
            return _int(gate.get("score"), _int(_get(result, "win_score"), 0))
    return _int(_get(result, "win_score"), 0)


def _today_from_iso(value: Any) -> datetime | None:
    try:
        raw = str(value or "")
        if not raw:
            return None
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _is_stale(entry: dict[str, Any], today: datetime) -> bool:
    last = _today_from_iso(entry.get("last_seen") or entry.get("last_updated"))
    if last is None:
        return False
    return (today.date() - last.astimezone(VN_TZ).date()).days > STALE_KEEP_DAYS


def _drop_stale_entries(entries: Iterable[dict[str, Any]], today: datetime) -> list[dict[str, Any]]:
    return [entry for entry in _clean_entries(entries) if not _is_stale(entry, today)]


def _entry_from_result(
    result: Any,
    existing: dict[str, Any] | None,
    metrics: dict[str, Any] | None,
    mode: str,
    category: str,
    updated_at: datetime,
) -> dict[str, Any]:
    symbol = normalize_symbol(_get(result, "symbol"))
    today = updated_at.date().isoformat()
    existing = dict(existing or {})
    prev_seen = str(existing.get("last_seen") or "")
    day_key = "accumulation_days" if category == "strong" else "consolidation_days"
    old_days = _int(existing.get(day_key, existing.get("accumulation_days", existing.get("consolidation_days", 0))), 0)
    days = max(1, old_days + (0 if prev_seen == today else 1))
    score = _score(result, metrics)
    reason = str(_get(result, "reason", "") or "").strip()
    flags = []
    if bool(_get(result, "near_break", False)):
        flags.append("near_break")
    if bool(_get(result, "obv_up", False)):
        flags.append("obv_up")
    if _float(_get(result, "mfi"), 0.0) >= 50:
        flags.append("mfi_ok")
    if bool(_get(result, "failed_break", False)):
        flags.append("failed_break")

    item = {
        "symbol": symbol,
        "category": category,
        "first_detected": existing.get("first_detected") or today,
        "last_seen": today,
        "last_updated": updated_at.isoformat(timespec="seconds"),
        "last_mode": mode,
        "score": score,
        "win_score": _int(_get(result, "win_score"), score),
        "close": round(_float(_get(result, "close")), 2),
        "sector": str(_get(result, "sector", "") or ""),
        "setup": str(_get(result, "setup", "") or ""),
        "near_break": bool(_get(result, "near_break", False)),
        "obv_up": bool(_get(result, "obv_up", False)),
        "failed_break": bool(_get(result, "failed_break", False)),
        "rsi": round(_float(_get(result, "rsi")), 1),
        "mfi": round(_float(_get(result, "mfi")), 1),
        "vol_ratio": round(_float(_get(result, "vol_ratio")), 2),
        day_key: days,
        "last_signal": reason[:180],
        "flags": flags,
        "note": str(existing.get("note", "") or ""),
    }
    if category == "strong":
        item["accumulation_days"] = days
    else:
        item["consolidation_days"] = days
    if isinstance(metrics, dict):
        item["rs_score"] = _int((metrics.get("rs") or {}).get("rs_score"), _int(metrics.get("rs_score"), 0))
        item["risk_reward"] = round(_float((metrics.get("trade") or {}).get("risk_reward")), 2)
    return item


def _sort_entries(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        _clean_entries(entries),
        key=lambda item: (
            _int(item.get("score")),
            bool(item.get("near_break")),
            bool(item.get("obv_up")),
            str(item.get("last_seen") or ""),
        ),
        reverse=True,
    )


def _unique_entries(entries: Iterable[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in _sort_entries(entries):
        symbol = normalize_symbol(entry.get("symbol"))
        if not symbol or symbol in seen:
            continue
        output.append(entry)
        seen.add(symbol)
        if len(output) >= limit:
            break
    return output


def _update_memory_state_impl(
    results: dict[str, Any] | Iterable[Any],
    mode: str,
    focus_symbols: Iterable[Any] | None = None,
    watch_items: dict[str, dict[str, Any]] | None = None,
    metrics_by_symbol: dict[str, dict[str, Any]] | None = None,
    path: Path = MEMORY_FILE,
) -> dict[str, Any]:
    state = load_state(path)
    updated_at = now_vn()
    values = results.values() if isinstance(results, dict) else results
    by_symbol = {normalize_symbol(_get(result, "symbol")): result for result in values}
    by_symbol.pop("VNINDEX", None)
    old_entries = {
        item["symbol"]: item
        for item in _clean_entries(state.get("strong_stocks", [])) + _clean_entries(state.get("watchlist", []))
    }

    strong_entries: list[dict[str, Any]] = []
    watch_entries: list[dict[str, Any]] = []
    retired: list[dict[str, Any]] = []

    for symbol, result in by_symbol.items():
        if not symbol:
            continue
        metrics = (metrics_by_symbol or {}).get(symbol, {})
        score = _score(result, metrics)
        near_break = bool(_get(result, "near_break", False))
        obv_up = bool(_get(result, "obv_up", False))
        failed_break = bool(_get(result, "failed_break", False))
        existing = old_entries.get(symbol)
        if failed_break or score < 45:
            if existing:
                item = _entry_from_result(result, existing, metrics, mode, "retired", updated_at)
                item["retired_reason"] = "failed_break" if failed_break else "weak_score"
                retired.append(item)
            continue
        if score >= 82 or (score >= 74 and near_break and obv_up):
            strong_entries.append(_entry_from_result(result, existing, metrics, mode, "strong", updated_at))
        elif score >= 68 or (score >= 62 and near_break):
            watch_entries.append(_entry_from_result(result, existing, metrics, mode, "watchlist", updated_at))
        elif existing and score >= 58:
            watch_entries.append(_entry_from_result(result, existing, metrics, mode, "watchlist", updated_at))

    today = updated_at
    for item in _drop_stale_entries(state.get("strong_stocks", []), today):
        symbol = item["symbol"]
        if symbol not in by_symbol:
            strong_entries.append(item)
    for item in _drop_stale_entries(state.get("watchlist", []), today):
        symbol = item["symbol"]
        if symbol not in by_symbol:
            watch_entries.append(item)

    strong = _unique_entries(strong_entries, STRONG_LIMIT)
    strong_symbols = {item["symbol"] for item in strong}
    watch = _unique_entries([item for item in watch_entries if item.get("symbol") not in strong_symbols], WATCHLIST_LIMIT)

    focus_seed: list[Any] = []
    focus_seed.extend(focus_symbols or [])
    focus_seed.extend(item["symbol"] for item in strong)
    focus_seed.extend(item["symbol"] for item in watch)
    focus_seed.extend((watch_items or {}).keys())
    focus_seed.extend(state.get("session_focus", []))

    state["version"] = MEMORY_VERSION
    state["last_updated"] = updated_at.isoformat(timespec="seconds")
    state["last_mode"] = mode
    state["strong_stocks"] = strong
    state["watchlist"] = watch
    state["session_focus"] = _clean_symbols(focus_seed)[:SESSION_FOCUS_LIMIT]
    state["retired"] = _unique_entries(retired + _drop_stale_entries(state.get("retired", []), today), RETIRED_LIMIT)
    state["meta"].update(
        {
            "strong_count": len(strong),
            "watchlist_count": len(watch),
            "session_focus_count": len(state["session_focus"]),
            "retired_count": len(state["retired"]),
        }
    )
    save_state(state, path)
    return state


def update_memory_state(
    results: dict[str, Any] | Iterable[Any],
    mode: str,
    focus_symbols: Iterable[Any] | None = None,
    watch_items: dict[str, dict[str, Any]] | None = None,
    metrics_by_symbol: dict[str, dict[str, Any]] | None = None,
    path: Path = MEMORY_FILE,
) -> dict[str, Any]:
    return StateManager(path).update_from_results(results, mode, focus_symbols, watch_items, metrics_by_symbol)


def memory_focus_symbols(state: dict[str, Any] | None = None, limit: int = SESSION_FOCUS_LIMIT) -> list[str]:
    item = normalize_state(state or load_state())
    symbols: list[Any] = []
    symbols.extend(entry.get("symbol") for entry in item.get("strong_stocks", []))
    symbols.extend(entry.get("symbol") for entry in item.get("watchlist", []))
    symbols.extend(item.get("session_focus", []))
    return _clean_symbols(symbols)[:limit]


def memory_summary(state: dict[str, Any] | None = None) -> dict[str, Any]:
    item = normalize_state(state or load_state())
    return {
        "last_updated": item.get("last_updated"),
        "last_mode": item.get("last_mode"),
        "strong_count": len(item.get("strong_stocks", [])),
        "watchlist_count": len(item.get("watchlist", [])),
        "session_focus": item.get("session_focus", [])[:SESSION_FOCUS_LIMIT],
    }
