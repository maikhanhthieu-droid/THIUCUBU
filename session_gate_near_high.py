#!/usr/bin/env python3
from __future__ import annotations

import asyncio
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


def update_failed_breaks_no_index(results: list[scan.ScanResult]) -> list[dict[str, Any]]:
    return _ORIGINAL_UPDATE_FAILED_BREAKS([r for r in results if getattr(r, "symbol", "") != "VNINDEX"])


scan.update_failed_breaks = update_failed_breaks_no_index

import session_gate as gate

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
        return _old_save_session_outputs(
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
        return _old_save_session_outputs(mode, results, history_store, peak_store, focus_symbols, watch_items)


patch_scan_metadata()
gate.plus.sess.all_universe_symbols = all_universe_symbols_with_near_high_filter
gate.plus.sess.build_session_report = build_session_report_compat
gate.plus.sess.save_session_outputs = save_session_outputs_compat


if __name__ == "__main__":
    asyncio.run(gate.plus.main())