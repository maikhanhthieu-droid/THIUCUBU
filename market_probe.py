#!/usr/bin/env python3
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import scan

VN_TZ = timezone(timedelta(hours=7))
DATA_DIR = Path("data")
PROBE_STATE_PATH = DATA_DIR / "market_probe_state.json"

DEFAULT_PROBE_SYMBOLS = [
    "VNINDEX",
    "VCB",
    "BID",
    "CTG",
    "TCB",
    "MBB",
    "ACB",
    "VPB",
    "HDB",
    "STB",
    "SSI",
    "VND",
    "HCM",
    "VCI",
    "VIX",
    "HPG",
    "HSG",
    "NKG",
    "VIC",
    "VHM",
    "VRE",
    "FPT",
    "MWG",
    "FRT",
    "PNJ",
    "GAS",
    "PVD",
    "PVS",
    "BSR",
    "DGC",
    "DPM",
    "DCM",
    "DIG",
    "KDH",
    "NLG",
    "VNM",
    "MSN",
    "GMD",
]


@dataclass
class ProbeSnapshot:
    symbol: str
    latest_date: str
    close: float
    volume: float


@dataclass
class MarketProbeResult:
    checked: int
    with_previous: int
    changed: int
    unchanged: int
    old_date: int
    zero_volume: int
    no_data: int
    latest_dates: dict[str, int]
    inactive: bool
    reason: str
    policy: str
    action: str


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, min_value: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(min_value, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float, min_value: float = 0.0, max_value: float = 1.0) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(max_value, max(min_value, value))


def enabled() -> bool:
    return _env_bool("MARKET_ACTIVITY_PROBE_ENABLED", True)


def action() -> str:
    raw = os.getenv("MARKET_ACTIVITY_PROBE_ACTION", "warn").strip().lower()
    if raw in {"warn", "report", "continue", "scan"}:
        return "warn"
    if raw in {"skip", "stop", "halt"}:
        return "skip"
    return "warn"


def sample_size() -> int:
    return _env_int("MARKET_PROBE_SAMPLE_SIZE", 38, min_value=12)


def min_checked() -> int:
    return _env_int("MARKET_PROBE_MIN_CHECKED", 24, min_value=8)


def unchanged_threshold() -> float:
    return _env_float("MARKET_PROBE_UNCHANGED_RATIO", 0.85)


def old_date_threshold() -> float:
    return _env_float("MARKET_PROBE_OLD_DATE_RATIO", 0.80)


def zero_volume_threshold() -> float:
    return _env_float("MARKET_PROBE_ZERO_VOLUME_RATIO", 0.80)


def today_vn() -> date:
    return datetime.now(VN_TZ).date()


def normalize_symbol(value: Any) -> str:
    return str(value or "").replace("`", "").upper().strip()


def load_state(path: Path = PROBE_STATE_PATH) -> dict[str, Any]:
    raw = scan.json_load(path, {})
    return raw if isinstance(raw, dict) else {}


def save_state(
    snapshots: dict[str, ProbeSnapshot],
    result: MarketProbeResult,
    mode: str,
    path: Path = PROBE_STATE_PATH,
) -> None:
    payload = {
        "updated_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        "mode": mode,
        "result": asdict(result),
        "symbols": {symbol: asdict(snapshot) for symbol, snapshot in sorted(snapshots.items())},
    }
    scan.json_save(path, payload, pretty=True)


def previous_snapshots(state: dict[str, Any] | None = None) -> dict[str, ProbeSnapshot]:
    raw_symbols = (state or load_state()).get("symbols", {})
    if not isinstance(raw_symbols, dict):
        return {}
    snapshots: dict[str, ProbeSnapshot] = {}
    for symbol, raw in raw_symbols.items():
        if not isinstance(raw, dict):
            continue
        normalized = normalize_symbol(raw.get("symbol") or symbol)
        if not normalized:
            continue
        snapshots[normalized] = ProbeSnapshot(
            symbol=normalized,
            latest_date=str(raw.get("latest_date") or ""),
            close=_safe_float(raw.get("close")),
            volume=_safe_float(raw.get("volume")),
        )
    return snapshots


def choose_probe_symbols(universe: Iterable[Any], watch_symbols: Iterable[Any] | None = None) -> list[str]:
    limit = sample_size()
    configured = [
        normalize_symbol(item)
        for item in os.getenv("MARKET_PROBE_SYMBOLS", "").split(",")
        if normalize_symbol(item)
    ]
    candidates = configured or DEFAULT_PROBE_SYMBOLS
    result: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        symbol = normalize_symbol(value)
        if not symbol or symbol in seen:
            return
        result.append(symbol)
        seen.add(symbol)

    for symbol in candidates:
        add(symbol)
        if len(result) >= limit:
            return result
    for symbol in watch_symbols or []:
        add(symbol)
        if len(result) >= limit:
            return result
    for symbol in universe:
        add(symbol)
        if len(result) >= limit:
            return result
    return result


def snapshot_from_history(symbol: str, rows: list[dict[str, Any]] | None) -> ProbeSnapshot | None:
    if not rows:
        return None
    last = rows[-1]
    if not isinstance(last, dict):
        return None
    return ProbeSnapshot(
        symbol=normalize_symbol(symbol),
        latest_date=_date_text(last.get("time")),
        close=_safe_float(last.get("close")),
        volume=_safe_float(last.get("volume")),
    )


def snapshots_from_history(history_store: dict[str, Any]) -> dict[str, ProbeSnapshot]:
    snapshots: dict[str, ProbeSnapshot] = {}
    for symbol, rows in history_store.items():
        snapshot = snapshot_from_history(symbol, rows if isinstance(rows, list) else None)
        if snapshot:
            snapshots[snapshot.symbol] = snapshot
    return snapshots


def evaluate_activity(
    snapshots: dict[str, ProbeSnapshot],
    previous: dict[str, ProbeSnapshot] | None = None,
    *,
    today: date | None = None,
    no_data_count: int = 0,
    policy: str = "skip",
    action: str | None = None,
) -> MarketProbeResult:
    previous = previous or {}
    current_day = today or today_vn()
    checked = len(snapshots)
    with_previous = 0
    changed = 0
    unchanged = 0
    old_date = 0
    zero_volume = 0
    latest_dates: dict[str, int] = {}

    for symbol, snapshot in snapshots.items():
        latest_dates[snapshot.latest_date] = latest_dates.get(snapshot.latest_date, 0) + 1
        day = _parse_date(snapshot.latest_date)
        if day is None or day < current_day:
            old_date += 1
        if snapshot.volume <= 0:
            zero_volume += 1
        previous_snapshot = previous.get(symbol)
        if previous_snapshot is None:
            continue
        with_previous += 1
        if _same_snapshot(snapshot, previous_snapshot):
            unchanged += 1
        else:
            changed += 1

    inactive = False
    reasons: list[str] = []
    minimum = min_checked()
    if checked >= minimum:
        if old_date / max(checked, 1) >= old_date_threshold():
            inactive = True
            reasons.append(f"data_date_cu {old_date}/{checked}")
        if zero_volume / max(checked, 1) >= zero_volume_threshold():
            inactive = True
            reasons.append(f"khong_co_volume {zero_volume}/{checked}")
        if with_previous >= max(8, minimum // 2) and unchanged / max(with_previous, 1) >= unchanged_threshold():
            inactive = True
            reasons.append(f"khong_doi {unchanged}/{with_previous}")

    if not reasons:
        if checked < minimum:
            reasons.append(f"chua_du_mau {checked}/{minimum}")
        elif with_previous == 0:
            reasons.append("lan_dau_chua_co_moc_so_sanh")
        else:
            reasons.append(f"market_active changed {changed}/{with_previous}")

    return MarketProbeResult(
        checked=checked,
        with_previous=with_previous,
        changed=changed,
        unchanged=unchanged,
        old_date=old_date,
        zero_volume=zero_volume,
        no_data=no_data_count,
        latest_dates=dict(sorted(latest_dates.items(), key=lambda item: item[0], reverse=True)[:5]),
        inactive=inactive,
        reason="; ".join(reasons),
        policy=policy,
        action=action or globals()["action"](),
    )


def should_stop_for_inactive(result: MarketProbeResult) -> bool:
    return bool(result.inactive and result.action == "skip" and result.policy == "skip")


def report_note(result: MarketProbeResult | None) -> str:
    if result is None:
        return ""
    dates = ", ".join(f"{day}:{count}" for day, count in result.latest_dates.items()) or "unknown"
    if result.inactive:
        return (
            "*DATA STATUS*: DATA CU / THI TRUONG CO THE NGHI / API CHUA CAP NHAT\n"
            f"Probe {result.checked} OK, {result.no_data} no-data | "
            f"khong doi {result.unchanged}/{result.with_previous} | "
            f"date cu {result.old_date}/{result.checked} | vol=0 {result.zero_volume}/{result.checked}\n"
            f"Ngay data: {dates}\n"
            "Xu ly: van quet tiep de he thong ben; tin hieu intraday chi xem tham khao."
        )
    if result.no_data:
        return (
            "*DATA STATUS*: MARKET ACTIVE NHUNG CO MA NO-DATA\n"
            f"Probe {result.checked} OK, {result.no_data} no-data | {result.reason}"
        )
    return ""


def inactive_notice(mode: str, result: MarketProbeResult) -> str:
    dates = ", ".join(f"{day}:{count}" for day, count in result.latest_dates.items()) or "unknown"
    return "\n".join(
        [
            f"*THIEUCUBU MARKET CHECK* `{datetime.now(VN_TZ).strftime('%d/%m %H:%M')}`",
            f"Mode: `{mode}`",
            f"Ket luan: thi truong nghi / API chua cap nhat / data dang dung.",
            f"Kiem tra: {result.checked} ma OK, {result.no_data} no-data.",
            f"Khong doi: {result.unchanged}/{result.with_previous} | Date cu: {result.old_date}/{result.checked} | Vol=0: {result.zero_volume}/{result.checked}",
            f"Ngay data: {dates}",
            f"Ly do: {result.reason}",
            "Xu ly: dung theo MARKET_ACTIVITY_PROBE_ACTION=skip; watchdog se coi day la report hop le cua phien.",
        ]
    )


def inactive_alert_payload(mode: str, result: MarketProbeResult) -> dict[str, Any]:
    return {
        "updated_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        "mode": mode,
        "market_activity": asdict(result),
        "market_closed": {
            "date": today_vn().isoformat(),
            "closed": True,
            "reason": "Market data inactive/stale by activity probe",
            "policy": result.policy,
        },
        "focus_symbols": [],
        "portfolio_symbols": [],
        "market": None,
        "top": [],
    }


def _same_snapshot(current: ProbeSnapshot, previous: ProbeSnapshot) -> bool:
    close_tolerance = max(abs(previous.close) * 0.0001, 0.01)
    return (
        current.latest_date == previous.latest_date
        and abs(current.close - previous.close) <= close_tolerance
        and int(current.volume) == int(previous.volume)
    )


def _date_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return raw[:10]


def _parse_date(value: Any) -> date | None:
    raw = _date_text(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except Exception:
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
