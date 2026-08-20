#!/usr/bin/env python3
from __future__ import annotations

import logging
import math
import os
import random
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

import fetcher
import market_phase
import scan
import scoring
import signal_tracker
import technical_features

logger = logging.getLogger("thieucutoo.intel")
DATA_DIR = scan.DATA_DIR
VN_TZ = scan.VN_TZ
TRACKER_PATH = DATA_DIR / "signal_tracker.json"
ROTATION_PATH = DATA_DIR / "sector_rotation_history.json"
PORTFOLIO_STATE_PATH = DATA_DIR / "portfolio_threshold_state.json"
INDEX_ALIASES = {"VNINDEX": ["VNINDEX", "^VNINDEX", "VN-INDEX"]}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return default
        return value
    except Exception:
        return default


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def env_int(name: str, default: int, min_value: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(min_value, int(raw))
    except ValueError:
        return default


def is_cache_fresh_today(path: Path, ttl_minutes: int) -> bool:
    if not path.exists():
        return False
    mtime = path.stat().st_mtime
    file_date = datetime.fromtimestamp(mtime, tz=VN_TZ).date()
    if file_date < datetime.now(VN_TZ).date():
        return False
    return (time.time() - mtime) <= ttl_minutes * 60


def validate_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    required = {"time", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        return None
    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["time", "open", "high", "low", "close", "volume"])
    out = out[(out[["open", "high", "low", "close"]] > 0).all(axis=1)]
    out = out[out["volume"] > 0]
    out = out[out["high"] >= out[["open", "close", "low"]].max(axis=1)]
    out = out[out["low"] <= out[["open", "close", "high"]].min(axis=1)]
    out = out.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)
    if out.empty:
        return None
    daily_return = out["close"].pct_change().abs()
    out = out[daily_return.isna() | (daily_return < 0.50)].copy()
    return out.reset_index(drop=True) if not out.empty else None


def symbol_aliases(symbol: str) -> list[str]:
    return INDEX_ALIASES.get(symbol.upper(), [symbol])


def fetch_ohlcv_safe(symbol: str, bars: int = 260, force_refresh: bool = False) -> pd.DataFrame | None:
    import scan_safe

    ttl = 480 if not force_refresh else 0
    path = scan.cache_path(symbol, bars)
    if not force_refresh and is_cache_fresh_today(path, ttl):
        cached = validate_ohlcv(scan.read_cache_frame(path))
        if cached is not None and len(cached) >= 80:
            return fetcher.canonicalize_price_units(cached.tail(bars).reset_index(drop=True), symbol)

    attempts = env_int("SCAN_FETCH_MAX_ATTEMPTS", 3, min_value=1)
    days_back = max(300, int(bars * 1.7))
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    for attempt in range(attempts):
        for alias in symbol_aliases(symbol):
            for source in scan_safe.source_order_for_symbol(alias):
                limiter = scan_safe.API_LIMITERS[source]
                if limiter.disabled:
                    continue
                limiter.wait_turn(alias)
                try:
                    raw = scan_safe.fetch_source_history(source, alias, start, end)
                    raw_attrs = dict(getattr(raw, "attrs", {}))
                    df = validate_ohlcv(scan.normalize_ohlcv(raw))
                    if df is not None and len(df) >= 80:
                        df.attrs.update(raw_attrs)
                        df = fetcher.canonicalize_price_units(df, symbol, source)
                        if df is None:
                            continue
                        limiter.record_success()
                        df = df.tail(bars).reset_index(drop=True)
                        scan.write_cache_frame(path, df)
                        return df
                    logger.warning("[%s] %s/%s returned insufficient data", source, symbol, alias)
                except SystemExit as exc:
                    logger.warning("[%s] %s/%s stopped by data quota: %s", source, symbol, alias, str(exc).splitlines()[0])
                    if scan_safe.is_rate_limit_error(exc):
                        limiter.record_failure(
                            is_rate_limit=True,
                            retry_after_seconds=scan_safe.extract_retry_after_seconds(exc),
                        )
                    else:
                        limiter.disable(str(exc)[:180])
                except Exception as exc:
                    logger.warning("[%s] %s/%s failed: %s", source, symbol, alias, exc)
                    if scan_safe.is_unsupported_source_error(exc):
                        limiter.disable(str(exc)[:180])
                    elif scan_safe.is_invalid_symbol_error(exc):
                        logger.warning("[%s] %s/%s invalid symbol, skipping source penalty", source, symbol, alias)
                    else:
                        limiter.record_failure(
                            is_rate_limit=scan_safe.is_rate_limit_error(exc),
                            retry_after_seconds=scan_safe.extract_retry_after_seconds(exc),
                        )
        if attempt + 1 < attempts:
            wait = (2 ** attempt) + random.uniform(0, 1)
            logger.warning("[%s] retry %s/%s after %.1fs", symbol, attempt + 2, attempts, wait)
            time.sleep(wait)
    cached = scan.read_stale_cache(path)
    if cached is not None:
        return fetcher.canonicalize_price_units(cached.tail(bars).reset_index(drop=True), symbol)
    return None


def frame_from_history(history_store: dict[str, Any], symbol: str) -> pd.DataFrame | None:
    rows = history_store.get(symbol)
    if not rows:
        return None
    try:
        return validate_ohlcv(pd.DataFrame(rows))
    except Exception:
        return None


def weekly_trend(df: pd.DataFrame | None) -> dict[str, Any]:
    if df is None or len(df) < 80:
        return {"weekly_uptrend": False, "weekly_above_ema13": False, "weekly_ema13_slope": 0.0}
    data = df.copy()
    data["time"] = pd.to_datetime(data["time"], errors="coerce")
    data = data.dropna(subset=["time"]).sort_values("time")
    weekly = data.resample("W-FRI", on="time").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    if len(weekly) < 30:
        return {"weekly_uptrend": False, "weekly_above_ema13": False, "weekly_ema13_slope": 0.0}
    ema13w = scan.ema(weekly["close"], 13)
    ema26w = scan.ema(weekly["close"], 26)
    base = safe_float(ema13w.iloc[-4], safe_float(ema13w.iloc[-1], 1.0))
    slope = (safe_float(ema13w.iloc[-1]) - base) / max(abs(base), 1e-9)
    return {
        "weekly_uptrend": bool(ema13w.iloc[-1] > ema26w.iloc[-1]),
        "weekly_above_ema13": bool(weekly["close"].iloc[-1] > ema13w.iloc[-1]),
        "weekly_ema13_slope": round(slope, 4),
    }


def volume_patterns(df: pd.DataFrame | None) -> dict[str, Any]:
    base = {"accumulation_ratio": 1.0, "vol_contraction": False, "vol_expansion_up": False, "churning": False}
    if df is None or len(df) < 25:
        return base
    vol = df["volume"]
    close = df["close"]
    vol_avg20 = vol.rolling(20).mean()
    recent = df.tail(15)
    up_vol = safe_float(recent[recent["close"] >= recent["open"]]["volume"].mean())
    dn_vol = safe_float(recent[recent["close"] < recent["open"]]["volume"].mean())
    ratio = 9.99 if dn_vol <= 0 and up_vol > 0 else (up_vol / max(dn_vol, 1.0))
    ratio = min(ratio, 9.99)
    prev_close = safe_float(close.iloc[-2], safe_float(close.iloc[-1], 1.0))
    return {
        "accumulation_ratio": round(ratio, 2),
        "vol_contraction": bool(vol.tail(5).mean() < safe_float(vol_avg20.iloc[-1]) * 0.70),
        "vol_expansion_up": bool(vol.iloc[-1] > safe_float(vol_avg20.iloc[-1]) * 1.50 and close.iloc[-1] > close.iloc[-2]),
        "churning": bool(vol.iloc[-1] > safe_float(vol_avg20.iloc[-1]) * 2.0 and abs(close.iloc[-1] - close.iloc[-2]) / max(prev_close, 1e-9) < 0.005),
    }


def find_key_levels(df: pd.DataFrame | None, n: int = 5) -> dict[str, Any]:
    if df is None or len(df) < n * 4:
        return {"resistance": None, "support": None, "risk_reward": None}
    highs = df["high"].rolling(n, center=True).max()
    lows = df["low"].rolling(n, center=True).min()
    pivot_highs = df["high"][df["high"] == highs].tail(10)
    pivot_lows = df["low"][df["low"] == lows].tail(10)
    close = safe_float(df["close"].iloc[-1])
    nearest_res = pivot_highs[pivot_highs > close].min()
    nearest_sup = pivot_lows[pivot_lows < close].max()
    has_res = pd.notna(nearest_res)
    has_sup = pd.notna(nearest_sup)
    rr = None
    if has_res and has_sup and close - float(nearest_sup) > 0:
        rr = (float(nearest_res) - close) / (close - float(nearest_sup))
    return {
        "resistance": round(float(nearest_res), 2) if has_res else None,
        "support": round(float(nearest_sup), 2) if has_sup else None,
        "risk_reward": round(float(rr), 2) if rr is not None else None,
    }


def vcp_quality(df: pd.DataFrame | None) -> dict[str, Any]:
    if df is None or len(df) < 35:
        return {"vcp_contracting": False, "contraction_degree": 0.0, "vcp_score": 0}
    ranges: list[float] = []
    for window in [30, 20, 10]:
        seg = df.tail(window).head(10)
        mean_close = safe_float(seg["close"].mean(), 1.0)
        ranges.append((safe_float(seg["high"].max()) - safe_float(seg["low"].min())) / max(mean_close, 1e-9))
    contracting = all(ranges[i] > ranges[i + 1] for i in range(len(ranges) - 1))
    degree = (ranges[0] - ranges[-1]) / max(ranges[0], 0.01)
    return {"vcp_contracting": bool(contracting), "contraction_degree": round(degree, 3), "vcp_score": int(clamp(degree * 40, 0, 40)) if contracting else 0}


def relative_strength(df: pd.DataFrame | None, df_index: pd.DataFrame | None, periods: tuple[int, ...] = (20, 60, 120)) -> dict[str, Any]:
    out: dict[str, Any] = {f"rs_{p}d": 0.0 for p in periods}
    out["rs_score"] = 50
    if df is None or df_index is None or df.empty or df_index.empty:
        return out
    values: list[float] = []
    for p in periods:
        if len(df) <= p or len(df_index) <= p:
            continue
        sb = safe_float(df["close"].iloc[-p])
        ib = safe_float(df_index["close"].iloc[-p])
        if sb <= 0 or ib <= 0:
            continue
        rs = round((safe_float(df["close"].iloc[-1]) / sb - safe_float(df_index["close"].iloc[-1]) / ib) * 100, 2)
        out[f"rs_{p}d"] = rs
        values.append(rs)
    if values:
        out["rs_score"] = int(clamp(sum(values) / len(values) * 2 + 50))
    return out


def market_regime(df_index: pd.DataFrame | None) -> dict[str, Any]:
    default = {"regime": "UNKNOWN", "risk_multiplier": 0.5, "above_ema50": False, "above_ema200": False, "ema50_slope_pct": 0.0, "days_above_50_of_20": 0}
    if df_index is None or len(df_index) < 210:
        return default
    close = df_index["close"]
    ema50 = scan.ema(close, 50)
    ema200 = scan.ema(close, 200)
    above50 = bool(close.iloc[-1] > ema50.iloc[-1])
    above200 = bool(close.iloc[-1] > ema200.iloc[-1])
    base = safe_float(ema50.iloc[-10], safe_float(ema50.iloc[-1], 1.0))
    slope = (safe_float(ema50.iloc[-1]) - base) / max(abs(base), 1e-9)
    days = int((close.tail(20) > ema50.tail(20)).sum())
    if above200 and above50 and days >= 14:
        regime, mult = "BULL", 1.0
    elif above200 and days >= 10:
        regime, mult = "RECOVERY", 0.7
    elif not above200:
        regime, mult = "BEAR", 0.3
    else:
        regime, mult = "CHOPPY", 0.5
    return {"regime": regime, "risk_multiplier": mult, "above_ema50": above50, "above_ema200": above200, "ema50_slope_pct": round(slope * 100, 2), "days_above_50_of_20": days}


def compute_trade_levels(result: scan.ScanResult, levels: dict[str, Any]) -> dict[str, Any]:
    close = safe_float(result.close)
    if close <= 0:
        return {"stop_loss": None, "take_profit": None, "risk_reward": None}
    stop_pct = {"G1": 0.07, "G2": 0.08, "G3": 0.09, "G4": 0.10, "G5": 0.12, "G6": 0.12, "G7": 0.15}
    target_pct = {"G1": 0.15, "G2": 0.18, "G3": 0.20, "G4": 0.22, "G5": 0.25, "G6": 0.22, "G7": 0.25}
    support = levels.get("support")
    resistance = levels.get("resistance")
    group = getattr(result, "discount_group", "G4")
    sl_default = close * (1 - stop_pct.get(group, 0.10))
    sl = max(float(support) * 0.99, sl_default) if support else sl_default
    tp_default = close * (1 + target_pct.get(group, 0.20))
    tp = float(resistance) if resistance and float(resistance) > close * 1.05 else tp_default
    rr = (tp - close) / (close - sl) if close - sl > 0 else None
    return {"stop_loss": round(sl, 2), "take_profit": round(tp, 2), "risk_reward": round(float(rr), 2) if rr is not None else None}


def position_size(rr: Any, score: int, regime: str) -> str:
    rr = safe_float(rr)
    if regime == "BEAR":
        return "KHONG MUA (thi truong downtrend)"
    if score < 60:
        return "NHO / THEO DOI (~2% danh muc)"
    if score >= 80 and rr >= 2.5 and regime == "BULL":
        return "FULL SIZE (~10% danh muc)"
    if score >= 70 and rr >= 2.0:
        return "3/4 SIZE (~8% danh muc)"
    if score >= 60 and rr >= 1.5:
        return "1/2 SIZE (pilot ~5% danh muc)"
    return "NHO / THEO DOI (~2% danh muc)"


def advanced_signal(symbol: str, df: pd.DataFrame | None, result: scan.ScanResult, df_index: pd.DataFrame | None, regime: dict[str, Any]) -> dict[str, Any]:
    weekly = weekly_trend(df)
    volume = volume_patterns(df)
    levels = find_key_levels(df)
    vcp = vcp_quality(df)
    rs = relative_strength(df, df_index)
    trade = compute_trade_levels(result, levels)
    structure = market_phase.analyze_market_structure(df)
    technical_watch = technical_features.analyze_technical_watch(df)
    early_accumulation = technical_features.analyze_early_accumulation(
        df,
        result,
        structure=structure.to_dict(),
        technical=technical_watch,
        relative_strength=rs,
    )
    rr = trade.get("risk_reward")
    name = str(regime.get("regime", "UNKNOWN"))
    weekly_score = (
        (42 if weekly["weekly_uptrend"] else 12)
        + (30 if weekly["weekly_above_ema13"] else 8)
        + (18 if weekly["weekly_ema13_slope"] > 0 else 0)
        + min(int(vcp["vcp_score"]), 10)
    )
    volume_score = (
        (38 if volume["accumulation_ratio"] >= 1.30 else 22 if volume["accumulation_ratio"] >= 1.0 else 8)
        + (24 if volume["vol_contraction"] else 0)
        + (28 if volume["vol_expansion_up"] else 0)
        + (10 if result.obv_up else 0)
    )
    rr_value = safe_float(rr)
    rr_score = 90 if rr_value >= 2.5 else 72 if rr_value >= 1.8 else 55 if rr_value >= 1.2 else 25
    adv = scoring.enhanced_daily_score(
        base_score=int(result.win_score),
        weekly_score=clamp(weekly_score),
        volume_score=clamp(volume_score),
        rs_score=int(rs["rs_score"]),
        rr_score=rr_score,
        regime=name,
        churning=bool(volume["churning"]),
    )
    if result.failed_break:
        adv = min(adv, 38)
    elif structure.breakout.state == "FAILED_BREAK_WATCH":
        adv = min(adv, 58)
    if structure.overall_state == "DISTRIBUTION":
        adv = min(adv, 48)
    elif structure.timeframes["1W"].state in market_phase.RISK_PHASES:
        adv = min(adv, 62)
    trade["position_size"] = position_size(trade.get("risk_reward"), adv, name)
    return {
        "symbol": symbol,
        "advanced_score": adv,
        "grade": scoring.grade(adv),
        "score_version": scoring.SCORE_VERSION,
        "weekly": weekly,
        "volume": volume,
        "levels": levels,
        "vcp": vcp,
        "rs": rs,
        "trade": trade,
        "regime": regime,
        "market_structure": structure.to_dict(),
        "technical_watch": technical_watch,
        "early_accumulation": early_accumulation,
    }


def build_market_metrics(results: dict[str, scan.ScanResult], history_store: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    df_index = frame_from_history(history_store, "VNINDEX")
    regime = market_regime(df_index)
    metrics = {}
    for symbol, result in results.items():
        metrics[symbol] = advanced_signal(symbol, frame_from_history(history_store, symbol), result, df_index, regime)
    return metrics, regime


def fmt_price(value: Any) -> str:
    if value is None:
        return "n/a"
    price = safe_float(value)
    if price == 0:
        return "n/a"
    if abs(price) >= 1000:
        return f"{price:,.0f}"
    return f"{price:.2f}"


def fmt_num(value: Any) -> str:
    return "n/a" if value is None else f"{safe_float(value):.2f}".rstrip("0").rstrip(".")


def format_regime(regime: dict[str, Any] | None) -> str:
    if not regime:
        return "*MARKET REGIME*\nChua co du lieu VNINDEX."
    return f"*MARKET REGIME* `{regime.get('regime', 'UNKNOWN')}` | risk x{safe_float(regime.get('risk_multiplier'), 0.5):.1f} | EMA50 {'tren' if regime.get('above_ema50') else 'duoi'} | EMA200 {'tren' if regime.get('above_ema200') else 'duoi'} | {int(regime.get('days_above_50_of_20', 0))}/20 ngay tren EMA50"


def format_advanced_lines(metrics: dict[str, Any] | None) -> list[str]:
    if not metrics:
        return []
    rs = metrics.get("rs", {})
    trade = metrics.get("trade", {})
    levels = metrics.get("levels", {})
    weekly = metrics.get("weekly", {})
    volume = metrics.get("volume", {})
    vcp = metrics.get("vcp", {})
    regime = metrics.get("regime", {})
    gate = metrics.get("gate", {})
    structure = metrics.get("market_structure", {})
    technical_watch = metrics.get("technical_watch", {})
    early_accumulation = metrics.get("early_accumulation", {})
    rr_text = f"{fmt_num(trade.get('risk_reward'))}x" if trade.get("risk_reward") is not None else "n/a"
    adv = int(metrics.get("advanced_score", 0))
    grade = str(metrics.get("grade") or scoring.grade(adv))
    line1 = f"Intel {grade} · {adv}/97 | RS {int(rs.get('rs_score', 50))} | Regime {regime.get('regime', 'UNKNOWN')} | SL {fmt_price(trade.get('stop_loss'))} | TP {fmt_price(trade.get('take_profit'))} | R/R {rr_text}"
    flags: list[str] = []
    if weekly.get("weekly_uptrend"):
        flags.append("weekly up")
    if volume.get("accumulation_ratio", 1.0) >= 1.3:
        flags.append(f"tich luy {fmt_num(volume.get('accumulation_ratio'))}x")
    if volume.get("vol_contraction"):
        flags.append("vol kiet")
    if volume.get("vol_expansion_up"):
        flags.append("vol bung len")
    if volume.get("churning"):
        flags.append("churning")
    if vcp.get("vcp_contracting") and int(vcp.get("vcp_score", 0)) > 0:
        flags.append(f"VCP co {int(vcp.get('vcp_score', 0))}d")
    size_text = trade.get("position_size", "THEO DOI")
    if gate and not gate.get("allowed", True):
        size_text = f"CHUA MUA / THEO DOI ({gate.get('reason', 'loc tin hieu')})"
    line2 = f"HT {fmt_price(levels.get('support'))} | KC {fmt_price(levels.get('resistance'))} | Size: {size_text}"
    if flags:
        line2 += " | " + ", ".join(flags[:4])
    phase_line = (
        f"Cấu trúc đa khung {structure.get('label', 'N/A')} "
        f"{int(structure.get('score') or 0)}/100 | "
        f"Tin cậy {int(structure.get('confidence') or 0)}%"
    )
    breakout = structure.get("breakout", {}) if isinstance(structure, dict) else {}
    break_label = breakout.get("label")
    if break_label:
        phase_line += f" | {break_label}"
        if breakout.get("event_age_bars") is not None:
            phase_line += f" ({int(breakout['event_age_bars'])} phiên)"
    lines = [line1, phase_line, line2]
    secondary_labels: list[str] = []
    if early_accumulation.get("eligible"):
        secondary_labels.append(
            f"Early {early_accumulation.get('stage', 'E1')} {int(early_accumulation.get('score') or 0)}/97"
        )
    if technical_watch.get("watch"):
        secondary_labels.append(
            f"KT đáy {int(technical_watch.get('score') or 0)}/97: "
            + ", ".join(str(item) for item in technical_watch.get("signals", [])[:2])
        )
    if secondary_labels:
        lines.append("Nhãn phụ: " + " | ".join(secondary_labels))
    return lines


def structure_map_lines(
    results: list[scan.ScanResult],
    metrics_by_symbol: dict[str, dict[str, Any]],
    *,
    per_group: int = 6,
) -> list[str]:
    """Return a compact 1D/1W/1M map for the full scanned universe."""

    groups = {"OPPORTUNITY": [], "ACCUMULATION": [], "CAUTION": [], "DISTRIBUTION": []}
    for result in results:
        structure = metrics_by_symbol.get(result.symbol, {}).get("market_structure", {})
        state = str(structure.get("overall_state") or "CAUTION")
        if state not in groups:
            state = "CAUTION"
        groups[state].append((int(structure.get("score") or 0), result.symbol))
    labels = {
        "OPPORTUNITY": "CƠ HỘI",
        "ACCUMULATION": "TÍCH LŨY",
        "CAUTION": "CẨN THẬN",
        "DISTRIBUTION": "PHÂN PHỐI",
    }
    lines: list[str] = []
    for state in ("OPPORTUNITY", "ACCUMULATION", "CAUTION", "DISTRIBUTION"):
        ranked = sorted(groups[state], reverse=True)
        symbols = ", ".join(f"{symbol} {score}" for score, symbol in ranked[:per_group]) or "không có"
        lines.append(f"{labels[state]} {len(ranked)}: {symbols}")
    return lines


def format_breakout_watch(result: scan.ScanResult, metrics: dict[str, Any] | None) -> str:
    structure = (metrics or {}).get("market_structure", {})
    breakout = structure.get("breakout", {})
    frames = structure.get("timeframes", {})
    state = str(breakout.get("state") or getattr(result, "breakout_state", "NO_DATA"))
    level = breakout.get("breakout_level")
    age = breakout.get("event_age_bars")
    distance = breakout.get("distance_to_level_pct")
    invalidation = breakout.get("invalidation_price")
    level_text = fmt_price(level)
    age_text = "n/a" if age is None else f"{int(age)} phiên"
    distance_text = "n/a" if distance is None else f"{safe_float(distance):+.1f}%"
    return (
        f"`{result.symbol}` {market_phase.BREAKOUT_LABELS.get(state, state)} | "
        f"D {frames.get('1D', {}).get('label', 'N/A')} · "
        f"W {frames.get('1W', {}).get('label', 'N/A')} · "
        f"M {frames.get('1M', {}).get('label', 'N/A')} | "
        f"mốc {level_text} · {distance_text} · tuổi {age_text} · vô hiệu {fmt_price(invalidation)}"
    )


def update_signal_tracker(
    results: dict[str, scan.ScanResult],
    metrics_by_symbol: dict[str, dict[str, Any]],
    mode: str,
    min_score: int = 72,
    history_store: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return signal_tracker.update_tracker(
        path=TRACKER_PATH,
        results=results,
        metrics_by_symbol=metrics_by_symbol,
        history_store=history_store or {},
        mode=mode,
        min_score=min_score,
    )


def build_performance_report() -> str:
    return signal_tracker.build_performance_report(TRACKER_PATH)


def sector_scores(results: list[scan.ScanResult]) -> dict[str, dict[str, Any]]:
    by_symbol = {x.symbol: x for x in results}
    out: dict[str, dict[str, Any]] = {}
    for sector, symbols in scan.SECTORS.items():
        rows = [by_symbol[s] for s in symbols if s in by_symbol]
        if not rows:
            continue
        avg = sum(x.win_score for x in rows) / len(rows)
        flow = sum(1 for x in rows if x.obv_up and x.mfi >= 50)
        near = sum(1 for x in rows if x.near_break)
        failed = sum(1 for x in rows if x.failed_break)
        score = int(clamp(avg + flow / len(rows) * 10 + near / len(rows) * 5 - failed / len(rows) * 18))
        out[sector] = {"sector": sector, "score": score, "avg_win_score": round(avg, 1), "flow_count": flow, "near_break_count": near, "failed_count": failed, "count": len(rows)}
    return out


def previous_rotation_snapshot(history: list[Any]) -> dict[str, int]:
    if not history:
        return {}
    today = datetime.now(VN_TZ).date()
    fallback: dict[str, int] = {}
    for item in reversed(history):
        if not isinstance(item, dict):
            continue
        scores = {str(x.get("sector")): int(x.get("score", 50)) for x in item.get("sectors", []) if isinstance(x, dict)}
        if scores and not fallback:
            fallback = scores
        try:
            item_date = datetime.fromisoformat(str(item.get("updated_at"))).date()
        except Exception:
            continue
        if scores and item_date <= today - timedelta(days=5):
            return scores
    return fallback


def update_sector_rotation(results: list[scan.ScanResult]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    current = sector_scores(results)
    history = scan.json_load(ROTATION_PATH, [])
    if not isinstance(history, list):
        history = []
    prev = previous_rotation_snapshot(history)
    alerts: list[str] = []
    for sector, snap in sorted(current.items(), key=lambda x: x[1]["score"], reverse=True):
        delta = int(snap["score"]) - int(prev.get(sector, 50))
        if delta >= 12:
            alerts.append(f"ROTATION IN `{sector}` +{delta} diem (vao tien)")
        elif delta <= -12:
            alerts.append(f"ROTATION OUT `{sector}` {delta} diem (ra tien)")
    history.append({"updated_at": datetime.now(VN_TZ).isoformat(timespec="seconds"), "sectors": [current[k] for k in sorted(current)]})
    scan.json_save(ROTATION_PATH, history[-80:], pretty=False)
    return current, alerts


def auto_update_portfolio_thresholds(results: dict[str, scan.ScanResult]) -> bool:
    market = results.get("VNINDEX")
    if market is None:
        return False
    if market.win_score < 50:
        state, buy_adj, sell_adj = "weak", 5, 5
    elif market.win_score >= 70:
        state, buy_adj, sell_adj = "strong", -5, -5
    else:
        state, buy_adj, sell_adj = "neutral", 0, 0
    path = DATA_DIR / "portfolio.json"
    portfolio = scan.json_load(path, [])
    if not isinstance(portfolio, list) or not portfolio:
        return False
    changed = False
    for item in portfolio:
        if not isinstance(item, dict):
            continue
        base_buy = int(item.get("base_buy_more_score", item.get("buy_more_score", 78)))
        base_sell = int(item.get("base_sell_score", item.get("sell_score", 45)))
        item["base_buy_more_score"] = base_buy
        item["base_sell_score"] = base_sell
        new_buy = int(clamp(base_buy + buy_adj, 60, 90))
        new_sell = int(clamp(base_sell + sell_adj, 35, 55))
        if item.get("buy_more_score") != new_buy or item.get("sell_score") != new_sell:
            item["buy_more_score"] = new_buy
            item["sell_score"] = new_sell
            changed = True
    if changed:
        scan.json_save(path, portfolio)
    scan.json_save(PORTFOLIO_STATE_PATH, {"updated_at": datetime.now(VN_TZ).isoformat(timespec="seconds"), "market_state": state, "vnindex_score": market.win_score}, pretty=False)
    return changed


def build_scan_completion_summary(success_count: int, failed_symbols: list[str], elapsed_sec: float) -> str:
    failed = sorted(set(failed_symbols))
    text = f"Scan xong: {success_count} OK / {len(failed)} fail / {elapsed_sec:.0f}s"
    return text if not failed else text + "\nFailed: " + ",".join(failed[:15])


def warn_uncovered_groups() -> list[str]:
    uncovered = [t for t in scan.ALL_TICKERS if t not in scan.TICKER_GROUP]
    if uncovered:
        logger.warning("Tickers chua co group (dung G4): %s", ",".join(uncovered))
    return uncovered
