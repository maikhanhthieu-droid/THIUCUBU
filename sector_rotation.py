"""Sector relative-strength heatmap with a persistent four-state machine.

The old implementation only compared today's scanner score with an arbitrary
snapshot several days earlier.  This module measures each sector against
VNINDEX over 1W/1M/3M, combines participation and scanner quality, then keeps
state transitions stable across repeated EOD runs.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

import scan


DATA_DIR = scan.DATA_DIR
ROTATION_PATH = DATA_DIR / "sector_rotation_history.json"
SCHEMA_VERSION = "thieucubu.sector_rotation.v2"
STATE_LABELS = {
    "LEADING": "DẪN DẮT",
    "ENTERING": "ĐANG VÀO",
    "EXITING": "ĐANG RA",
    "LAGGING": "TỤT HẬU",
}
STATE_PRIORITY = {"LEADING": 0, "ENTERING": 1, "EXITING": 2, "LAGGING": 3}


def _safe(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return default if not math.isfinite(number) else number
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 97.0) -> float:
    return max(low, min(high, value))


def _frame(rows: Any) -> pd.DataFrame | None:
    if not isinstance(rows, list) or not rows:
        return None
    try:
        frame = pd.DataFrame(rows)
    except Exception:
        return None
    if not {"time", "close"}.issubset(frame.columns):
        return None
    frame = frame.copy()
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    try:
        frame["time"] = frame["time"].dt.tz_localize(None)
    except TypeError:
        frame["time"] = frame["time"].dt.tz_convert(None)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["time", "close"])
    frame = frame[frame["close"] > 0]
    frame = frame.sort_values("time").drop_duplicates("time", keep="last")
    return frame.reset_index(drop=True) if len(frame) >= 2 else None


def _relative_return(
    frame: pd.DataFrame | None,
    index_frame: pd.DataFrame | None,
    period: int,
) -> float | None:
    if frame is None or index_frame is None:
        return None
    stock = frame.set_index(frame["time"].dt.normalize())["close"].rename("stock")
    index = index_frame.set_index(index_frame["time"].dt.normalize())["close"].rename("index")
    joined = pd.concat([stock, index], axis=1).dropna().tail(period + 1)
    if len(joined) < max(4, int(period * 0.65)):
        return None
    stock_return = joined["stock"].iloc[-1] / joined["stock"].iloc[0] - 1.0
    index_return = joined["index"].iloc[-1] / joined["index"].iloc[0] - 1.0
    return round((stock_return - index_return) * 100.0, 2)


def _median(values: list[float]) -> float | None:
    clean = [value for value in values if math.isfinite(value)]
    return round(float(np.median(clean)), 2) if clean else None


def _pct(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100.0, 2) if denominator else None


def _load_payload(path: Path) -> dict[str, Any]:
    raw = scan.json_load(path, {})
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, list):
        return {}

    # Backward-compatible migration from the original list-of-snapshots file.
    current: dict[str, dict[str, Any]] = {}
    history: list[dict[str, Any]] = []
    for item in raw[-160:]:
        if not isinstance(item, dict):
            continue
        sectors = [row for row in item.get("sectors", []) if isinstance(row, dict)]
        history.append(
            {
                "updated_at": item.get("updated_at"),
                "trading_date": str(item.get("updated_at") or "")[:10],
                "sectors": sectors,
            }
        )
        for row in sectors:
            sector = str(row.get("sector") or "")
            if sector:
                current[sector] = dict(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "current": current,
        "history": history,
        "transitions": [],
    }


def _trading_date(results: list[scan.ScanResult]) -> str:
    dates = [str(item.as_of)[:10] for item in results if getattr(item, "as_of", None)]
    return max(dates) if dates else datetime.now(scan.VN_TZ).date().isoformat()


def analyze_sectors(
    results: list[scan.ScanResult],
    history_store: Mapping[str, Any] | None = None,
    index_frame: pd.DataFrame | None = None,
) -> dict[str, dict[str, Any]]:
    """Build sector snapshots from only the symbols that actually scanned."""

    history_store = history_store or {}
    if index_frame is None:
        index_frame = _frame(history_store.get("VNINDEX"))
    grouped: dict[str, list[scan.ScanResult]] = {}
    for result in results:
        if result.symbol != "VNINDEX":
            grouped.setdefault(str(result.sector or "Other"), []).append(result)

    snapshots: dict[str, dict[str, Any]] = {}
    for sector, rows in grouped.items():
        rs_1w: list[float] = []
        rs_1m: list[float] = []
        rs_3m: list[float] = []
        above20 = above50 = eligible20 = eligible50 = 0
        for result in rows:
            frame = _frame(history_store.get(result.symbol))
            for period, target in ((5, rs_1w), (20, rs_1m), (60, rs_3m)):
                value = _relative_return(frame, index_frame, period)
                if value is not None:
                    target.append(value)
            if frame is None:
                continue
            close = frame["close"]
            if len(close) >= 20:
                eligible20 += 1
                above20 += int(close.iloc[-1] > close.tail(20).mean())
            if len(close) >= 50:
                eligible50 += 1
                above50 += int(close.iloc[-1] > close.tail(50).mean())

        avg_win = sum(item.win_score for item in rows) / max(len(rows), 1)
        flow = sum(item.obv_up and item.mfi >= 50 for item in rows)
        near = sum(item.near_break for item in rows)
        failed = sum(item.failed_break for item in rows)
        rs5 = _median(rs_1w)
        rs20 = _median(rs_1m)
        rs60 = _median(rs_3m)
        pct20 = _pct(above20, eligible20)
        pct50 = _pct(above50, eligible50)
        rs_signal = _clamp(
            50
            + _safe(rs5) * 2.0
            + _safe(rs20) * 1.25
            + _safe(rs60) * 0.55
        )
        participation = (
            _safe(pct20, 50.0) * 0.55 + _safe(pct50, 50.0) * 0.45
        )
        flow_ratio = flow / max(len(rows), 1)
        near_ratio = near / max(len(rows), 1)
        failed_ratio = failed / max(len(rows), 1)
        raw_score = int(
            round(
                _clamp(
                    rs_signal * 0.38
                    + participation * 0.25
                    + avg_win * 0.27
                    + flow_ratio * 8
                    + near_ratio * 4
                    - failed_ratio * 15
                )
            )
        )
        history_sample = max(len(rs_1w), len(rs_1m), len(rs_3m))
        confidence = int(
            _clamp(
                30
                + min(len(rows) / 6.0, 1.0) * 35
                + min(history_sample / 6.0, 1.0) * 35,
                0,
                95,
            )
        )
        score = int(round(50 + (raw_score - 50) * confidence / 100.0))
        snapshots[sector] = {
            "sector": sector,
            "score": score,
            "raw_score": raw_score,
            "confidence": confidence,
            "avg_win_score": round(avg_win, 1),
            "rs_1w_pct": rs5,
            "rs_1m_pct": rs20,
            "rs_3m_pct": rs60,
            "pct_above_ma20": pct20,
            "pct_above_ma50": pct50,
            "flow_count": flow,
            "near_break_count": near,
            "failed_count": failed,
            "failed_ratio": round(failed_ratio, 3),
            "history_sample": history_sample,
            "count": len(rows),
        }

    ranked = sorted(snapshots.values(), key=lambda item: item["score"], reverse=True)
    total = max(len(ranked), 1)
    for rank, snapshot in enumerate(ranked, start=1):
        snapshot["rank"] = rank
        snapshot["rank_percentile"] = round((total - rank + 1) / total * 100.0, 1)
    return snapshots


def _candidate_state(snapshot: Mapping[str, Any], previous: Mapping[str, Any]) -> str:
    score = int(_safe(snapshot.get("score"), 50))
    rs5 = _safe(snapshot.get("rs_1w_pct"))
    rs20 = _safe(snapshot.get("rs_1m_pct"))
    rs60 = _safe(snapshot.get("rs_3m_pct"))
    above50 = _safe(snapshot.get("pct_above_ma50"), 50.0)
    percentile = _safe(snapshot.get("rank_percentile"), 50.0)
    failed = _safe(snapshot.get("failed_ratio"))
    confidence = int(_safe(snapshot.get("confidence")))
    previous_score = _safe(previous.get("score"), score)
    delta = score - previous_score

    if confidence < 55:
        return "ENTERING" if rs5 >= 0 else "EXITING"
    if score <= 37 or failed >= 0.45 or (rs20 <= -3.0 and rs60 <= -4.0):
        return "LAGGING"
    if (
        score >= 63
        and percentile >= 70
        and rs20 > 0
        and rs60 >= -1.0
        and above50 >= 48
    ):
        return "LEADING"
    if (rs5 >= 0.8 and rs20 >= -1.5 and score >= 50) or delta >= 7:
        return "ENTERING"
    if (rs5 <= -0.8 and (rs20 <= 0 or score < 55)) or delta <= -7:
        return "EXITING"
    if str(previous.get("state") or "") in STATE_LABELS:
        return str(previous["state"])
    return "ENTERING" if rs20 >= 0 else "EXITING"


def _resolve_state(
    previous: Mapping[str, Any],
    candidate: str,
    snapshot: Mapping[str, Any],
    *,
    new_trading_day: bool,
) -> tuple[str, str | None, int]:
    prior = str(previous.get("state") or "")
    if prior not in STATE_LABELS:
        return candidate, None, 0
    if candidate == prior:
        return prior, None, 0

    severe_lag = bool(
        candidate == "LAGGING"
        and int(_safe(snapshot.get("confidence"))) >= 60
        and (
            int(_safe(snapshot.get("score"), 50)) <= 28
            or _safe(snapshot.get("failed_ratio")) >= 0.60
        )
    )
    if severe_lag:
        return candidate, None, 0

    pending = str(previous.get("pending_state") or "")
    count = int(_safe(previous.get("pending_count")))
    if pending != candidate:
        pending, count = candidate, 1
    elif new_trading_day:
        count += 1
    if count >= 2:
        return candidate, None, 0
    return prior, pending, count


def update_sector_rotation(
    results: list[scan.ScanResult],
    *,
    history_store: Mapping[str, Any] | None = None,
    index_frame: pd.DataFrame | None = None,
    persist: bool = True,
    path: Path = ROTATION_PATH,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Resolve sector states and optionally persist one compact EOD snapshot."""

    payload = _load_payload(path)
    previous_current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    trading_date = _trading_date(results)
    previous_date = str(payload.get("trading_date") or "")
    new_trading_day = bool(previous_date and previous_date != trading_date)
    current = analyze_sectors(results, history_store, index_frame)
    alerts: list[str] = []
    transitions = payload.get("transitions") if isinstance(payload.get("transitions"), list) else []
    now = datetime.now(scan.VN_TZ).isoformat(timespec="seconds")

    for sector, snapshot in current.items():
        previous = previous_current.get(sector, {})
        previous = previous if isinstance(previous, dict) else {}
        candidate = _candidate_state(snapshot, previous)
        resolved, pending, pending_count = _resolve_state(
            previous,
            candidate,
            snapshot,
            new_trading_day=new_trading_day,
        )
        prior_state = str(previous.get("state") or "")
        snapshot["candidate_state"] = candidate
        snapshot["state"] = resolved
        snapshot["state_label"] = STATE_LABELS[resolved]
        snapshot["pending_state"] = pending
        snapshot["pending_count"] = pending_count
        snapshot["score_delta"] = int(snapshot["score"] - _safe(previous.get("score"), snapshot["score"]))
        if prior_state in STATE_LABELS and resolved != prior_state:
            transition = {
                "updated_at": now,
                "trading_date": trading_date,
                "sector": sector,
                "from": prior_state,
                "to": resolved,
                "score": snapshot["score"],
                "rs_1m_pct": snapshot.get("rs_1m_pct"),
            }
            transitions.append(transition)
            alerts.append(
                f"`{sector}` {STATE_LABELS[prior_state]} → {STATE_LABELS[resolved]} "
                f"| {snapshot['score']}/97 | RS1M {_safe(snapshot.get('rs_1m_pct')):+.1f}%"
            )

    if persist:
        history = payload.get("history") if isinstance(payload.get("history"), list) else []
        history = [item for item in history if item.get("trading_date") != trading_date]
        compact = []
        for sector in sorted(current):
            row = current[sector]
            compact.append(
                {
                    key: row.get(key)
                    for key in (
                        "sector",
                        "score",
                        "rank",
                        "state",
                        "rs_1w_pct",
                        "rs_1m_pct",
                        "rs_3m_pct",
                        "pct_above_ma20",
                        "pct_above_ma50",
                        "count",
                    )
                }
            )
        history.append(
            {
                "updated_at": now,
                "trading_date": trading_date,
                "sectors": compact,
            }
        )
        output = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": now,
            "trading_date": trading_date,
            "current": current,
            "history": history[-180:],
            "transitions": transitions[-180:],
        }
        scan.json_save(path, output, pretty=False)
    return current, alerts


def format_heatmap(current: Mapping[str, Mapping[str, Any]] | None) -> list[str]:
    if not current:
        return ["Chưa đủ dữ liệu luân chuyển ngành."]
    lines: list[str] = []
    for state in sorted(STATE_LABELS, key=STATE_PRIORITY.get):
        rows = [item for item in current.values() if item.get("state") == state]
        rows.sort(key=lambda item: int(item.get("score") or 0), reverse=True)
        if not rows:
            continue
        details = ", ".join(
            f"{item.get('sector')} {int(item.get('score') or 0)} "
            f"(W/M/Q {_safe(item.get('rs_1w_pct')):+.1f}/"
            f"{_safe(item.get('rs_1m_pct')):+.1f}/"
            f"{_safe(item.get('rs_3m_pct')):+.1f}%)"
            for item in rows[:6]
        )
        lines.append(f"*{STATE_LABELS[state]}*: {details}")
    return lines or ["Chưa đủ dữ liệu luân chuyển ngành."]


def sector_scores(results: list[scan.ScanResult]) -> dict[str, dict[str, Any]]:
    """Compatibility wrapper for older callers."""

    return analyze_sectors(results)
