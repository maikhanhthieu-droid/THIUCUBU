#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config
import market_calendar
import scan

VN_TZ = timezone(timedelta(hours=7))


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=VN_TZ)
    except ValueError:
        return None


def file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime, tz=VN_TZ).isoformat(timespec="seconds"),
    }


def build_health() -> dict[str, Any]:
    settings = config.get_settings()
    data_dir = Path(getattr(settings, "data_dir", Path("data")))
    latest = scan.json_load(data_dir / "session_alerts_latest.json", {})
    latest_updated = parse_dt(latest.get("updated_at")) if isinstance(latest, dict) else None
    age_minutes = None
    if latest_updated is not None:
        age_minutes = round((datetime.now(VN_TZ) - latest_updated.astimezone(VN_TZ)).total_seconds() / 60, 1)

    source_stats: list[str] = []
    try:
        import scan_safe

        source_stats = [limiter.snapshot() for limiter in scan_safe.API_LIMITERS.values()]
    except Exception as exc:
        source_stats = [f"scan_safe unavailable: {exc}"]

    warnings: list[str] = []
    market_status = market_calendar.get_market_day_status()
    if settings.effective_dry_run:
        warnings.append("telegram not configured or DRY_RUN enabled")
    if market_status.closed:
        warnings.append(f"market closed: {market_status.reason} ({market_status.policy})")
    if age_minutes is None:
        warnings.append("missing session_alerts_latest.json")
    elif age_minutes > 24 * 60:
        warnings.append(f"latest report is stale: {age_minutes} minutes old")

    return {
        "status": "WARN" if warnings else "OK",
        "checked_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        "settings": config.settings_summary(settings),
        "latest_report": {
            "mode": latest.get("mode") if isinstance(latest, dict) else None,
            "updated_at": latest.get("updated_at") if isinstance(latest, dict) else None,
            "age_minutes": age_minutes,
        },
        "market_day": {
            "date": market_status.date,
            "closed": market_status.closed,
            "reason": market_status.reason,
            "policy": market_status.policy,
        },
        "files": {
            "portfolio": file_info(data_dir / "portfolio.json"),
            "notes": file_info(data_dir / "notes.json"),
            "market_holidays": file_info(data_dir / "market_holidays.json"),
            "run_journal": file_info(data_dir / "run_journal.json"),
            "source_health": file_info(data_dir / "source_health.json"),
            "session_latest": file_info(data_dir / "session_alerts_latest.json"),
            "weekend_latest": file_info(data_dir / "weekend_opportunities_latest.json"),
        },
        "sources": source_stats,
        "warnings": warnings,
    }


def format_health_text(health: dict[str, Any]) -> str:
    latest = health["latest_report"]
    lines = [
        f"*THIEUCUBU HEALTH* `{health['status']}`",
        f"Checked: {health['checked_at']}",
        f"Latest: {latest.get('mode') or 'n/a'} | age {latest.get('age_minutes') if latest.get('age_minutes') is not None else 'n/a'}m",
        f"Telegram: {'OK' if health['settings']['telegram_configured'] else 'MISSING'} | DRY_RUN {health['settings']['dry_run']}",
        "Sources: " + " | ".join(health.get("sources") or ["n/a"]),
    ]
    warnings = health.get("warnings") or []
    if warnings:
        lines.append("Warnings: " + "; ".join(warnings))
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--telegram", action="store_true", help="Send health summary to Telegram")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    health = build_health()
    if args.telegram:
        await scan.send_chunks("*THIEUCUBU HEALTH*", format_health_text(health))
    else:
        print(json.dumps(health, ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == "__main__":
    asyncio.run(main())
