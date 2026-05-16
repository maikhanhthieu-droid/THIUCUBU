#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from typing import Any

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

import weekend_plus as plus

scan_safe.fetch_ohlcv_safe = _SAFE_FETCH
plus.weekend.scan_safe.fetch_ohlcv_safe = _SAFE_FETCH

_old_fetch_fundamental = plus.weekend.fetch_fundamental
_old_build_report = plus.weekend.build_report


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


def fetch_fundamental_safe(symbol: str) -> Any:
    try:
        return _old_fetch_fundamental(symbol)
    except SystemExit as exc:
        plus.weekend.logger.warning("Fundamental %s stopped by vnstock quota: %s", symbol, str(exc).splitlines()[0])
        return None


def build_report_compat(opportunities, sectors, mode, near_high=None, missing_fundamental=None) -> str:
    try:
        return _old_build_report(opportunities, sectors, mode, near_high, missing_fundamental)
    except TypeError:
        report = _old_build_report(opportunities, sectors, mode, near_high)
        if missing_fundamental:
            preview = ",".join(missing_fundamental[:12])
            suffix = "..." if len(missing_fundamental) > 12 else ""
            report = report.replace(
                "Quet PE/PB + chiet khau gia + chat luong + nganh + risk. Khong phai khuyen nghi mua ban.",
                "Quet PE/PB + chiet khau gia + chat luong + nganh + risk. Khong phai khuyen nghi mua ban.\n"
                f"Fundamental missing: {len(missing_fundamental)} ma ({preview}{suffix})",
            )
        return report


patch_scan_metadata()
plus.weekend.fetch_fundamental = fetch_fundamental_safe
plus.weekend.build_report = build_report_compat


if __name__ == "__main__":
    asyncio.run(plus.weekend.main())
