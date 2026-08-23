#!/usr/bin/env python3
import asyncio
import logging
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import pandas as pd

import fetcher
import market_intel as intel
import scan
import source_router

logger = logging.getLogger("thieucutoo.safe")
DATA_DIR = scan.DATA_DIR
SOURCE_HEALTH_PATH = DATA_DIR / "source_health.json"
FETCH_PROVENANCE: dict[str, dict[str, Any]] = {}


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


SUPPORTED_QUOTE_SOURCES = fetcher.SUPPORTED_SOURCES
DEFAULT_API_SOURCES = fetcher.DEFAULT_SOURCES
INDEX_CAPABLE_SOURCES = fetcher.INDEX_CAPABLE_SOURCES


def filter_api_sources(sources: list[str]) -> list[str]:
    valid: list[str] = []
    ignored: list[str] = []
    for source in sources:
        normalized = fetcher.normalize_source(source)
        if normalized in SUPPORTED_QUOTE_SOURCES and fetcher.source_is_available(normalized):
            if normalized not in valid:
                valid.append(normalized)
        elif normalized not in SUPPORTED_QUOTE_SOURCES:
            ignored.append(source)
    if ignored:
        logger.warning("Ignoring unsupported OHLCV source(s): %s", ",".join(ignored))
    return valid or fetcher.filter_sources(DEFAULT_API_SOURCES.copy())


def quote_source_name(source: str) -> str:
    return fetcher.normalize_source(source).lower()


def fetch_source_history(source: str, symbol: str, start: str, end: str) -> pd.DataFrame:
    return fetcher.fetch_source_history(source, symbol, start, end)


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


def is_invalid_symbol_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "invalid symbol" in text
        or "symbol format" in text
        or "symbol must be between" in text
        or "symbol is not recognized" in text
    )


def is_authentication_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        needle in text
        for needle in (
            "incorrect password",
            "user does not exist",
            "invalid credential",
            "unauthorized",
            "authentication",
            "login failed",
            "please login before calling data",
        )
    )


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
SOURCE_RECOVER_AFTER = env_float("SCAN_SOURCE_RECOVER_AFTER_SEC", 300.0, min_value=30.0)
RETRY_AFTER_MAX = env_float("SCAN_RETRY_AFTER_MAX_SEC", 300.0, min_value=1.0)
FETCH_MAX_ATTEMPTS = env_int("SCAN_FETCH_MAX_ATTEMPTS", 3, min_value=1)
INDEX_ALIASES = {"VNINDEX": ["VNINDEX", "^VNINDEX", "VN-INDEX"]}


def load_source_health(path: Path = SOURCE_HEALTH_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = scan.json_load(path, {})
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


PREVIOUS_SOURCE_HEALTH = load_source_health()


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
        self.rate_limit_failures = 0
        self.transient_failures = 0
        self.parked_count = 0
        self.last_error = ""
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

    def record_failure(self, is_rate_limit: bool = False, retry_after_seconds: float | None = None) -> None:
        with self.lock:
            self.failures += 1
            if (
                not is_rate_limit
                and SOURCE_DISABLE_AFTER_FAILURES
                and self.failures >= SOURCE_DISABLE_AFTER_FAILURES
            ):
                self.transient_failures += 1
                self.parked_count += 1
                self.last_error = "transient parked"
                cooldown = SOURCE_RECOVER_AFTER + random.uniform(REQUEST_JITTER_MIN, REQUEST_JITTER_MAX)
                self.cooldown_until = max(self.cooldown_until, time.monotonic() + cooldown)
                self.failures = 0
                logger.warning(
                    "[%s] parked %.1fs after consecutive transient failures; source can recover later",
                    self.source,
                    cooldown,
                )
                return
            if is_rate_limit and retry_after_seconds is not None:
                self.rate_limit_failures += 1
                cooldown = min(RETRY_AFTER_MAX, max(0.0, retry_after_seconds)) + random.uniform(
                    REQUEST_JITTER_MIN,
                    REQUEST_JITTER_MAX,
                )
                reason = f"rate-limit Retry-After={retry_after_seconds:.1f}s"
            elif is_rate_limit:
                self.rate_limit_failures += 1
                multiplier = min(self.failures, 4)
                reason = "rate-limit"
                cooldown = random.uniform(SOURCE_COOLDOWN_MIN, SOURCE_COOLDOWN_MAX) * multiplier
            else:
                self.transient_failures += 1
                multiplier = min(max(self.failures * 0.3, 0.5), 2.0)
                reason = "transient"
                cooldown = random.uniform(SOURCE_COOLDOWN_MIN, SOURCE_COOLDOWN_MAX) * multiplier
            self.last_error = reason
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

    def health_dict(self) -> dict[str, Any]:
        with self.lock:
            attempts = max(1, self.attempts)
            success_rate = self.successes / attempts
            penalty = self.rate_limit_failures * 18 + self.transient_failures * 8 + self.parked_count * 12
            score = max(0, min(100, int(success_rate * 100 - penalty)))
            return {
                "source": self.source,
                "rpm_limit": self.rpm_limit,
                "effective_rpm": round(self.effective_rpm, 2),
                "attempts": self.attempts,
                "successes": self.successes,
                "failures": self.failures,
                "rate_limit_failures": self.rate_limit_failures,
                "transient_failures": self.transient_failures,
                "parked_count": self.parked_count,
                "disabled": self.disabled,
                "last_error": self.last_error,
                "health_score": score,
            }


API_LIMITERS = {
    source: ApiSourceLimiter(source, SOURCE_RPM_LIMITS.get(source, scan.REQUESTS_PER_MINUTE), SOURCE_USAGE_RATIO)
    for source in API_SOURCES
}


def extract_retry_after_seconds(exc: BaseException) -> float | None:
    """Best-effort parser for API Retry-After hints exposed through wrappers."""
    header_value: str | None = None
    for container in (getattr(exc, "response", None), exc):
        headers = getattr(container, "headers", None)
        if not headers:
            continue
        try:
            header_value = headers.get("Retry-After") or headers.get("retry-after")
        except AttributeError:
            try:
                header_value = headers["Retry-After"] or headers["retry-after"]
            except Exception:
                header_value = None
        if header_value:
            break

    text = str(exc)
    if not header_value:
        match = re.search(r"retry-after\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
        if match:
            header_value = match.group(1)
    if not header_value:
        return None

    raw = str(header_value).strip()
    try:
        return max(0.0, min(float(raw), RETRY_AFTER_MAX))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = (parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, min(seconds, RETRY_AFTER_MAX))
    except Exception:
        return None


def source_order_for_symbol(symbol: str) -> list[str]:
    return source_router.source_order(
        symbol,
        API_SOURCES,
        index_capable_sources=INDEX_CAPABLE_SOURCES,
        previous_health=PREVIOUS_SOURCE_HEALTH,
    )


def symbol_aliases(symbol: str) -> list[str]:
    return INDEX_ALIASES.get(symbol.upper(), [symbol])


def effective_total_api_rpm() -> float:
    return sum(limiter.effective_rpm for limiter in API_LIMITERS.values())


def source_health_payload() -> dict[str, Any]:
    return {
        "updated_at": datetime.now(scan.VN_TZ).isoformat(timespec="seconds"),
        "sources": {source: limiter.health_dict() for source, limiter in API_LIMITERS.items()},
        "fiinquant_monthly_budget": fetcher.fiinquant_provider.quota_snapshot(),
        # Per-symbol provenance makes workflow artifacts auditable without
        # exposing credentials: recent-price source and optional deep backfill.
        "symbol_provenance": {
            symbol: dict(metadata)
            for symbol, metadata in sorted(FETCH_PROVENANCE.items())
        },
    }


def save_source_health(
    path: Path = SOURCE_HEALTH_PATH,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = source_health_payload()
    if extra:
        payload.update(extra)
    try:
        scan.json_save(path, payload, pretty=True)
    except Exception as exc:
        logger.warning("Cannot save source health %s: %s", path, exc)
    return payload


def cache_metadata_path(path: Path) -> Path:
    return path.with_suffix(".meta.json")


def dataframe_as_of(df: pd.DataFrame) -> str | None:
    if "time" not in df.columns or df.empty:
        return None
    try:
        value = pd.to_datetime(df["time"], errors="coerce").dropna().max()
        return value.date().isoformat() if pd.notna(value) else None
    except Exception:
        return None


def with_provenance(
    symbol: str,
    df: pd.DataFrame,
    *,
    source: str | None,
    cache_status: str,
    history_backfill_source: str | None = None,
) -> pd.DataFrame:
    result = df.copy().reset_index(drop=True)
    metadata = {
        "symbol": symbol.upper(),
        "as_of": dataframe_as_of(result),
        "data_source": source,
        "cache_status": cache_status,
        "observed_at": datetime.now(scan.VN_TZ).isoformat(timespec="seconds"),
        "price_unit": df.attrs.get("price_unit", "index_points" if symbol.upper() in fetcher.INDEX_SYMBOLS else "thousand_vnd"),
        "unit_scale_applied": float(df.attrs.get("unit_scale_applied", 1.0)),
        "unit_repaired_from_cache": bool(df.attrs.get("unit_repaired_from_cache", False)),
    }
    if history_backfill_source:
        metadata["history_backfill_source"] = history_backfill_source
    result.attrs.update(metadata)
    FETCH_PROVENANCE[symbol.upper()] = metadata
    return result


def load_cache_metadata(path: Path) -> dict[str, Any]:
    raw = scan.json_load(cache_metadata_path(path), {})
    return raw if isinstance(raw, dict) else {}


def compatible_cache_paths(symbol: str, bars: int) -> list[Path]:
    """Return the exact cache followed by larger caches that can serve it.

    Daily, weekly and monthly analyzers all consume the same daily OHLCV. A
    fresh 1,560-bar weekend cache therefore also satisfies a 520/780-bar
    request without another provider call.
    """

    exact = scan.cache_path(symbol, bars)
    candidates: list[tuple[int, Path]] = []
    prefix = f"{symbol.upper()}_D_"
    for candidate in exact.parent.glob(f"{symbol.upper()}_D_*.parquet"):
        stem = candidate.stem
        if not stem.startswith(prefix):
            continue
        try:
            cached_bars = int(stem[len(prefix) :])
        except ValueError:
            continue
        if cached_bars >= bars and candidate != exact:
            candidates.append((cached_bars, candidate))
    candidates.sort(key=lambda item: item[0])
    return [exact, *(path for _, path in candidates)]


def load_compatible_cache(
    symbol: str,
    bars: int,
    *,
    ttl_minutes: int | None = None,
    stale_max_days: int | None = None,
    quiet_stale: bool = False,
) -> tuple[pd.DataFrame | None, Path | None]:
    best: tuple[pd.DataFrame, Path] | None = None
    for candidate in compatible_cache_paths(symbol, bars):
        if ttl_minutes is not None:
            if not intel.is_cache_fresh_today(candidate, ttl_minutes):
                continue
            cached = scan.read_cache_frame(candidate)
        else:
            if quiet_stale:
                if not candidate.exists() or not stale_max_days or stale_max_days <= 0:
                    continue
                modified_at = datetime.fromtimestamp(candidate.stat().st_mtime, tz=scan.VN_TZ)
                age_days = (datetime.now(scan.VN_TZ) - modified_at).total_seconds() / 86400
                if age_days > stale_max_days:
                    continue
                cached = scan.read_cache_frame(candidate)
            else:
                cached = scan.read_stale_cache(candidate, stale_max_days)
        cached = intel.validate_ohlcv(cached)
        if cached is not None and len(cached) >= 80:
            if len(cached) >= bars:
                return cached, candidate
            if best is None or len(cached) > len(best[0]):
                best = (cached, candidate)
    return best if best is not None else (None, None)


def merge_recent_history(history: pd.DataFrame, recent: pd.DataFrame) -> pd.DataFrame | None:
    """Overlay recent validated bars on a deeper cache, preferring recent data."""

    recent_attrs = dict(recent.attrs)
    combined = pd.concat([history, recent], ignore_index=True)
    combined["time"] = pd.to_datetime(combined["time"], errors="coerce")
    # De-duplicate before validate_ohlcv sorts the rows. Pandas' default sort
    # is not stable for equal timestamps, which could otherwise let an older
    # backfill row overwrite the FiinQuant overlay nondeterministically.
    combined = combined.dropna(subset=["time"]).drop_duplicates("time", keep="last")
    merged = intel.validate_ohlcv(combined)
    if merged is not None:
        merged.attrs.update(recent_attrs)
    return merged


def fetch_ohlcv_safe(symbol: str, bars: int = 260, force_refresh: bool = False) -> pd.DataFrame | None:
    ttl = 480 if not force_refresh else 0
    path = scan.cache_path(symbol, bars)
    if not force_refresh:
        cached, cached_path = load_compatible_cache(symbol, bars, ttl_minutes=ttl)
        if cached is not None and cached_path is not None:
            metadata = load_cache_metadata(cached_path)
            cached = fetcher.canonicalize_price_units(cached, symbol, metadata.get("data_source"))
            if cached is None:
                return None
            return with_provenance(
                symbol,
                cached.tail(bars),
                source=metadata.get("data_source"),
                cache_status="fresh_cache",
                history_backfill_source=metadata.get("history_backfill_source"),
            )

    days_back = max(300, int(bars * 1.7))
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    reference = None
    old_metadata: dict[str, Any] = {}
    fiinquant_recent: pd.DataFrame | None = None
    reference, reference_path = load_compatible_cache(
        symbol,
        bars,
        stale_max_days=scan.STALE_CACHE_MAX_DAYS,
        quiet_stale=True,
    )
    if reference is not None and reference_path is not None:
        old_metadata = load_cache_metadata(reference_path)
        reference = fetcher.canonicalize_price_units(reference, symbol, old_metadata.get("data_source"))

    for attempt in range(FETCH_MAX_ATTEMPTS):
        for alias in symbol_aliases(symbol):
            for source in source_order_for_symbol(alias):
                limiter = API_LIMITERS[source]
                if limiter.disabled:
                    continue
                if source == "FIINQUANT" and fiinquant_recent is not None:
                    continue
                limiter.wait_turn(alias)
                try:
                    raw = fetch_source_history(source, alias, start, end)
                    raw_attrs = dict(getattr(raw, "attrs", {}))
                    df = intel.validate_ohlcv(scan.normalize_ohlcv(raw))
                    if df is not None and len(df) >= 80:
                        df.attrs.update(raw_attrs)
                        df = fetcher.canonicalize_price_units(df, symbol, source)
                        if df is None:
                            continue
                        df, repaired = fetcher.harmonize_with_reference(df, reference, symbol)
                        history_backfill_source = None
                        result_source = source
                        if source == "FIINQUANT" and raw_attrs.get("history_partial"):
                            if reference is None:
                                limiter.record_success()
                                fiinquant_recent = df
                                logger.info(
                                    "[FIINQUANT] %s recent data OK; using a standard source for deep-history backfill",
                                    symbol,
                                )
                                continue
                            merged = merge_recent_history(reference, df)
                            if merged is None:
                                continue
                            df = merged
                            history_backfill_source = str(
                                old_metadata.get("data_source") or "VALIDATED_CACHE"
                            )
                        elif source != "FIINQUANT" and fiinquant_recent is not None:
                            recent, recent_repaired = fetcher.harmonize_with_reference(
                                fiinquant_recent,
                                df,
                                symbol,
                            )
                            merged = merge_recent_history(df, recent)
                            if merged is None:
                                continue
                            df = merged
                            repaired = repaired or recent_repaired
                            result_source = "FIINQUANT"
                            history_backfill_source = source
                        limiter.record_success()
                        df = df.tail(bars).reset_index(drop=True)
                        scan.write_cache_frame(path, df)
                        scan.json_save(
                            cache_metadata_path(path),
                            {
                                "symbol": symbol.upper(),
                                "data_source": result_source,
                                "history_backfill_source": history_backfill_source,
                                "as_of": dataframe_as_of(df),
                                "cached_at": datetime.now(scan.VN_TZ).isoformat(timespec="seconds"),
                                "price_unit": df.attrs.get("price_unit"),
                                "unit_scale_applied": df.attrs.get("unit_scale_applied", 1.0),
                                "unit_repaired_from_cache": repaired,
                            },
                            pretty=True,
                        )
                        return with_provenance(
                            symbol,
                            df,
                            source=result_source,
                            cache_status="live",
                            history_backfill_source=history_backfill_source,
                        )
                    logger.warning("[%s] %s/%s returned insufficient data", source, symbol, alias)
                    if source == "FIINQUANT":
                        limiter.record_failure()
                except SystemExit as exc:
                    logger.warning("[%s] %s/%s stopped by vnstock quota: %s", source, symbol, alias, str(exc).splitlines()[0])
                    if is_rate_limit_error(exc):
                        limiter.record_failure(is_rate_limit=True, retry_after_seconds=extract_retry_after_seconds(exc))
                    else:
                        limiter.disable(str(exc)[:180])
                except Exception as exc:
                    logger.warning("[%s] %s/%s failed: %s", source, symbol, alias, exc)
                    if is_authentication_error(exc):
                        limiter.disable("authentication failed; verify GitHub Secrets")
                    elif is_unsupported_source_error(exc):
                        limiter.disable(str(exc)[:180])
                    elif is_invalid_symbol_error(exc):
                        logger.warning("[%s] %s/%s invalid symbol, skipping source penalty", source, symbol, alias)
                    elif source == "FIINQUANT":
                        limiter.record_failure(
                            is_rate_limit=is_rate_limit_error(exc),
                            retry_after_seconds=extract_retry_after_seconds(exc),
                        )
                    else:
                        limiter.record_failure(
                            is_rate_limit=is_rate_limit_error(exc),
                            retry_after_seconds=extract_retry_after_seconds(exc),
                        )
        if attempt + 1 < FETCH_MAX_ATTEMPTS:
            wait = (2 ** attempt) + random.uniform(0, 1)
            logger.warning("[%s] retry %s/%s after %.1fs", symbol, attempt + 2, FETCH_MAX_ATTEMPTS, wait)
            time.sleep(wait)
    cached, cached_path = load_compatible_cache(
        symbol,
        bars,
        stale_max_days=scan.STALE_CACHE_MAX_DAYS,
    )
    if cached is not None and cached_path is not None:
        metadata = load_cache_metadata(cached_path)
        cached = fetcher.canonicalize_price_units(cached, symbol, metadata.get("data_source"))
        if cached is None:
            return None
        return with_provenance(
            symbol,
            cached.tail(bars),
            source=metadata.get("data_source"),
            cache_status="stale_cache",
            history_backfill_source=metadata.get("history_backfill_source"),
        )
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
