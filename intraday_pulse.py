#!/usr/bin/env python3
"""Thirty-minute market-wide anomaly radar.

The fixed daily scanners remain the source of the five-stream classification.
This module only polls a bulk price board, compares it with the previous pulse,
and promotes unusual symbols into the next deep scan.  It never turns a pulse
into an automatic buy instruction.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

import market_calendar


logger = logging.getLogger("thieucutoo.intraday_pulse")
VN_TZ = timezone(timedelta(hours=7))
DATA_DIR = Path("data")
STATE_PATH = DATA_DIR / "intraday_pulse_state.json"
LATEST_PATH = DATA_DIR / "intraday_pulse_latest.json"
SCHEMA_VERSION = "thieucubu.intraday_pulse.v1"
SYMBOL_LIMIT = 12


@dataclass(frozen=True)
class PulseEvent:
    symbol: str
    event_type: str
    direction: str
    score: int
    price: float
    change_30m_pct: float
    session_change_pct: float
    value_30m_billion: float
    total_value_billion: float
    close_position: float
    order_imbalance: float | None
    source: str
    verified: bool | None
    reasons: list[str]


def _safe(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return default if not math.isfinite(number) else number
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if 3 <= len(text) <= SYMBOL_LIMIT and text.isalnum() else ""


def _price(value: Any) -> float:
    number = _safe(value)
    if abs(number) >= 1000:
        number /= 1000.0
    return round(number, 4)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _timestamp(value: Any, fallback: datetime | None = None) -> str:
    fallback = fallback or datetime.now(VN_TZ)
    try:
        raw = float(value)
        if raw > 10_000_000_000:
            parsed = datetime.fromtimestamp(raw / 1000.0, tz=timezone.utc)
        elif raw > 1_000_000_000:
            parsed = datetime.fromtimestamp(raw, tz=timezone.utc)
        else:
            raise ValueError
        return parsed.astimezone(VN_TZ).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=VN_TZ)
        return parsed.astimezone(VN_TZ).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return fallback.isoformat(timespec="seconds")


def _first(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value is not None and not (isinstance(value, float) and math.isnan(value)):
            return value
    return None


def normalize_board(frame: pd.DataFrame | None, source: str) -> dict[str, dict[str, Any]]:
    """Normalize KBS/VCI bulk price-board rows to one compact schema."""

    output: dict[str, dict[str, Any]] = {}
    if frame is None or frame.empty:
        return output
    source = source.upper()
    for raw in frame.to_dict(orient="records"):
        symbol = _symbol(_first(raw, "symbol", "listing_symbol"))
        if not symbol:
            continue
        close = _price(_first(raw, "close_price", "match_match_price"))
        reference = _price(
            _first(raw, "reference_price", "match_reference_price", "listing_ref_price")
        )
        open_price = _price(_first(raw, "open_price", "match_open_price"))
        high = _price(_first(raw, "high_price", "match_highest"))
        low = _price(_first(raw, "low_price", "match_lowest"))
        volume = max(
            0,
            _integer(_first(raw, "volume_accumulated", "match_accumulated_volume")),
        )
        if close <= 0 or reference <= 0:
            continue
        if open_price <= 0:
            open_price = reference
        if high <= 0:
            high = max(open_price, close)
        if low <= 0:
            low = min(open_price, close)
        expected_value = volume * max(close, 0.0) * 1000.0
        raw_value = _safe(_first(raw, "total_value", "match_accumulated_value"))
        if expected_value > 0 and not (expected_value * 0.05 <= raw_value <= expected_value * 20):
            raw_value = expected_value
        bid_price = _price(_first(raw, "bid_price_1", "bid_ask_bid_1_price"))
        ask_price = _price(_first(raw, "ask_price_1", "bid_ask_ask_1_price"))
        bid_volume = max(0, _integer(_first(raw, "bid_vol_1", "bid_ask_bid_1_volume")))
        ask_volume = max(0, _integer(_first(raw, "ask_vol_1", "bid_ask_ask_1_volume")))
        output[symbol] = {
            "symbol": symbol,
            "timestamp": _timestamp(
                _first(raw, "time", "match_sending_time", "bid_ask_transaction_time")
            ),
            "source": source,
            "exchange": str(_first(raw, "exchange", "listing_exchange") or ""),
            "close": close,
            "reference": reference,
            "open": open_price,
            "high": max(high, close),
            "low": min(low, close),
            "volume": volume,
            "value": round(max(raw_value, 0.0), 2),
            "bid_price_1": bid_price,
            "bid_volume_1": bid_volume,
            "ask_price_1": ask_price,
            "ask_volume_1": ask_volume,
        }
    return output


def _create_client(source: str) -> Any:
    if source == "KBS":
        from vnstock.explorer.kbs.trading import Trading

        return Trading(show_log=False)
    if source == "VCI":
        from vnstock.explorer.vci.trading import Trading

        return Trading(show_log=False)
    raise ValueError(f"Unsupported pulse board source: {source}")


def _fetch_batch(client: Any, source: str, symbols: list[str]) -> dict[str, dict[str, Any]]:
    if source == "VCI":
        frame = client.price_board(symbols, show_log=False, flatten_columns=True)
    else:
        frame = client.price_board(symbols, show_log=False)
    return normalize_board(frame, source)


def fetch_market_board(
    symbols: Iterable[str],
    *,
    sources: tuple[str, ...] = ("KBS", "VCI"),
    batch_size: int = 100,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Fetch each batch once from a primary bulk board, falling back per batch."""

    values = list(dict.fromkeys(item for item in (_symbol(value) for value in symbols) if item))
    clients: dict[str, Any] = {}
    output: dict[str, dict[str, Any]] = {}
    source_counts = {source: 0 for source in sources}
    failures: list[str] = []
    delay = max(0.0, _safe(os.getenv("PULSE_BATCH_DELAY_SEC", "0.2"), 0.2))
    for start in range(0, len(values), max(1, batch_size)):
        missing = values[start : start + batch_size]
        for source in sources:
            if not missing:
                break
            try:
                if source not in clients:
                    clients[source] = _create_client(source)
                rows = _fetch_batch(clients[source], source, missing)
                found = [symbol for symbol in missing if symbol in rows]
                output.update({symbol: rows[symbol] for symbol in found})
                source_counts[source] += len(found)
                missing = [symbol for symbol in missing if symbol not in rows]
            except Exception as exc:
                failures.append(f"{source}:{type(exc).__name__}")
                logger.warning("Pulse board %s batch failed: %s", source, exc)
        if delay:
            time.sleep(delay)
    return output, {
        "requested": len(values),
        "received": len(output),
        "source_counts": source_counts,
        "failures": failures[:12],
    }


def load_watch_symbols() -> list[str]:
    symbols: list[str] = []
    portfolio = _read_json(DATA_DIR / "portfolio.json", [])
    if isinstance(portfolio, list):
        symbols.extend(_symbol(item.get("symbol")) for item in portfolio if isinstance(item, dict))
    notes = _read_json(DATA_DIR / "notes.json", {})
    if isinstance(notes, dict):
        symbols.extend(_symbol(item) for item in notes)
    return list(dict.fromkeys(item for item in symbols if item))


def load_market_symbols() -> list[str]:
    """Prefer the discovered exchange universe; retain the curated core as fallback."""

    watch = load_watch_symbols()
    try:
        import universe

        # Pulse owns its discovery state so it can never race with or block the
        # fixed scanner's rotating-universe cursor.
        state = universe.refresh_state(path=DATA_DIR / "intraday_universe_state.json")
        discovered = list(state.get("symbols") or [])
    except Exception as exc:
        logger.warning("Pulse universe discovery failed: %s", exc)
        discovered = []
    try:
        import scan

        core = list(scan.ALL_TICKERS)
    except Exception:
        core = []
    values = list(dict.fromkeys([*watch, *discovered, *core]))
    maximum = max(50, _integer(os.getenv("PULSE_MAX_SYMBOLS", "1800"), 1800))
    return values[:maximum]


def _elapsed_minutes(previous_updated_at: Any, current: datetime) -> float | None:
    try:
        previous = datetime.fromisoformat(str(previous_updated_at).replace("Z", "+00:00"))
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=VN_TZ)
        elapsed = (current - previous.astimezone(VN_TZ)).total_seconds() / 60.0
        return elapsed if elapsed > 0 else None
    except (TypeError, ValueError):
        return None


def _as_vn_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=VN_TZ)
        return parsed.astimezone(VN_TZ)
    except (TypeError, ValueError):
        return None


def _event_type(
    change: float,
    value_30m: float,
    close_position: float,
    imbalance: float | None,
) -> tuple[str, str]:
    if change >= 1.0:
        return ("BREAKOUT_PULSE" if close_position >= 0.82 else "BUYING_SURGE", "UP")
    if change <= -1.0:
        return ("FAILED_OR_SELLING" if close_position <= 0.25 else "SELLING_SURGE", "DOWN")
    if value_30m >= 25:
        if close_position >= 0.58 or (imbalance is not None and imbalance >= 1.5):
            return "ABSORPTION", "UP"
        if close_position <= 0.42 or (imbalance is not None and imbalance <= 0.67):
            return "DISTRIBUTION_PULSE", "DOWN"
        return "VOLUME_SURGE", "NEUTRAL"
    return "SESSION_MOVER", "UP" if change >= 0 else "DOWN"


def compare_snapshots(
    current: Mapping[str, Mapping[str, Any]],
    previous: Mapping[str, Mapping[str, Any]],
    *,
    elapsed_minutes: float,
) -> list[PulseEvent]:
    """Return material cross-sectional anomalies normalized to a 30-minute rate."""

    events: list[PulseEvent] = []
    rate = 30.0 / max(elapsed_minutes, 5.0)
    for symbol, now in current.items():
        before = previous.get(symbol)
        if not isinstance(before, Mapping):
            continue
        price_now = _safe(now.get("close"))
        price_before = _safe(before.get("close"))
        reference = _safe(now.get("reference"))
        if price_now <= 0 or price_before <= 0 or reference <= 0:
            continue
        change = (price_now / price_before - 1.0) * 100.0
        if abs(change) > 20:
            continue
        session_change = (price_now / reference - 1.0) * 100.0
        previous_session_change = (price_before / reference - 1.0) * 100.0
        value_delta = max(0.0, _safe(now.get("value")) - _safe(before.get("value")))
        value_30m = value_delta * rate / 1_000_000_000.0
        total_value = _safe(now.get("value")) / 1_000_000_000.0
        high = _safe(now.get("high"), price_now)
        low = _safe(now.get("low"), price_now)
        close_position = (price_now - low) / max(high - low, 1e-9)
        bid_volume = _safe(now.get("bid_volume_1"))
        ask_volume = _safe(now.get("ask_volume_1"))
        imbalance = bid_volume / ask_volume if bid_volume > 0 and ask_volume > 0 else None

        score = 0
        absolute_change = abs(change)
        if absolute_change >= 3.0:
            score += 34
        elif absolute_change >= 2.0:
            score += 27
        elif absolute_change >= 1.2:
            score += 20
        elif absolute_change >= 0.7:
            score += 10
        if value_30m >= 50:
            score += 30
        elif value_30m >= 25:
            score += 24
        elif value_30m >= 10:
            score += 18
        elif value_30m >= 3:
            score += 11
        elif value_30m >= 1:
            score += 6
        crossed_session_band = (
            abs(session_change) >= 3
            and abs(previous_session_change) < 3
        ) or (
            abs(session_change) >= 5
            and abs(previous_session_change) < 5
        )
        if abs(session_change) >= 5:
            score += 13
        elif abs(session_change) >= 3:
            score += 8
        if (change > 0 and close_position >= 0.82) or (change < 0 and close_position <= 0.18):
            score += 8
        if imbalance is not None and ((change >= 0 and imbalance >= 2) or (change < 0 and imbalance <= 0.5)):
            score += 5
        if value_30m >= 25 and absolute_change <= 0.6:
            score += 10

        liquid_enough = value_30m >= 1 or total_value >= 5
        material = bool(
            liquid_enough
            and (
                score >= 38
                or (absolute_change >= 2.5 and value_30m >= 0.5)
                or crossed_session_band
            )
        )
        if not material:
            continue
        event_type, direction = _event_type(change, value_30m, close_position, imbalance)
        reasons: list[str] = []
        if absolute_change >= 0.7:
            reasons.append(f"giá 30p {change:+.2f}%")
        if value_30m >= 1:
            reasons.append(f"GTGD 30p {value_30m:.1f} tỷ")
        if crossed_session_band:
            reasons.append(f"vượt ngưỡng phiên {session_change:+.2f}%")
        if close_position >= 0.82:
            reasons.append("bám đỉnh phiên")
        elif close_position <= 0.18:
            reasons.append("sát đáy phiên")
        events.append(
            PulseEvent(
                symbol=symbol,
                event_type=event_type,
                direction=direction,
                score=min(score, 97),
                price=round(price_now, 2),
                change_30m_pct=round(change, 2),
                session_change_pct=round(session_change, 2),
                value_30m_billion=round(value_30m, 2),
                total_value_billion=round(total_value, 2),
                close_position=round(close_position, 3),
                order_imbalance=round(imbalance, 2) if imbalance is not None else None,
                source=str(now.get("source") or "UNKNOWN"),
                verified=None,
                reasons=reasons[:4],
            )
        )
    return sorted(events, key=lambda item: (item.score, item.value_30m_billion), reverse=True)


def compact_snapshot(board: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """Persist only fields needed by the next comparison to limit Git growth."""

    return {
        symbol: {
            "close": round(_safe(row.get("close")), 4),
            "reference": round(_safe(row.get("reference")), 4),
            "value": round(max(0.0, _safe(row.get("value"))), 2),
        }
        for symbol, row in board.items()
        if _safe(row.get("close")) > 0 and _safe(row.get("reference")) > 0
    }


def verify_events(
    events: list[PulseEvent],
    *,
    limit: int = 20,
) -> list[PulseEvent]:
    """Use VCI as a second-source price check; keep events if validation is unavailable."""

    candidates = [item.symbol for item in events[:limit] if item.source != "VCI"]
    if not candidates:
        return events
    try:
        client = _create_client("VCI")
        verification = _fetch_batch(client, "VCI", candidates)
    except Exception as exc:
        logger.warning("Pulse second-source validation unavailable: %s", exc)
        return events
    output: list[PulseEvent] = []
    for event in events:
        row = verification.get(event.symbol)
        if row is None:
            output.append(event)
            continue
        second_price = _safe(row.get("close"))
        difference = abs(second_price / max(event.price, 1e-9) - 1.0) * 100.0
        data = asdict(event)
        data["verified"] = difference <= 0.8
        if difference > 0.8:
            data["event_type"] = "SOURCE_MISMATCH"
            data["direction"] = "NEUTRAL"
            data["score"] = max(1, int(event.score) - 25)
            data["reasons"] = [*event.reasons[:3], f"lệch nguồn {difference:.1f}%"]
        output.append(PulseEvent(**data))
    return sorted(output, key=lambda item: (item.score, item.value_30m_billion), reverse=True)


def fetch_vnindex_snapshot() -> dict[str, Any] | None:
    try:
        import fetcher

        end = (datetime.now(VN_TZ) + timedelta(days=1)).date().isoformat()
        start = (datetime.now(VN_TZ) - timedelta(days=12)).date().isoformat()
        for source in ("KBS", "VCI"):
            try:
                frame = fetcher.fetch_source_history(source, "VNINDEX", start, end)
                if frame is None or frame.empty:
                    continue
                row = frame.iloc[-1]
                close = _safe(row.get("close"))
                if close <= 0:
                    continue
                return {
                    "symbol": "VNINDEX",
                    "timestamp": _timestamp(row.get("time")),
                    "source": source,
                    "close": round(close, 2),
                    "open": round(_safe(row.get("open"), close), 2),
                    "high": round(_safe(row.get("high"), close), 2),
                    "low": round(_safe(row.get("low"), close), 2),
                }
            except Exception as exc:
                logger.warning("Pulse VNINDEX %s failed: %s", source, exc)
    except Exception as exc:
        logger.warning("Pulse VNINDEX fetch unavailable: %s", exc)
    latest = _read_json(DATA_DIR / "session_alerts_latest.json", {})
    market = latest.get("market") if isinstance(latest, dict) else None
    if isinstance(market, dict) and _safe(market.get("close")) > 0:
        return {
            "symbol": "VNINDEX",
            "timestamp": latest.get("updated_at"),
            "source": "LAST_SESSION_REPORT",
            "close": _safe(market.get("close")),
            "open": _safe(market.get("close")),
            "high": _safe(market.get("close")),
            "low": _safe(market.get("close")),
        }
    return None


def pulse_posture(market: Mapping[str, Any] | None, events: list[PulseEvent]) -> str:
    up = sum(item.direction == "UP" for item in events)
    down = sum(item.direction == "DOWN" for item in events)
    session_change = 0.0
    if market:
        close = _safe(market.get("close"))
        open_price = _safe(market.get("open"), close)
        if close > 0 and open_price > 0:
            session_change = (close / open_price - 1.0) * 100.0
    if session_change <= -1.0 or down >= max(3, up * 2):
        return "CẨN TRỌNG: ưu tiên bảo vệ vị thế, không mua đuổi"
    if session_change >= 0.7 and (not events or up >= max(2, down)):
        return "TÍCH CỰC CÓ CHỌN LỌC: chỉ theo mã mạnh và có xác nhận"
    return "TRUNG TÍNH: tiếp tục quét, hành động nhỏ và chờ 5 luồng xác nhận"


def _event_line(item: PulseEvent) -> str:
    verification = "2 nguồn" if item.verified is True else "lệch nguồn" if item.verified is False else "1 nguồn"
    event_name = item.event_type.replace("_", " ")
    return (
        f"`{item.symbol}` P{item.score}/97 | {event_name} | Giá {item.price:.2f} | "
        f"30p {item.change_30m_pct:+.2f}% | Phiên {item.session_change_pct:+.2f}% | "
        f"GTGD30 {item.value_30m_billion:.1f} tỷ | {verification}"
    )


def build_report(
    *,
    generated_at: datetime,
    board: Mapping[str, Mapping[str, Any]],
    events: list[PulseEvent],
    market: Mapping[str, Any] | None,
    portfolio_symbols: list[str],
    fetch_meta: Mapping[str, Any],
    elapsed_minutes: float | None,
) -> str:
    elapsed_text = "gieo baseline" if elapsed_minutes is None else f"so với {elapsed_minutes:.0f} phút trước"
    source_bits = ", ".join(
        f"{source} {count}" for source, count in (fetch_meta.get("source_counts") or {}).items() if count
    ) or "không có nguồn"
    lines = [
        f"*THIEUCUBU PULSE 30P* `{generated_at.strftime('%d/%m %H:%M')}`",
        f"Quét {len(board)}/{int(fetch_meta.get('requested') or 0)} mã | {elapsed_text} | {source_bits}",
    ]
    if market:
        close = _safe(market.get("close"))
        open_price = _safe(market.get("open"), close)
        session_change = (close / max(open_price, 1e-9) - 1.0) * 100.0
        lines.append(
            f"*VNINDEX* {close:,.1f} | Phiên {session_change:+.2f}% | {pulse_posture(market, events)}"
        )
    else:
        lines.append("*VNINDEX* chưa lấy được dữ liệu mới; Pulse vẫn tiếp tục quét cổ phiếu.")

    up = [item for item in events if item.direction == "UP"]
    down = [item for item in events if item.direction == "DOWN"]
    neutral = [item for item in events if item.direction == "NEUTRAL"]
    lines += [
        "",
        "*MÃ ĐỘT BIẾN TÓM TẮT*",
        "Tăng: " + (", ".join(f"`{item.symbol}` {item.score}" for item in up[:15]) or "không có"),
        "Giảm: " + (", ".join(f"`{item.symbol}` {item.score}" for item in down[:15]) or "không có"),
        "Dòng tiền: " + (", ".join(f"`{item.symbol}` {item.score}" for item in neutral[:10]) or "không có"),
    ]
    if elapsed_minutes is None:
        lines += ["", "Đã tạo baseline đầu buổi; lượt Pulse kế tiếp mới tính biến động 30 phút."]
    elif events:
        lines += ["", "*CHI TIẾT ĐÁNG CHÚ Ý*"]
        lines += [_event_line(item) for item in events[:20]]
    else:
        lines += ["", "Không có mã vượt ngưỡng đột biến; hệ thống vẫn lưu snapshot cho lượt sau."]

    lines += ["", "*PORTFOLIO PULSE*", "Mã: " + (", ".join(f"`{item}`" for item in portfolio_symbols) or "chưa có")]
    for symbol in portfolio_symbols:
        row = board.get(symbol)
        if not row:
            lines.append(f"`{symbol}` NO_DATA")
            continue
        session_change = (_safe(row.get("close")) / max(_safe(row.get("reference")), 1e-9) - 1) * 100
        lines.append(
            f"`{symbol}` Giá {_safe(row.get('close')):.2f} | Phiên {session_change:+.2f}% | "
            f"GTGD {_safe(row.get('value')) / 1_000_000_000:.1f} tỷ | {row.get('source', 'UNKNOWN')}"
        )
    lines += ["", "Pulse chỉ phát hiện bất thường; quyết định Lướt/Cầm/Gom chốt tại báo cáo 5 luồng."]
    return "\n".join(lines)


def in_pulse_window(now: datetime | None = None) -> bool:
    current = (now or datetime.now(VN_TZ)).astimezone(VN_TZ).time()
    # Wider edges tolerate GitHub Actions queue delays while still excluding
    # unrelated night/weekend dispatches.
    return dt_time(8, 55) <= current <= dt_time(12, 0) or dt_time(12, 55) <= current <= dt_time(15, 0)


async def send_report(text: str) -> None:
    import scan

    await scan.send_chunks("*THIEUCUBU PULSE 30P*", text)


def print_report(text: str) -> None:
    """Print Vietnamese safely even when Windows inherited a legacy code page."""

    try:
        print(text)
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            print(text.encode("ascii", errors="replace").decode("ascii"))
        else:
            buffer.write((text + "\n").encode("utf-8"))
            buffer.flush()


def run(*, force: bool = False, notify: bool = True) -> dict[str, Any]:
    now = datetime.now(VN_TZ)
    market_day = market_calendar.get_market_day_status(now.date())
    if market_day.closed and not force:
        logger.info("Pulse skipped: %s", market_day.reason)
        return {"status": "skipped", "reason": market_day.reason}
    if not in_pulse_window(now) and not force:
        logger.info("Pulse skipped outside intraday window: %s", now.isoformat())
        return {"status": "skipped", "reason": "outside_window"}

    state = _read_json(STATE_PATH, {})
    previous_date = str(state.get("trading_date") or "") if isinstance(state, dict) else ""
    elapsed = _elapsed_minutes(state.get("updated_at") if isinstance(state, dict) else None, now)
    minimum_interval = max(5, _integer(os.getenv("PULSE_MIN_INTERVAL_MINUTES", "20"), 20))
    if previous_date == now.date().isoformat() and elapsed is not None and elapsed < minimum_interval and not force:
        logger.info("Pulse duplicate skipped: previous snapshot is only %.1f minutes old", elapsed)
        return {"status": "skipped", "reason": "duplicate", "elapsed_minutes": round(elapsed, 2)}

    symbols = load_market_symbols()
    sources = tuple(
        item for item in (value.strip().upper() for value in os.getenv("PULSE_BOARD_SOURCES", "KBS,VCI").split(","))
        if item in {"KBS", "VCI"}
    ) or ("KBS", "VCI")
    board, fetch_meta = fetch_market_board(
        symbols,
        sources=sources,
        batch_size=max(20, _integer(os.getenv("PULSE_BATCH_SIZE", "100"), 100)),
    )
    previous = state.get("latest") if isinstance(state, dict) else None
    previous = previous if isinstance(previous, dict) else {}
    previous_at = _as_vn_datetime(state.get("updated_at") if isinstance(state, dict) else None)
    afternoon_reset = bool(
        previous_at
        and previous_at.time() < dt_time(12, 0)
        and now.time() >= dt_time(12, 55)
    )
    if not previous or previous_date != now.date().isoformat() or elapsed is None or elapsed > 95 or afternoon_reset:
        elapsed = None
    events = compare_snapshots(board, previous, elapsed_minutes=elapsed) if elapsed is not None else []
    events = verify_events(events)
    market = fetch_vnindex_snapshot()
    portfolio_symbols = load_watch_symbols()
    report = build_report(
        generated_at=now,
        board=board,
        events=events,
        market=market,
        portfolio_symbols=portfolio_symbols,
        fetch_meta=fetch_meta,
        elapsed_minutes=elapsed,
    )
    event_rows = [asdict(item) for item in events]
    latest_payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now.isoformat(timespec="seconds"),
        "trading_date": now.date().isoformat(),
        "status": "ok" if board else "degraded",
        "universe_count": len(symbols),
        "snapshot_count": len(board),
        "elapsed_minutes": round(elapsed, 2) if elapsed is not None else None,
        "fetch": fetch_meta,
        "market": market,
        "portfolio_symbols": portfolio_symbols,
        "top_symbols": [item.symbol for item in events[:30]],
        "events": event_rows,
    }
    history = state.get("events", []) if isinstance(state, dict) else []
    if not isinstance(history, list):
        history = []
    state_payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now.isoformat(timespec="seconds"),
        "trading_date": now.date().isoformat(),
        "latest": compact_snapshot(board),
        "events": (history + [{"updated_at": latest_payload["updated_at"], "items": event_rows}])[-12:],
    }
    # A total source outage must not replace the last healthy baseline. The
    # next successful Pulse can then normalize over the real elapsed time.
    if board:
        _write_json(STATE_PATH, state_payload)
    _write_json(LATEST_PATH, latest_payload)
    if notify:
        asyncio.run(send_report(report))
    else:
        print_report(report)
    return latest_payload


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    parser = argparse.ArgumentParser(description="THIEUCUBU 30-minute anomaly pulse")
    parser.add_argument("--force", action="store_true", help="Run outside trading window for diagnostics")
    parser.add_argument("--no-notify", action="store_true", help="Print instead of sending Telegram")
    args = parser.parse_args()
    result = run(force=args.force, notify=not args.no_notify)
    return 0 if result.get("status") != "degraded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
