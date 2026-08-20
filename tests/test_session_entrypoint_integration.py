from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_session_gate_entrypoint_runs_with_mocked_scan(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    script = tmp_path / "run_entrypoint.py"
    script.write_text(
        r'''
import asyncio
import os
import sys
from datetime import datetime

import pandas as pd

repo = os.environ["REPO_UNDER_TEST"]
sys.path.insert(0, repo)
os.chdir(os.environ["TMP_RUN_DIR"])

import scan
import session_gate_near_high as entry

sent = []


def fake_df(symbol: str):
    dates = pd.date_range("2025-01-01", periods=260, freq="B")
    base = 1000.0 if symbol == "VNINDEX" else 20.0
    close = pd.Series([base + idx * 0.1 for idx in range(len(dates))])
    return pd.DataFrame(
        {
            "time": dates,
            "open": close - 0.05,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": [1000000 + idx for idx in range(len(dates))],
        }
    )


def fake_result(symbol: str):
    if symbol == "VNINDEX":
        return scan.ScanResult(symbol, "Index", 1026.0, 75, "INDEX", 0, 0, "INDEX", 60, 0, 15, 0, 0, 55, 60, 1.0, True, True, False, "", "test index")
    score = 88 if symbol in {"VCB", "TCB"} else 66
    return scan.ScanResult(symbol, "Test", 25.0, score, "VCP_BREAK", 20, 15, "G2", 30, 20, 20, 18, 0, 55, 60, 1.1, True, True, False, "", "integration")


async def fake_scan_symbols(symbols, force_refresh, history_store, peak_store, label, **kwargs):
    output = {}
    for symbol in symbols:
        df = fake_df(symbol)
        output[symbol] = fake_result(symbol)
        scan.save_history(symbol, df, history_store, peak_store)
    return output


async def fake_send_chunks(title, text):
    sent.append((title, text))


entry.gate.plus.sess.scan_symbols = fake_scan_symbols
scan.send_chunks = fake_send_chunks
entry.gate.plus.scan.send_chunks = fake_send_chunks
entry.gate.plus.sess.SESSION_RANDOM_START_MAX = 0

sys.argv = ["session_gate_near_high.py", "--mode", "test"]
asyncio.run(entry.gate.plus.main())

latest = scan.json_load(scan.DATA_DIR / "session_alerts_latest.json", {})
assert latest.get("mode") == "test"
assert latest.get("market") is not None
assert sent, "report was not sent"
assert "THIEUCUBU TEST" in sent[0][1]
assert "TÓM TẮT 5 LUỒNG" in sent[0][1]
assert "LUỒNG 1 — PORTFOLIO" in sent[0][1]
assert "LUỒNG 5 — BREAK XỊT" in sent[0][1]
print("integration-ok", datetime.now().isoformat())
''',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["REPO_UNDER_TEST"] = str(repo)
    env["TMP_RUN_DIR"] = str(tmp_path)
    env["DRY_RUN"] = "1"
    env["MARKET_CLOSED_POLICY"] = "scan_old"
    completed = subprocess.run(
        [sys.executable, str(script)],
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "integration-ok" in completed.stdout
