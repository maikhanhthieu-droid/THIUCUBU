from __future__ import annotations

import config


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("SCAN_SOURCE_USAGE_RATIO", "0.8")
    config.get_settings.cache_clear()

    settings = config.get_settings()

    assert settings.telegram_token == "token"
    assert settings.telegram_chat_id == "chat"
    assert settings.effective_dry_run is False
    assert float(settings.scan_source_usage_ratio) == 0.8


def test_env_helpers_are_safe(monkeypatch):
    monkeypatch.setenv("BAD_INT", "abc")
    monkeypatch.setenv("TOO_LOW_FLOAT", "-5")
    monkeypatch.setenv("CSV_VALUE", "vci, kbs,,dnse")

    assert config.env_int("BAD_INT", 12, min_value=1) == 12
    assert config.env_float("TOO_LOW_FLOAT", 0.7, min_value=0.1) == 0.1
    assert config.env_csv("CSV_VALUE", "VCI") == ["VCI", "KBS", "DNSE"]
