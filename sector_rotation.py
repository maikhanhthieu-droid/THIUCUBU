#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

import market_intel
import scan


def sector_scores(results: list[scan.ScanResult]) -> dict[str, dict[str, Any]]:
    return market_intel.sector_scores(results)


def update_sector_rotation(results: list[scan.ScanResult]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    return market_intel.update_sector_rotation(results)
