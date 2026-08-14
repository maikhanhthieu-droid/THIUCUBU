#!/usr/bin/env python3
"""Safe one-call smoke test for the optional VIMO provider."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vimo_provider


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="FPT")
    args = parser.parse_args()
    if not vimo_provider.is_configured():
        print("VIMO_API_KEY is not configured")
        return 2
    try:
        result = vimo_provider.fetch_ta_signal(args.symbol, ttl_seconds=0)
    except vimo_provider.VimoError as exc:
        print(f"VIMO check failed: {type(exc).__name__}")
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "symbol": result.get("symbol"),
                "signal": result.get("signal"),
                "confidence": result.get("confidence"),
                "date": result.get("date"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
