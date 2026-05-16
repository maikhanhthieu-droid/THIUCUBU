#!/usr/bin/env python3
from __future__ import annotations

import asyncio

import scan_safe

_SAFE_FETCH = scan_safe.fetch_ohlcv_safe

import weekend_plus as plus

scan_safe.fetch_ohlcv_safe = _SAFE_FETCH
plus.weekend.scan_safe.fetch_ohlcv_safe = _SAFE_FETCH


if __name__ == "__main__":
    asyncio.run(plus.weekend.main())
