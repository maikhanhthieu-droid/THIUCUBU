#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import math
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import scan
import scan_safe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("thieucutoo.weekend")

VN_TZ = timezone(timedelta(hours=7))
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

try:
    from vnstock import Fundamental as VnFundamental
except Exception as exc:  # pragma: no cover - depends on vnstock build
    VnFundamental = None
    logger.warning("Cannot import vnstock.Fundamental: %s", exc)

try:
    from vnstock import Company as VnCompany
except Exception as exc:  # pragma: no cover - depends on vnstock build
    VnCompany = None
    logger.warning("Cannot import vnstock.Company: %s", exc)


def env_int(name: str, default: int, min_value: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using %s", name, raw, default)
        return default
    return max(min_value, value)


def env_float(name: str, default: float, min_value: float = 0.0, max_value: float | None = None) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using %s", name, raw, default)
        return default
    value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


TOP_N = env_int("WEEKEND_TOP_N", 20, min_value=5)
MIN_SCORE = env_int("WEEKEND_MIN_SCORE", 58, min_value=0)
MIN_SECTOR_SCORE = env_int("WEEKEND_MIN_SECTOR_SCORE", 50, min_value=0)
FUNDAMENTAL_DELAY_MIN = env_float("WEEKEND_FUNDAMENTAL_DELAY_MIN_SEC", 0.7, min_value=0.0)
FUNDAMENTAL_DELAY_MAX = max(
    FUNDAMENTAL_DELAY_MIN,
    env_float("WEEKEND_FUNDAMENTAL_DELAY_MAX_SEC", 2.2, min_value=0.0),
)
RANDOM_START_MAX = env_int("WEEKEND_RANDOM_START_MAX_SEC", 600, min_value=0)

FINANCIAL_SECTORS = {"Bank", "Chung khoan", "Bao hiem"}
PB_HEAVY_SECTORS = {
    "Bank",
    "Chung khoan",
    "Bao hiem",
    "BDS dan cu",
    "BDS KCN",
    "Dau khi",
    "Dien tien ich",
    "Thep",
    "Xay dung dau tu cong",
}


@dataclass
class FundamentalSnapshot:
    symbol: str
    pe: float | None
    pb: float | None
    roe: float | None
    roa: float | None
    debt_to_equity: float | None
    current_ratio: float | None
    profit_margin: float | None
    eps: float | None
    period: str
    source: str


@dataclass
class SectorSnapshot:
    sector: str
    score: int
    avg_win_score: float
    median_pe: float | None
    median_pb: float | None
    median_roe: float | None
    failed_ratio: float
    count: int


@dataclass
class Opportunity:
    symbol: str
    sector: str
    close: float
    action: str
    opportunity_score: int
    valuation_score: int
    quality_score: int
    technical_score: int
    sector_score: int
    risk_score: int
    pe: float | None
    pb: float | None
    sector_pe: float | None
    sector_pb: float | None
    pe_discount_pct: float | None
    pb_discount_pct: float | None
    roe: float | None
    roa: float | None
    debt_to_equity: float | None
    discount_pct: float
    target_discount_pct: float
    setup: str
    bull_case: str
    bear_case: str
    data_source: str


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def clean_key(value: Any) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
        if not value or value.lower() in {"nan", "none", "null", "-"}:
            return None
    try:
        result = float(value)
    except Exception:
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def as_percent(value: Any) -> float | None:
    result = safe_float(value)
    if result is None:
        return None
    if -1.5 <= result <= 1.5:
        return result * 100
    return result


def median(values: list[float]) -> float | None:
    values = sorted(v for v in values if v is not None and math.isfinite(v))
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def fmt_pct(value: float | None, digits: int = 0, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.{digits}f}%"


ALIASES = {
    "pe": ["pe", "p/e", "pe_ratio", "peratio", "priceToEarning", "priceToEarnings", "price_to_earning"],
    "pb": ["pb", "p/b", "pb_ratio", "pbratio", "priceToBook", "price_to_book", "priceToBookRatio"],
    "roe": ["roe", "returnOnEquity", "return_on_equity"],
    "roa": ["roa", "returnOnAssets", "return_on_assets"],
    "debt": ["debt_to_equity", "debtToEquity", "debt/equity"],
    "current": ["current_ratio", "currentRatio"],
    "margin": ["profit_margin", "net_margin", "netMargin"],
    "eps": ["eps", "earningPerShare", "earningsPerShare"],
    "period": ["date", "year_report", "year", "quarter", "report_date"],
}


def value_from_row(row: pd.Series, names: list[str]) -> Any:
    columns = {clean_key(col): col for col in row.index}
    for name in names:
        key = clean_key(name)
        if key in columns:
            return row[columns[key]]
    return None


def latest_ratio_row(df: pd.DataFrame) -> pd.Series | None:
    if df is None or df.empty:
        return None
    data = df.copy()
    sort_cols = [col for col in data.columns if clean_key(col) in {"date", "yearreport", "year", "quarter", "reportdate"}]
    if sort_cols:
        try:
            return data.sort_values(sort_cols).iloc[-1]
        except Exception:
            pass
    return data.iloc[-1]


def snapshot_from_df(symbol: str, df: pd.DataFrame, source: str) -> FundamentalSnapshot | None:
    row = latest_ratio_row(df)
    if row is None:
        return None
    pe = safe_float(value_from_row(row, ALIASES["pe"]))
    pb = safe_float(value_from_row(row, ALIASES["pb"]))
    if pe is None and pb is None:
        return None
    period_raw = value_from_row(row, ALIASES["period"])
    return FundamentalSnapshot(
        symbol=symbol,
        pe=pe,
        pb=pb,
        roe=as_percent(value_from_row(row, ALIASES["roe"])),
        roa=as_percent(value_from_row(row, ALIASES["roa"])),
        debt_to_equity=safe_float(value_from_row(row, ALIASES["debt"])),
        current_ratio=safe_float(value_from_row(row, ALIASES["current"])),
        profit_margin=as_percent(value_from_row(row, ALIASES["margin"])),
        eps=safe_float(value_from_row(row, ALIASES["eps"])),
        period=str(period_raw) if period_raw is not None else "",
        source=source,
    )


def call_ratio_method(method: Any, args: tuple[Any, ...], source: str, symbol: str) -> FundamentalSnapshot | None:
    kwargs_options = (
        {"period": "quarter", "orient": "time_series"},
        {"orient": "time_series"},
        {"period": "quarter"},
        {},
    )
    for kwargs in kwargs_options:
        try:
            df = method(*args, **kwargs)
        except TypeError:
            continue
        except Exception as exc:
            logger.debug("%s %s failed: %s", source, symbol, exc)
            continue
        snap = snapshot_from_df(symbol, df, source)
        if snap:
            return snap
    return None


def fetch_fundamental(symbol: str) -> FundamentalSnapshot | None:
    if VnFundamental is not None:
        try:
            fun = VnFundamental()
            equity = getattr(fun, "equity", None)
            if callable(equity):
                try:
                    equity_obj = equity(symbol)
                    method = getattr(equity_obj, "ratio", None)
                    if callable(method):
                        snap = call_ratio_method(method, (), "Fundamental.equity().ratio", symbol)
                        if snap:
                            return snap
                except Exception as exc:
                    logger.debug("Fundamental equity object %s failed: %s", symbol, exc)
            method = getattr(equity, "ratio", None)
            if callable(method):
                snap = call_ratio_method(method, (symbol,), "Fundamental.equity.ratio", symbol)
                if snap:
                    return snap
        except Exception as exc:
            logger.debug("Fundamental %s failed: %s", symbol, exc)

    if VnCompany is not None:
        for source in ("VCI", "KBS"):
            try:
                company = VnCompany(symbol=symbol, source=source)
                method = getattr(company, "ratio_summary", None)
                if callable(method):
                    df = method()
                    snap = snapshot_from_df(symbol, df, f"Company.ratio_summary({source})")
                    if snap:
                        return snap
            except Exception as exc:
                logger.debug("Company %s %s failed: %s", source, symbol, exc)
    return None


def build_universe(mode: str) -> list[str]:
    if mode == "test":
        return ["VCB", "TCB", "FPT", "HPG", "KDH", "DIG", "CEO", "PDR", "SSI", "VIX", "PVD", "VNM"]
    tickers = list(scan.ALL_TICKERS)
    portfolio = scan.json_load(DATA_DIR / "portfolio.json", [])
    for item in portfolio:
        symbol = str(item.get("symbol", "")).upper().strip()
        if symbol and symbol not in tickers:
            tickers.append(symbol)
    return sorted(set(tickers))


def fetch_symbol_packet(symbol: str, force_refresh: bool) -> dict[str, Any]:
    sector = scan.TICKER_TO_SECTOR.get(symbol, "Other")
    df = scan_safe.fetch_ohlcv_safe(symbol, bars=260, force_refresh=force_refresh)
    tech = scan.analyze_symbol(symbol, df) if df is not None else None
    time.sleep(random.uniform(FUNDAMENTAL_DELAY_MIN, FUNDAMENTAL_DELAY_MAX))
    fundamental = fetch_fundamental(symbol)
    close = safe_float(df["close"].iloc[-1]) if df is not None and not df.empty else None
    if close is None and tech is not None:
        close = tech.close
    return {
        "symbol": symbol,
        "sector": sector,
        "df": df,
        "tech": tech,
        "fundamental": fundamental,
        "close": close,
    }


def build_sector_snapshots(packets: list[dict[str, Any]]) -> dict[str, SectorSnapshot]:
    by_sector: dict[str, list[dict[str, Any]]] = {}
    for packet in packets:
        by_sector.setdefault(packet["sector"], []).append(packet)

    snapshots: dict[str, SectorSnapshot] = {}
    for sector, items in by_sector.items():
        pe_values = []
        pb_values = []
        roe_values = []
        win_values = []
        failed = 0
        for item in items:
            fund = item["fundamental"]
            tech = item["tech"]
            if fund is not None:
                if fund.pe is not None and fund.pe > 0:
                    pe_values.append(fund.pe)
                if fund.pb is not None and fund.pb > 0:
                    pb_values.append(fund.pb)
                if fund.roe is not None:
                    roe_values.append(fund.roe)
            if tech is not None:
                win_values.append(float(tech.win_score))
                if tech.failed_break:
                    failed += 1
        avg_win = sum(win_values) / len(win_values) if win_values else 45.0
        median_roe = median(roe_values)
        quality_bonus = 0.0
        if median_roe is not None:
            quality_bonus = clamp((median_roe - 8) * 1.1, -10, 16)
        failed_ratio = failed / max(len(items), 1)
        score = int(clamp(avg_win * 0.78 + quality_bonus + (4 if len(items) >= 4 else 0) - failed_ratio * 20))
        snapshots[sector] = SectorSnapshot(
            sector=sector,
            score=score,
            avg_win_score=round(avg_win, 1),
            median_pe=median(pe_values),
            median_pb=median(pb_values),
            median_roe=median_roe,
            failed_ratio=round(failed_ratio, 3),
            count=len(items),
        )
    return snapshots


def relative_discount(value: float | None, benchmark: float | None) -> float | None:
    if value is None or benchmark is None or value <= 0 or benchmark <= 0:
        return None
    return (benchmark - value) / benchmark * 100


def valuation_score(packet: dict[str, Any], sector: SectorSnapshot) -> tuple[int, float | None, float | None]:
    fund = packet["fundamental"]
    tech = packet["tech"]
    if fund is None:
        return 35, None, None

    pe_discount = relative_discount(fund.pe, sector.median_pe)
    pb_discount = relative_discount(fund.pb, sector.median_pb)
    score = 50.0

    pb_weight = 0.55 if packet["sector"] in PB_HEAVY_SECTORS else 0.32
    pe_weight = 0.52 if packet["sector"] not in PB_HEAVY_SECTORS else 0.35
    if pb_discount is not None:
        score += clamp(pb_discount * pb_weight, -18, 28)
    if pe_discount is not None:
        score += clamp(pe_discount * pe_weight, -16, 25)

    if fund.pb is not None and fund.pb > 0:
        if fund.pb <= 1.0:
            score += 12
        elif fund.pb <= 1.6:
            score += 7
        elif fund.pb <= 2.3:
            score += 3
    if fund.pe is not None and fund.pe > 0:
        if fund.pe <= 8:
            score += 10
        elif fund.pe <= 13:
            score += 6
        elif fund.pe <= 18:
            score += 2

    if tech is not None and tech.target_discount_pct > 0:
        discount_ratio = tech.discount_pct / tech.target_discount_pct
        if discount_ratio >= 1.0:
            score += 11
        elif discount_ratio >= 0.85:
            score += 6

    if fund.pe is not None and fund.pe <= 0:
        score -= 18
    if fund.pb is not None and fund.pb <= 0:
        score -= 12
    return int(clamp(score)), pe_discount, pb_discount


def quality_score(packet: dict[str, Any]) -> int:
    fund = packet["fundamental"]
    sector = packet["sector"]
    if fund is None:
        return 35
    score = 50.0
    if fund.roe is not None:
        score += clamp((fund.roe - 10) * 1.15, -16, 25)
    if fund.roa is not None:
        score += clamp((fund.roa - 4) * 1.25, -10, 15)
    if fund.profit_margin is not None:
        score += clamp((fund.profit_margin - 6) * 0.45, -8, 10)
    if sector not in FINANCIAL_SECTORS:
        if fund.debt_to_equity is not None:
            if fund.debt_to_equity <= 0.8:
                score += 8
            elif fund.debt_to_equity <= 1.5:
                score += 3
            elif fund.debt_to_equity >= 2.5:
                score -= 12
        if fund.current_ratio is not None:
            if fund.current_ratio >= 1.2:
                score += 5
            elif fund.current_ratio < 0.85:
                score -= 8
    if fund.eps is not None and fund.eps <= 0:
        score -= 22
    return int(clamp(score))


def technical_score(packet: dict[str, Any]) -> int:
    tech = packet["tech"]
    if tech is None:
        return 35
    score = float(tech.win_score)
    if tech.near_break and not tech.failed_break:
        score += 5
    if tech.failed_break:
        score -= 28
    return int(clamp(score))


def risk_score(packet: dict[str, Any], quality: int, sector: SectorSnapshot, pe_disc: float | None, pb_disc: float | None) -> tuple[int, list[str]]:
    fund = packet["fundamental"]
    tech = packet["tech"]
    risk = 0.0
    flags: list[str] = []

    if fund is None:
        risk += 22
        flags.append("thieu PE/PB")
    else:
        if fund.pe is not None and fund.pe <= 0:
            risk += 22
            flags.append("PE am")
        if fund.eps is not None and fund.eps <= 0:
            risk += 22
            flags.append("EPS am")
        if packet["sector"] not in FINANCIAL_SECTORS and fund.debt_to_equity is not None and fund.debt_to_equity >= 2.5:
            risk += 14
            flags.append("no cao")
        if fund.roe is not None and fund.roe < 5:
            risk += 10
            flags.append("ROE yeu")
    if tech is None:
        risk += 12
        flags.append("thieu chart")
    elif tech.failed_break:
        risk += 34
        flags.append("failed break")
    if quality < 45:
        risk += 10
        flags.append("chat luong thap")
    if sector.score < 45:
        risk += 12
        flags.append("nganh yeu")
    if pe_disc is not None and pb_disc is not None and pe_disc < -10 and pb_disc < -10:
        risk += 10
        flags.append("dinh gia cao hon nganh")
    return int(clamp(risk)), flags


def build_bull_case(packet: dict[str, Any], sector: SectorSnapshot, pe_disc: float | None, pb_disc: float | None) -> str:
    fund = packet["fundamental"]
    tech = packet["tech"]
    parts: list[str] = []
    if pb_disc is not None and pb_disc >= 20:
        parts.append(f"PB re hon nganh {pb_disc:.0f}%")
    if pe_disc is not None and pe_disc >= 20:
        parts.append(f"PE re hon nganh {pe_disc:.0f}%")
    if tech is not None and tech.discount_pct >= tech.target_discount_pct * 0.85:
        parts.append(f"gia chiet khau {tech.discount_pct:.0f}%")
    if sector.score >= 60:
        parts.append("nganh co dong tien")
    if fund is not None and fund.roe is not None and fund.roe >= 15:
        parts.append(f"ROE {fund.roe:.0f}%")
    if tech is not None and tech.near_break and not tech.failed_break:
        parts.append("gan nen break")
    return "; ".join(parts[:4]) or "dinh gia/ky thuat dang can theo doi"


def make_action(score: int, risk: int, valuation: int, sector_score: int) -> str:
    if score >= 78 and risk <= 30 and valuation >= 65 and sector_score >= 55:
        return "CO_HOI_LON"
    if score >= 70 and risk <= 40:
        return "CANH_MUA_TUNG_PHAN"
    if score >= 62:
        return "WATCHLIST"
    return "THEO_DOI"


def build_opportunities(packets: list[dict[str, Any]], sectors: dict[str, SectorSnapshot]) -> list[Opportunity]:
    opportunities: list[Opportunity] = []
    for packet in packets:
        fund = packet["fundamental"]
        tech = packet["tech"]
        if fund is None or (fund.pe is None and fund.pb is None):
            continue
        sector = sectors[packet["sector"]]
        val_score, pe_disc, pb_disc = valuation_score(packet, sector)
        qual_score = quality_score(packet)
        tech_score = technical_score(packet)
        risk, risk_flags = risk_score(packet, qual_score, sector, pe_disc, pb_disc)
        raw_score = (
            val_score * 0.33
            + qual_score * 0.22
            + tech_score * 0.22
            + sector.score * 0.23
            - risk * 0.30
        )
        score = int(clamp(raw_score))
        if score < MIN_SCORE or sector.score < MIN_SECTOR_SCORE:
            continue
        action = make_action(score, risk, val_score, sector.score)
        bull_case = build_bull_case(packet, sector, pe_disc, pb_disc)
        bear_case = "; ".join(risk_flags[:4]) if risk_flags else "cho diem mua va quan tri ty trong"
        opportunities.append(
            Opportunity(
                symbol=packet["symbol"],
                sector=packet["sector"],
                close=float(packet["close"] or 0),
                action=action,
                opportunity_score=score,
                valuation_score=val_score,
                quality_score=qual_score,
                technical_score=tech_score,
                sector_score=sector.score,
                risk_score=risk,
                pe=fund.pe,
                pb=fund.pb,
                sector_pe=sector.median_pe,
                sector_pb=sector.median_pb,
                pe_discount_pct=pe_disc,
                pb_discount_pct=pb_disc,
                roe=fund.roe,
                roa=fund.roa,
                debt_to_equity=fund.debt_to_equity,
                discount_pct=tech.discount_pct if tech is not None else 0.0,
                target_discount_pct=tech.target_discount_pct if tech is not None else 0.0,
                setup=tech.setup if tech is not None else "NO_CHART",
                bull_case=bull_case,
                bear_case=bear_case,
                data_source=fund.source,
            )
        )
    return sorted(opportunities, key=lambda item: item.opportunity_score, reverse=True)


def opportunity_line(item: Opportunity) -> str:
    return (
        f"`{item.symbol}` {item.opportunity_score}/100 {item.action} | {item.sector} | "
        f"PE {fmt_num(item.pe)} vs {fmt_num(item.sector_pe)} ({fmt_pct(item.pe_discount_pct, signed=True)}) | "
        f"PB {fmt_num(item.pb, 2)} vs {fmt_num(item.sector_pb, 2)} ({fmt_pct(item.pb_discount_pct, signed=True)}) | "
        f"DD {item.discount_pct:.0f}/{item.target_discount_pct:.0f}% | "
        f"V/Q/T/S {item.valuation_score}/{item.quality_score}/{item.technical_score}/{item.sector_score} | "
        f"{item.bull_case} | risk: {item.bear_case}"
    )


def sector_line(item: SectorSnapshot) -> str:
    return (
        f"`{item.sector}` score {item.score} | avg tech {item.avg_win_score:.0f} | "
        f"PE {fmt_num(item.median_pe)} PB {fmt_num(item.median_pb, 2)} ROE {fmt_pct(item.median_roe)} | "
        f"failed {item.failed_ratio * 100:.0f}% | {item.count} ma"
    )


def build_report(opportunities: list[Opportunity], sectors: dict[str, SectorSnapshot], mode: str) -> str:
    now = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M")
    top = opportunities[:TOP_N]
    strong = [item for item in top if item.action in {"CO_HOI_LON", "CANH_MUA_TUNG_PHAN"}]
    sector_rows = sorted(sectors.values(), key=lambda item: item.score, reverse=True)[:8]

    lines = [
        f"*THIEUCUTOO WEEKEND OPPORTUNITIES* `{now}`",
        "Quet PE/PB + chiet khau gia + chat luong + nganh + risk. Khong phai khuyen nghi mua ban.",
        "",
        "*CO HOI LON / CANH MUA*",
    ]
    lines += [opportunity_line(item) for item in strong[:10]] or ["Chua co ma du nguong co hoi lon."]
    lines += ["", "*TOP WATCHLIST DINH GIA TOT*"]
    lines += [opportunity_line(item) for item in top] or ["Chua co ma dat nguong loc."]
    lines += ["", "*NGANH DANG NGON*"]
    lines += [sector_line(item) for item in sector_rows] or ["Chua du du lieu nganh."]
    if mode == "test":
        lines += ["", "`TEST MODE`: chi quet mot tap ma mau."]
    return "\n".join(lines)


def save_outputs(opportunities: list[Opportunity], sectors: dict[str, SectorSnapshot]) -> None:
    now = datetime.now(VN_TZ).isoformat(timespec="seconds")
    latest = {
        "updated_at": now,
        "top": [asdict(item) for item in opportunities[:TOP_N]],
        "sectors": [asdict(item) for item in sorted(sectors.values(), key=lambda x: x.score, reverse=True)],
    }
    scan.json_save(DATA_DIR / "weekend_opportunities_latest.json", latest, pretty=False)

    history_path = DATA_DIR / "weekend_opportunities_history.json"
    history = scan.json_load(history_path, [])
    history.append({"updated_at": now, "top": latest["top"][:10]})
    history = history[-60:]
    scan.json_save(history_path, history, pretty=False)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=os.getenv("WEEKEND_MODE", "full"))
    args = parser.parse_args()
    mode = str(args.mode).strip().lower()
    if mode not in {"full", "test"}:
        logger.warning("Unknown mode=%r, using full", mode)
        mode = "full"

    if os.getenv("GITHUB_ACTIONS") and mode != "test":
        delay = random.randint(0, max(RANDOM_START_MAX, 0))
        logger.info("Weekend random start delay %ss", delay)
        await asyncio.sleep(delay)

    tickers = build_universe(mode)
    random.shuffle(tickers)
    logger.info("Weekend opportunity scan mode=%s tickers=%s", mode, len(tickers))

    packets = []
    force_refresh = mode == "test"
    for index, symbol in enumerate(tickers, start=1):
        logger.info("[%s/%s] Analyze %s", index, len(tickers), symbol)
        try:
            packet = await asyncio.to_thread(fetch_symbol_packet, symbol, force_refresh)
            packets.append(packet)
        except Exception as exc:
            logger.exception("[%s] weekend scan failed: %s", symbol, exc)

    sectors = build_sector_snapshots(packets)
    opportunities = build_opportunities(packets, sectors)
    save_outputs(opportunities, sectors)

    report = build_report(opportunities, sectors, mode)
    await scan.send_chunks("*THIEUCUTOO WEEKEND*", report)
    logger.info("Weekend opportunities found: %s", len(opportunities))


if __name__ == "__main__":
    asyncio.run(main())
