#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("thieucutoo.calendar")

VN_TZ = timezone(timedelta(hours=7))
DATA_DIR = Path("data")
DEFAULT_HOLIDAYS_FILE = DATA_DIR / "market_holidays.json"

SCAN_OLD_POLICIES = {"scan_old", "old", "stale", "scan"}
SKIP_POLICIES = {"skip", "rest", "off", "nghi"}


@dataclass(frozen=True)
class MarketDayStatus:
    date: str
    closed: bool
    reason: str
    policy: str


def today_vn() -> date:
    return datetime.now(VN_TZ).date()


def market_closed_policy() -> str:
    raw = os.getenv("MARKET_CLOSED_POLICY", "skip").strip().lower()
    if raw in SCAN_OLD_POLICIES:
        return "scan_old"
    if raw in SKIP_POLICIES:
        return "skip"
    logger.warning("Invalid MARKET_CLOSED_POLICY=%r, using skip", raw)
    return "skip"


def holidays_path() -> Path:
    return Path(os.getenv("MARKET_HOLIDAYS_FILE", str(DEFAULT_HOLIDAYS_FILE)))


def load_holidays(path: Path | None = None) -> dict[str, str]:
    source = path or holidays_path()
    if not source.exists():
        return {}
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Cannot read market holidays %s: %s", source, exc)
        return {}
    values = raw.get("holidays", raw) if isinstance(raw, dict) else raw
    result: dict[str, str] = {}
    if isinstance(values, list):
        for item in values:
            if isinstance(item, str):
                result[item] = "Market holiday"
            elif isinstance(item, dict):
                day = str(item.get("date") or "").strip()
                name = str(item.get("name") or item.get("reason") or "Market holiday").strip()
                if day:
                    result[day] = name
    elif isinstance(values, dict):
        for day, name in values.items():
            result[str(day)] = str(name or "Market holiday")
    return result


def get_market_day_status(day: date | None = None, path: Path | None = None) -> MarketDayStatus:
    current = day or today_vn()
    policy = market_closed_policy()
    if current.weekday() >= 5:
        return MarketDayStatus(current.isoformat(), True, "Weekend", policy)
    holidays = load_holidays(path)
    reason = holidays.get(current.isoformat())
    if reason:
        return MarketDayStatus(current.isoformat(), True, reason, policy)
    return MarketDayStatus(current.isoformat(), False, "Trading day", policy)


def should_skip_scan(status: MarketDayStatus | None = None) -> bool:
    item = status or get_market_day_status()
    return bool(item.closed and item.policy == "skip")


def should_scan_old_data(status: MarketDayStatus | None = None) -> bool:
    item = status or get_market_day_status()
    return bool(item.closed and item.policy == "scan_old")


def closed_notice(mode: str, status: MarketDayStatus) -> str:
    if status.policy == "scan_old":
        action = "van quet data cu theo MARKET_CLOSED_POLICY=scan_old"
    else:
        action = "nghi quet de tranh spam tin hieu stale"
    return "\n".join(
        [
            f"*THIEUCUTOO MARKET CLOSED* `{status.date}`",
            f"Mode: `{mode}` | Ly do: {status.reason}",
            f"Xu ly: {action}.",
            "Neu muon ep quet data cu trong ngay nghi, set `MARKET_CLOSED_POLICY=scan_old`.",
        ]
    )


def closed_alert_payload(mode: str, status: MarketDayStatus) -> dict[str, Any]:
    return {
        "updated_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        "mode": mode,
        "market_closed": asdict(status),
        "focus_symbols": [],
        "portfolio_symbols": [],
        "market": None,
        "top": [],
    }
