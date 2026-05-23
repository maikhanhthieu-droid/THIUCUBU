#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

VN_TZ = timezone(timedelta(hours=7))
DATA_DIR = Path("data")
JOURNAL_PATH = DATA_DIR / "run_journal.json"
JOURNAL_LIMIT = int(os.getenv("RUN_JOURNAL_LIMIT", "80") or "80")


def now_vn() -> datetime:
    return datetime.now(VN_TZ)


def _load(path: Path = JOURNAL_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "runs": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "runs": []}
    if isinstance(raw, list):
        return {"version": 1, "runs": raw}
    if not isinstance(raw, dict):
        return {"version": 1, "runs": []}
    runs = raw.get("runs", [])
    return {"version": int(raw.get("version") or 1), "runs": runs if isinstance(runs, list) else []}


def _save(data: dict[str, Any], path: Path = JOURNAL_PATH) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    runs = data.get("runs", [])
    if not isinstance(runs, list):
        runs = []
    data["version"] = int(data.get("version") or 1)
    data["last_updated"] = now_vn().isoformat(timespec="seconds")
    data["runs"] = runs[-max(1, JOURNAL_LIMIT):]
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return data


def load_journal(path: Path = JOURNAL_PATH) -> dict[str, Any]:
    return _load(path)


def append_event(event: dict[str, Any], path: Path = JOURNAL_PATH) -> dict[str, Any]:
    data = _load(path)
    item = dict(event)
    item.setdefault("updated_at", now_vn().isoformat(timespec="seconds"))
    data["runs"].append(item)
    return _save(data, path)


def start_run(mode: str, trigger: str = "", path: Path = JOURNAL_PATH) -> str:
    run_id = f"{now_vn().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    append_event(
        {
            "run_id": run_id,
            "mode": mode,
            "trigger": trigger,
            "status": "started",
            "started_at": now_vn().isoformat(timespec="seconds"),
        },
        path,
    )
    return run_id


def finish_run(
    run_id: str,
    mode: str,
    status: str,
    success_count: int = 0,
    failed_symbols: list[str] | None = None,
    elapsed_sec: float | None = None,
    telegram_sent: bool = False,
    path: Path = JOURNAL_PATH,
) -> dict[str, Any]:
    failed = sorted(set(failed_symbols or []))
    return append_event(
        {
            "run_id": run_id,
            "mode": mode,
            "status": status,
            "finished_at": now_vn().isoformat(timespec="seconds"),
            "success_count": int(success_count),
            "failed_count": len(failed),
            "failed_symbols": failed[:50],
            "elapsed_sec": round(float(elapsed_sec or 0.0), 1),
            "telegram_sent": bool(telegram_sent),
        },
        path,
    )


def fail_run(
    run_id: str,
    mode: str,
    error: BaseException | str,
    elapsed_sec: float | None = None,
    fallback_sent: bool = False,
    path: Path = JOURNAL_PATH,
) -> dict[str, Any]:
    return append_event(
        {
            "run_id": run_id,
            "mode": mode,
            "status": "failed",
            "finished_at": now_vn().isoformat(timespec="seconds"),
            "error": str(error)[:500],
            "elapsed_sec": round(float(elapsed_sec or 0.0), 1),
            "fallback_sent": bool(fallback_sent),
        },
        path,
    )


def latest_for_mode(mode: str, path: Path = JOURNAL_PATH) -> dict[str, Any] | None:
    data = _load(path)
    base = base_mode(mode)
    for item in reversed(data.get("runs", [])):
        if base_mode(str(item.get("mode") or "")) == base:
            return item
    return None


def base_mode(value: str) -> str:
    if value.startswith("morning"):
        return "morning"
    if value.startswith("afternoon"):
        return "afternoon"
    return value
