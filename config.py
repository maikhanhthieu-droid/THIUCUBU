#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger("thieucutoo.config")

TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}

try:  # Keep local scripts usable even before dependencies are installed.
    from pydantic import Field, SecretStr
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:  # pragma: no cover - fallback is for bare local Python only
    BaseSettings = None
    Field = None
    SecretStr = None
    SettingsConfigDict = None


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in TRUE_VALUES:
        return True
    if raw in FALSE_VALUES:
        return False
    logger.warning("Invalid %s=%r, using %s", name, raw, default)
    return default


def env_int(name: str, default: int, min_value: int = 0, max_value: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using %s", name, raw, default)
        return default
    value = max(min_value, value)
    return min(value, max_value) if max_value is not None else value


def env_float(
    name: str,
    default: float,
    min_value: float = 0.0,
    max_value: float | None = None,
) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using %s", name, raw, default)
        return default
    value = max(min_value, value)
    return min(value, max_value) if max_value is not None else value


def env_csv(name: str, default: str) -> list[str]:
    values = [item.strip().upper() for item in os.getenv(name, default).split(",")]
    return [item for item in values if item]


if BaseSettings is not None:

    class Settings(BaseSettings):
        telegram_token: str = Field("", validation_alias="TELEGRAM_TOKEN")
        telegram_chat_id: str = Field("", validation_alias="TELEGRAM_CHAT_ID")
        vnstock_api_key: str = Field("", validation_alias="VNSTOCK_API_KEY")
        fiinquant_username: SecretStr = Field("", validation_alias="FIINQUANT_USERNAME")
        fiinquant_password: SecretStr = Field("", validation_alias="FIINQUANT_PASSWORD")
        vimo_api_key: SecretStr = Field("", validation_alias="VIMO_API_KEY")
        gemini_api_key: SecretStr = Field("", validation_alias="GEMINI_API_KEY")
        ssi_mcp_api_key: SecretStr = Field("", validation_alias="SSI_MCP_API_KEY")
        tcbs_mcp_api_key: SecretStr = Field("", validation_alias="TCBS_MCP_API_KEY")
        dry_run: bool = Field(False, validation_alias="DRY_RUN")
        scan_source_usage_ratio: float = Field(0.70, validation_alias="SCAN_SOURCE_USAGE_RATIO")
        scan_source_requests_per_minute: int = Field(15, validation_alias="SCAN_SOURCE_REQUESTS_PER_MINUTE")
        data_dir: Path = Field(Path("data"), validation_alias="DATA_DIR")

        model_config = SettingsConfigDict(env_file=".env", extra="ignore")

        @property
        def effective_dry_run(self) -> bool:
            return bool(self.dry_run or not (self.telegram_token and self.telegram_chat_id))

else:

    class Settings:  # pragma: no cover - exercised only without pydantic-settings
        def __init__(self) -> None:
            logger.warning("pydantic-settings unavailable; using lightweight env fallback")
            self.telegram_token = os.getenv("TELEGRAM_TOKEN", "").strip()
            self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
            self.vnstock_api_key = os.getenv("VNSTOCK_API_KEY", "").strip()
            self.fiinquant_username = os.getenv("FIINQUANT_USERNAME", "").strip()
            self.fiinquant_password = os.getenv("FIINQUANT_PASSWORD", "").strip()
            self.vimo_api_key = os.getenv("VIMO_API_KEY", "").strip()
            self.gemini_api_key = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", "")).strip()
            self.ssi_mcp_api_key = os.getenv("SSI_MCP_API_KEY", "").strip()
            self.tcbs_mcp_api_key = os.getenv("TCBS_MCP_API_KEY", "").strip()
            self.dry_run = env_bool("DRY_RUN", False)
            self.scan_source_usage_ratio = env_float("SCAN_SOURCE_USAGE_RATIO", 0.70, min_value=0.05, max_value=1.0)
            self.scan_source_requests_per_minute = env_int("SCAN_SOURCE_REQUESTS_PER_MINUTE", 15, min_value=1)
            self.data_dir = Path(os.getenv("DATA_DIR", "data"))

        @property
        def effective_dry_run(self) -> bool:
            return bool(self.dry_run or not (self.telegram_token and self.telegram_chat_id))


def _secret_str(value: Any) -> str:
    if hasattr(value, "get_secret_value"):
        return str(value.get_secret_value() or "").strip()
    return str(value or "").strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if getattr(settings, "vnstock_api_key", ""):
        os.environ.setdefault("VNSTOCK_API_KEY", settings.vnstock_api_key)
        os.environ.setdefault("VNDATA_API_KEY", settings.vnstock_api_key)
    username = _secret_str(getattr(settings, "fiinquant_username", ""))
    password = _secret_str(getattr(settings, "fiinquant_password", ""))
    if username and password:
        os.environ.setdefault("FIINQUANT_USERNAME", username)
        os.environ.setdefault("FIINQUANT_PASSWORD", password)
    vimo_key = _secret_str(getattr(settings, "vimo_api_key", ""))
    if vimo_key:
        os.environ.setdefault("VIMO_API_KEY", vimo_key)
    gemini_key = _secret_str(getattr(settings, "gemini_api_key", "")) or os.getenv("GOOGLE_API_KEY", "").strip()
    if gemini_key:
        os.environ.setdefault("GEMINI_API_KEY", gemini_key)
    return settings


def settings_summary(settings: Settings | None = None) -> dict[str, Any]:
    item = settings or get_settings()
    username = _secret_str(getattr(item, "fiinquant_username", ""))
    password = _secret_str(getattr(item, "fiinquant_password", ""))
    vimo_key = _secret_str(getattr(item, "vimo_api_key", ""))
    gemini_key = _secret_str(getattr(item, "gemini_api_key", "")) or os.getenv("GOOGLE_API_KEY", "").strip()
    ssi_key = _secret_str(getattr(item, "ssi_mcp_api_key", ""))
    tcbs_key = _secret_str(getattr(item, "tcbs_mcp_api_key", ""))
    return {
        "telegram_configured": bool(item.telegram_token and item.telegram_chat_id),
        "dry_run": item.effective_dry_run,
        "vnstock_api_key": bool(item.vnstock_api_key),
        "fiinquant_configured": bool(username and password),
        "vimo_configured": bool(vimo_key),
        "gemini_configured": bool(gemini_key),
        "ssi_mcp_configured": bool(ssi_key or os.getenv("SSI_MCP_ENDPOINT", "").strip()),
        "tcbs_mcp_configured": bool(tcbs_key or os.getenv("TCBS_MCP_ENDPOINT", "").strip()),
        "scan_source_usage_ratio": float(item.scan_source_usage_ratio),
        "scan_source_requests_per_minute": int(item.scan_source_requests_per_minute),
        "data_dir": str(item.data_dir),
    }
