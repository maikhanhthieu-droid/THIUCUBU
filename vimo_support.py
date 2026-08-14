#!/usr/bin/env python3
"""Run optional VIMO confirmation after a successful core scan.

The command intentionally exits successfully when VIMO is unavailable.  It
never changes the THIUCUBU score and only sends a compact, change-only support
note to the owner's Telegram.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

import scan
import vimo_provider


logger = logging.getLogger("thieucutoo.vimo_support")
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
STATE_PATH = Path(os.getenv("VIMO_SUPPORT_STATE_FILE", str(DATA_DIR / "cache/vimo/support_state.json")))
POSITIVE_SIGNALS = {"BUY", "STRONG_BUY", "ACCUMULATE", "OUTPERFORM"}
NEGATIVE_SIGNALS = {"SELL", "STRONG_SELL", "REDUCE", "UNDERPERFORM"}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _symbol(value: Any) -> str:
    return str(value or "").replace("`", "").upper().strip()


def _daily_candidates(limit: int) -> list[dict[str, Any]]:
    payload = _load_json(DATA_DIR / "session_alerts_latest.json", {})
    if not isinstance(payload, Mapping):
        return []
    advanced = payload.get("advanced_top") if isinstance(payload.get("advanced_top"), Mapping) else {}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload.get("top", []):
        if not isinstance(item, Mapping):
            continue
        symbol = _symbol(item.get("symbol"))
        if not symbol or symbol == "VNINDEX" or symbol in seen:
            continue
        metric = advanced.get(symbol) if isinstance(advanced.get(symbol), Mapping) else {}
        structure = metric.get("market_structure") if isinstance(metric.get("market_structure"), Mapping) else {}
        score = _number(metric.get("advanced_score"), _number(item.get("win_score")))
        selected.append(
            {
                "symbol": symbol,
                "local_score": int(round(score)),
                "local_price": _number(item.get("close")),
                "failed_break": bool(item.get("failed_break")),
                "market_state": str(structure.get("overall_state") or item.get("market_state") or "").upper(),
            }
        )
        seen.add(symbol)
        if len(selected) >= limit:
            break
    return selected


def _weekend_candidates(limit: int) -> list[dict[str, Any]]:
    payload = _load_json(DATA_DIR / "weekend_opportunities_latest.json", {})
    if not isinstance(payload, Mapping):
        return []
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    rows = list(payload.get("convictions", [])) + list(payload.get("top", []))
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        symbol = _symbol(item.get("symbol"))
        if not symbol or symbol in seen:
            continue
        selected.append(
            {
                "symbol": symbol,
                "local_score": int(round(_number(item.get("opportunity_score")))),
                "local_price": _number(item.get("close")),
                "action": str(item.get("action") or "THEO_DOI"),
                "selected": bool(item.get("selected")),
            }
        )
        seen.add(symbol)
        if len(selected) >= limit:
            break
    return selected


def _fetch_parallel(
    rows: list[dict[str, Any]],
    fetch: Callable[[str], dict[str, Any]],
    workers: int,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    output: list[tuple[dict[str, Any], dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(rows) or 1))) as executor:
        futures = {executor.submit(fetch, row["symbol"]): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            try:
                output.append((row, future.result()))
            except Exception as exc:
                logger.warning("[VIMO] %s skipped: %s", row["symbol"], type(exc).__name__)
    order = {row["symbol"]: index for index, row in enumerate(rows)}
    return sorted(output, key=lambda pair: order[pair[0]["symbol"]])


def _is_recent(value: str, max_days: int = 7) -> bool:
    if not value:
        return True
    try:
        observed = date.fromisoformat(value[:10])
    except ValueError:
        return True
    return observed >= datetime.now(scan.VN_TZ).date() - timedelta(days=max_days)


def _daily_verdict(local: Mapping[str, Any], vimo: Mapping[str, Any]) -> dict[str, Any]:
    signal = str(vimo.get("signal") or "NEUTRAL").upper()
    confidence = int(round(_number(vimo.get("confidence"))))
    local_score = int(local["local_score"])
    local_negative = bool(local.get("failed_break")) or local.get("market_state") == "DISTRIBUTION" or local_score < 45
    local_positive = local_score >= 72 and not local_negative
    vimo_positive = signal in POSITIVE_SIGNALS
    vimo_negative = signal in NEGATIVE_SIGNALS
    local_price = _number(local.get("local_price"))
    vimo_price = _number(vimo.get("price"))
    price_gap = abs(vimo_price - local_price) / local_price * 100 if local_price > 0 and vimo_price > 0 else 0.0
    data_ok = _is_recent(str(vimo.get("date") or "")) and price_gap <= 10.0
    if not data_ok:
        status = "DATA_MISMATCH"
    elif confidence < 55 or (not vimo_positive and not vimo_negative):
        status = "NEUTRAL"
    elif (local_positive and vimo_positive) or (local_negative and vimo_negative):
        status = "SUPPORT"
    elif (local_positive and vimo_negative) or (local_negative and vimo_positive):
        status = "CONFLICT"
    else:
        status = "NEUTRAL"
    return {
        "symbol": local["symbol"],
        "local_score": local_score,
        "signal": signal,
        "confidence": confidence,
        "status": status,
        "price_gap_pct": round(price_gap, 1),
        "date": vimo.get("date"),
    }


def _weekend_verdict(local: Mapping[str, Any], vimo: Mapping[str, Any]) -> dict[str, Any]:
    average = _number(vimo.get("strategy_average"), -1.0)
    local_score = int(local["local_score"])
    if average < 0:
        status = "NO_DATA"
    elif local_score >= 76 and average >= 60:
        status = "SUPPORT"
    elif local_score >= 76 and average < 45:
        status = "CONFLICT"
    else:
        status = "NEUTRAL"
    return {
        "symbol": local["symbol"],
        "local_score": local_score,
        "strategy_average": round(average, 1) if average >= 0 else None,
        "buy_count": int(vimo.get("buy_count") or 0),
        "strategy_count": int(vimo.get("strategy_count") or 0),
        "status": status,
        "year": vimo.get("year"),
    }


def _changed(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> bool:
    if not previous:
        return True
    if current.get("status") != previous.get("status") or current.get("signal") != previous.get("signal"):
        return True
    return abs(_number(current.get("local_score")) - _number(previous.get("local_score"))) >= 7


def _daily_line(item: Mapping[str, Any]) -> str:
    labels = {"SUPPORT": "XÁC NHẬN", "CONFLICT": "MÂU THUẪN", "DATA_MISMATCH": "LỆCH DỮ LIỆU"}
    suffix = f" | lệch giá {item['price_gap_pct']:.1f}%" if item["status"] == "DATA_MISMATCH" else ""
    return (
        f"`{item['symbol']}` THIUCUBU {item['local_score']} | VIMO {item['signal']} "
        f"{item['confidence']}% | {labels.get(item['status'], item['status'])}{suffix}"
    )


def _weekend_line(item: Mapping[str, Any]) -> str:
    labels = {"SUPPORT": "ỦNG HỘ", "CONFLICT": "MÂU THUẪN", "NEUTRAL": "TRUNG TÍNH"}
    return (
        f"`{item['symbol']}` THIUCUBU {item['local_score']} | VIMO chiến lược "
        f"{item.get('strategy_average', 'n/a')}/100 ({item['buy_count']}/{item['strategy_count']} Buy) "
        f"| {labels.get(item['status'], item['status'])}"
    )


async def _send(title: str, lines: list[str]) -> bool:
    report = "\n".join(
        [
            f"*{title}*",
            "Nguồn phụ độc lập; không thay đổi Score THIUCUBU và không phải khuyến nghị mua bán.",
            *lines,
            "Nguồn xác nhận: VIMO Financial Intelligence.",
        ]
    )
    return bool(await scan.send_chunks(title, report))


def run(mode: str) -> int:
    if not vimo_provider.is_configured():
        logger.info("VIMO_API_KEY is absent; optional support flow skipped")
        return 0
    state = _load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    previous = state.get(mode) if isinstance(state.get(mode), Mapping) else {}
    workers = max(1, int(os.getenv("VIMO_MAX_WORKERS", "2")))
    if mode == "daily":
        limit = max(1, int(os.getenv("VIMO_DAILY_MAX_SYMBOLS", "8")))
        rows = _daily_candidates(limit)
        pairs = _fetch_parallel(rows, vimo_provider.fetch_ta_signal, workers)
        verdicts = [_daily_verdict(local, vimo) for local, vimo in pairs]
        significant = [item for item in verdicts if item["status"] in {"SUPPORT", "CONFLICT", "DATA_MISMATCH"}]
        lines = [_daily_line(item) for item in significant if _changed(item, previous.get(item["symbol"]))]
        title = "THIEUCUBU VIMO XÁC NHẬN"
    else:
        limit = max(1, int(os.getenv("VIMO_WEEKEND_MAX_SYMBOLS", "12")))
        rows = _weekend_candidates(limit)
        pairs = _fetch_parallel(rows, vimo_provider.fetch_bctc_support, workers)
        verdicts = [_weekend_verdict(local, vimo) for local, vimo in pairs]
        significant = [item for item in verdicts if item["status"] in {"SUPPORT", "CONFLICT"}]
        lines = [_weekend_line(item) for item in significant if _changed(item, previous.get(item["symbol"]))]
        title = "THIEUCUBU VIMO CUỐI TUẦN"
    state[mode] = {item["symbol"]: item for item in verdicts}
    state["health"] = vimo_provider.health_dict()
    state["updated_at"] = datetime.now(scan.VN_TZ).isoformat(timespec="seconds")
    _save_json(STATE_PATH, state)
    if not lines:
        logger.info("VIMO %s completed; no new or materially changed confirmations", mode)
        return 0
    asyncio.run(_send(title, lines))
    logger.info("VIMO %s sent %s changed confirmations", mode, len(lines))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "weekend"], default="daily")
    args = parser.parse_args()
    try:
        return run(args.mode)
    except Exception as exc:
        # Optional support must never fail the core scanner job.
        logger.exception("Optional VIMO support failed: %s", type(exc).__name__)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
