"""Persist and report only material market-structure or score transitions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "thieucubu.market_state_history.v1"
VN_TZ = timezone(timedelta(hours=7))
IMPORTANT_BREAKOUT = {
    "FAILED_BREAK_CONFIRMED",
    "FAILED_BREAK_WATCH",
    "REACCUMULATION",
    "HEALTHY_RETEST",
    "RECLAIMED_BREAK",
    "BREAKOUT_CONFIRMED",
    "BREAKOUT_UNCONFIRMED",
}
IMPORTANT_MARKET = {"OPPORTUNITY", "ACCUMULATION", "CAUTION", "DISTRIBUTION"}
SCORE_BANDS = (62, 72, 82, 90)


def _load(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return {"schema_version": SCHEMA_VERSION, "states": {}, "events": []}
    raw["states"] = raw.get("states") if isinstance(raw.get("states"), dict) else {}
    raw["events"] = raw.get("events") if isinstance(raw.get("events"), list) else []
    return raw


def _save(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _state(result: Any, metrics: Mapping[str, Any]) -> dict[str, Any]:
    structure = metrics.get("market_structure") if isinstance(metrics.get("market_structure"), Mapping) else {}
    breakout = structure.get("breakout") if isinstance(structure.get("breakout"), Mapping) else {}
    return {
        "as_of": getattr(result, "as_of", None),
        "score": int(metrics.get("advanced_score", getattr(result, "win_score", 0)) or 0),
        "market_state": str(structure.get("overall_state") or getattr(result, "market_state", "NO_DATA")),
        "breakout_state": str(breakout.get("state") or getattr(result, "breakout_state", "NO_DATA")),
        "daily_phase": str(getattr(result, "daily_phase", "NO_DATA")),
        "weekly_phase": str(getattr(result, "weekly_phase", "NO_DATA")),
        "monthly_phase": str(getattr(result, "monthly_phase", "NO_DATA")),
        "primary_stream": str(metrics.get("primary_stream") or "unclassified"),
    }


def _crossed_band(old_score: int, new_score: int) -> int | None:
    crossed = [band for band in SCORE_BANDS if (old_score < band <= new_score) or (new_score < band <= old_score)]
    return max(crossed) if crossed else None


def _event(symbol: str, kind: str, old: Any, new: Any, score: int, as_of: Any) -> dict[str, Any]:
    now = datetime.now(VN_TZ).isoformat(timespec="seconds")
    return {
        "event_id": f"{symbol}:{kind}:{as_of or now}:{new}",
        "detected_at": now,
        "as_of": as_of,
        "symbol": symbol,
        "kind": kind,
        "from": old,
        "to": new,
        "score": score,
    }


def _material_events(symbol: str, old: Mapping[str, Any], new: Mapping[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    old_breakout = str(old.get("breakout_state", "NO_DATA"))
    new_breakout = str(new.get("breakout_state", "NO_DATA"))
    if old_breakout != new_breakout and new_breakout in IMPORTANT_BREAKOUT:
        events.append(_event(symbol, "BREAKOUT_STATE", old_breakout, new_breakout, int(new["score"]), new.get("as_of")))

    old_market = str(old.get("market_state", "NO_DATA"))
    new_market = str(new.get("market_state", "NO_DATA"))
    if old_market != new_market and (old_market in IMPORTANT_MARKET or new_market in IMPORTANT_MARKET):
        events.append(_event(symbol, "MARKET_STATE", old_market, new_market, int(new["score"]), new.get("as_of")))

    old_stream = str(old.get("primary_stream") or "unclassified")
    new_stream = str(new.get("primary_stream") or "unclassified")
    if "primary_stream" in old and old_stream != new_stream and (
        old_stream != "unclassified" or new_stream != "unclassified"
    ):
        events.append(
            _event(symbol, "PRIMARY_STREAM", old_stream, new_stream, int(new["score"]), new.get("as_of"))
        )

    old_score = int(old.get("score", 0) or 0)
    new_score = int(new.get("score", 0) or 0)
    band = _crossed_band(old_score, new_score)
    if band is not None and abs(new_score - old_score) >= 8:
        kind = "SCORE_UP" if new_score > old_score else "SCORE_DOWN"
        event = _event(symbol, kind, old_score, new_score, new_score, new.get("as_of"))
        event["crossed_band"] = band
        events.append(event)
    return events


def update_transitions(
    *,
    path: Path,
    results: Mapping[str, Any],
    metrics_by_symbol: Mapping[str, Mapping[str, Any]],
    persist: bool = True,
) -> list[dict[str, Any]]:
    payload = _load(path)
    previous = payload["states"]
    current: dict[str, dict[str, Any]] = {
        str(symbol): dict(state)
        for symbol, state in previous.items()
        if isinstance(state, Mapping)
    }
    detected: list[dict[str, Any]] = []
    for symbol, result in results.items():
        normalized = str(symbol).upper()
        if normalized == "VNINDEX":
            continue
        state = _state(result, metrics_by_symbol.get(normalized, {}))
        current[normalized] = state
        old = previous.get(normalized)
        if isinstance(old, Mapping):
            detected.extend(_material_events(normalized, old, state))

    known_ids = {str(item.get("event_id")) for item in payload["events"] if isinstance(item, Mapping)}
    detected = [item for item in detected if item["event_id"] not in known_ids]
    detected.sort(key=lambda item: (item["kind"] not in {"BREAKOUT_STATE", "MARKET_STATE"}, -int(item["score"])))
    payload["states"] = current
    payload["events"] = (payload["events"] + detected)[-500:]
    payload["updated_at"] = datetime.now(VN_TZ).isoformat(timespec="seconds")
    if persist:
        _save(path, payload)
    return detected


def format_transition(event: Mapping[str, Any]) -> str:
    symbol = event.get("symbol", "")
    kind = event.get("kind")
    old = str(event.get("from", ""))
    new = str(event.get("to", ""))
    score = int(event.get("score", 0) or 0)
    if kind == "SCORE_UP":
        return f"`{symbol}` điểm tăng đáng kể {old} → {new} (vượt mốc {event.get('crossed_band')})"
    if kind == "SCORE_DOWN":
        return f"`{symbol}` điểm giảm đáng kể {old} → {new} (rơi mốc {event.get('crossed_band')})"
    if kind == "BREAKOUT_STATE":
        return f"`{symbol}` cấu trúc break: {old} → *{new}* | điểm {score}"
    if kind == "PRIMARY_STREAM":
        return f"`{symbol}` chuyển luồng: {old} → *{new}* | điểm {score}"
    return f"`{symbol}` trạng thái: {old} → *{new}* | điểm {score}"
