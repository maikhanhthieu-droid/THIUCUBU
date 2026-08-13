#!/usr/bin/env python3
import asyncio
from datetime import datetime

import market_intel as intel
import scan_safe
import weekend_opportunities as weekend
import telegram_format as tf

scan_safe.fetch_ohlcv_safe = intel.fetch_ohlcv_safe
weekend.scan_safe.fetch_ohlcv_safe = intel.fetch_ohlcv_safe

def opportunity_line(item: weekend.Opportunity) -> str:
    return tf.format_opportunity_card(item)


def build_report(opportunities: list[weekend.Opportunity], sectors: dict[str, weekend.SectorSnapshot], mode: str) -> str:
    now = datetime.now(weekend.VN_TZ).strftime("%d/%m/%Y %H:%M")
    top = opportunities[:weekend.TOP_N]
    high_confidence = [item for item in top if item.selected][:2]
    prep = [
        item for item in top
        if not item.selected and item.structure_state in {"EARLY_MARKUP", "READY_TO_ACCUMULATE", "PREP_BASE"}
    ]
    sector_rows = sorted(sectors.values(), key=lambda item: item.score, reverse=True)[:8]

    lines = [
        f"*THIEUCUBU WEEKLY CONVICTION* `{now}`",
        "Score v2 (tối đa 97): định giá + chất lượng + cấu trúc tuần + thời điểm + rủi ro. Không phải khuyến nghị mua bán.",
        "",
        "*💎 TỐI ĐA 2 MÃ ƯU TIÊN GOM*",
    ]
    lines += [opportunity_line(item) for item in high_confidence] or ["Tuần này chưa có mã đồng thời đủ 5 cửa; không ép chọn."]
    lines += ["", "*🟢 CẤU TRÚC ĐANG CHUẨN BỊ*"]
    lines += [opportunity_line(item) for item in prep[:8]] or ["Chưa có mã chuẩn bị đủ rõ."]
    lines += ["", "*👀 WATCHLIST ĐỊNH GIÁ / CHỜ GIÁ*"]
    lines += [opportunity_line(item) for item in top if not item.selected][:10] or ["Chưa có mã đạt ngưỡng lọc."]
    lines += ["", "*NGÀNH ĐÁNG CHÚ Ý*"]
    lines += [tf.format_sector_line(weekend.sector_line(item)) for item in sector_rows] or ["Chưa đủ dữ liệu ngành."]
    lines += ["", intel.build_performance_report()]
    if mode == "test":
        lines += ["", "`TEST MODE`: chi quet mot tap ma mau."]
    return "\n".join(lines)


weekend.opportunity_line = opportunity_line
weekend.build_report = build_report


if __name__ == "__main__":
    asyncio.run(weekend.main())
