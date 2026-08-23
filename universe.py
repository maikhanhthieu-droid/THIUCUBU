"""Discover and rotate through the broader Vietnamese equity universe.

The scanner has a curated liquid core.  This module adds bounded rolling
coverage so less-obvious stocks can enter the raw feature layer without making
time-sensitive reports wait for the whole exchange on every run.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import scan


logger = logging.getLogger("thieucutoo.universe")
VN_TZ = timezone(timedelta(hours=7))
STATE_PATH = Path("data/universe_state.json")
SYMBOL_RE = re.compile(r"^[A-Z]{3}$")


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        symbol = str(value or "").strip().upper()
        if not SYMBOL_RE.fullmatch(symbol) or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def _fresh(updated_at: Any, days: int = 7) -> bool:
    try:
        value = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=VN_TZ)
        return datetime.now(VN_TZ) - value.astimezone(VN_TZ) <= timedelta(days=days)
    except (TypeError, ValueError):
        return False


def _listing_frame(source: str) -> Any:
    """Call explorer implementations directly.

    vnstock 4.0.2's public ``Listing(source=...)`` wrapper can have an empty
    provider registry even though the bundled KBS/VCI explorers are healthy.
    Direct imports also keep the fallback deterministic when one upstream is
    unavailable.
    """

    if source == "KBS":
        from vnstock.explorer.kbs.listing import Listing
    elif source == "VCI":
        from vnstock.explorer.vci.listing import Listing
    else:  # pragma: no cover - guarded by discover_symbols
        raise ValueError(f"Unsupported listing source: {source}")
    return Listing(show_log=False).all_symbols(show_log=False)


def discover_symbols() -> list[str]:
    for source in ("KBS", "VCI"):
        try:
            frame = _listing_frame(source)
            if frame is not None and "symbol" in frame.columns:
                symbols = sorted(_unique(frame["symbol"].tolist()))
                if len(symbols) >= 300:
                    logger.info("Discovered %s stock symbols from %s", len(symbols), source)
                    return symbols
                logger.warning(
                    "Universe source %s returned only %s valid stock symbols",
                    source,
                    len(symbols),
                )
        except Exception as exc:
            logger.warning("Universe source %s failed: %s", source, exc)
    return []


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    raw = scan.json_load(path, {})
    if not isinstance(raw, dict):
        raw = {}
    return {
        "schema_version": "thieucubu.universe.v1",
        "updated_at": raw.get("updated_at"),
        "cursor": max(0, int(raw.get("cursor") or 0)),
        "symbols": sorted(_unique(raw.get("symbols") or [])),
        "last_batch": _unique(raw.get("last_batch") or []),
    }


def refresh_state(path: Path = STATE_PATH) -> dict[str, Any]:
    state = load_state(path)
    if state["symbols"] and _fresh(state.get("updated_at")):
        return state
    discovered = discover_symbols()
    if discovered:
        state["symbols"] = discovered
        state["updated_at"] = datetime.now(VN_TZ).isoformat(timespec="seconds")
        state["cursor"] %= len(discovered)
        scan.json_save(path, state, pretty=True)
    return state


def rotating_batch(
    core_symbols: Iterable[Any],
    *,
    limit: int | None = None,
    path: Path = STATE_PATH,
) -> list[str]:
    limit = limit or _env_int("SCAN_ROTATING_UNIVERSE_SIZE", 36)
    if limit <= 0:
        return []
    state = refresh_state(path)
    core = set(_unique(core_symbols))
    pool = [symbol for symbol in state["symbols"] if symbol not in core]
    if not pool:
        return []
    cursor = state["cursor"] % len(pool)
    batch = (pool[cursor:] + pool[:cursor])[:limit]
    state["cursor"] = (cursor + len(batch)) % len(pool)
    state["last_batch"] = batch
    state["last_batch_at"] = datetime.now(VN_TZ).isoformat(timespec="seconds")
    scan.json_save(path, state, pretty=True)
    return batch
