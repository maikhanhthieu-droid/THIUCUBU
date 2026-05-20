#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from typing import Any

import near_high_filter
import scan
import scan_safe

_ORIGINAL_SAFE_FETCH = scan_safe.fetch_ohlcv_safe


def fetch_ohlcv_no_exit(*args: Any, **kwargs: Any):
    try:
        return _ORIGINAL_SAFE_FETCH(*args, **kwargs)
    except SystemExit as exc:
        scan_safe.logger.warning("fetch_ohlcv stopped by vnstock quota: %s", str(exc).splitlines()[0])
        return None


_SAFE_FETCH = fetch_ohlcv_no_exit

_ORIGINAL_DISABLE_SOURCE = scan_safe.ApiSourceLimiter.disable


def disable_source_with_rate_cooldown(self: scan_safe.ApiSourceLimiter, reason: str) -> None:
    if scan_safe.is_rate_limit_error(Exception(reason)):
        self.record_failure(is_rate_limit=True)
        return
    _ORIGINAL_DISABLE_SOURCE(self, reason)


scan_safe.ApiSourceLimiter.disable = disable_source_with_rate_cooldown

_ORIGINAL_UPDATE_FAILED_BREAKS = scan.update_failed_breaks


def is_test_mode() -> bool:
    args = " ".join(sys.argv).lower()
    return os.getenv("SCAN_MODE", "").strip().lower() == "test" or "--mode test" in args or "--mode=test" in args


def normalize_failed_break_symbol(value: Any) -> str:
    return str(value or "").replace("`", "").upper().strip()


def normalize_failed_breaks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        symbol = normalize_failed_break_symbol(item.get("symbol"))
        if not symbol:
            continue
        normalized = dict(item)
        normalized["symbol"] = symbol
        cleaned.append(normalized)
    return cleaned


def latest_failed_breaks(records: list[dict[str, Any]], only_date: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for item in normalize_failed_breaks(records):
        if only_date and item.get("date") != only_date:
            continue
        seen[str(item["symbol"])] = item
    return sorted(seen.values(), key=lambda item: str(item.get("date", "")), reverse=True)[:limit]


def update_failed_breaks_no_index(results: list[scan.ScanResult]) -> list[dict[str, Any]]:
    if is_test_mode():
        return []
    records = _ORIGINAL_UPDATE_FAILED_BREAKS([r for r in results if getattr(r, "symbol", "") != "VNINDEX"])
    cleaned = normalize_failed_breaks(records)
    scan.json_save(scan.DATA_DIR / "failed_breaks.json", cleaned)
    return cleaned


scan.update_failed_breaks = update_failed_breaks_no_index
scan.latest_failed_breaks = latest_failed_breaks

import session_gate as gate

def fmt_price_vn(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        price = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if price == 0:
        return "n/a"
    if abs(price) >= 1000:
        return f"{price:,.0f}"
    return f"{price:.2f}"


gate.intel.fmt_price = fmt_price_vn

_ORIGINAL_FORMAT_ADVANCED_LINES = gate.intel.format_advanced_lines


def format_advanced_lines_with_gate(metrics: dict[str, Any] | None) -> list[str]:
    lines = _ORIGINAL_FORMAT_ADVANCED_LINES(metrics)
    if not metrics:
        return lines
    gate_info = metrics.get("gate") or {}
    if not gate_info or gate_info.get("allowed", True):
        return lines
    reason = str(gate_info.get("reason") or "loc tin hieu")
    for idx, line in enumerate(lines):
        if not line.startswith("HT ") or " | Size:" not in line:
            continue
        prefix, rest = line.split(" | Size:", 1)
        flags = ""
        if " | " in rest:
            flags = rest[rest.find(" | "):]
        lines[idx] = f"{prefix} | Size: CHUA MUA / THEO DOI ({reason}){flags}"
        break
    return lines


gate.intel.format_advanced_lines = format_advanced_lines_with_gate

scan_safe.fetch_ohlcv_safe = _SAFE_FETCH
scan.fetch_ohlcv = _SAFE_FETCH
gate.plus.scan_safe.fetch_ohlcv_safe = _SAFE_FETCH
gate.plus.scan.fetch_ohlcv = _SAFE_FETCH

_old_all_universe_symbols = gate.plus.sess.all_universe_symbols
_old_build_session_report = gate.plus.sess.build_session_report
_old_save_session_outputs = gate.plus.sess.save_session_outputs


def patch_scan_metadata() -> None:
    sector_default_group = getattr(
        scan,
        "SECTOR_DEFAULT_GROUP",
        {
            "Bank": "G2",
            "Chung khoan": "G4",
            "Bao hiem": "G3",
            "BDS dan cu": "G5",
            "BDS KCN": "G3",
            "Xay dung dau tu cong": "G6",
            "Thep": "G4",
            "Da xi mang nhua duong": "G4",
            "Go cao su": "G3",
            "Hoa chat phan bon": "G4",
            "Cao su nhua": "G4",
            "Dau khi": "G4",
            "Dien tien ich": "G3",
            "Ban le": "G2",
            "Thuc pham do uong": "G2",
            "Det may san xuat": "G6",
            "Thuy san": "G6",
            "Nong nghiep chan nuoi": "G6",
            "Cong nghe vien thong": "G2",
            "Logistics cang bien": "G4",
        },
    )
    scan.TICKER_GROUP["VNINDEX"] = "G1"
    for ticker in scan.ALL_TICKERS:
        scan.TICKER_GROUP.setdefault(ticker, sector_default_group.get(scan.TICKER_TO_SECTOR.get(ticker, ""), "G4"))
    scan.SECTOR_LEADERS.update(
        {
            "Bao hiem": ["BVH", "BMI", "PVI", "MIG", "EVF"],
            "BDS dan cu": ["VIC", "VHM", "KDH", "DIG", "NVL"],
            "BDS KCN": ["BCM", "KBC", "IDC", "VGC", "SZC"],
            "Xay dung dau tu cong": ["VCG", "LCG", "HHV", "CII", "FCN"],
            "Da xi mang nhua duong": ["KSB", "DHA", "HT1", "BCC", "PLC"],
            "Go cao su": ["PTB", "DPR", "DRI", "TTF"],
            "Hoa chat phan bon": ["DGC", "DPM", "DCM", "CSV", "LAS"],
            "Cao su nhua": ["BMP", "DRC", "AAA", "CSM"],
            "Dien tien ich": ["POW", "REE", "PC1", "HDG", "NT2"],
            "Thuc pham do uong": ["VNM", "MSN", "SAB", "MCH", "QNS"],
            "Det may san xuat": ["TNG", "MSH", "TCM", "GIL", "VEA"],
            "Thuy san": ["VHC", "ANV", "FMC", "IDI", "ASM"],
            "Nong nghiep chan nuoi": ["DBC", "HAG", "PAN", "BAF", "LTG"],
            "Cong nghe vien thong": ["FPT", "CMG", "ELC", "VGI", "CTR"],
            "Logistics cang bien": ["GMD", "HAH", "VOS", "VTO", "SGP"],
        }
    )


def all_universe_symbols_with_near_high_filter(mode: str, watch_items: dict[str, dict[str, Any]]) -> list[str]:
    symbols = _old_all_universe_symbols(mode, watch_items)
    valid_symbols = [s for s in symbols if 3 <= len(str(s).strip()) <= 12]
    dropped = sorted(set(symbols) - set(valid_symbols))
    if dropped:
        gate.plus.sess.logger.warning("Dropping invalid ticker(s): %s", ",".join(dropped))
    symbols = valid_symbols
    if mode in {"test", "eod"}:
        return symbols
    filtered, skipped = near_high_filter.filter_symbols(symbols, protected=set(watch_items))
    if skipped:
        gate.plus.sess.logger.info("Near-high weekday filter removed %s symbols", len(skipped))
    return filtered


def build_session_report_compat(
    mode: str,
    results: dict[str, scan.ScanResult],
    focus_symbols: list[str],
    watch_items: dict[str, dict[str, Any]],
    metrics_by_symbol: dict[str, dict[str, Any]] | None = None,
    regime: dict[str, Any] | None = None,
    rotation_alerts: list[str] | None = None,
    performance_text: str = "",
) -> str:
    try:
        return _old_build_session_report(
            mode,
            results,
            focus_symbols,
            watch_items,
            metrics_by_symbol,
            regime,
            rotation_alerts,
            performance_text,
        )
    except TypeError:
        return _old_build_session_report(mode, results, focus_symbols, watch_items)


def save_session_outputs_compat(
    mode: str,
    results: dict[str, scan.ScanResult],
    history_store: dict[str, Any],
    peak_store: dict[str, Any],
    focus_symbols: list[str],
    watch_items: dict[str, dict[str, Any]],
    metrics_by_symbol: dict[str, dict[str, Any]] | None = None,
    regime: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    try:
        failed_breaks = _old_save_session_outputs(
            mode,
            results,
            history_store,
            peak_store,
            focus_symbols,
            watch_items,
            metrics_by_symbol,
            regime,
        )
    except TypeError:
        failed_breaks = _old_save_session_outputs(mode, results, history_store, peak_store, focus_symbols, watch_items)
    if mode in {"eod", "test"}:
        return []
    today = datetime.now(scan.VN_TZ).date().isoformat()
    return latest_failed_breaks(failed_breaks, only_date=today)


patch_scan_metadata()
gate.plus.sess.all_universe_symbols = all_universe_symbols_with_near_high_filter
gate.plus.sess.build_session_report = build_session_report_compat
gate.plus.sess.save_session_outputs = save_session_outputs_compat


if __name__ == "__main__":
    asyncio.run(gate.plus.main())
