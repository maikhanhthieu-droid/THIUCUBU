"""Pure VNINDEX-to-horizon policy used by reports and downstream consumers."""

from __future__ import annotations

from typing import Any


def horizon_strategy(
    market: Any | None,
    regime: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Translate VNINDEX state into separate scalp/hold/accumulate postures."""

    name = str((regime or {}).get("regime") or "UNKNOWN").upper()
    score = int(getattr(market, "win_score", 0) or 0)
    structure = str(getattr(market, "market_state", "NO_DATA") or "NO_DATA").upper()
    failed = bool(getattr(market, "failed_break", False))
    risk_off = failed or name == "BEAR" or structure == "DISTRIBUTION" or (market is not None and score < 45)
    constructive = name == "BULL" and score >= 62 and structure != "DISTRIBUTION"
    recovering = name == "RECOVERY" or (score >= 55 and structure in {"OPPORTUNITY", "ACCUMULATION"})

    if market is None:
        return {
            "posture": "THIẾU DỮ LIỆU — KHÔNG TĂNG RỦI RO",
            "scalp": "Chỉ quan sát; chưa mở lệnh mới khi VNINDEX chưa xác nhận.",
            "hold": "Giữ theo stop riêng từng mã, không suy diễn từ dữ liệu cũ.",
            "accumulate": "Tạm hoãn giải ngân mới.",
            "risk": "Cao do thiếu dữ liệu; radar và 5 luồng vẫn tiếp tục quét.",
        }
    if risk_off:
        return {
            "posture": "PHÒNG THỦ / CẨN TRỌNG CAO",
            "scalp": "Không mua đuổi; chỉ lướt rất nhỏ ở mã mạnh độc lập, có stop rõ.",
            "hold": "Rà soát hỗ trợ; giảm phần yếu/gãy nền, không bán sạch máy móc.",
            "accumulate": "Chưa gom mới; chỉ lập danh sách chờ reclaim và cạn cung.",
            "risk": "Ưu tiên tiền mặt và bảo vệ vị thế; hệ thống vẫn quét, vẫn báo.",
        }
    if constructive:
        return {
            "posture": "TÍCH CỰC CÓ CHỌN LỌC",
            "scalp": "Được phép lướt mã Luồng 2 có trigger, thanh khoản và R/R đạt chuẩn.",
            "hold": "Tiếp tục cầm mã khỏe; chốt từng phần khi quá xa nền/kháng cự.",
            "accumulate": "Có thể gom từng phần mã nền chặt; không giải ngân một lần.",
            "risk": "Không mua đuổi mã đã kéo; stop và tỷ trọng vẫn theo từng cổ phiếu.",
        }
    if recovering:
        return {
            "posture": "HỒI PHỤC / THĂM DÒ",
            "scalp": "Chỉ lướt vị thế nhỏ khi giá và dòng tiền cùng xác nhận.",
            "hold": "Giữ mã mạnh hơn VNINDEX; hạ mã hồi yếu hoặc mất hỗ trợ.",
            "accumulate": "Gom thăm dò từng phần ở Luồng 2/3, chờ nâng tỷ trọng sau xác nhận.",
            "risk": "Tránh dùng tỷ trọng lớn khi xu hướng trung hạn chưa đồng thuận.",
        }
    return {
        "posture": "TRUNG TÍNH / TÍCH LŨY THẬN TRỌNG",
        "scalp": "Lướt nhỏ, ưu tiên mua gần hỗ trợ và bán từng phần gần kháng cự.",
        "hold": "Cầm mã còn cấu trúc; không tăng tỷ trọng mã suy yếu.",
        "accumulate": "Chỉ gom thăm dò khi cạn cung; chờ VNINDEX xác nhận trước khi tăng.",
        "risk": "Thị trường dễ nhiễu; radar vẫn báo nhưng tín hiệu phải qua 5 luồng.",
    }


def format_lines(strategy: dict[str, str]) -> list[str]:
    return [
        f"*QUYẾT ĐỊNH CUỐI PHIÊN* — `{strategy['posture']}`",
        f"LƯỚT: {strategy['scalp']}",
        f"CẦM: {strategy['hold']}",
        f"GOM: {strategy['accumulate']}",
        f"RỦI RO: {strategy['risk']}",
    ]
