from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_scanner_and_weekend_share_the_seventy_percent_budget() -> None:
    for name in ("scanner.yml", "weekend-opportunities.yml"):
        text = workflow(name)
        assert 'SCAN_SOURCE_LIMITS: "FIINQUANT=90,VCI=20,KBS=20,DNSE=15"' in text
        assert 'FIINQUANT_USAGE_RATIO: "0.70"' in text
        assert 'FIINQUANT_MONTHLY_REQUEST_BUDGET: "70000"' in text
        assert 'SCAN_SOURCE_USAGE_RATIO: "0.70"' in text
        assert 'VIMO_DAILY_REQUEST_BUDGET: "70"' in text
        assert "path: data/api_budget" in text


def test_full_audits_are_non_persistent_and_pulse_is_rate_limited() -> None:
    scanner = workflow("scanner.yml")
    weekend = workflow("weekend-opportunities.yml")
    pulse = workflow("intraday-pulse.yml")

    assert "audit_full" in scanner
    assert 'SESSION_SKIP_WAIT="1"' in scanner
    assert "inputs.mode != 'audit_full'" in scanner
    assert 'description: "full | test | audit"' in weekend
    assert "inputs.mode != 'audit'" in weekend
    assert 'PULSE_SOURCE_LIMITS: "KBS=20,VCI=20"' in pulse
    assert 'PULSE_SOURCE_USAGE_RATIO: "0.70"' in pulse
    assert 'EXTRA_ARGS+=(--no-notify)' in pulse
    assert "inputs.persist == true" in pulse
    assert "path: data/api_budget" in workflow("fiinquant-check.yml")
