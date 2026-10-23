#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

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
    stage_timings: Mapping[str, float] | None = None,
    path: Path = JOURNAL_PATH,
) -> dict[str, Any]:
    failed = sorted(set(failed_symbols or []))
    event: dict[str, Any] = {
        "run_id": run_id,
        "mode": mode,
        "status": status,
        "finished_at": now_vn().isoformat(timespec="seconds"),
        "success_count": int(success_count),
        "failed_count": len(failed),
        "failed_symbols": failed[:50],
        "elapsed_sec": round(float(elapsed_sec or 0.0), 1),
        "telegram_sent": bool(telegram_sent),
    }
    if stage_timings:
        event["stage_timings"] = {
            str(k): round(float(v), 2) for k, v in stage_timings.items() if v is not None
        }
    return append_event(event, path)


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


def format_github_summary_markdown(
    record: Mapping[str, Any],
    source_health: Mapping[str, Any] | None = None,
) -> str:
    """Format a concise Markdown table for $GITHUB_STEP_SUMMARY."""
    status = str(record.get("status") or "unknown").upper()
    mode = str(record.get("mode") or "auto")
    run_id = str(record.get("run_id") or "n/a")
    elapsed = float(record.get("elapsed_sec") or 0.0)
    ok_count = int(record.get("success_count") or 0)
    fail_count = int(record.get("failed_count") or 0)
    tg = "Yes" if record.get("telegram_sent") else "No"
    lines = [
        f"### THIEUCUBU Run Summary (`{mode}` — `{status}`)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Run ID | `{run_id}` |",
        f"| Symbols OK / Failed | **{ok_count}** / {fail_count} |",
        f"| Total Elapsed | `{elapsed:.1f}s` |",
        f"| Telegram Sent | {tg} |",
    ]
    timings = record.get("stage_timings")
    if isinstance(timings, Mapping) and timings:
        timing_str = ", ".join(f"`{k}={float(v):.1f}s`" for k, v in timings.items())
        lines.append(f"| Stage Timings | {timing_str} |")

    sources = (source_health or {}).get("sources") if isinstance(source_health, Mapping) else None
    if isinstance(sources, Mapping) and sources:
        lines += [
            "",
            "#### Data Source Health",
            "| Source | Score | OK / Attempts | Rate-Limit | Transient | Status |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for src, info in sources.items():
            if not isinstance(info, Mapping):
                continue
            state = "Disabled" if info.get("disabled") else "Active"
            lines.append(
                f"| `{src}` | {int(info.get('health_score') or 0)} | "
                f"{int(info.get('successes') or 0)}/{int(info.get('attempts') or 0)} | "
                f"{int(info.get('rate_limit_failures') or 0)} | "
                f"{int(info.get('transient_failures') or 0)} | {state} |"
            )
    return "\n".join(lines) + "\n"


def write_github_step_summary(
    record: Mapping[str, Any],
    source_health: Mapping[str, Any] | None = None,
    summary_path: Path | str | None = None,
) -> bool:
    target_raw = str(summary_path or os.getenv("GITHUB_STEP_SUMMARY", "")).strip()
    if not target_raw:
        return False
    try:
        target = Path(target_raw)
        target.parent.mkdir(parents=True, exist_ok=True)
        markdown = format_github_summary_markdown(record, source_health=source_health)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(markdown)
        return True
    except OSError:
        return False


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
