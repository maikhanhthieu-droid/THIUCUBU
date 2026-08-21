"""Exclusive five-stream classification for the human-facing session report."""

from __future__ import annotations

from typing import Any, Mapping


PORTFOLIO = "portfolio"
OPPORTUNITY = "opportunity"
EARLY = "early"
TECHNICAL = "technical"
STRUCTURE = "structure"

DISPLAY_ORDER = (PORTFOLIO, OPPORTUNITY, EARLY, TECHNICAL, STRUCTURE)
STRUCTURE_ALERT_STATES = {
    "FAILED_BREAK_CONFIRMED",
    "FAILED_BREAK_WATCH",
    "REACCUMULATION",
    "HEALTHY_RETEST",
    "RECLAIMED_BREAK",
    "BREAKOUT_UNCONFIRMED",
}


def _metric(metrics: Mapping[str, Mapping[str, Any]], symbol: str) -> Mapping[str, Any]:
    item = metrics.get(symbol, {})
    return item if isinstance(item, Mapping) else {}


def _advanced(result: Any, metrics: Mapping[str, Mapping[str, Any]]) -> int:
    return int(_metric(metrics, str(result.symbol)).get("advanced_score", getattr(result, "win_score", 0)) or 0)


def _structure(result: Any, metrics: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    item = _metric(metrics, str(result.symbol)).get("market_structure", {})
    return item if isinstance(item, Mapping) else {}


def stream_score(stream: str, result: Any, metrics: Mapping[str, Mapping[str, Any]]) -> int:
    item = _metric(metrics, str(result.symbol))
    if stream == EARLY:
        return int((item.get("early_accumulation") or {}).get("score", 0) or 0)
    if stream == TECHNICAL:
        return int((item.get("technical_watch") or {}).get("score", 0) or 0)
    if stream == OPPORTUNITY:
        return max(
            _advanced(result, metrics),
            int(getattr(result, "position_score", 0) or 0),
            int(getattr(result, "trade_score", 0) or 0),
        )
    return _advanced(result, metrics)


def classify_streams(
    results: Mapping[str, Any],
    metrics: Mapping[str, Mapping[str, Any]],
    watch_items: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[Any]]:
    """Assign every displayed stock to at most one primary report stream.

    Portfolio is an ownership overlay and always wins.  For remaining symbols,
    explicit break/retest diagnostics win over opportunity scoring so a failed
    structure cannot be presented as a fresh buy candidate.
    """

    streams: dict[str, list[Any]] = {key: [] for key in DISPLAY_ORDER}
    portfolio_symbols = {str(symbol).upper() for symbol in watch_items}
    for symbol in portfolio_symbols:
        result = results.get(symbol)
        if result is not None and symbol != "VNINDEX":
            streams[PORTFOLIO].append(result)

    for symbol, result in results.items():
        normalized = str(symbol).upper()
        if normalized == "VNINDEX" or normalized in portfolio_symbols:
            continue
        item = _metric(metrics, normalized)
        structure = _structure(result, metrics)
        breakout = structure.get("breakout") if isinstance(structure.get("breakout"), Mapping) else {}
        breakout_state = str(breakout.get("state") or getattr(result, "breakout_state", "NO_DATA"))
        overall = str(structure.get("overall_state") or getattr(result, "market_state", "NO_DATA"))
        early = item.get("early_accumulation") if isinstance(item.get("early_accumulation"), Mapping) else {}
        technical = item.get("technical_watch") if isinstance(item.get("technical_watch"), Mapping) else {}
        advanced = _advanced(result, metrics)
        position = int(getattr(result, "position_score", 0) or 0)

        if getattr(result, "failed_break", False) or breakout_state in STRUCTURE_ALERT_STATES:
            streams[STRUCTURE].append(result)
        elif bool(technical.get("risk_watch")) and technical.get("risk_dominant", True):
            streams[TECHNICAL].append(result)
        elif (
            overall == "OPPORTUNITY"
            or (overall == "ACCUMULATION" and max(position, advanced) >= 55)
            or (advanced >= 72 and overall not in {"DISTRIBUTION", "NO_DATA"})
        ):
            streams[OPPORTUNITY].append(result)
        elif bool(early.get("eligible")):
            streams[EARLY].append(result)
        elif bool(technical.get("watch")):
            streams[TECHNICAL].append(result)

    for stream, rows in streams.items():
        rows.sort(
            key=lambda row: (
                stream_score(stream, row, metrics),
                int(getattr(row, "win_score", 0) or 0),
                str(getattr(row, "symbol", "")),
            ),
            reverse=True,
        )
    return streams


def symbol_summary(
    stream: str,
    rows: list[Any],
    metrics: Mapping[str, Mapping[str, Any]],
    *,
    extra_symbols: list[str] | None = None,
    limit: int = 30,
) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for row in rows:
        symbol = str(getattr(row, "symbol", "")).upper()
        seen.add(symbol)
        score = stream_score(stream, row, metrics)
        if stream == EARLY:
            early = _metric(metrics, symbol).get("early_accumulation") or {}
            stage = str(early.get("stage", "E1"))
            pre = str(early.get("pre_label") or "NONE")
            suffix = f"/{pre}" if pre != "NONE" else ""
            parts.append(f"`{symbol}` {stage}-{score}{suffix}")
        elif stream == TECHNICAL:
            technical = _metric(metrics, symbol).get("technical_watch") or {}
            pre = str(technical.get("pre_label") or "NONE")
            if pre == "NONE":
                pre = str(technical.get("risk_label") or "NONE")
            suffix = f"/{pre}" if pre != "NONE" else ""
            parts.append(f"`{symbol}` T-{score}{suffix}")
        else:
            parts.append(f"`{symbol}` {score}")
    for symbol in extra_symbols or []:
        normalized = str(symbol).upper()
        if normalized not in seen:
            parts.append(f"`{normalized}` NO_DATA")
            seen.add(normalized)
    if not parts:
        return "không có"
    hidden = max(0, len(parts) - limit)
    text = ", ".join(parts[:limit])
    return f"{text}, +{hidden} mã" if hidden else text


def primary_stream_map(streams: Mapping[str, list[Any]]) -> dict[str, str]:
    return {
        str(getattr(row, "symbol", "")).upper(): stream
        for stream, rows in streams.items()
        for row in rows
    }


def serialize_streams(
    streams: Mapping[str, list[Any]],
    metrics: Mapping[str, Mapping[str, Any]],
    *,
    portfolio_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Return a stable machine-readable view for downstream projects."""

    payload: dict[str, list[dict[str, Any]]] = {}
    for stream in DISPLAY_ORDER:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for result in streams.get(stream, []):
            symbol = str(getattr(result, "symbol", "")).upper()
            item: dict[str, Any] = {
                "symbol": symbol,
                "rank_score": stream_score(stream, result, metrics),
            }
            if stream == EARLY:
                early = _metric(metrics, symbol).get("early_accumulation") or {}
                item["stage"] = early.get("stage")
                item["pre_label"] = early.get("pre_label")
                item["trigger_price"] = early.get("trigger_price")
                item["invalidation_price"] = early.get("invalidation_price")
            elif stream == TECHNICAL:
                technical = _metric(metrics, symbol).get("technical_watch") or {}
                item["stage"] = technical.get("stage")
                item["pre_label"] = technical.get("pre_label")
                item["risk_label"] = technical.get("risk_label")
                item["bottom_count"] = technical.get("bottom_count")
                item["trigger_price"] = technical.get("trigger_price")
                item["invalidation_price"] = technical.get("invalidation_price")
            rows.append(item)
            seen.add(symbol)
        if stream == PORTFOLIO:
            for symbol in portfolio_symbols or []:
                normalized = str(symbol).upper()
                if normalized not in seen:
                    rows.append({"symbol": normalized, "rank_score": None, "data_status": "NO_DATA"})
        payload[stream] = rows
    return {
        "schema_version": "thieucubu.five_streams.v1",
        "display_order": list(DISPLAY_ORDER),
        "streams": payload,
    }
