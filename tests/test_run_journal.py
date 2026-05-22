from __future__ import annotations

import run_journal


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
        path=path,
    )

    assert path.exists()
    assert len(data["runs"]) == 2
    latest = run_journal.latest_for_mode("afternoon", path)
    assert latest is not None
    assert latest["run_id"] == run_id
    assert latest["failed_symbols"] == ["AAA", "BBB"]


def test_run_journal_bounds_entries(tmp_path, monkeypatch):
    path = tmp_path / "run_journal.json"
    monkeypatch.setattr(run_journal, "JOURNAL_LIMIT", 3)

    for idx in range(5):
        run_journal.append_event({"run_id": str(idx), "mode": "test", "status": "started"}, path)

    assert [item["run_id"] for item in run_journal.load_journal(path)["runs"]] == ["2", "3", "4"]
