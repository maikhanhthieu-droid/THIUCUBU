from __future__ import annotations

import json
from pathlib import Path

import pytest

import macro_catalyst as mc
import mcp_market_server as mcp_srv
import scan
from scripts import safe_commit_data


@pytest.fixture(autouse=True)
def clean_macro_env(monkeypatch):
    for var in mc.FORBIDDEN_TRADING_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("SSI_MCP_ENDPOINT", raising=False)
    monkeypatch.delenv("TCBS_MCP_ENDPOINT", raising=False)
    monkeypatch.delenv("MACRO_SHOCKS_JSON", raising=False)


def test_account_security_guard_blocks_live_trading_credentials(monkeypatch, tmp_path):
    status = mc.verify_account_safety()
    assert status["safe"] is True
    assert status["mode"] == "SAFE_READ_ONLY"
    assert status["ssi_trading_account_affected"] is False

    monkeypatch.setenv("SSI_PRIVATE_KEY", "secret-rsa-trading-key")
    unsafe_status = mc.verify_account_safety()
    assert unsafe_status["safe"] is False
    assert unsafe_status["mode"] == "UNSAFE_TRADING_KEY_PRESENT"
    assert "SSI_PRIVATE_KEY" in unsafe_status["exposed_trading_vars"]

    with pytest.raises(mc.AccountSecurityError):
        mc.assert_account_safety()

    # Verify safe_commit_data blocks accidental secret commits
    leaked_file = tmp_path / "leaked.json"
    leaked_file.write_text('{"key": "AIzaSyD-fake-gemini-key"}', encoding="utf-8")
    assert safe_commit_data.validate_json_file(leaked_file) is False

    clean_file = tmp_path / "clean.json"
    clean_file.write_text('{"mode": "SAFE_READ_ONLY"}', encoding="utf-8")
    assert safe_commit_data.validate_json_file(clean_file) is True


def test_minor_international_fluctuations_are_filtered_out_and_major_shocks_trigger_picks():
    observations = [
        {"factor": "PORK_HOGS", "weekly_change_pct": 1.2, "monthly_change_pct": 2.0},  # Minor noise -> ignored
        {"factor": "CRUDE_OIL", "weekly_change_pct": -1.5, "monthly_change_pct": -2.5},  # Minor noise -> ignored
        {"factor": "PORK_HOGS", "weekly_change_pct": 6.5, "monthly_change_pct": 10.0},  # Major pork surge!
        {"factor": "STEEL_HRC", "weekly_change_pct": 5.2, "monthly_change_pct": 8.5},  # Major steel surge!
    ]
    results = {
        "DBC": scan.ScanResult(
            symbol="DBC",
            sector="Nong nghiep chan nuoi",
            close=30.5,
            win_score=84,
            setup="VCP_BREAK",
            discount_pct=12.0,
            target_discount_pct=15.0,
            discount_group="G6",
            trend_score=30,
            base_score=20,
            flow_score=20,
            break_score=14,
            risk_score=0,
            rsi=62.0,
            mfi=65.0,
            vol_ratio=1.5,
            obv_up=True,
            near_break=True,
            failed_break=False,
            warning="",
            reason="ok",
        ),
        "HPG": scan.ScanResult(
            symbol="HPG",
            sector="Thep",
            close=28.0,
            win_score=82,
            setup="BASE",
            discount_pct=14.0,
            target_discount_pct=15.0,
            discount_group="G4",
            trend_score=28,
            base_score=20,
            flow_score=20,
            break_score=14,
            risk_score=0,
            rsi=60.0,
            mfi=60.0,
            vol_ratio=1.3,
            obv_up=True,
            near_break=True,
            failed_break=False,
            warning="",
            reason="ok",
        ),
    }
    metrics = {
        "DBC": {
            "advanced_score": 86,
            "breakout_acceleration": {"strict_conviction": True, "tier": "SQUEEZE_IGNITION"},
            "trade": {"entry_zone": "30.0-30.6", "stop_loss": 28.4},
        },
        "HPG": {
            "advanced_score": 82,
            "breakout_acceleration": {"strict_conviction": True, "tier": "POCKET_PIVOT_CONVICTION"},
            "trade": {"entry_zone": "27.8-28.2", "stop_loss": 26.5},
        },
    }

    report = mc.evaluate_all_major_shocks(observations, results=results, metrics=metrics, regime="BULL")
    assert report["ignored_minor_count"] == 2
    assert report["actionable_count"] == 2

    pork_shock = next(item for item in report["major_shocks"] if item["factor"] == "PORK_HOGS")
    assert "DBC" in pork_shock["primary_tickers"]
    assert "BAF" in pork_shock["primary_tickers"]
    dbc_rec = next(p for p in pork_shock["stock_recommendations"] if p["symbol"] == "DBC")
    assert dbc_rec["recommended_nav_pct"] >= 8.0
    assert dbc_rec["sizing_tier"] == "FULL_CONVICTION"

    steel_shock = next(item for item in report["major_shocks"] if item["factor"] == "STEEL_HRC")
    assert "HPG" in steel_shock["primary_tickers"]
    assert any("VCG" in note or "xây dựng" in note.lower() for note in steel_shock["watch_notes"])

    lines = mc.format_macro_catalyst_section(report)
    joined = "\n".join(lines)
    assert "GIÁ THỊT HEO TĂNG MẠNH" in joined
    assert "`DBC`" in joined
    assert "GIÁ SẮT THÉP / HRC TĂNG MẠNH" in joined
    assert "`HPG`" in joined


def test_natural_scenarios_for_pork_oil_gold_fed_and_steel():
    pork = mc.parse_natural_scenario("khi giá thịt heo tăng -> canh mua heo", regime="BULL")
    assert "PORK_HOGS" in pork["matched_factors"]
    assert pork["scenarios"][0]["is_actionable"] is True
    assert "DBC" in pork["scenarios"][0]["primary_tickers"]
    assert "NAV" in pork["scenarios"][0]["overall_nav_guidance"]

    oil_gold_fed = mc.parse_natural_scenario("giá dầu vàng fed biến động mạnh -> mua gì bao nhiêu % nav", regime="SIDEWAYS")
    assert set(oil_gold_fed["matched_factors"]) >= {"CRUDE_OIL", "GOLD_XAU", "FED_DXY_RATES"}
    for sc in oil_gold_fed["scenarios"]:
        assert sc["is_actionable"] is True
        assert sc["primary_tickers"]
        assert "%–" in sc["overall_nav_guidance"] or "% NAV" in sc["overall_nav_guidance"]

    steel = mc.parse_natural_scenario("giá sắt thép biến động -> chú ý gì", regime="BULL")
    assert "STEEL_HRC" in steel["matched_factors"]
    assert "HPG" in steel["scenarios"][0]["primary_tickers"]
    assert steel["scenarios"][0]["watch_notes"]


def test_gemini_advisor_and_mcp_server_jsonrpc(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    class DummyResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": "Khi giá thịt heo tăng mạnh >5%/tuần: Canh mua DBC (8-10% NAV), BAF (6-8% NAV)."
                                }
                            ]
                        }
                    }
                ]
            }

    class DummyClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, *args, **kwargs):
            return DummyResp()

    monkeypatch.setattr(mc.httpx, "Client", DummyClient)

    res = mc.ask_gemini_macro_advisor("giá thịt heo tăng mạnh mua gì", regime="BULL")
    assert res["gemini_configured"] is True
    assert "DBC" in (res["ai_synthesis"] or "")

    # Verify MCP JSON-RPC tools/list and tools/call
    init_resp = mcp_srv.handle_jsonrpc_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_resp is not None
    assert init_resp["result"]["serverInfo"]["name"] == "thiucubu-readonly-market-mcp"

    list_resp = mcp_srv.handle_jsonrpc_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert list_resp is not None
    tool_names = {t["name"] for t in list_resp["result"]["tools"]}
    assert {
        "check_account_security",
        "get_ssi_tcbs_quote",
        "scan_major_macro_shocks",
        "analyze_scenario_impact",
    }.issubset(tool_names)

    call_resp = mcp_srv.handle_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "analyze_scenario_impact",
                "arguments": {"query": "giá dầu vàng fed biến động mua gì", "regime": "BULL"},
            },
        }
    )
    assert call_resp is not None
    assert call_resp["result"]["isError"] is False
    structured = call_resp["result"]["structuredContent"]
    assert structured["account_safety"]["safe"] is True
    assert len(structured["scenarios"]) >= 3
