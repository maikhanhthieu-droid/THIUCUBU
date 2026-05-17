from typing import Any


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("_", " ").replace("*", "").replace("`", "'")


def setup_label(value: Any) -> str:
    return clean_text(value).upper()


def score_label(score: int) -> str:
    if score >= 82:
        return "RAT MANH"
    if score >= 72:
        return "MANH"
    if score >= 62:
        return "THEO DOI"
    if score >= 50:
        return "TRUNG BINH"
    return "YEU"


def format_stock_card(r: Any, action: str | None = None, note: str = "", timing: str = "") -> str:
    setup = setup_label(getattr(r, "setup", ""))
    status = clean_text(action or score_label(int(getattr(r, "win_score", 0)))).upper()
    reason = clean_text(getattr(r, "reason", ""))
    note = clean_text(note)
    timing = clean_text(timing)
    close = getattr(r, "close", 0.0)
    close_text = f"{float(close):.2f}" if close else "n/a"
    obv = "OBV up" if getattr(r, "obv_up", False) else "OBV flat"
    flags = []
    if getattr(r, "near_break", False):
        flags.append("gan break")
    if getattr(r, "failed_break", False):
        flags.append("failed break")
    flag_text = f" | {', '.join(flags)}" if flags else ""

    lines = [
        f"`{getattr(r, 'symbol', '')}`  *{int(getattr(r, 'win_score', 0))}/100*  {status}",
        f"Gia {close_text} | Setup {setup} | Nganh {clean_text(getattr(r, 'sector', ''))}{flag_text}",
        (
            f"DD {float(getattr(r, 'discount_pct', 0.0)):.1f}%/"
            f"{float(getattr(r, 'target_discount_pct', 0.0)):.0f}% | "
            f"Volx{float(getattr(r, 'vol_ratio', 0.0)):.1f} | "
            f"RSI {float(getattr(r, 'rsi', 0.0)):.0f} | "
            f"MFI {float(getattr(r, 'mfi', 0.0)):.0f} | {obv}"
        ),
    ]
    if timing:
        lines.append(f"Thoi diem: {timing}")
    if reason:
        lines.append(f"Ly do: {reason}")
    if note:
        lines.append(f"Note: {note}")
    sl = getattr(r, "stop_loss", None) or getattr(r, "sl", None)
    tp = getattr(r, "take_profit", None) or getattr(r, "tp", None)
    rr = getattr(r, "risk_reward", None) or getattr(r, "rr", None)
    if sl and tp:
        rr_text = f" | R/R {float(rr):.1f}x" if rr is not None else ""
        lines.append(f"SL {float(sl):.2f} | TP {float(tp):.2f}{rr_text}")
    return "\n".join(lines) + "\n"


def format_market_card(market: Any, state: str) -> str:
    if market is None:
        return "*VNINDEX*\nChua co du lieu."
    reason = clean_text(getattr(market, "reason", ""))
    close = getattr(market, "close", None)
    close_text = f"VNI {float(close):.0f}pt" if close else "VNI n/a"
    above_ema34 = getattr(market, "above_ema34", None)
    ema34_text = ""
    if above_ema34 is not None:
        ema34_text = f" | EMA34 {'tren' if above_ema34 else 'duoi'}"
    return "\n".join(
        [
            f"*VNINDEX*  `{int(getattr(market, 'win_score', 0))}/100`  {clean_text(state)}",
            (
                f"{close_text}{ema34_text} | "
                f"RSI {float(getattr(market, 'rsi', 0.0)):.0f} | "
                f"MFI {float(getattr(market, 'mfi', 0.0)):.0f} | "
                f"Volx{float(getattr(market, 'vol_ratio', 0.0)):.1f}"
            ),
            f"Trang thai: {reason}" if reason else "Trang thai: dang cap nhat",
        ]
    )


def format_sector_line(line: str) -> str:
    # Sector summaries already include Telegram Markdown, so keep them untouched.
    return line


def format_opportunity_card(item: Any) -> str:
    action = clean_text(getattr(item, "action", "")).upper()
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
    return "\n".join(
        [
            f"`{symbol}`  *{score}/100*  {action}",
            f"Nganh {sector} | PE {pe_text} vs {sector_pe_text} ({pe_disc_text})",
            f"PB {pb_text} vs {sector_pb_text} ({pb_disc_text}) | DD {float(getattr(item, 'discount_pct', 0.0)):.0f}/{float(getattr(item, 'target_discount_pct', 0.0)):.0f}%",
            (
                f"Diem V/Q/T/S "
                f"{int(getattr(item, 'valuation_score', 0))}/"
                f"{int(getattr(item, 'quality_score', 0))}/"
                f"{int(getattr(item, 'technical_score', 0))}/"
                f"{int(getattr(item, 'sector_score', 0))}"
            ),
            f"Case: {clean_text(getattr(item, 'bull_case', ''))}",
            f"Risk: {clean_text(getattr(item, 'bear_case', ''))}",
        ]
    ) + "\n"
