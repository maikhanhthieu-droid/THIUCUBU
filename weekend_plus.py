#!/usr/bin/env python3
import asyncio
import os
from datetime import datetime

import weekend_opportunities as weekend
import telegram_format as tf


def env_int(name: str, default: int, min_value: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, value)


HIGH_CONFIDENCE_SCORE = env_int("WEEKEND_HIGH_CONFIDENCE_SCORE", 80, min_value=60)
_old_make_action = weekend.make_action


def make_action(score: int, risk: int, valuation: int, sector_score: int) -> str:
    if score >= HIGH_CONFIDENCE_SCORE and risk <= 25 and valuation >= 68 and sector_score >= 60:
        return "XAC SUAT AN TOAN CAO"
    return _old_make_action(score, risk, valuation, sector_score)


def display_action(action: str) -> str:
    return tf.clean_text(action).upper()


def opportunity_line(item: weekend.Opportunity) -> str:
    return tf.format_opportunity_card(item)


def build_report(opportunities: list[weekend.Opportunity], sectors: dict[str, weekend.SectorSnapshot], mode: str) -> str:
    now = datetime.now(weekend.VN_TZ).strftime("%d/%m/%Y %H:%M")
    top = opportunities[:weekend.TOP_N]
    high_confidence = [item for item in top if item.action == "XAC SUAT AN TOAN CAO"]
    strong = [
        item for item in top
        if item.action in {"XAC SUAT AN TOAN CAO", "CO_HOI_LON", "CANH_MUA_TUNG_PHAN"}
    ]
    sector_rows = sorted(sectors.values(), key=lambda item: item.score, reverse=True)[:8]

    lines = [
        f"*THIEUCUTOO WEEKEND OPPORTUNITIES* `{now}`",
        "Quet PE/PB + chiet khau gia + chat luong + nganh + risk. Khong phai khuyen nghi mua ban.",
        "",
        "*CO HOI XAC SUAT AN TOAN CAO*",
    ]
    lines += [opportunity_line(item) for item in high_confidence[:8]] or ["Chua co ma dat nguong an toan cao."]
    lines += ["", "*CO HOI LON / CANH MUA*"]
    lines += [opportunity_line(item) for item in strong[:10]] or ["Chua co ma du nguong co hoi lon."]
    lines += ["", "*TOP WATCHLIST DINH GIA TOT*"]
    lines += [opportunity_line(item) for item in top] or ["Chua co ma dat nguong loc."]
    lines += ["", "*NGANH DANG NGON*"]
    lines += [tf.format_sector_line(weekend.sector_line(item)) for item in sector_rows] or ["Chua du du lieu nganh."]
    if mode == "test":
        lines += ["", "`TEST MODE`: chi quet mot tap ma mau."]
    return "\n".join(lines)


weekend.make_action = make_action
weekend.opportunity_line = opportunity_line
weekend.build_report = build_report


if __name__ == "__main__":
    asyncio.run(weekend.main())
