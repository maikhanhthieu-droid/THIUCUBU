#!/usr/bin/env python3
"""Minimal FiinQuantX smoke test; never prints credentials or market payloads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config import get_settings
import fetcher


def main() -> None:
    get_settings()  # Also loads a local .env file when the check is run off GitHub.
    vn_tz = timezone(timedelta(hours=7))
    end = datetime.now(vn_tz).date()
    start = end - timedelta(days=180)
    frame = fetcher.fetch_source_history(
        "FIINQUANT",
        "VCB",
        start.isoformat(),
        end.isoformat(),
    )
    if frame is None or len(frame) < 20:
        raise RuntimeError("FiinQuantX sample returned fewer than 20 daily bars")
    as_of = frame["time"].max().date().isoformat()
    print(f"FiinQuantX OK: login succeeded; VCB daily bars={len(frame)}; latest={as_of}")


if __name__ == "__main__":
    main()
