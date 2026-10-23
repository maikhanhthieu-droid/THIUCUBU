#!/usr/bin/env python3
"""Macro & Commodity Shock Catalyst Engine with Gemini AI Studio and Read-Only SSI/TCBS adapters.

Key design principles:
1. Noise Filtering (Major Shocks Only): Minor daily/weekly international fluctuations
   are ignored. Actionable signals are emitted ONLY when a macro/commodity move
   crosses its major shock threshold.
2. Technical Confluence & % NAV Sizing: Macro beneficiaries are cross-checked against
   THIUCUBU's technical scanner (score, strict 6-gate conviction, failed-break guard)
   to recommend concrete % NAV position sizes and risk warnings.
3. 100% Account Safety (Read-Only): Never uses or accepts SSI/TCBS trading private keys,
   PINs, or order-execution credentials.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

logger = logging.getLogger("thieucutoo.macro")
VN_TZ = timezone(timedelta(hours=7))
CACHE_DIR = Path(os.getenv("MACRO_CACHE_DIR", "data/cache/macro"))
GEMINI_BUDGET_FILE = Path(os.getenv("GEMINI_BUDGET_FILE", "data/api_budget/gemini.json"))
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=8.0)

# Credentials that belong to live order-placement / trading accounts and MUST NEVER
# be used in this repository or pushed to GitHub.
FORBIDDEN_TRADING_ENV_VARS: tuple[str, ...] = (
    "SSI_PRIVATE_KEY",
    "SSI_FC_PRIVATE_KEY",
    "SSI_TRADING_PIN",
    "SSI_ACCOUNT_PASSWORD",
    "SSI_TRADING_OTP",
    "TCBS_TRADING_PASSWORD",
    "TCBS_TRADING_PIN",
    "TCBS_OTP_TOKEN",
)


class MacroCatalystError(RuntimeError):
    """Base error for macro catalyst and AI advisor operations."""


class AccountSecurityError(MacroCatalystError):
    """Raised when live trading credentials are detected in an environment meant for read-only scanning."""


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
        return num if math.isfinite(num) else default
    except (TypeError, ValueError):
        return default


def verify_account_safety(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Verify that no SSI/TCBS order-placement or trading credentials are exposed.

    Market scanning and MCP tools in THIUCUBU operate strictly in READ-ONLY mode.
    """
    source_env = env if env is not None else os.environ
    exposed: list[str] = []
    for var_name in FORBIDDEN_TRADING_ENV_VARS:
        if str(source_env.get(var_name, "")).strip():
            exposed.append(var_name)

    safe = len(exposed) == 0
    return {
        "safe": safe,
        "mode": "SAFE_READ_ONLY" if safe else "UNSAFE_TRADING_KEY_PRESENT",
        "exposed_trading_vars": exposed,
        "ssi_trading_account_affected": not safe,
        "guidance": (
            "An toàn 100%: Hệ thống chỉ đọc dữ liệu thị trường công khai (Read-Only), "
            "không kết nối đặt lệnh và không chạm tới tài khoản giao dịch SSI cá nhân."
            if safe
            else (
                "CẢNH BÁO BẢO MẬT: Phát hiện biến môi trường đặt lệnh giao dịch ("
                + ", ".join(exposed)
                + "). Hãy xóa ngay khỏi .env / GitHub Secrets. Chỉ dùng chế độ Read-Only!"
            )
        ),
    }


def assert_account_safety(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    status = verify_account_safety(env)
    if not status["safe"]:
        raise AccountSecurityError(status["guidance"])
    return status


# ---------------------------------------------------------------------------
# Major Macro & Commodity Playbook (Noise-filtered thresholds + % NAV sizing)
# ---------------------------------------------------------------------------

MACRO_PLAYBOOK: dict[str, dict[str, Any]] = {
    "PORK_HOGS": {
        "name": "Giá Thịt Heo / Heo Hơi",
        "unit": "%",
        "major_weekly_pct": 4.0,
        "extreme_weekly_pct": 8.0,
        "major_monthly_pct": 7.5,
        "keywords": ("thịt heo", "giá heo", "heo hơi", "lợn", "pork", "lean hogs", "chăn nuôi heo"),
        "up": {
            "headline": "GIÁ THỊT HEO TĂNG MẠNH — CANH MUA NHÓM CHĂN NUÔI HEO",
            "primary_tickers": ["DBC", "BAF", "HAG", "MCH"],
            "watch_tickers": ["PAN", "VSN"],
            "avoid_tickers": [],
            "base_stock_nav_pct": (6.0, 10.0),
            "max_sector_nav_pct": 18.0,
            "action_summary": "Canh mua nhóm chăn nuôi heo chủ lực tự chủ con giống (3F) khi giá heo hơi bứt tốc vượt ngưỡng.",
            "watch_notes": [
                "Ưu tiên DBC, BAF (mô hình 3F khép kín, biên lợi nhuận gộp mở rộng nhanh nhất khi giá heo hơi > 65.000đ/kg).",
                "Chú ý kiểm tra giá ngô/khô đậu tương đầu vào và tình hình dịch tả heo châu Phi (ASF) tại từng vùng.",
                "Chỉ giải ngân đủ tỷ trọng khi cổ phiếu giữ trên EMA20 hoặc có Pocket Pivot / nền tích lũy chặt.",
            ],
        },
        "down": {
            "headline": "GIÁ THỊT HEO LAO DỐC — HẠ TỶ TRỌNG NHÓM CHĂN NUÔI",
            "primary_tickers": [],
            "watch_tickers": ["VNM", "MCH"],
            "avoid_tickers": ["DBC", "BAF", "HAG"],
            "base_stock_nav_pct": (0.0, 3.0),
            "max_sector_nav_pct": 5.0,
            "action_summary": "Chốt lời / hạ tỷ trọng nhóm chăn nuôi heo, tránh bắt đáy sớm khi giá heo hơi gãy xu hướng.",
            "watch_notes": [
                "Không bắt đáy DBC, BAF, HAG khi giá heo hơi giảm mạnh liên tiếp.",
                "Chú ý biên lợi nhuận ngành chăn nuôi thường tạo đỉnh trước khi giá heo hơi giảm sâu.",
            ],
        },
    },
    "CRUDE_OIL": {
        "name": "Giá Dầu Quốc Tế (Brent / WTI)",
        "unit": "%",
        "major_weekly_pct": 4.5,
        "extreme_weekly_pct": 9.0,
        "major_monthly_pct": 9.0,
        "keywords": ("giá dầu", "dầu brent", "dầu wti", "crude oil", "brent", "wti", "opec", "dầu khí"),
        "up": {
            "headline": "GIÁ DẦU BỨT TỐC MẠNH — MUA NHÓM DẦU KHÍ THƯỢNG/TRUNG NGUỒN",
            "primary_tickers": ["PVD", "PVS", "BSR", "GAS", "PVT", "PLX"],
            "watch_tickers": ["OIL", "PVC"],
            "avoid_tickers": [],
            "base_stock_nav_pct": (8.0, 12.0),
            "max_sector_nav_pct": 22.0,
            "action_summary": "Giải ngân nhóm Dầu khí (ưu tiên BSR nhạy crack spread, PVD/PVS hưởng lợi giá thuê giàn & backlog, PVT vận tải dầu).",
            "watch_notes": [
                "BSR và PLX phản ứng nhanh nhất với đà tăng ngắn hạn của dầu Brent & crack spread lọc dầu.",
                "PVD, PVS có độ bền xu hướng trung hạn nhờ giá thuê giàn khoan và đại dự án Lô B - Ô Môn.",
                "Chú ý rủi ro đội chi phí nhiên liệu đầu vào ở nhóm Hàng không / Vận tải / Nhựa (HVN, VJC, BMP).",
            ],
        },
        "down": {
            "headline": "GIÁ DẦU SỤT GIẢM MẠNH — HẠ DẦU KHÍ, CHÚ Ý NHÓM HƯỞNG LỢI ĐẦU VÀO",
            "primary_tickers": ["BMP", "NT2", "DCM", "DPM"],
            "watch_tickers": ["PLC", "HPG"],
            "avoid_tickers": ["BSR", "PVD", "PLX", "OIL"],
            "base_stock_nav_pct": (5.0, 8.0),
            "max_sector_nav_pct": 14.0,
            "action_summary": "Hạ tỷ trọng nhóm Dầu khí; chuyển sự chú ý sang nhóm Nhựa (BMP) và Phân bón/Điện khí hưởng lợi giá đầu vào giảm.",
            "watch_notes": [
                "Tránh mua đuổi BSR, PVD, PLX khi giá dầu Brent gãy hỗ trợ tuần (rủi ro trích lập dự phòng tồn kho ở BSR/PLX).",
                "BMP hưởng lợi khi giá hạt nhựa PVC (chế phẩm dầu mỏ) hạ nhiệt.",
            ],
        },
    },
    "GOLD_XAU": {
        "name": "Giá Vàng Thế Giới (XAU/USD)",
        "unit": "%",
        "major_weekly_pct": 3.0,
        "extreme_weekly_pct": 6.0,
        "major_monthly_pct": 6.0,
        "keywords": ("giá vàng", "vàng", "gold", "xau", "xauusd", "sjc", "kim loại quý"),
        "up": {
            "headline": "GIÁ VÀNG BỨT PHÁ LỚN — CHÚ Ý PNJ & PHÒNG THỦ RỦI RO VĨ MÔ",
            "primary_tickers": ["PNJ", "FPT", "REE"],
            "watch_tickers": ["DGC", "NT2", "VCB"],
            "avoid_tickers": [],
            "base_stock_nav_pct": (6.0, 10.0),
            "max_sector_nav_pct": 16.0,
            "action_summary": "Canh mua PNJ (hưởng lợi giá trị tồn kho & doanh thu vàng trang sức); đồng thời nâng đệm tiền mặt phòng thủ Risk-Off.",
            "watch_notes": [
                "Vàng tăng sốc (>3%/tuần) thường đi kèm căng thẳng địa chính trị hoặc bất ổn tiền tệ toàn cầu.",
                "Khuyến nghị giữ đệm tiền mặt tối thiểu 25%–35% NAV, không dùng margin cao khi vàng tăng dựng đứng.",
                "Ưu tiên cổ phiếu đầu ngành có bảng cân đối tiền mặt mạnh (PNJ, FPT, REE, DGC).",
            ],
        },
        "down": {
            "headline": "GIÁ VÀNG HẠ NHIỆT MẠNH — DÒNG TIỀN QUAY LẠI TÀI SẢN RỦI RO (RISK-ON)",
            "primary_tickers": ["SSI", "TCB", "FPT", "MBB"],
            "watch_tickers": ["VCI", "HCM", "HPG"],
            "avoid_tickers": [],
            "base_stock_nav_pct": (8.0, 12.0),
            "max_sector_nav_pct": 20.0,
            "action_summary": "Tâm lý trú ẩn hạ nhiệt, ưu tiên xoay vòng sang nhóm dẫn dắt thị trường (Ngân hàng, Chứng khoán, Công nghệ).",
            "watch_notes": [
                "Dòng tiền đầu cơ vàng trong nước có xu hướng dịch chuyển sang kênh chứng khoán khi vàng điều chỉnh sâu.",
            ],
        },
    },
    "FED_DXY_RATES": {
        "name": "FED / Lãi Suất / Chỉ Số DXY & Tỷ Giá USD/VND",
        "unit": "%",
        "major_weekly_pct": 1.5,
        "extreme_weekly_pct": 2.8,
        "major_monthly_pct": 3.0,
        "keywords": ("fed", "lãi suất", "dxy", "tỷ giá", "usd/vnd", "us10y", "fomc", "cắt giảm lãi suất", "tăng lãi suất"),
        "up": {
            "headline": "FED DIỀU HÂU / DXY & TỶ GIÁ TĂNG CĂNG — PHÒNG THỦ TIỀN MẶT & XUẤT KHẨU USD",
            "primary_tickers": ["FPT", "DGC", "VHC", "ANV", "GMD", "HAH"],
            "watch_tickers": ["NT2", "REE", "QNS", "PTB"],
            "avoid_tickers": ["DIG", "NVL", "PDR", "DXG", "CEO", "VIX"],
            "base_stock_nav_pct": (4.0, 7.0),
            "max_sector_nav_pct": 15.0,
            "action_summary": "Hạ tổng tỷ trọng cổ phiếu về 35%–50% NAV (giữ 50%–65% tiền mặt). Chỉ nắm giữ nhóm giàu tiền mặt ròng & xuất khẩu thu USD.",
            "watch_notes": [
                "Khi DXY bật tăng mạnh hoặc FED giữ lãi suất cao lâu hơn dự kiến, áp lực tỷ giá USD/VND và khối ngoại bán ròng sẽ tăng.",
                "Hạ ngay tỷ trọng nhóm BĐS đầu cơ, Chứng khoán beta cao và doanh nghiệp vay nợ USD lớn.",
                "Ưu tiên FPT (doanh thu IT toàn cầu), DGC (xuất khẩu hóa chất), VHC/ANV (thủy sản xuất khẩu), GMD/HAH (cảng biển).",
            ],
        },
        "down": {
            "headline": "FED HẠ LÃI SUẤT / DXY SUY YẾU MẠNH — SÓNG LỚN NGÂN HÀNG, CHỨNG KHOÁN, BĐS",
            "primary_tickers": ["SSI", "VCI", "HCM", "TCB", "MBB", "ACB", "KDH", "NLG", "IDC"],
            "watch_tickers": ["VND", "CTG", "BID", "KBC", "HPG", "MWG"],
            "avoid_tickers": [],
            "base_stock_nav_pct": (10.0, 14.0),
            "max_sector_nav_pct": 25.0,
            "action_summary": "Nâng tổng tỷ trọng cổ phiếu lên 70%–85% NAV; tập trung giải ngân nhóm nhạy lãi suất & thanh khoản (Chứng khoán, Ngân hàng, BĐS sạch).",
            "watch_notes": [
                "Áp lực tỷ giá USD/VND giải tỏa giúp NHNN duy trì chính sách tiền tệ nới lỏng và kích thích dòng vốn ngoại quay lại.",
                "Nhóm Chứng khoán (SSI, VCI, HCM) và Ngân hàng tư nhân (TCB, MBB, ACB) thường dẫn sóng đầu tiên.",
                "Nhóm BĐS có pháp lý sạch (KDH, NLG) và BĐS KCN (IDC, KBC) hưởng lợi kép từ chi phí vốn rẻ và dòng vốn FDI.",
            ],
        },
    },
    "STEEL_HRC": {
        "name": "Giá Sắt Thép / HRC / Quặng Sắt Thế Giới",
        "unit": "%",
        "major_weekly_pct": 4.0,
        "extreme_weekly_pct": 7.5,
        "major_monthly_pct": 7.5,
        "keywords": ("sắt thép", "giá thép", "thép", "sắt", "hrc", "quặng sắt", "tôn mạ", "steel", "iron ore"),
        "up": {
            "headline": "GIÁ SẮT THÉP / HRC TĂNG MẠNH — CANH MUA THÉP & TÔN MẠ, CHÚ Ý CHI PHÍ XÂY DỰNG",
            "primary_tickers": ["HPG", "HSG", "NKG", "VGS"],
            "watch_tickers": ["GDA", "TLH"],
            "avoid_tickers": [],
            "base_stock_nav_pct": (8.0, 12.0),
            "max_sector_nav_pct": 20.0,
            "action_summary": "Canh mua HPG (đầu ngành lò cao & HRC Dung Quất 2) và HSG, NKG (hưởng lợi tồn kho HRC giá rẻ khi giá bán đầu ra tăng).",
            "watch_notes": [
                "HPG phù hợp nắm giữ tỷ trọng lớn (8%–12% NAV) nhờ biên lợi nhuận bền vững và thị phần số 1.",
                "HSG, NKG có độ nhạy (beta) cao hơn theo chu kỳ giá HRC ngắn hạn, phù hợp đánh sóng bứt tốc (5%–8% NAV).",
                "CHÚ Ý RỦI RO: Giá thép tăng sốc có thể làm co hẹp biên lợi nhuận của các nhà thầu xây dựng cố định đơn giá (VCG, CTD, HHV, LCG).",
            ],
        },
        "down": {
            "headline": "GIÁ SẮT THÉP / HRC LAO DỐC — TRÁNH BẮT ĐÁY TÔN MẠ, CHÚ Ý NHÓM XÂY DỰNG HẠ TẦNG",
            "primary_tickers": ["CTD", "VCG", "HHV", "KSB"],
            "watch_tickers": ["LCG", "CII", "DPG"],
            "avoid_tickers": ["HSG", "NKG", "TLH", "SMC"],
            "base_stock_nav_pct": (5.0, 8.0),
            "max_sector_nav_pct": 15.0,
            "action_summary": "Tuyệt đối tránh bắt đáy sớm nhóm thương mại tôn mạ (HSG, NKG); chuyển chú ý sang nhóm Xây dựng / Đầu tư công hưởng lợi giá vật liệu giảm.",
            "watch_notes": [
                "Khi giá HRC giảm sâu, HSG và NKG thường chịu áp lực trích lập dự phòng giảm giá hàng tồn kho.",
                "Các nhà thầu xây dựng & đầu tư công (CTD, VCG, HHV) được cải thiện biên lợi nhuận gộp nhờ chi phí thép đầu vào hạ nhiệt.",
            ],
        },
    },
    "UREA_CHEMICALS": {
        "name": "Giá Phân Bón Ure / Phốt Pho / Hóa Chất",
        "unit": "%",
        "major_weekly_pct": 5.0,
        "extreme_weekly_pct": 9.0,
        "major_monthly_pct": 9.0,
        "keywords": ("phân bón", "giá ure", "ure", "urea", "phốt pho", "hóa chất", "dgc", "đạm"),
        "up": {
            "headline": "GIÁ PHÂN BÓN / HÓA CHẤT TĂNG SỐC — CANH MUA DGC, DCM, DPM, CSV",
            "primary_tickers": ["DGC", "DCM", "DPM", "CSV"],
            "watch_tickers": ["LAS", "BFC", "DDV"],
            "avoid_tickers": [],
            "base_stock_nav_pct": (6.0, 10.0),
            "max_sector_nav_pct": 18.0,
            "action_summary": "Canh mua nhóm Hóa chất & Phân bón hưởng lợi trực tiếp từ giá bán xuất khẩu và nội địa tăng vọt.",
            "watch_notes": [
                "DGC dẫn đầu chuỗi phốt pho vàng & hóa chất bán dẫn/pin với lượng tiền mặt ròng cực lớn.",
                "DCM, DPM hưởng lợi tức thì khi giá Ure thế giới (Trung Đông/Biển Đen/Trung Quốc hạn chế xuất khẩu) bật tăng.",
            ],
        },
        "down": {
            "headline": "GIÁ PHÂN BÓN / HÓA CHẤT HẠ NHIỆT MẠNH — HẠ TỶ TRỌNG NHÓM ĐẠM",
            "primary_tickers": [],
            "watch_tickers": ["PAN", "LTG"],
            "avoid_tickers": ["DCM", "DPM", "LAS"],
            "base_stock_nav_pct": (0.0, 3.0),
            "max_sector_nav_pct": 5.0,
            "action_summary": "Chốt lời/hạ tỷ trọng nhóm phân bón khi giá Ure thế giới đảo chiều giảm mạnh.",
            "watch_notes": [
                "Tránh mua đuổi cổ phiếu phân bón ở vùng đỉnh chu kỳ giá hàng hóa.",
            ],
        },
    },
    "SHIPPING_FREIGHT": {
        "name": "Giá Cước Vận Tải Biển (Container SCFI / Baltic Dry)",
        "unit": "%",
        "major_weekly_pct": 6.0,
        "extreme_weekly_pct": 12.0,
        "major_monthly_pct": 12.0,
        "keywords": ("cước biển", "cước vận tải", "vận tải biển", "container", "scfi", "bdi", "cảng biển", "biển đỏ"),
        "up": {
            "headline": "GIÁ CƯỚC VẬN TẢI BIỂN TĂNG VỌT — CANH MUA HAH, GMD, VOS, PVT",
            "primary_tickers": ["HAH", "GMD", "VOS", "PVT"],
            "watch_tickers": ["SGP", "VIP", "VTO"],
            "avoid_tickers": [],
            "base_stock_nav_pct": (6.0, 10.0),
            "max_sector_nav_pct": 18.0,
            "action_summary": "Giải ngân nhóm đội tàu container & khai thác cảng biển sâu khi giá cước quốc tế bước vào pha bứt tốc.",
            "watch_notes": [
                "HAH hưởng lợi trực tiếp từ giá cước định hạn (time charter) và cước giao ngay nội Á.",
                "GMD mang tính bền vững cao nhờ cụm cảng nước sâu Gemalink và Nam Đình Vũ.",
            ],
        },
        "down": {
            "headline": "GIÁ CƯỚC BIỂN LAO DỐC — CHỐT LỜI ĐỘI TÀU CONTAINER, GIỮ CẢNG NƯỚC SÂU",
            "primary_tickers": ["VHC", "ANV", "TNG"],
            "watch_tickers": ["GMD"],
            "avoid_tickers": ["VOS", "HAH"],
            "base_stock_nav_pct": (4.0, 7.0),
            "max_sector_nav_pct": 12.0,
            "action_summary": "Hạ tỷ trọng nhóm tàu biển thuần túy (VOS, HAH); chú ý doanh nghiệp xuất khẩu (VHC, ANV, TNG) giảm được chi phí logistics.",
            "watch_notes": [
                "Chi phí cước tàu giảm sâu giúp cải thiện biên lợi nhuận bán hàng của các doanh nghiệp xuất khẩu thủy sản, dệt may, gỗ.",
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# Noise Filter & Shock Evaluation
# ---------------------------------------------------------------------------


def classify_shock_magnitude(
    factor_key: str,
    *,
    weekly_change_pct: float = 0.0,
    monthly_change_pct: float = 0.0,
    explicit_major_event: bool = False,
) -> dict[str, Any]:
    """Filter out minor international fluctuations and keep only major/extreme shocks."""
    rule = MACRO_PLAYBOOK.get(factor_key)
    if not rule:
        return {"is_actionable": False, "severity": "UNKNOWN_FACTOR", "direction": "FLAT", "intensity_ratio": 0.0}

    major_w = float(rule["major_weekly_pct"])
    extreme_w = float(rule["extreme_weekly_pct"])
    major_m = float(rule["major_monthly_pct"])

    abs_w = abs(weekly_change_pct)
    abs_m = abs(monthly_change_pct)
    ratio_w = abs_w / max(major_w, 1e-6)
    ratio_m = abs_m / max(major_m, 1e-6)
    intensity = max(ratio_w, ratio_m, 1.15 if explicit_major_event else 0.0)

    dominant_val = weekly_change_pct if abs_w >= abs_m * 0.45 or abs_w > 0 else monthly_change_pct
    direction = "UP" if dominant_val > 0 else "DOWN" if dominant_val < 0 else "UP"

    if abs_w >= extreme_w or intensity >= 1.65:
        severity = "EXTREME_SHOCK"
        is_actionable = True
    elif abs_w >= major_w or abs_m >= major_m or explicit_major_event:
        severity = "MAJOR_SHOCK"
        is_actionable = True
    else:
        severity = "MINOR_NOISE"
        is_actionable = False

    return {
        "is_actionable": is_actionable,
        "severity": severity,
        "direction": direction,
        "intensity_ratio": round(intensity, 2),
        "weekly_change_pct": round(weekly_change_pct, 2),
        "monthly_change_pct": round(monthly_change_pct, 2),
        "threshold_weekly_pct": major_w,
        "threshold_monthly_pct": major_m,
    }


def compute_nav_allocation(
    base_range: tuple[float, float],
    max_sector_nav_pct: float,
    *,
    severity: str,
    regime: str = "SIDEWAYS",
    stock_score: int | None = None,
    strict_conviction: bool = False,
    failed_break: bool = False,
) -> dict[str, Any]:
    """Calculate concrete % NAV per stock and sector based on macro shock + technical confluence."""
    if severity not in {"MAJOR_SHOCK", "EXTREME_SHOCK"}:
        return {
            "recommended_nav_pct": 0.0,
            "nav_range_str": "0% NAV (biến động nhỏ, bỏ qua)",
            "max_sector_nav_pct": 0.0,
            "sizing_tier": "IGNORE_NOISE",
            "sizing_reason": "Biến động quốc tế chưa vượt ngưỡng cú sốc lớn.",
        }

    low_pct, high_pct = base_range
    if high_pct <= 0:
        return {
            "recommended_nav_pct": 0.0,
            "nav_range_str": "0% NAV (đứng ngoài / hạ tỷ trọng)",
            "max_sector_nav_pct": round(max_sector_nav_pct, 1),
            "sizing_tier": "DEFENSIVE_EXIT",
            "sizing_reason": "Biến động bất lợi cho nhóm ngành, ưu tiên bảo toàn vốn.",
        }

    regime_mult = {
        "BULL": 1.0,
        "FAVORABLE": 1.0,
        "SIDEWAYS": 0.80,
        "NEUTRAL": 0.80,
        "BEAR": 0.45,
        "HIGH_RISK": 0.45,
    }.get(str(regime or "SIDEWAYS").upper(), 0.80)

    severity_mult = 1.15 if severity == "EXTREME_SHOCK" else 1.0

    if failed_break:
        return {
            "recommended_nav_pct": 0.0,
            "nav_range_str": "0% NAV (cổ phiếu đang break xịt)",
            "max_sector_nav_pct": round(max_sector_nav_pct * regime_mult, 1),
            "sizing_tier": "BLOCKED_BY_TECHNICAL",
            "sizing_reason": "Vĩ mô ủng hộ nhưng cổ phiếu đang bị Failed Break — chờ tích lũy lại.",
        }

    if stock_score is None:
        mid = (low_pct + high_pct) / 2.0 * regime_mult * severity_mult
        rec = round(min(high_pct * 1.1, max(2.0, mid)), 1)
        lo = round(max(2.0, low_pct * regime_mult), 1)
        hi = round(min(15.0, high_pct * regime_mult * severity_mult), 1)
        return {
            "recommended_nav_pct": rec,
            "nav_range_str": f"{lo:.0f}%–{hi:.0f}% NAV/mã (tối đa ngành {max_sector_nav_pct * regime_mult:.0f}% NAV)",
            "max_sector_nav_pct": round(max_sector_nav_pct * regime_mult, 1),
            "sizing_tier": "STANDARD_CATALYST",
            "sizing_reason": f"Phân bổ theo cú sốc {severity} trong bối cảnh thị trường {regime}.",
        }

    if strict_conviction or stock_score >= 80:
        rec = round(min(15.0, high_pct * regime_mult * severity_mult), 1)
        lo = round(max(4.0, (low_pct + 2.0) * regime_mult), 1)
        return {
            "recommended_nav_pct": rec,
            "nav_range_str": f"{lo:.0f}%–{rec:.0f}% NAV",
            "max_sector_nav_pct": round(max_sector_nav_pct * regime_mult, 1),
            "sizing_tier": "FULL_CONVICTION",
            "sizing_reason": "Đồng thuận KÉP: Cú sốc vĩ mô lớn + Cổ phiếu đạt chuẩn bứt tốc kỹ thuật.",
        }
    if stock_score >= 65:
        rec = round(max(3.0, ((low_pct + high_pct) / 2.0) * regime_mult), 1)
        lo = round(max(3.0, low_pct * regime_mult * 0.8), 1)
        return {
            "recommended_nav_pct": rec,
            "nav_range_str": f"{lo:.0f}%–{rec:.0f}% NAV (gom từng phần)",
            "max_sector_nav_pct": round(max_sector_nav_pct * regime_mult, 1),
            "sizing_tier": "PILOT_ACCUMULATE",
            "sizing_reason": "Vĩ mô thuận lợi, kỹ thuật đang tích lũy — giải ngân thăm dò tại nền.",
        }
    return {
        "recommended_nav_pct": 2.0,
        "nav_range_str": "0%–2% NAV (chỉ theo dõi, chờ điểm mua)",
        "max_sector_nav_pct": round(max_sector_nav_pct * regime_mult, 1),
        "sizing_tier": "WAIT_TECHNICAL",
        "sizing_reason": f"Điểm kỹ thuật còn thấp ({stock_score}/97) — không mua đuổi theo tin.",
    }


def evaluate_macro_shock(
    factor_key: str,
    *,
    weekly_change_pct: float = 0.0,
    monthly_change_pct: float = 0.0,
    explicit_major_event: bool = False,
    custom_note: str = "",
    results: Mapping[str, Any] | None = None,
    metrics: Mapping[str, Mapping[str, Any]] | None = None,
    regime: str = "SIDEWAYS",
) -> dict[str, Any]:
    """Evaluate a single macro/commodity factor and map it to VN stock picks + % NAV."""
    factor_key = str(factor_key or "").strip().upper()
    rule = MACRO_PLAYBOOK.get(factor_key)
    if not rule:
        raise ValueError(f"Unsupported macro factor: {factor_key}")

    mag = classify_shock_magnitude(
        factor_key,
        weekly_change_pct=weekly_change_pct,
        monthly_change_pct=monthly_change_pct,
        explicit_major_event=explicit_major_event,
    )
    direction_key = "up" if mag["direction"] == "UP" else "down"
    branch = rule[direction_key]

    overall_nav = compute_nav_allocation(
        branch["base_stock_nav_pct"],
        branch["max_sector_nav_pct"],
        severity=mag["severity"],
        regime=regime,
    )

    results = results or {}
    metrics = metrics or {}
    stock_recommendations: list[dict[str, Any]] = []

    for sym in branch["primary_tickers"]:
        res = results.get(sym)
        metric = metrics.get(sym, {}) if isinstance(metrics.get(sym), Mapping) else {}
        accel = metric.get("breakout_acceleration") if isinstance(metric.get("breakout_acceleration"), Mapping) else {}
        trade = metric.get("trade") if isinstance(metric.get("trade"), Mapping) else {}

        adv_score = None
        if metric.get("advanced_score") is not None:
            adv_score = int(metric["advanced_score"])
        elif res is not None and getattr(res, "win_score", None) is not None:
            adv_score = int(getattr(res, "win_score"))

        strict_ok = bool(accel.get("strict_conviction"))
        failed_brk = bool(getattr(res, "failed_break", False)) if res is not None else False
        sizing = compute_nav_allocation(
            branch["base_stock_nav_pct"],
            branch["max_sector_nav_pct"],
            severity=mag["severity"],
            regime=regime,
            stock_score=adv_score,
            strict_conviction=strict_ok,
            failed_break=failed_brk,
        )
        stock_recommendations.append(
            {
                "symbol": sym,
                "close": round(_safe_float(getattr(res, "close", None)), 2) if res is not None else None,
                "advanced_score": adv_score,
                "strict_conviction": strict_ok,
                "acceleration_tier": str(accel.get("tier") or "NONE"),
                "recommended_nav_pct": sizing["recommended_nav_pct"],
                "nav_range": sizing["nav_range_str"],
                "sizing_tier": sizing["sizing_tier"],
                "sizing_reason": sizing["sizing_reason"],
                "entry_zone": trade.get("entry_zone"),
                "stop_loss": trade.get("stop_loss"),
            }
        )

    stock_recommendations.sort(
        key=lambda item: (
            1 if item["strict_conviction"] else 0,
            int(item["advanced_score"] or 0),
            float(item["recommended_nav_pct"] or 0.0),
        ),
        reverse=True,
    )

    return {
        "factor": factor_key,
        "name": rule["name"],
        "is_actionable": mag["is_actionable"],
        "severity": mag["severity"],
        "direction": mag["direction"],
        "weekly_change_pct": mag["weekly_change_pct"],
        "monthly_change_pct": mag["monthly_change_pct"],
        "threshold_weekly_pct": mag["threshold_weekly_pct"],
        "headline": branch["headline"] if mag["is_actionable"] else f"{rule['name']}: Biến động nhỏ ({mag['weekly_change_pct']:+.1f}%/tuần) — Bỏ qua nhiễu",
        "action_summary": branch["action_summary"] if mag["is_actionable"] else "Biến động quốc tế nhỏ dưới ngưỡng lọc gắt, giữ nguyên kỷ luật danh mục.",
        "overall_nav_guidance": overall_nav["nav_range_str"],
        "max_sector_nav_pct": overall_nav["max_sector_nav_pct"],
        "primary_tickers": list(branch["primary_tickers"]),
        "watch_tickers": list(branch["watch_tickers"]),
        "avoid_tickers": list(branch["avoid_tickers"]),
        "watch_notes": list(branch["watch_notes"]),
        "stock_recommendations": stock_recommendations,
        "custom_note": custom_note,
    }


def evaluate_all_major_shocks(
    observations: Sequence[Mapping[str, Any]] | None = None,
    *,
    results: Mapping[str, Any] | None = None,
    metrics: Mapping[str, Mapping[str, Any]] | None = None,
    regime: str = "SIDEWAYS",
) -> dict[str, Any]:
    """Filter a batch of macro/commodity observations and return ONLY actionable major shocks."""
    if observations is None:
        observations = load_macro_observations()

    actionable: list[dict[str, Any]] = []
    ignored_noise: list[dict[str, Any]] = []

    for item in observations:
        factor = str(item.get("factor") or "").strip().upper()
        if factor not in MACRO_PLAYBOOK:
            continue
        evaluated = evaluate_macro_shock(
            factor,
            weekly_change_pct=_safe_float(item.get("weekly_change_pct"), 0.0),
            monthly_change_pct=_safe_float(item.get("monthly_change_pct"), 0.0),
            explicit_major_event=bool(item.get("explicit_major_event", False)),
            custom_note=str(item.get("note") or ""),
            results=results,
            metrics=metrics,
            regime=regime,
        )
        if evaluated["is_actionable"]:
            actionable.append(evaluated)
        else:
            ignored_noise.append(
                {
                    "factor": factor,
                    "name": evaluated["name"],
                    "weekly_change_pct": evaluated["weekly_change_pct"],
                    "monthly_change_pct": evaluated["monthly_change_pct"],
                    "severity": evaluated["severity"],
                }
            )

    return {
        "updated_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        "regime": regime,
        "account_safety": verify_account_safety(),
        "actionable_count": len(actionable),
        "ignored_minor_count": len(ignored_noise),
        "major_shocks": actionable,
        "ignored_minor_moves": ignored_noise,
    }


LIVE_BENCHMARK_TICKERS: dict[str, tuple[str, str]] = {
    "PORK_HOGS": ("HE=F", "CME Lean Hogs Futures"),
    "CRUDE_OIL": ("BZ=F", "Brent Crude Oil"),
    "GOLD_XAU": ("GC=F", "COMEX Gold Futures"),
    "FED_DXY_RATES": ("DX-Y.NYB", "US Dollar Index (DXY)"),
    "STEEL_HRC": ("SLX", "VanEck Steel ETF"),
}


def fetch_live_macro_observations(*, ttl_seconds: int = 7200, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Fetch live 1-week (5d) and 1-month (20d) changes for Pork, Crude Oil, Gold, DXY/FED, and Steel."""
    cache_path = CACHE_DIR / "live_macro_observations.json"
    if not force_refresh and cache_path.exists():
        try:
            if time.time() - cache_path.stat().st_mtime <= ttl_seconds:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, list) and cached:
                    return cached
        except (OSError, json.JSONDecodeError):
            pass

    observations: list[dict[str, Any]] = []
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), headers=headers) as client:
        for factor_key, (ticker, label) in LIVE_BENCHMARK_TICKERS.items():
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1mo&interval=1d"
            try:
                resp = client.get(url)
                resp.raise_for_status()
                payload = resp.json()
                result = ((payload.get("chart") or {}).get("result") or [None])[0] or {}
                quote = (((result.get("indicators") or {}).get("quote") or [None])[0]) or {}
                closes = [
                    float(c)
                    for c in (quote.get("close") or [])
                    if c is not None and math.isfinite(float(c)) and float(c) > 0
                ]
                if len(closes) < 2:
                    continue
                last_close = closes[-1]
                prev_5d = closes[max(0, len(closes) - 6)]
                prev_20d = closes[0]
                weekly_pct = (last_close / prev_5d - 1.0) * 100.0 if prev_5d > 0 else 0.0
                monthly_pct = (last_close / prev_20d - 1.0) * 100.0 if prev_20d > 0 else 0.0
                observations.append(
                    {
                        "factor": factor_key,
                        "benchmark_ticker": ticker,
                        "benchmark_name": label,
                        "last_price": round(last_close, 2),
                        "weekly_change_pct": round(weekly_pct, 2),
                        "monthly_change_pct": round(monthly_pct, 2),
                        "source": "YAHOO_MACRO_LIVE",
                    }
                )
            except Exception as exc:
                logger.debug("Live macro fetch skipped for %s (%s): %s", factor_key, ticker, type(exc).__name__)

    if observations:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return observations


def load_macro_observations(path: Path | None = None, *, allow_live_fetch: bool | None = None) -> list[dict[str, Any]]:
    """Load macro/commodity shock observations from JSON env, local file, or live global feed."""
    raw_env = os.getenv("MACRO_SHOCKS_JSON", "").strip()
    if raw_env:
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, Mapping)]
        except json.JSONDecodeError:
            logger.warning("Invalid MACRO_SHOCKS_JSON environment variable")

    file_path = path or Path("data/macro_shocks.json")
    if file_path.exists():
        try:
            parsed = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, Mapping)]
            if isinstance(parsed, Mapping) and isinstance(parsed.get("observations"), list):
                return [item for item in parsed["observations"] if isinstance(item, Mapping)]
        except (OSError, json.JSONDecodeError):
            pass

    if allow_live_fetch is None:
        allow_live_fetch = os.getenv("MACRO_LIVE_FETCH_ENABLED", "0").strip() in {"1", "true", "yes", "on"}
    if allow_live_fetch:
        return fetch_live_macro_observations()
    return []


# ---------------------------------------------------------------------------
# Natural Language Scenario Analyzer (Heo / Dầu / Vàng / FED / Sắt Thép / ...)
# ---------------------------------------------------------------------------


def parse_natural_scenario(
    query: str,
    *,
    results: Mapping[str, Any] | None = None,
    metrics: Mapping[str, Mapping[str, Any]] | None = None,
    regime: str = "SIDEWAYS",
) -> dict[str, Any]:
    """Parse a Vietnamese scenario query (e.g. 'giá thịt heo tăng', 'dầu vàng fed biến động', 'giá sắt thép')
    and return concrete stock picks, % NAV sizing, and risk notes.
    """
    text = str(query or "").strip().lower()
    matched_factors: list[str] = []
    for factor_key, rule in MACRO_PLAYBOOK.items():
        if any(kw in text for kw in rule["keywords"]):
            matched_factors.append(factor_key)

    if not matched_factors:
        matched_factors = ["PORK_HOGS", "CRUDE_OIL", "GOLD_XAU", "FED_DXY_RATES", "STEEL_HRC"]

    pct_match = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*%", text)
    parsed_pct = float(pct_match.group(1).replace(",", ".")) if pct_match else None

    down_words = ("giảm", "lao dốc", "sập", "rơi", "hạ lãi suất", "cắt lãi suất", "dxy giảm", "suy yếu")
    up_words = ("tăng", "vọt", "bứt phá", "lập đỉnh", "leo thang", "tăng lãi suất", "dxy tăng")

    is_minor_explicit = any(w in text for w in ("nhỏ", "nhẹ", "đi ngang", "không đáng kể"))
    scenarios: list[dict[str, Any]] = []

    for factor_key in matched_factors:
        rule = MACRO_PLAYBOOK[factor_key]
        major_w = float(rule["major_weekly_pct"])

        if factor_key == "FED_DXY_RATES" and any(w in text for w in ("hạ lãi suất", "cắt lãi suất", "dovish", "dxy giảm")):
            direction_sign = -1.0
        elif any(w in text for w in down_words) and not any(w in text for w in up_words):
            direction_sign = -1.0
        else:
            direction_sign = 1.0

        if parsed_pct is not None:
            weekly_val = parsed_pct if parsed_pct < 0 else direction_sign * abs(parsed_pct)
            explicit_major = False
        elif is_minor_explicit:
            weekly_val = direction_sign * (major_w * 0.35)
            explicit_major = False
        else:
            weekly_val = direction_sign * (major_w * 1.25)
            explicit_major = True

        evaluated = evaluate_macro_shock(
            factor_key,
            weekly_change_pct=weekly_val,
            explicit_major_event=explicit_major,
            custom_note=query,
            results=results,
            metrics=metrics,
            regime=regime,
        )
        scenarios.append(evaluated)

    return {
        "query": query,
        "regime": regime,
        "matched_factors": matched_factors,
        "account_safety": verify_account_safety(),
        "scenarios": scenarios,
    }


# ---------------------------------------------------------------------------
# Read-Only SSI & TCBS Market Data & MCP Adapters
# ---------------------------------------------------------------------------


def call_broker_mcp(
    broker: str,
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Call an external Read-Only SSI or TCBS MCP endpoint via JSON-RPC 2.0 if configured."""
    assert_account_safety()
    broker_norm = str(broker or "").strip().upper()
    if broker_norm not in {"SSI", "TCBS"}:
        raise ValueError(f"Unsupported broker MCP: {broker}")

    endpoint = os.getenv(f"{broker_norm}_MCP_ENDPOINT", "").strip()
    api_key = os.getenv(f"{broker_norm}_MCP_API_KEY", "").strip()
    if not endpoint:
        raise MacroCatalystError(f"{broker_norm}_MCP_ENDPOINT is not configured")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "THIUCUBU/2.0 (Read-Only Market Scanner)",
    }
    if api_key:
        headers["x-api-key"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": dict(arguments or {}),
        },
    }
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        response = client.post(endpoint, headers=headers, json=body)
    if response.status_code in {401, 403}:
        raise MacroCatalystError(f"{broker_norm} MCP authentication failed (verify Read-Only token)")
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, Mapping) and isinstance(payload.get("result"), Mapping):
        result = payload["result"]
        for item in result.get("content", []):
            if isinstance(item, Mapping) and item.get("type") == "text":
                try:
                    decoded = json.loads(str(item.get("text") or "{}"))
                    if isinstance(decoded, Mapping):
                        return dict(decoded)
                except json.JSONDecodeError:
                    return {"text": str(item.get("text") or "")}
        return dict(result)
    raise MacroCatalystError(f"{broker_norm} MCP returned invalid JSON-RPC payload")


def fetch_ssi_tcbs_snapshot(symbol: str, *, prefer: str = "SSI") -> dict[str, Any]:
    """Fetch read-only market quote/OHLCV snapshot from SSI/TCBS MCP or public iBoard/DNSE APIs.

    Never touches personal trading accounts or order-execution APIs.
    """
    assert_account_safety()
    sym = str(symbol or "VNINDEX").strip().upper()
    prefer_norm = str(prefer or "SSI").strip().upper()
    order = ["SSI", "TCBS"] if prefer_norm == "SSI" else ["TCBS", "SSI"]

    # 1. Try configured MCP endpoints first if set
    for broker in order:
        if os.getenv(f"{broker}_MCP_ENDPOINT", "").strip():
            try:
                mcp_data = call_broker_mcp(broker, "get_quote", {"symbol": sym})
                return {
                    "symbol": sym,
                    "source": f"{broker}_MCP",
                    "read_only": True,
                    "data": mcp_data,
                }
            except Exception as exc:
                logger.warning("[%s_MCP] quote fallback for %s: %s", broker, sym, type(exc).__name__)

    # 2. Try public Read-Only SSI iBoard Charts API (verified 200 OK zero-auth) & TCBS / DNSE fallback
    now_ts = int(time.time())
    from_ts = now_ts - 40 * 86400
    for broker in order:
        try:
            if broker == "SSI":
                url = "https://iboard-api.ssi.com.vn/statistics/charts/history"
                params = {"resolution": "1D", "symbol": sym, "from": str(from_ts), "to": str(now_ts)}
                headers = {
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://iboard.ssi.com.vn/",
                }
                with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                    resp = client.get(url, params=params, headers=headers)
                    resp.raise_for_status()
                    raw = resp.json()
                data = raw.get("data") if isinstance(raw, Mapping) else {}
                closes = data.get("c") if isinstance(data, Mapping) else []
                vols = data.get("v") if isinstance(data, Mapping) else []
                if isinstance(closes, list) and len(closes) >= 2:
                    price = _safe_float(closes[-1])
                    prev_price = _safe_float(closes[-2], price)
                    prev_5d = _safe_float(closes[max(0, len(closes) - 6)], price)
                    if sym != "VNINDEX" and price >= 1000:
                        price = round(price / 1000.0, 2)
                        prev_price = prev_price / 1000.0
                        prev_5d = prev_5d / 1000.0
                    change_pct = (price / prev_price - 1.0) * 100.0 if prev_price > 0 else 0.0
                    change_5d_pct = (price / prev_5d - 1.0) * 100.0 if prev_5d > 0 else 0.0
                    return {
                        "symbol": sym,
                        "source": "SSI_IBOARD_PUBLIC_LIVE",
                        "read_only": True,
                        "price": round(price, 2),
                        "change_pct": round(change_pct, 2),
                        "change_5d_pct": round(change_5d_pct, 2),
                        "volume": int(_safe_float(vols[-1])) if isinstance(vols, list) and vols else 0,
                    }
            else:
                url = f"https://apipubaws.tcbs.com.vn/tcanalysis/v1/ticker/{sym}/overview"
                headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
                with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                    resp = client.get(url, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                if isinstance(data, Mapping) and data:
                    return {
                        "symbol": sym,
                        "source": "TCBS_PUBLIC_LIVE",
                        "read_only": True,
                        "industry": data.get("industry"),
                        "exchange": data.get("exchange"),
                        "stock_rating": _safe_float(data.get("stockRating")),
                        "delta_in_week": _safe_float(data.get("deltaInWeek")),
                        "delta_in_month": _safe_float(data.get("deltaInMonth")),
                    }
        except Exception as exc:
            logger.debug("[%s_PUBLIC] failed for %s: %s", broker, sym, exc)

    return {
        "symbol": sym,
        "source": "LOCAL_FALLBACK",
        "read_only": True,
        "price": None,
        "note": "Không lấy được snapshot trực tuyến từ SSI/TCBS, dùng dữ liệu OHLCV nội bộ.",
    }


def check_live_connections(test_symbol: str = "FPT") -> dict[str, Any]:
    """Verify live connectivity to SSI iBoard, TCBS/MCP, Yahoo Global Macro Feed, and Gemini AI Studio."""
    ssi_quote = fetch_ssi_tcbs_snapshot(test_symbol, prefer="SSI")
    macro_obs = fetch_live_macro_observations(force_refresh=True)
    gemini_ready = is_gemini_configured()
    ssi_mcp_ready = bool(os.getenv("SSI_MCP_ENDPOINT", "").strip())
    tcbs_mcp_ready = bool(os.getenv("TCBS_MCP_ENDPOINT", "").strip())

    return {
        "checked_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        "account_safety": verify_account_safety(),
        "ssi_iboard_public_api": {
            "connected": ssi_quote.get("source") == "SSI_IBOARD_PUBLIC_LIVE",
            "source": ssi_quote.get("source"),
            "sample_quote": ssi_quote,
        },
        "ssi_mcp_custom": {
            "configured": ssi_mcp_ready,
            "note": "Đã kết nối trực tiếp SSI iBoard Public API (Read-Only) nên không bắt buộc cấu hình SSI_MCP_ENDPOINT.",
        },
        "tcbs_mcp_custom": {
            "configured": tcbs_mcp_ready,
            "note": "Có thể gắn thêm TCBS_MCP_ENDPOINT nếu dùng MCP riêng; mặc định tự động fallback sang SSI iBoard Public.",
        },
        "global_macro_commodities_live": {
            "connected": len(macro_obs) > 0,
            "count": len(macro_obs),
            "observations": macro_obs,
        },
        "gemini_ai_studio": {
            "configured": gemini_ready,
            "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            "how_to_enable": (
                "Đã nạp GEMINI_API_KEY."
                if gemini_ready
                else "Lấy key miễn phí tại https://aistudio.google.com/app/apikey và thêm vào GitHub Secrets (GEMINI_API_KEY) hoặc file .env."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Google AI Studio (Gemini API) Integration with Search Grounding & Fallback
# ---------------------------------------------------------------------------


def is_gemini_configured() -> bool:
    return bool(
        os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
    )


def _gemini_api_key() -> str:
    return (
        os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
    )


def ask_gemini_macro_advisor(
    query: str,
    *,
    results: Mapping[str, Any] | None = None,
    metrics: Mapping[str, Mapping[str, Any]] | None = None,
    regime: str = "SIDEWAYS",
    enable_search_grounding: bool = True,
) -> dict[str, Any]:
    """Query Google AI Studio Gemini API (with Google Search grounding) for major macro/commodity
    catalysts, combined with deterministic THIUCUBU rules and % NAV sizing.

    If GEMINI_API_KEY is not configured or the network fails, seamlessly falls back to
    the deterministic rule-based catalyst engine so workflows and tests never break.
    """
    assert_account_safety()
    deterministic = parse_natural_scenario(
        query,
        results=results,
        metrics=metrics,
        regime=regime,
    )

    api_key = _gemini_api_key()
    if not api_key:
        return {
            **deterministic,
            "provider": "DETERMINISTIC_PLAYBOOK",
            "gemini_configured": False,
            "ai_synthesis": None,
        }

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    scenario_summaries = []
    for sc in deterministic.get("scenarios", []):
        top_picks = [
            f"{p['symbol']} ({p['nav_range']}, score={p['advanced_score']})"
            for p in sc.get("stock_recommendations", [])[:4]
        ]
        scenario_summaries.append(
            f"- {sc['name']} ({sc['severity']}): {sc['headline']} | Cổ phiếu: {', '.join(top_picks)} | Lưu ý: {'; '.join(sc.get('watch_notes', [])[:2])}"
        )

    prompt = (
        "Bạn là Giám đốc Chiến lược Đầu tư Chứng khoán Việt Nam (hệ thống THIUCUBU).\n"
        f"Bối cảnh thị trường hiện tại (Regime): {regime}.\n"
        f"Yêu cầu phân tích: {query}\n\n"
        "Quy tắc bắt buộc:\n"
        "1. BỎ QUA hoàn toàn các biến động quốc tế nhỏ lẻ nhiễu. Chỉ tập trung vào CÚ SỐC LỚN / BIẾN ĐỘNG LỚN có khả năng tạo sóng ngành hoặc rủi ro vĩ mô.\n"
        "2. Trả lời ngắn gọn bằng Tiếng Việt: (a) Biến động đó tác động thế nào, (b) Canh mua cổ phiếu Việt Nam nào & tránh cổ phiếu nào, (c) Khuyến nghị tỷ trọng bao nhiêu % NAV cho từng mã và cả ngành, (d) Điều cần đặc biệt chú ý (tồn kho, chi phí đầu vào, tỷ giá).\n"
        "Dữ liệu định lượng từ bộ lọc THIUCUBU:\n"
        + "\n".join(scenario_summaries)
    )

    payload: dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 700,
        },
    }
    if enable_search_grounding and os.getenv("GEMINI_ENABLE_SEARCH_GROUNDING", "1").strip() != "0":
        payload["tools"] = [{"google_search": {}}]

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(url, params={"key": api_key}, json=payload)
        if resp.status_code in {401, 403}:
            raise MacroCatalystError("Gemini API authentication failed; check GEMINI_API_KEY")
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") if isinstance(data, Mapping) else []
        ai_text = ""
        if isinstance(candidates, list) and candidates:
            content = candidates[0].get("content") if isinstance(candidates[0], Mapping) else {}
            parts = content.get("parts") if isinstance(content, Mapping) else []
            if isinstance(parts, list):
                ai_text = "\n".join(
                    str(p.get("text") or "").strip()
                    for p in parts
                    if isinstance(p, Mapping) and p.get("text")
                ).strip()
        return {
            **deterministic,
            "provider": f"GEMINI_AI_STUDIO ({model})",
            "gemini_configured": True,
            "ai_synthesis": ai_text or None,
        }
    except Exception as exc:
        safe_msg = type(exc).__name__
        logger.warning("Gemini AI Studio fallback to deterministic playbook (%s)", safe_msg)
        return {
            **deterministic,
            "provider": "DETERMINISTIC_PLAYBOOK_FALLBACK",
            "gemini_configured": True,
            "ai_error": safe_msg,
            "ai_synthesis": None,
        }


def format_macro_catalyst_section(
    shock_report: Mapping[str, Any] | None,
    *,
    include_quick_reference: bool = True,
) -> list[str]:
    """Format actionable macro/commodity shocks and % NAV playbook for Telegram reports."""
    header = "*🌍 CÚ SỐC VĨ MÔ & HÀNG HÓA LỚN (GEMINI · SSI/TCBS) — MUA GÌ & % NAV*"
    lines = [header]

    shocks = list((shock_report or {}).get("major_shocks") or [])
    ignored_count = int((shock_report or {}).get("ignored_minor_count") or 0)

    if shocks:
        for item in shocks[:5]:
            lines.append(
                f"🔥 *{item['headline']}* (`{item['weekly_change_pct']:+.1f}%/tuần` | Phân bổ: *{item['overall_nav_guidance']}*)"
            )
            picks = item.get("stock_recommendations") or []
            if picks:
                pick_bits = []
                for p in picks[:4]:
                    score_str = f"{p['advanced_score']}/97" if p.get("advanced_score") is not None else "n/a"
                    conv_tag = " ⚡LọcGắt" if p.get("strict_conviction") else ""
                    pick_bits.append(f"`{p['symbol']}` ({p['nav_range']} · Intel {score_str}{conv_tag})")
                lines.append("   ↳ *Canh mua:* " + " | ".join(pick_bits))
            avoid = item.get("avoid_tickers") or []
            if avoid:
                lines.append("   ↳ *Cần tránh/Hạ tỷ trọng:* " + ", ".join(f"`{s}`" for s in avoid[:5]))
            notes = item.get("watch_notes") or []
            if notes:
                lines.append(f"   ↳ *Chú ý:* {notes[0]}")
        if ignored_count > 0:
            lines.append(f"_(Đã tự động lọc bỏ {ignored_count} biến động quốc tế nhỏ lẻ không đủ ngưỡng giao dịch)_")
        return lines

    if not include_quick_reference:
        lines.append("Không có biến động vĩ mô/hàng hóa quốc tế vượt ngưỡng cú sốc lớn trong phiên.")
        return lines

    lines.append(
        "Chưa có cú sốc quốc tế vượt ngưỡng lớn (bỏ qua nhiễu nhỏ). *Bản đồ phản ứng nhanh khi có biến động lớn:*"
    )
    lines.append("• *Giá thịt heo tăng mạnh (>=+4%/tuần)* → Canh mua `DBC`, `BAF`, `HAG` (*6%–10% NAV/mã*, ưu tiên chuỗi 3F).")
    lines.append("• *Giá Dầu Brent tăng sốc (>=+4.5%/tuần)* → Mua `PVD`, `PVS`, `BSR`, `GAS`, `PVT` (*8%–12% NAV/mã*); chú ý chi phí vận tải/nhựa.")
    lines.append("• *Giá Vàng phá đỉnh (>=+3%/tuần)* → Chú ý `PNJ` (*6%–10% NAV*) & nâng tiền mặt phòng thủ *25%–35% NAV*.")
    lines.append("• *FED/DXY biến động lớn*: Hạ lãi suất/DXY giảm → Mua `SSI`, `VCI`, `TCB`, `MBB`, `KDH` (*10%–14% NAV*); DXY/Tỷ giá căng → Giữ tiền mặt + xuất khẩu USD `FPT`, `DGC`, `VHC`, `GMD` (*4%–7% NAV*).")
    lines.append("• *Giá Sắt Thép/HRC tăng mạnh (>=+4%/tuần)* → Canh `HPG` (*8%–12% NAV*), `HSG`, `NKG` (*5%–8% NAV*); chú ý rủi ro chi phí thầu xây dựng `VCG`, `CTD`, `HHV`.")
    return lines
