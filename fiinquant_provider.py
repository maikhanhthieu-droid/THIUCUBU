#!/usr/bin/env python3
"""Lazy, rate-limited FiinQuantX adapter.

The scanner can run without FiinQuant credentials.  When credentials are
configured, one authenticated session is reused for the lifetime of the
process and every call remains historical/request-response (no realtime
WebSocket is opened).
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import pandas as pd

VN_TZ = timezone(timedelta(hours=7))
SOURCE_NAME = "FIINQUANT"
SDK_SOURCE_NAME = "FiinQuantX"
INDEX_ALIASES = {
    "^VNINDEX": "VNINDEX",
    "VN-INDEX": "VNINDEX",
    "HNXINDEX": "HNXIndex",
    "UPCOMINDEX": "UpcomIndex",
}


class FiinQuantError(RuntimeError):
    """Base error safe to expose in scanner logs."""


class FiinQuantNotConfigured(FiinQuantError):
    """Raised when the required GitHub Secrets/environment variables are absent."""


class FiinQuantAuthenticationError(FiinQuantError):
    """Raised when FiinQuant rejects the configured account."""


def _env_int(name: str, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


def _env_float(
    name: str,
    default: float,
    minimum: float = 0.05,
    maximum: float = 1.0,
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


def credentials() -> tuple[str, str]:
    return (
        os.getenv("FIINQUANT_USERNAME", "").strip(),
        os.getenv("FIINQUANT_PASSWORD", "").strip(),
    )


def is_configured() -> bool:
    username, password = credentials()
    return bool(username and password)


def canonical_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    return INDEX_ALIASES.get(value, value)


def _safe_error(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    for secret in credentials():
        if secret:
            text = text.replace(secret, "***")
    return text[:300]


def _looks_like_auth_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        hint in text
        for hint in (
            "incorrect password",
            "user does not exist",
            "invalid password",
            "invalid credential",
            "unauthorized",
            "authentication",
            "login failed",
            "please login before calling data",
        )
    )


class _RequestGate:
    """Limit request starts while allowing a small number of in-flight calls."""

    def __init__(self) -> None:
        rpm = _env_int("FIINQUANT_REQUESTS_PER_MINUTE", 80, minimum=1, maximum=90)
        usage = _env_float("FIINQUANT_USAGE_RATIO", 0.75)
        self.min_interval = 60.0 / max(1.0, rpm * usage)
        self.next_at = 0.0
        self.start_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(
            _env_int("FIINQUANT_MAX_CONCURRENCY", 2, minimum=1, maximum=4)
        )

    @contextmanager
    def request(self) -> Iterator[None]:
        with self.slots:
            with self.start_lock:
                now = time.monotonic()
                wait = max(0.0, self.next_at - now)
                if wait:
                    time.sleep(wait)
                self.next_at = time.monotonic() + self.min_interval
            yield


_REQUEST_GATE = _RequestGate()
_SESSION: Any | None = None
_SESSION_ERROR: FiinQuantError | None = None
_SESSION_LOCK = threading.Lock()


def _load_session_type() -> Any:
    try:
        from FiinQuantX import FiinSession
    except ImportError as exc:  # pragma: no cover - depends on optional SDK install
        raise FiinQuantError(
            "FiinQuantX is not installed; install requirements-fiinquant.txt"
        ) from exc
    return FiinSession


def get_session() -> Any:
    global _SESSION, _SESSION_ERROR
    if _SESSION is not None:
        return _SESSION
    if _SESSION_ERROR is not None:
        raise _SESSION_ERROR
    username, password = credentials()
    if not username or not password:
        raise FiinQuantNotConfigured(
            "FIINQUANT_USERNAME and FIINQUANT_PASSWORD are not configured"
        )
    with _SESSION_LOCK:
        if _SESSION is not None:
            return _SESSION
        if _SESSION_ERROR is not None:
            raise _SESSION_ERROR
        try:
            session_type = _load_session_type()
            with _REQUEST_GATE.request():
                session = session_type(username=username, password=password).login()
            if session is None:
                raise RuntimeError("login returned no session")
            _SESSION = session
            return _SESSION
        except Exception as exc:
            if _looks_like_auth_error(exc):
                _SESSION_ERROR = FiinQuantAuthenticationError(
                    f"FiinQuantX authentication failed: {_safe_error(exc)}"
                )
                raise _SESSION_ERROR from None
            raise FiinQuantError(
                f"FiinQuantX session unavailable: {_safe_error(exc)}"
            ) from None


def reset_session() -> None:
    """Clear process-local state; primarily useful for tests and one-shot retries."""

    global _SESSION, _SESSION_ERROR
    with _SESSION_LOCK:
        _SESSION = None
        _SESSION_ERROR = None


def _run_request(call: Any, operation: str) -> Any:
    global _SESSION, _SESSION_ERROR
    try:
        with _REQUEST_GATE.request():
            return call()
    except FiinQuantError:
        raise
    except Exception as exc:
        message = f"FiinQuantX {operation} failed: {_safe_error(exc)}"
        if _looks_like_auth_error(exc):
            error = FiinQuantAuthenticationError(message)
            with _SESSION_LOCK:
                _SESSION = None
                _SESSION_ERROR = error
            raise error from None
        raise FiinQuantError(message) from None


def fetch_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch adjusted daily OHLCV without opening a realtime stream."""

    session = get_session()
    ticker = canonical_symbol(symbol)

    def request() -> Any:
        return session.Fetch_Trading_Data(
            realtime=False,
            tickers=[ticker],
            fields=["open", "high", "low", "close", "volume"],
            adjusted=True,
            by="1d",
            from_date=start,
            to_date=end,
            lasted=True,
        ).get_data()

    raw = _run_request(request, f"historical request for {ticker}")
    if not isinstance(raw, pd.DataFrame):
        try:
            raw = pd.DataFrame(raw)
        except (TypeError, ValueError) as exc:
            raise FiinQuantError(f"FiinQuantX returned invalid OHLCV for {ticker}") from exc
    if raw.empty:
        raise FiinQuantError(f"FiinQuantX returned no OHLCV for {ticker}")
    ticker_column = next(
        (column for column in raw.columns if str(column).strip().lower() in {"ticker", "symbol"}),
        None,
    )
    if ticker_column is not None:
        selected = raw[raw[ticker_column].astype(str).str.upper() == ticker.upper()]
        if not selected.empty:
            raw = selected
    raw = raw.copy()
    raw.attrs["provider"] = SDK_SOURCE_NAME
    return raw


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, pd.DataFrame):
        return payload.to_dict(orient="records")
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "values", "result"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [payload]
    return []


def _sort_key(record: dict[str, Any]) -> tuple[int, int, str]:
    def integer(name: str) -> int:
        try:
            return int(record.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    timestamp = str(record.get("timestamp") or record.get("date") or "")
    return integer("year"), integer("quarter"), timestamp


def _clean_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _flatten_values(value: Any, output: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(nested, (dict, list)):
                output[_clean_key(key)] = nested
            _flatten_values(nested, output)
    elif isinstance(value, list):
        for nested in value:
            _flatten_values(nested, output)


def _number(values: dict[str, Any], *aliases: str) -> float | None:
    for alias in aliases:
        raw = values.get(_clean_key(alias))
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _latest_valuation(session: Any, symbol: str) -> dict[str, Any]:
    end = datetime.now(VN_TZ).date()
    start = end - timedelta(days=45)

    def request() -> Any:
        return session.MarketDepth().get_stock_valuation(
            tickers=[symbol],
            from_date=start.isoformat(),
            to_date=end.isoformat(),
        )

    rows = _records(_run_request(request, f"valuation request for {symbol}"))
    if not rows:
        return {}
    return sorted(rows, key=_sort_key)[-1]


def fetch_fundamental(symbol: str) -> dict[str, Any] | None:
    """Return a normalized latest valuation/quality snapshot for weekend scoring."""

    session = get_session()
    ticker = canonical_symbol(symbol)
    current_year = datetime.now(VN_TZ).year

    def request() -> Any:
        return session.FundamentalAnalysis().get_ratios(
            tickers=[ticker],
            years=[current_year - 1, current_year],
            quarters=[1, 2, 3, 4],
            type="consolidated",
        )

    rows = _records(_run_request(request, f"fundamental request for {ticker}"))
    latest = sorted(rows, key=_sort_key)[-1] if rows else {}
    flattened: dict[str, Any] = {}
    _flatten_values(latest, flattened)
    pe = _number(flattened, "PriceToEarning", "pe")
    pb = _number(flattened, "PriceToBook", "pb")
    if pe is None or pb is None:
        valuation = _latest_valuation(session, ticker)
        _flatten_values(valuation, flattened)
        pe = pe if pe is not None else _number(flattened, "PriceToEarning", "pe")
        pb = pb if pb is not None else _number(flattened, "PriceToBook", "pb")
    if not flattened:
        return None
    year = latest.get("year")
    quarter = latest.get("quarter")
    period = f"{year}Q{quarter}" if year and quarter else str(year or latest.get("timestamp") or "")
    return {
        "symbol": ticker,
        "pe": pe,
        "pb": pb,
        "roe": _number(flattened, "ROE", "PreprovisionROE"),
        "roa": _number(flattened, "ROA", "PreprovisionROA"),
        "debt_to_equity": _number(flattened, "DebtToEquityRatio"),
        "current_ratio": _number(flattened, "CurrentRatio"),
        "profit_margin": _number(flattened, "NetProfitMargin"),
        "eps": _number(flattened, "BasicEPS", "eps"),
        "period": period,
        "source": SDK_SOURCE_NAME,
    }
