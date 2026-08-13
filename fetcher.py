#!/usr/bin/env python3
"""Resilient OHLCV fetch adapter.

Keep vendor-specific import paths and direct HTTP fallbacks out of scanner logic.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pandas as pd

logger = logging.getLogger("thieucutoo.fetcher")
VN_TZ = timezone(timedelta(hours=7))

try:  # vnstock has moved public import paths across releases.
    from vnstock.api.quote import Quote as VnQuote
except ImportError:  # pragma: no cover - depends on installed vnstock build
    try:
        from vnstock import Quote as VnQuote
    except ImportError:  # pragma: no cover
        VnQuote = None

try:
    from vietfin import vf as Vietfin
except ImportError:  # pragma: no cover - optional adapter
    Vietfin = None

SUPPORTED_SOURCES = {"VCI", "KBS", "DNSE"}
DEFAULT_SOURCES = ["VCI", "KBS", "DNSE"]
INDEX_CAPABLE_SOURCES = {"VCI", "KBS"}
INDEX_SYMBOLS = {"VNINDEX", "^VNINDEX", "VN-INDEX", "VN30", "HNX30", "HNXINDEX", "UPCOMINDEX", "VN100"}
SOURCE_ALIASES = {
    "VIETFIN": "DNSE",
    "VFIN": "DNSE",
}
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0, write=10.0, pool=10.0)

VCI_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,vi-VN;q=0.8,vi;q=0.7",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "DNT": "1",
    "Pragma": "no-cache",
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-mobile": "?0",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Referer": "https://trading.vietcap.com.vn/",
    "Origin": "https://trading.vietcap.com.vn/",
    "Device-Id": "f208a1226230bf66",
    "Cookie": "device_id=f208a1226230bf66",
}

KBS_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,vi-VN;q=0.8,vi;q=0.7",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Referer": "https://kbbuddywts.kbsec.com.vn/",
}


def normalize_source(source: str) -> str:
    value = str(source or "").strip().upper()
    return SOURCE_ALIASES.get(value, value)


def filter_sources(sources: list[str], include_index_sources_only: bool = False) -> list[str]:
    valid: list[str] = []
    ignored: list[str] = []
    allowed = INDEX_CAPABLE_SOURCES if include_index_sources_only else SUPPORTED_SOURCES
    for source in sources:
        normalized = normalize_source(source)
        if normalized in allowed:
            if normalized not in valid:
                valid.append(normalized)
        elif normalized:
            ignored.append(source)
    if ignored:
        logger.warning("Ignoring unsupported OHLCV source(s): %s", ",".join(ignored))
    return valid or [source for source in DEFAULT_SOURCES if source in allowed]


def normalize_ohlcv(raw: Any) -> pd.DataFrame | None:
    if raw is None:
        return None
    if isinstance(raw, pd.DataFrame):
        df = raw.copy()
    elif isinstance(raw, (dict, list, tuple)):
        if not raw:
            return None
        try:
            df = pd.DataFrame(raw).copy()
        except (AttributeError, TypeError, ValueError):
            return None
    else:
        return None

    if df.empty:
        return None
    try:
        df.columns = [str(c).lower() for c in df.columns]
        col_map = {
            "date": "time",
            "datetime": "time",
            "time": "time",
            "tradingdate": "time",
            "t": "time",
            "o": "open",
            "open": "open",
            "h": "high",
            "high": "high",
            "l": "low",
            "low": "low",
            "c": "close",
            "close": "close",
            "v": "volume",
            "volume": "volume",
        }
        df = df.rename(columns={c: col_map[c] for c in df.columns if c in col_map})
        required = {"time", "open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            return None
        df = df[["time", "open", "high", "low", "close", "volume"]].copy()
        time_as_text = df["time"].astype(str).str.strip()
        if pd.api.types.is_numeric_dtype(df["time"]) or time_as_text.str.fullmatch(r"\d{9,13}").all():
            numeric_time = pd.to_numeric(df["time"], errors="coerce")
            valid_time = numeric_time.dropna()
            if valid_time.empty:
                return None
            unit = "ms" if valid_time.median() > 10_000_000_000 else "s"
            df["time"] = pd.to_datetime(numeric_time, unit=unit, utc=True).dt.tz_convert(VN_TZ).dt.tz_localize(None)
        else:
            df["time"] = pd.to_datetime(df["time"], errors="coerce")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["time", "close", "volume"]).sort_values("time").reset_index(drop=True)
    except (AttributeError, KeyError, TypeError, ValueError, pd.errors.ParserError):
        return None
    return df if not df.empty else None


def canonicalize_price_units(
    df: pd.DataFrame | None,
    symbol: str,
    source: str | None = None,
) -> pd.DataFrame | None:
    """Return stock OHLC in thousand-VND units, independent of provider.

    VCI/KBS quote endpoints usually expose thousand VND while DNSE exposes
    VND.  Keeping both representations made cross-run returns jump 1000x when
    the fallback source changed.  Index series remain in index points.
    """

    if df is None or df.empty:
        return df
    out = df.copy()
    normalized_symbol = str(symbol or "").upper()
    if normalized_symbol in INDEX_SYMBOLS:
        out.attrs.update({"price_unit": "index_points", "unit_scale_applied": 1.0})
        return out
    close = pd.to_numeric(out.get("close"), errors="coerce").dropna()
    if close.empty:
        return out
    median_close = float(close.tail(30).median())
    normalized_source = normalize_source(str(source or ""))
    # DNSE stock prices are VND. The generic >=1000 check also protects us if
    # another provider changes representation for a subset of symbols.
    should_scale = median_close >= 1000.0 or (normalized_source == "DNSE" and median_close >= 100.0)
    scale = 0.001 if should_scale else 1.0
    if should_scale:
        for column in ("open", "high", "low", "close"):
            out[column] = pd.to_numeric(out[column], errors="coerce") * scale
    out.attrs.update(
        {
            "price_unit": "thousand_vnd",
            "unit_scale_applied": scale,
            "raw_median_close": median_close,
        }
    )
    return out


def harmonize_with_reference(
    candidate: pd.DataFrame,
    reference: pd.DataFrame | None,
    symbol: str,
) -> tuple[pd.DataFrame, bool]:
    """Repair a residual 1000x mismatch using overlapping trading dates."""

    if reference is None or reference.empty or candidate.empty or str(symbol).upper() in INDEX_SYMBOLS:
        return candidate, False
    left = candidate[["time", "close"]].copy()
    right = reference[["time", "close"]].copy()
    left["time"] = pd.to_datetime(left["time"], errors="coerce")
    right["time"] = pd.to_datetime(right["time"], errors="coerce")
    overlap = pd.merge(left, right, on="time", suffixes=("_new", "_old")).dropna().tail(40)
    overlap = overlap[(overlap["close_new"] > 0) & (overlap["close_old"] > 0)]
    if len(overlap) < 3:
        return candidate, False
    ratio = float((overlap["close_new"] / overlap["close_old"]).median())
    factor = 0.001 if 500 <= ratio <= 2000 else 1000.0 if 0.0005 <= ratio <= 0.002 else 1.0
    if factor == 1.0:
        return candidate, False
    repaired = candidate.copy()
    for column in ("open", "high", "low", "close"):
        repaired[column] = pd.to_numeric(repaired[column], errors="coerce") * factor
    repaired.attrs.update(candidate.attrs)
    repaired.attrs["unit_scale_applied"] = float(candidate.attrs.get("unit_scale_applied", 1.0)) * factor
    repaired.attrs["unit_repaired_from_cache"] = True
    logger.warning("[%s] repaired cross-source price unit ratio %.1fx", symbol, ratio)
    return repaired, True


def _vn_ts(value: str, end_of_day: bool = False) -> int:
    base = datetime.fromisoformat(value).replace(tzinfo=VN_TZ)
    if end_of_day:
        base = base.replace(hour=23, minute=59, second=59)
    return int(base.timestamp())


def _from_dnse_payload(data: Any) -> pd.DataFrame | None:
    if not isinstance(data, dict):
        return normalize_ohlcv(data)
    if {"t", "o", "h", "l", "c", "v"}.issubset(data):
        return normalize_ohlcv(
            {
                "time": data["t"],
                "open": data["o"],
                "high": data["h"],
                "low": data["l"],
                "close": data["c"],
                "volume": data["v"],
            }
        )
    rows = data.get("data") or data.get("items") or data.get("values")
    return normalize_ohlcv(rows)


def fetch_dnse_direct(symbol: str, start: str, end: str) -> pd.DataFrame:
    params = {
        "from": _vn_ts(start),
        "to": _vn_ts(end, end_of_day=True),
        "symbol": symbol.upper(),
        "resolution": "1D",
    }
    url = "https://services.entrade.com.vn/chart-api/v2/ohlcs/stock"
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    df = _from_dnse_payload(data)
    if df is None or df.empty:
        raise ValueError(f"DNSE direct returned no OHLCV for {symbol}")
    return df


def fetch_vietfin_dnse(symbol: str, start: str, end: str) -> pd.DataFrame:
    if Vietfin is None:
        raise RuntimeError("vietfin is not installed")
    packet = Vietfin.equity.price.historical(
        symbol=symbol.lower(),
        provider="dnse",
        start_date=start,
        end_date=end,
        interval="1d",
    )
    df = packet.to_df()
    if df is not None and "time" not in df.columns and "date" not in df.columns:
        df = df.reset_index()
    normalized = normalize_ohlcv(df)
    if normalized is None or normalized.empty:
        raise ValueError(f"vietfin DNSE returned no OHLCV for {symbol}")
    return normalized


def fetch_vnstock_quote(source: str, symbol: str, start: str, end: str) -> pd.DataFrame:
    if VnQuote is None:
        raise RuntimeError("vnstock Quote is not available")
    q = VnQuote(symbol=symbol, source=source.lower())
    df = normalize_ohlcv(q.history(start=start, end=end, interval="1D"))
    if df is None or df.empty:
        raise ValueError(f"vnstock {source} returned no OHLCV for {symbol}")
    return df


def _business_countback(start: str, end: str, minimum: int = 260) -> int:
    try:
        days = len(pd.bdate_range(pd.to_datetime(start), pd.to_datetime(end))) + 5
    except Exception:
        days = minimum
    return max(minimum, int(days))


def _from_vci_payload(data: Any) -> pd.DataFrame | None:
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and {"t", "o", "h", "l", "c", "v"}.issubset(first):
            return normalize_ohlcv(
                {
                    "time": first["t"],
                    "open": first["o"],
                    "high": first["h"],
                    "low": first["l"],
                    "close": first["c"],
                    "volume": first["v"],
                }
            )
    return normalize_ohlcv(data)


def _scale_vnd_ohlc_to_quote_units(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if symbol.upper() in INDEX_SYMBOLS:
        return df
    out = df.copy()
    for col in ["open", "high", "low", "close"]:
        out[col] = out[col] / 1000.0
    return out


def fetch_vci_direct(symbol: str, start: str, end: str) -> pd.DataFrame:
    end_dt = datetime.fromisoformat(end) + timedelta(days=1)
    payload = {
        "timeFrame": "ONE_DAY",
        "symbols": [symbol.upper()],
        "to": int(end_dt.timestamp()),
        "countBack": _business_countback(start, end),
    }
    url = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        response = client.post(url, headers=VCI_HEADERS, json=payload)
        response.raise_for_status()
        data = response.json()
    df = _from_vci_payload(data)
    if df is None or df.empty:
        raise ValueError(f"VCI direct returned no OHLCV for {symbol}")
    return _scale_vnd_ohlc_to_quote_units(df, symbol)


def _kbs_date(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%d-%m-%Y")


def fetch_kbs_direct(symbol: str, start: str, end: str) -> pd.DataFrame:
    is_index = symbol.upper() in {"VNINDEX", "HNXINDEX", "UPCOMINDEX", "VN30", "HNX30", "VN100"}
    segment = "index" if is_index else "stocks"
    url = f"https://kbbuddywts.kbsec.com.vn/iis-server/investment/{segment}/{symbol.upper()}/data_day"
    params = {"sdate": _kbs_date(start), "edate": _kbs_date(end)}
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        response = client.get(url, headers=KBS_HEADERS, params=params)
        response.raise_for_status()
        data = response.json()
    rows = data.get("data_day") if isinstance(data, dict) else data
    df = normalize_ohlcv(rows)
    if df is None or df.empty:
        raise ValueError(f"KBS direct returned no OHLCV for {symbol}")
    return _scale_vnd_ohlc_to_quote_units(df, symbol)


def fetch_source_history(source: str, symbol: str, start: str, end: str) -> pd.DataFrame:
    normalized = normalize_source(source)
    if normalized == "DNSE":
        try:
            frame = fetch_vietfin_dnse(symbol, start, end)
        except Exception as exc:
            logger.warning("[DNSE] vietfin failed for %s, trying direct HTTP: %s", symbol, exc)
            frame = fetch_dnse_direct(symbol, start, end)
    elif normalized == "VCI":
        try:
            frame = fetch_vnstock_quote(normalized, symbol, start, end)
        except Exception as exc:
            logger.warning("[VCI] vnstock failed for %s, trying direct HTTP: %s", symbol, exc)
            frame = fetch_vci_direct(symbol, start, end)
    elif normalized == "KBS":
        try:
            frame = fetch_vnstock_quote(normalized, symbol, start, end)
        except Exception as exc:
            logger.warning("[KBS] vnstock failed for %s, trying direct HTTP: %s", symbol, exc)
            frame = fetch_kbs_direct(symbol, start, end)
    else:
        raise ValueError(f"Unsupported OHLCV source: {source}")
    normalized_frame = canonicalize_price_units(frame, symbol, normalized)
    if normalized_frame is None:
        raise ValueError(f"{normalized} returned invalid OHLCV for {symbol}")
    return normalized_frame


def fetch_ohlcv(symbol: str, bars: int = 260, sources: list[str] | None = None) -> pd.DataFrame | None:
    days_back = max(300, int(bars * 1.7))
    end = datetime.now(VN_TZ).strftime("%Y-%m-%d")
    start = (datetime.now(VN_TZ) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    is_index = symbol.upper() in {"VNINDEX", "^VNINDEX", "VN-INDEX", "VN30", "HNX30"}
    ordered_sources = filter_sources(sources or DEFAULT_SOURCES, include_index_sources_only=is_index)
    for source in ordered_sources:
        try:
            df = fetch_source_history(source, symbol, start, end)
            if df is not None and len(df) >= 80:
                return df.tail(bars).reset_index(drop=True)
            logger.warning("[%s] %s returned insufficient OHLCV", source, symbol)
        except Exception as exc:
            logger.warning("[%s] %s failed: %s", source, symbol, exc)
            time.sleep(0.2)
    return None
