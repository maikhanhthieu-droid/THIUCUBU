#!/usr/bin/env python3
"""Read-Only MCP Server for THIUCUBU (SSI + TCBS + Gemini AI Studio + Macro Shock Playbook).

Security Architecture:
- 100% READ-ONLY: This MCP server never connects to SSI/TCBS order-execution endpoints
  and actively blocks live trading credentials (SSI_PRIVATE_KEY, SSI_TRADING_PIN, etc.).
- Safe to commit & push to GitHub: Contains only code and public/read-only adapters.
  API keys (such as GEMINI_API_KEY) are read from environment variables at runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import macro_catalyst
import scan


MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "check_account_security",
        "description": (
            "Kiểm tra an toàn tài khoản giao dịch SSI/TCBS. Đảm bảo hệ thống chỉ chạy ở chế độ "
            "Read-Only (chỉ đọc dữ liệu thị trường), tuyệt đối không ảnh hưởng tài khoản giao dịch thật."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "check_live_connections",
        "description": (
            "Kiểm tra trạng thái kết nối thực tế (Live Connection Status) tới SSI iBoard Public API, "
            "TCBS/MCP, Dữ liệu Hàng hóa/Vĩ mô Quốc tế (Heo, Dầu, Vàng, FED/DXY, Sắt thép), và Google AI Studio (Gemini)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "FPT"},
            },
        },
    },
    {
        "name": "get_ssi_tcbs_quote",
        "description": "Lấy dữ liệu báo giá và dòng tiền khối ngoại Read-Only từ SSI / TCBS cho một mã cổ phiếu.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Mã cổ phiếu Việt Nam (VD: FPT, HPG, DBC, VCB)"},
                "prefer": {"type": "string", "enum": ["SSI", "TCBS"], "default": "SSI"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "scan_major_macro_shocks",
        "description": (
            "Quét các biến động vĩ mô & hàng hóa quốc tế (Thịt heo, Dầu, Vàng, FED/DXY, Sắt thép, Phân bón, Cước biển). "
            "Tự động lọc bỏ biến động nhỏ nhiễu, chỉ giữ các cú sốc lớn kèm danh sách cổ phiếu canh mua và % NAV."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "observations": {
                    "type": "array",
                    "description": "Danh sách biến động đầu vào [{factor, weekly_change_pct, monthly_change_pct}]",
                    "items": {"type": "object"},
                },
                "regime": {"type": "string", "default": "SIDEWAYS"},
            },
        },
    },
    {
        "name": "analyze_scenario_impact",
        "description": (
            "Phân tích kịch bản biến động bằng Tiếng Việt (VD: 'giá thịt heo tăng', 'giá dầu vàng fed biến động', "
            "'giá sắt thép tăng mạnh chú ý gì') -> Trả về cổ phiếu mua gì, % NAV bao nhiêu, và lưu ý rủi ro."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Câu hỏi kịch bản biến động hàng hóa / vĩ mô"},
                "regime": {"type": "string", "default": "SIDEWAYS"},
                "use_gemini": {"type": "boolean", "default": True},
            },
            "required": ["query"],
        },
    },
]


def _load_latest_scanner_context() -> tuple[dict[str, Any], dict[str, Any], str]:
    """Load latest local scanner results and metrics if available to enrich macro picks."""
    alerts = scan.json_load(Path("data/session_alerts_latest.json"), {})
    regime_name = "SIDEWAYS"
    if isinstance(alerts, Mapping):
        reg = alerts.get("market_regime")
        if isinstance(reg, Mapping) and reg.get("regime"):
            regime_name = str(reg["regime"])

    raw_results = scan.json_load(Path("data/results_latest.json"), [])
    results_map: dict[str, Any] = {}
    if isinstance(raw_results, list):
        for item in raw_results:
            if isinstance(item, Mapping) and item.get("symbol"):
                sym = str(item["symbol"]).upper()
                try:
                    results_map[sym] = scan.ScanResult(**item)
                except TypeError:
                    pass

    metrics_map: dict[str, Any] = {}
    if isinstance(alerts, Mapping) and isinstance(alerts.get("advanced_top"), Mapping):
        metrics_map = dict(alerts["advanced_top"])
    return results_map, metrics_map, regime_name


def handle_tool_call(name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Execute one MCP tool call safely in Read-Only mode."""
    args = dict(arguments or {})
    if name == "check_account_security":
        return macro_catalyst.verify_account_safety()

    if name == "check_live_connections":
        symbol = str(args.get("symbol") or "FPT").strip().upper()
        return macro_catalyst.check_live_connections(symbol=symbol)

    if name == "get_ssi_tcbs_quote":
        symbol = str(args.get("symbol") or "VNINDEX").strip().upper()
        prefer = str(args.get("prefer") or "SSI").strip().upper()
        return macro_catalyst.fetch_ssi_tcbs_snapshot(symbol, prefer=prefer)

    results_map, metrics_map, detected_regime = _load_latest_scanner_context()
    regime = str(args.get("regime") or detected_regime or "SIDEWAYS").upper()

    if name == "scan_major_macro_shocks":
        obs = args.get("observations") if isinstance(args.get("observations"), list) else None
        return macro_catalyst.evaluate_all_major_shocks(
            obs,
            results=results_map,
            metrics=metrics_map,
            regime=regime,
        )

    if name == "analyze_scenario_impact":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("Missing required argument: query")
        use_gemini = bool(args.get("use_gemini", True))
        if use_gemini:
            return macro_catalyst.ask_gemini_macro_advisor(
                query,
                results=results_map,
                metrics=metrics_map,
                regime=regime,
            )
        return macro_catalyst.parse_natural_scenario(
            query,
            results=results_map,
            metrics=metrics_map,
            regime=regime,
        )

    raise ValueError(f"Unknown MCP tool: {name}")


def handle_jsonrpc_request(request: Mapping[str, Any]) -> dict[str, Any] | None:
    """Process a single MCP JSON-RPC 2.0 request message."""
    method = str(request.get("method") or "")
    req_id = request.get("id")

    if method == "notifications/initialized":
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "thiucubu-readonly-market-mcp",
                    "version": "2.0.0",
                },
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": MCP_TOOLS},
        }

    if method == "tools/call":
        params = request.get("params") if isinstance(request.get("params"), Mapping) else {}
        tool_name = str(params.get("name") or "")
        tool_args = params.get("arguments") if isinstance(params.get("arguments"), Mapping) else {}
        try:
            output = handle_tool_call(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(output, ensure_ascii=False),
                        }
                    ],
                    "structuredContent": output,
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": str(exc)}, ensure_ascii=False)}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve_stdio() -> None:
    """Run MCP server over stdin/stdout (JSON-RPC 2.0 line-delimited)."""
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(req, Mapping):
            continue
        resp = handle_jsonrpc_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="THIUCUBU Read-Only Market & Macro Catalyst MCP Server")
    parser.add_argument("--query", help="Phân tích nhanh kịch bản vĩ mô/hàng hóa bằng Tiếng Việt")
    parser.add_argument("--check-security", action="store_true", help="Kiểm tra an toàn tài khoản giao dịch SSI/TCBS")
    parser.add_argument(
        "--check-connections",
        action="store_true",
        help="Kiểm tra trạng thái kết nối trực tiếp SSI, TCBS, Vĩ mô Quốc tế và Google AI Studio",
    )
    args = parser.parse_args()

    if args.check_security:
        print(json.dumps(macro_catalyst.verify_account_safety(), ensure_ascii=False, indent=2))
        return
    if args.check_connections:
        print(json.dumps(macro_catalyst.check_live_connections(), ensure_ascii=False, indent=2))
        return
    if args.query:
        res = handle_tool_call("analyze_scenario_impact", {"query": args.query})
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    serve_stdio()


if __name__ == "__main__":
    main()
