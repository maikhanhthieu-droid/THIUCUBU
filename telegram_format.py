from typing import Any

import market_phase
import scoring


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("_", " ").replace("*", "").replace("`", "'")


def setup_label(value: Any) -> str:
    return clean_text(value).upper()


def score_label(score: int) -> str:
    labels = {
        "S": "HIẾM / RẤT MẠNH",
        "A+": "RẤT HẤP DẪN",
        "A": "HẤP DẪN",
        "B+": "CÓ THỂ HÀNH ĐỘNG",
        "B": "THEO DÕI",
        "C": "CHƯA ĐỦ ĐIỀU KIỆN",
        "D": "YẾU / RỦI RO",
    }
    return labels[scoring.grade(score)]


def format_price(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        price = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if price == 0:
        return "n/a"
    if abs(price) >= 1000:
        return f"{price:,.0f}"
    return f"{price:.2f}"


def format_stock_card(r: Any, action: str | None = None, note: str = "", timing: str = "") -> str:
    setup = setup_label(getattr(r, "setup", ""))
    status = clean_text(action or score_label(int(getattr(r, "win_score", 0)))).upper()
    reason = clean_text(getattr(r, "reason", ""))
    note = clean_text(note)
    timing = clean_text(timing)
    close = getattr(r, "close", 0.0)
    close_text = format_price(close)
    obv = "OBV tăng" if getattr(r, "obv_up", False) else "OBV phẳng"
    flags = []
    if getattr(r, "near_break", False):
        flags.append("gần break")
    if getattr(r, "failed_break", False):
        flags.append("failed-break")
    flag_text = f" | {', '.join(flags)}" if flags else ""

    score = int(getattr(r, "win_score", 0))
    grade = str(getattr(r, "grade", "") or scoring.grade(score))
    trade_score = int(getattr(r, "trade_score", score))
    position_score = int(getattr(r, "position_score", score))
    confidence = int(getattr(r, "confidence", 0))
    horizon = clean_text(getattr(r, "horizon", "WATCH"))
    confidence_text = f" | Tin cậy {confidence}%" if confidence else ""
    lines = [
        f"`{getattr(r, 'symbol', '')}`  *{grade} · {score}/97*  {status}",
        f"Lướt {trade_score} | Gom {position_score} | {horizon}{confidence_text}",
        f"Giá {close_text} | Setup {setup} | Ngành {clean_text(getattr(r, 'sector', ''))}{flag_text}",
        (
            f"DD {float(getattr(r, 'discount_pct', 0.0)):.1f}%/"
            f"{float(getattr(r, 'target_discount_pct', 0.0)):.0f}% | "
            f"Volx{float(getattr(r, 'vol_ratio', 0.0)):.1f} | "
            f"RSI {float(getattr(r, 'rsi', 0.0)):.0f} | "
            f"MFI {float(getattr(r, 'mfi', 0.0)):.0f} | {obv}"
        ),
    ]
    market_state = str(getattr(r, "market_state", "NO_DATA"))
    if market_state != "NO_DATA":
        daily_phase = str(getattr(r, "daily_phase", "NO_DATA"))
        weekly_phase = str(getattr(r, "weekly_phase", "NO_DATA"))
        monthly_phase = str(getattr(r, "monthly_phase", "NO_DATA"))
        breakout_state = str(getattr(r, "breakout_state", "NO_DATA"))
        lines.insert(
            1,
            f"TT {market_phase.OVERALL_LABELS.get(market_state, market_state)} | "
            f"D {market_phase.PHASE_LABELS.get(daily_phase, daily_phase)} · "
            f"W {market_phase.PHASE_LABELS.get(weekly_phase, weekly_phase)} · "
            f"M {market_phase.PHASE_LABELS.get(monthly_phase, monthly_phase)} | "
            f"{market_phase.BREAKOUT_LABELS.get(breakout_state, breakout_state)}",
        )
    if timing:
        lines.append(f"Thời điểm: {timing}")
    if reason:
        lines.append(f"Lý do: {reason}")
    if note:
        lines.append(f"Ghi chú: {note}")
    sl = getattr(r, "stop_loss", None) or getattr(r, "sl", None)
    tp = getattr(r, "take_profit", None) or getattr(r, "tp", None)
    rr = getattr(r, "risk_reward", None) or getattr(r, "rr", None)
    if sl and tp:
        rr_text = f" | R/R {float(rr):.1f}x" if rr is not None else ""
        lines.append(f"SL {format_price(sl)} | TP {format_price(tp)}{rr_text}")
    return "\n".join(lines) + "\n"


def format_market_card(market: Any, state: str) -> str:
    if market is None:
        return "*VNINDEX*\nChưa có dữ liệu."
    reason = clean_text(getattr(market, "reason", ""))
    close = getattr(market, "close", None)
    close_text = f"VNI {float(close):,.0f}pt" if close else "VNI n/a"
    above_ema34 = getattr(market, "above_ema34", None)
    ema34_text = ""
    if above_ema34 is not None:
        ema34_text = f" | EMA34 {'trên' if above_ema34 else 'dưới'}"
    return "\n".join(
        [
            f"*VNINDEX*  `{int(getattr(market, 'win_score', 0))}/100`  {clean_text(state)}",
            (
                f"{close_text}{ema34_text} | "
                f"RSI {float(getattr(market, 'rsi', 0.0)):.0f} | "
                f"MFI {float(getattr(market, 'mfi', 0.0)):.0f} | "
                f"Volx{float(getattr(market, 'vol_ratio', 0.0)):.1f}"
            ),
            f"Trạng thái: {reason}" if reason else "Trạng thái: đang cập nhật",
        ]
    )


def format_sector_line(line: str) -> str:
    # Sector summaries already include Telegram Markdown, so keep them untouched.
    return line


def format_opportunity_card(item: Any) -> str:
    action_key = str(getattr(item, "action", "")).upper()
    action = {
        "UU_TIEN_GOM": "ƯU TIÊN GOM",
        "UNG_VIEN_GOM": "ỨNG VIÊN GOM",
        "CHO_DIEM_GOM": "CHỜ ĐIỂM GOM",
        "THEO_DOI_DINH_GIA": "THEO DÕI ĐỊNH GIÁ",
    }.get(action_key, clean_text(action_key).upper())
    symbol = getattr(item, "symbol", "")
    score = int(getattr(item, "opportunity_score", 0))
    sector = clean_text(getattr(item, "sector", ""))
    pe = getattr(item, "pe", None)
    pb = getattr(item, "pb", None)
    sector_pe = getattr(item, "sector_pe", None)
    sector_pb = getattr(item, "sector_pb", None)
    pe_disc = getattr(item, "pe_discount_pct", None)
    pb_disc = getattr(item, "pb_discount_pct", None)
    pe_text = "n/a" if pe is None else f"{float(pe):.1f}"
    pb_text = "n/a" if pb is None else f"{float(pb):.2f}"
    sector_pe_text = "n/a" if sector_pe is None else f"{float(sector_pe):.1f}"
    sector_pb_text = "n/a" if sector_pb is None else f"{float(sector_pb):.2f}"
    pe_disc_text = "n/a" if pe_disc is None else f"{float(pe_disc):+.0f}%"
    pb_disc_text = "n/a" if pb_disc is None else f"{float(pb_disc):+.0f}%"
    grade = clean_text(getattr(item, "grade", "") or scoring.grade(score))
    confidence = int(getattr(item, "confidence", 0))
    structure_score = int(getattr(item, "structure_score", 0))
    structure_state = clean_text(getattr(item, "structure_state", "WAIT"))
    trigger = clean_text(getattr(item, "trigger", "WAIT"))
    buy_low = getattr(item, "buy_zone_low", None)
    buy_high = getattr(item, "buy_zone_high", None)
    invalidation = getattr(item, "invalidation_price", None)
    selected = bool(getattr(item, "selected", False))
    marker = "💎 " if selected else ""
    lines = [
            f"{marker}`{symbol}`  *{grade} · {score}/97*  {action}",
            f"Cấu trúc tuần {structure_score} | {structure_state} | Trigger {trigger} | Tin cậy {confidence}%",
            f"Ngành {sector} | PE {pe_text} vs {sector_pe_text} ({pe_disc_text})",
            f"PB {pb_text} vs {sector_pb_text} ({pb_disc_text}) | DD {float(getattr(item, 'discount_pct', 0.0)):.0f}/{float(getattr(item, 'target_discount_pct', 0.0)):.0f}%",
            (
                f"Điểm ĐG/CL/KT/Ngành "
                f"{int(getattr(item, 'valuation_score', 0))}/"
                f"{int(getattr(item, 'quality_score', 0))}/"
                f"{int(getattr(item, 'technical_score', 0))}/"
                f"{int(getattr(item, 'sector_score', 0))}"
            ),
            f"Case: {clean_text(getattr(item, 'bull_case', ''))}",
            f"Risk: {clean_text(getattr(item, 'bear_case', ''))}",
    ]
    market_state = str(getattr(item, "market_state", "NO_DATA"))
    breakout_state = str(getattr(item, "breakout_state", "NO_DATA"))
    if market_state != "NO_DATA":
        lines.insert(
            1,
            f"TT {market_phase.OVERALL_LABELS.get(market_state, clean_text(market_state))} | "
            f"{market_phase.BREAKOUT_LABELS.get(breakout_state, clean_text(breakout_state))}",
        )
    as_of = getattr(item, "as_of", None)
    cache_status = clean_text(getattr(item, "cache_status", "unknown")).upper()
    if as_of:
        lines.append(f"Dữ liệu {cache_status} · {as_of}")
    if buy_low is not None and buy_high is not None:
        lines.append(f"Vùng gom {format_price(buy_low)}–{format_price(buy_high)} | Vô hiệu dưới {format_price(invalidation)}")
    return "\n".join(lines) + "\n"
