#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from typing import Any

import near_high_filter
import scan
import scan_safe

_SAFE_FETCH = scan_safe.fetch_ohlcv_safe

import session_gate as gate

scan_safe.fetch_ohlcv_safe = _SAFE_FETCH
scan.fetch_ohlcv = _SAFE_FETCH
gate.plus.scan_safe.fetch_ohlcv_safe = _SAFE_FETCH
gate.plus.scan.fetch_ohlcv = _SAFE_FETCH

_old_all_universe_symbols = gate.plus.sess.all_universe_symbols


def all_universe_symbols_with_near_high_filter(mode: str, watch_items: dict[str, dict[str, Any]]) -> list[str]:
    symbols = _old_all_universe_symbols(mode, watch_items)
    if mode in {"test", "eod"}:
        return symbols
    filtered, skipped = near_high_filter.filter_symbols(symbols, protected=set(watch_items))
    if skipped:
        gate.plus.sess.logger.info("Near-high weekday filter removed %s symbols", len(skipped))
    return filtered


gate.plus.sess.all_universe_symbols = all_universe_symbols_with_near_high_filter


if __name__ == "__main__":
    asyncio.run(gate.plus.main())
