#!/usr/bin/env python3
import asyncio
import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta

import pandas as pd
from vnstock.api.quote import Quote

import market_intel as intel
import scan

logger = logging.getLogger("thieucutoo.safe")


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


def env_csv(name: str, default: str) -> list[str]:
    values = [item.strip().upper() for item in os.getenv(name, default).split(",")]
    return [item for item in values if item]


SUPPORTED_QUOTE_SOURCES = {"VCI", "KBS", "MSN", "FMP", "FMARKET"}
DEFAULT_API_SOURCES = ["VCI", "KBS"]


def filter_api_sources(sources: list[str]) -> list[str]:
    valid: list[str] = []
    ignored: list[str] = []
    for source in sources:
        if source in SUPPORTED_QUOTE_SOURCES:
            if source not in valid:
                valid.append(source)
        else:
            ignored.append(source)
    if ignored:
        logger.warning("Ignoring unsupported vnstock Quote source(s): %s", ",".join(ignored))
    return valid or DEFAULT_API_SOURCES.copy()


def quote_source_name(source: str) -> str:
    return source.lower()


def is_unsupported_source_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "provider 'quote/" in text
        or ("available:" in text and "quote" in text)
        or ("chi nhan" in text and "source" in text)
        or ("tham" in text and "source" in text)
    )


def is_rate_limit_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    needles = (
        "429",
        "too many request",
        "too many requests",
        "rate limit",
        "ratelimit",
        "quota",
        "exceeded",
        "temporarily blocked",
    )
    return any(needle in text for needle in needles)


def parse_source_limits(raw: str, sources: list[str], default: int) -> dict[str, int]:
    limits = {source: default for source in sources}
    if not raw.strip():
        return limits
    for part in raw.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            try:
                parsed = max(1, int(part.strip()))
            except ValueError:
                logger.warning("Invalid source limit %r, keeping default", part)
                return limits
            return {source: parsed for source in sources}
        source, value = part.split("=", 1)
        source = source.strip().upper()
        if source in limits:
            try:
                limits[source] = max(1, int(value.strip()))
            except ValueError:
                logger.warning("Invalid source limit %r, keeping default", part)
    return limits


API_SOURCES = filter_api_sources(env_csv("SCAN_API_SOURCES", ",".join(DEFAULT_API_SOURCES)))
SOURCE_USAGE_RATIO = env_float("SCAN_SOURCE_USAGE_RATIO", 0.70, min_value=0.05, max_value=1.0)
SOURCE_RPM_LIMITS = parse_source_limits(
    os.getenv("SCAN_SOURCE_LIMITS", ""),
    API_SOURCES,
    env_int("SCAN_SOURCE_REQUESTS_PER_MINUTE", scan.REQUESTS_PER_MINUTE, min_value=1),
)
REQUEST_JITTER_MIN = env_float("SCAN_REQUEST_JITTER_MIN_SEC", 0.5, min_value=0.0)
REQUEST_JITTER_MAX = max(REQUEST_JITTER_MIN, env_float("SCAN_REQUEST_JITTER_MAX_SEC", 2.5, min_value=0.0))
SOURCE_COOLDOWN_MIN = env_float("SCAN_SOURCE_ERROR_COOLDOWN_MIN_SEC", 45.0, min_value=0.0)
SOURCE_COOLDOWN_MAX = max(SOURCE_COOLDOWN_MIN, env_float("SCAN_SOURCE_ERROR_COOLDOWN_MAX_SEC", 150.0, min_value=0.0))
SOURCE_DISABLE_AFTER_FAILURES = env_int("SCAN_SOURCE_DISABLE_AFTER_FAILURES", 3, min_value=0)
FETCH_MAX_ATTEMPTS = env_int("SCAN_FETCH_MAX_ATTEMPTS", 3, min_value=1)
INDEX_ALIASES = {"VNINDEX": ["VNINDEX", "^VNINDEX", "VN-INDEX"]}


class ApiSourceLimiter:
    def __init__(self, source: str, rpm_limit: int, usage_ratio: float) -> None:
        self.source = source
        self.rpm_limit = rpm_limit
        self.usage_ratio = usage_ratio
        self.effective_rpm = max(0.1, rpm_limit * usage_ratio)
        self.min_interval = 60.0 / self.effective_rpm
        self.next_at = time.monotonic() + random.uniform(0, self.min_interval)
        self.cooldown_until = 0.0
        self.failures = 0
        self.attempts = 0
        self.successes = 0
        self.disabled = False
        self.lock = threading.Lock()

    def wait_turn(self, symbol: str) -> None:
        with self.lock:
            now = time.monotonic()
            earliest = max(now, self.next_at, self.cooldown_until)
            jitter = random.uniform(REQUEST_JITTER_MIN, REQUEST_JITTER_MAX)
            sleep_for = max(0.0, earliest - now) + jitter
            self.next_at = earliest + self.min_interval + jitter
            self.attempts += 1
        if sleep_for >= 1.0:
            logger.info("[%s] %s throttle sleep %.1fs", self.source, symbol, sleep_for)
        if sleep_for > 0:
            time.sleep(sleep_for)

    def record_success(self) -> None:
        with self.lock:
            self.successes += 1
            self.failures = 0

    def record_failure(self, is_rate_limit: bool = False) -> None:
        with self.lock:
            self.failures += 1
            if (
                not is_rate_limit
                and SOURCE_DISABLE_AFTER_FAILURES
                and self.failures >= SOURCE_DISABLE_AFTER_FAILURES
            ):
                self.disabled = True
                self.cooldown_until = 0.0
                logger.warning("[%s] disabled after %s consecutive failure(s)", self.source, self.failures)
                return
            if is_rate_limit:
                multiplier = min(self.failures, 4)
                reason = "rate-limit"
            else:
                multiplier = min(max(self.failures * 0.3, 0.5), 2.0)
                reason = "transient"
            cooldown = random.uniform(SOURCE_COOLDOWN_MIN, SOURCE_COOLDOWN_MAX) * multiplier
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + cooldown)
            logger.warning(
                "[%s] cooling down %.1fs after %s %s failure(s)",
                self.source,
                cooldown,
                self.failures,
                reason,
            )

    def disable(self, reason: str) -> None:
        with self.lock:
            self.disabled = True
            self.cooldown_until = 0.0
            logger.warning("[%s] disabled source: %s", self.source, reason)

    def snapshot(self) -> str:
        with self.lock:
            status = "disabled" if self.disabled else "active"
            return (
                f"{self.source}: limit {self.rpm_limit}/min, use {self.effective_rpm:.1f}/min "
                f"({self.usage_ratio:.0%}), ok {self.successes}/{self.attempts}, fail {self.failures}, {status}"
            )


API_LIMITERS = {
    source: ApiSourceLimiter(source, SOURCE_RPM_LIMITS.get(source, scan.REQUESTS_PER_MINUTE), SOURCE_USAGE_RATIO)
    for source in API_SOURCES
}


def source_order_for_symbol(symbol: str) -> list[str]:
    start = sum(ord(char) for char in symbol.upper()) % len(API_SOURCES)
    return API_SOURCES[start:] + API_SOURCES[:start]


def symbol_aliases(symbol: str) -> list[str]:
    return INDEX_ALIASES.get(symbol.upper(), [symbol])


def effective_total_api_rpm() -> float:
    return sum(limiter.effective_rpm for limiter in API_LIMITERS.values())


def fetch_ohlcv_safe(symbol: str, bars: int = 260, force_refresh: bool = False) -> pd.DataFrame | None:
    ttl = 480 if not force_refresh else 0
    path = scan.cache_path(symbol, bars)
    if not force_refresh and intel.is_cache_fresh_today(path, ttl):
        try:
            cached = intel.validate_ohlcv(pd.read_parquet(path))
            if cached is not None and len(cached) >= 80:
                return cached
        except Exception as exc:
            logger.debug("Cannot read cache %s: %s", path, exc)

    days_back = max(300, int(bars * 1.7))
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    for attempt in range(FETCH_MAX_ATTEMPTS):
        for alias in symbol_aliases(symbol):
            for source in source_order_for_symbol(alias):
                limiter = API_LIMITERS[source]
                if limiter.disabled:
                    continue
                limiter.wait_turn(alias)
                try:
                    q = Quote(symbol=alias, source=quote_source_name(source))
                    raw = q.history(start=start, end=end, interval="1D")
                    df = intel.validate_ohlcv(scan.normalize_ohlcv(raw))
                    if df is not None and len(df) >= 80:
                        limiter.record_success()
                        df = df.tail(bars).reset_index(drop=True)
                        try:
                            df.to_parquet(path, index=False)
                        except Exception:
                            pass
                        return df
                    logger.warning("[%s] %s/%s returned insufficient data", source, symbol, alias)
                except SystemExit as exc:
                    logger.warning("[%s] %s/%s stopped by vnstock quota: %s", source, symbol, alias, str(exc).splitlines()[0])
                    if is_rate_limit_error(exc):
                        limiter.record_failure(is_rate_limit=True)
                    else:
                        limiter.disable(str(exc)[:180])
                except Exception as exc:
                    logger.warning("[%s] %s/%s failed: %s", source, symbol, alias, exc)
                    if is_unsupported_source_error(exc):
                        limiter.disable(str(exc)[:180])
                    else:
                        limiter.record_failure(is_rate_limit=is_rate_limit_error(exc))
        if attempt + 1 < FETCH_MAX_ATTEMPTS:
            wait = (2 ** attempt) + random.uniform(0, 1)
            logger.warning("[%s] retry %s/%s after %.1fs", symbol, attempt + 2, FETCH_MAX_ATTEMPTS, wait)
            time.sleep(wait)
    return None


async def main() -> None:
    scan.fetch_ohlcv = fetch_ohlcv_safe
    scan.MAX_WORKERS = min(env_int("SCAN_MAX_WORKERS", len(API_SOURCES), min_value=1), max(1, len(API_SOURCES)))
    scan.REQUESTS_PER_MINUTE = max(1, int(effective_total_api_rpm()))
    logger.info(
        "Safe API mode: sources=%s effective_rpm=%.1f workers=%s",
        ",".join(API_SOURCES),
        effective_total_api_rpm(),
        scan.MAX_WORKERS,
    )
    await scan.main()
    logger.info("API source stats: %s", " | ".join(limiter.snapshot() for limiter in API_LIMITERS.values()))


if __name__ == "__main__":
    asyncio.run(main())