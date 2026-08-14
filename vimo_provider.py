#!/usr/bin/env python3
"""Optional VIMO MCP adapter.

VIMO is an enrichment source, never an OHLCV dependency.  All public helpers
return compact normalized payloads so raw/narrative data is not persisted or
redistributed by the scanner.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx


logger = logging.getLogger("thieucutoo.vimo")
VN_TZ = timezone(timedelta(hours=7))
BASE_URL = os.getenv("VIMO_BASE_URL", "https://vimo.cuthongthai.vn").rstrip("/")
MCP_ENDPOINT = os.getenv("VIMO_MCP_ENDPOINT", f"{BASE_URL}/api/mcp-server")
CACHE_DIR = Path(os.getenv("VIMO_CACHE_DIR", "data/cache/vimo"))
TIMEOUT = httpx.Timeout(
    float(os.getenv("VIMO_TIMEOUT_SEC", "20")),
    connect=float(os.getenv("VIMO_CONNECT_TIMEOUT_SEC", "8")),
)


class VimoError(RuntimeError):
    """Base error for the optional provider."""


class VimoConfigurationError(VimoError):
    pass


class VimoAuthenticationError(VimoError):
    pass


class VimoRateLimitError(VimoError):
    pass


class VimoResponseError(VimoError):
    pass


_LOCK = threading.Lock()
_NEXT_REQUEST_AT = 0.0
_HEALTH: dict[str, Any] = {
    "attempts": 0,
    "successes": 0,
    "failures": 0,
    "rate_limited": 0,
    "last_error": "",
    "last_success_at": None,
}


def is_configured() -> bool:
    return bool(os.getenv("VIMO_API_KEY", "").strip())


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _request_interval() -> float:
    try:
        rpm = max(1.0, float(os.getenv("VIMO_REQUESTS_PER_MINUTE", "20")))
        ratio = max(0.05, min(1.0, float(os.getenv("VIMO_USAGE_RATIO", "0.75"))))
    except ValueError:
        rpm, ratio = 20.0, 0.75
    return 60.0 / (rpm * ratio)


def _wait_turn() -> None:
    global _NEXT_REQUEST_AT
    with _LOCK:
        now = time.monotonic()
        wait_for = max(0.0, _NEXT_REQUEST_AT - now)
        _NEXT_REQUEST_AT = max(now, _NEXT_REQUEST_AT) + _request_interval()
        _HEALTH["attempts"] += 1
    if wait_for:
        time.sleep(wait_for)


def _record_success() -> None:
    with _LOCK:
        _HEALTH["successes"] += 1
        _HEALTH["last_error"] = ""
        _HEALTH["last_success_at"] = datetime.now(VN_TZ).isoformat(timespec="seconds")


def _record_failure(exc: BaseException, *, rate_limited: bool = False) -> None:
    with _LOCK:
        _HEALTH["failures"] += 1
        if rate_limited:
            _HEALTH["rate_limited"] += 1
        _HEALTH["last_error"] = type(exc).__name__


def health_dict() -> dict[str, Any]:
    with _LOCK:
        payload = dict(_HEALTH)
    payload.update({"provider": "VIMO", "configured": is_configured(), "optional": True})
    return payload


def reset_health() -> None:
    global _NEXT_REQUEST_AT
    with _LOCK:
        _NEXT_REQUEST_AT = 0.0
        _HEALTH.update(
            attempts=0,
            successes=0,
            failures=0,
            rate_limited=0,
            last_error="",
            last_success_at=None,
        )


def _cache_path(kind: str, symbol: str) -> Path:
    safe_symbol = "".join(ch for ch in symbol.upper() if ch.isalnum() or ch in {"-", "_"})
    return CACHE_DIR / f"{kind}_{safe_symbol or 'MARKET'}.json"


def _load_cache(kind: str, symbol: str, ttl_seconds: int) -> dict[str, Any] | None:
    path = _cache_path(kind, symbol)
    try:
        age = time.time() - path.stat().st_mtime
        if age > ttl_seconds:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _save_cache(kind: str, symbol: str, payload: Mapping[str, Any]) -> None:
    path = _cache_path(kind, symbol)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(dict(payload), ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    except OSError as exc:
        logger.warning("Cannot save VIMO cache %s: %s", path, exc)


def _decode_tool_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise VimoResponseError("VIMO returned a non-object response")
    if payload.get("error"):
        error = payload["error"]
        message = error.get("message") if isinstance(error, Mapping) else str(error)
        raise VimoResponseError(f"VIMO MCP error: {message}")
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise VimoResponseError("VIMO response has no MCP result")
    for item in result.get("content", []):
        if not isinstance(item, Mapping) or item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, Mapping):
                return dict(decoded)
    structured = result.get("structuredContent")
    if isinstance(structured, Mapping):
        return dict(structured)
    raise VimoResponseError("VIMO MCP result has no structured JSON content")


def call_tool(name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Call one stateless VIMO MCP tool with bounded retry.

    Credentials are never included in exception messages or logs.
    """

    api_key = os.getenv("VIMO_API_KEY", "").strip()
    if not api_key:
        raise VimoConfigurationError("VIMO_API_KEY is not configured")
    attempts = max(1, min(3, int(os.getenv("VIMO_MAX_ATTEMPTS", "2"))))
    headers = {
        "x-api-key": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "THIUCUBU/1.0 (personal research)",
    }
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": dict(arguments or {})},
    }
    last_error: BaseException | None = None
    for attempt in range(attempts):
        _wait_turn()
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                response = client.post(MCP_ENDPOINT, headers=headers, json=body)
            if response.status_code in {401, 403}:
                raise VimoAuthenticationError("VIMO authentication failed; verify GitHub Secret")
            if response.status_code == 429:
                raise VimoRateLimitError("VIMO request limit reached")
            response.raise_for_status()
            decoded = _decode_tool_result(response.json())
            _record_success()
            return decoded
        except (VimoAuthenticationError, VimoRateLimitError) as exc:
            _record_failure(exc, rate_limited=isinstance(exc, VimoRateLimitError))
            raise
        except (httpx.HTTPError, ValueError, VimoResponseError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
                continue
            _record_failure(exc)
    raise VimoError(f"VIMO tool {name} unavailable: {type(last_error).__name__}")


def fetch_ta_signal(symbol: str, *, ttl_seconds: int = 4 * 3600) -> dict[str, Any]:
    normalized = symbol.upper().strip()
    cached = _load_cache("ta", normalized, ttl_seconds)
    if cached is not None:
        cached["cache_status"] = "cache"
        return cached
    packet = call_tool("get_ta_signals", {"symbol": normalized})
    data = packet.get("data") if isinstance(packet.get("data"), Mapping) else packet
    result = {
        "symbol": str(data.get("symbol") or normalized).upper(),
        "signal": str(data.get("signal") or "NEUTRAL").upper().replace(" ", "_"),
        "score": _safe_float(data.get("score")),
        "confidence": _safe_float(data.get("confidence")),
        "price": _safe_float(data.get("price")),
        "change_percent": _safe_float(data.get("change_percent")),
        "date": str(data.get("date") or ""),
        "generated_at": str(data.get("generated_at") or ""),
        "source": "VIMO",
        "cache_status": "live",
    }
    _save_cache("ta", normalized, result)
    return result


def fetch_bctc_support(symbol: str, *, ttl_seconds: int = 7 * 24 * 3600) -> dict[str, Any]:
    normalized = symbol.upper().strip()
    cached = _load_cache("bctc", normalized, ttl_seconds)
    if cached is not None:
        cached["cache_status"] = "cache"
        return cached
    packet = call_tool("get_bctc_profile", {"symbol": normalized})
    data = packet.get("data") if isinstance(packet.get("data"), Mapping) else packet
    strategies: list[dict[str, Any]] = []
    for item in data.get("strategies", []):
        if not isinstance(item, Mapping):
            continue
        score = _safe_float(item.get("score"))
        if score is None:
            continue
        strategies.append(
            {
                "strategy": str(item.get("strategy") or "unknown"),
                "score": round(score, 2),
                "grade": str(item.get("grade") or ""),
            }
        )
    scores = [item["score"] for item in strategies]
    result = {
        "symbol": str(data.get("symbol") or normalized).upper(),
        "year": data.get("year"),
        "strategy_average": round(sum(scores) / len(scores), 2) if scores else None,
        "strong_buy_count": sum("strong buy" in item["grade"].lower() for item in strategies),
        "buy_count": sum(item["grade"].lower() in {"buy", "strong buy"} for item in strategies),
        "strategy_count": len(strategies),
        "strategies": strategies,
        "generated_at": str(data.get("generated_at") or ""),
        "source": "VIMO",
        "cache_status": "live",
    }
    _save_cache("bctc", normalized, result)
    return result
