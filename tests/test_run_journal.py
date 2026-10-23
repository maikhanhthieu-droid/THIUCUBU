from __future__ import annotations

import run_journal
from scripts import safe_commit_data


def test_run_journal_start_finish_and_latest(tmp_path):
    path = tmp_path / "run_journal.json"

    run_id = run_journal.start_run("afternoon_split", "unit", path)
    data = run_journal.finish_run(
        run_id,
        "afternoon_split",
        "success",
        success_count=12,
        failed_symbols=["AAA", "AAA", "BBB"],
        elapsed_sec=42.4,
        telegram_sent=True,
        stage_timings={"scan_sec": 30.1, "metrics_sec": 8.2, "save_sec": 4.1},
        path=path,
    )

    assert path.exists()
    assert len(data["runs"]) == 2
    latest = run_journal.latest_for_mode("afternoon", path)
    assert latest is not None
    assert latest["run_id"] == run_id
    assert latest["failed_symbols"] == ["AAA", "BBB"]
    assert latest["stage_timings"]["scan_sec"] == 30.1


def test_run_journal_bounds_entries(tmp_path, monkeypatch):
    path = tmp_path / "run_journal.json"
    monkeypatch.setattr(run_journal, "JOURNAL_LIMIT", 3)

    for idx in range(5):
        run_journal.append_event({"run_id": str(idx), "mode": "test", "status": "started"}, path)

    assert [item["run_id"] for item in run_journal.load_journal(path)["runs"]] == ["2", "3", "4"]


def test_github_step_summary_writer_and_safe_commit_json_validation(tmp_path):
    summary_file = tmp_path / "step_summary.md"
    record = {
        "run_id": "20261023-abc12345",
        "mode": "eod",
        "status": "success",
        "success_count": 150,
        "failed_count": 2,
        "elapsed_sec": 58.3,
        "telegram_sent": True,
        "stage_timings": {"scan_sec": 45.0, "metrics_sec": 10.0, "save_sec": 3.3},
    }
    health = {
        "sources": {
            "VCI": {
                "health_score": 96,
                "successes": 100,
                "attempts": 102,
                "rate_limit_failures": 0,
                "transient_failures": 2,
                "disabled": False,
            }
        }
    }
    wrote = run_journal.write_github_step_summary(record, source_health=health, summary_path=summary_file)
    assert wrote is True
    content = summary_file.read_text(encoding="utf-8")
    assert "THIEUCUBU Run Summary" in content
    assert "`scan_sec=45.0s`" in content
    assert "`VCI`" in content

    valid_json = tmp_path / "valid.json"
    valid_json.write_text('{"ok": true}', encoding="utf-8")
    corrupt_json = tmp_path / "corrupt.json"
    corrupt_json.write_text("{broken json", encoding="utf-8")
    empty_json = tmp_path / "empty.json"
    empty_json.write_text("   ", encoding="utf-8")

    selected = safe_commit_data.existing_paths([str(valid_json), str(corrupt_json), str(empty_json)])
    assert selected == [valid_json]
