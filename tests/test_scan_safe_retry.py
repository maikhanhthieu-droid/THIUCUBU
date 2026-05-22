from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import scan_safe


class DummyResponse:
    def __init__(self, retry_after: str) -> None:
        self.headers = {"Retry-After": retry_after}


class DummyError(Exception):
    def __init__(self, retry_after: str) -> None:
        super().__init__("429 Too Many Requests")
        self.response = DummyResponse(retry_after)


def test_extract_retry_after_seconds_from_numeric_header():
    assert scan_safe.extract_retry_after_seconds(DummyError("42")) == 42


def test_extract_retry_after_seconds_from_http_date():
    future = datetime.now(timezone.utc) + timedelta(seconds=30)
    seconds = scan_safe.extract_retry_after_seconds(DummyError(format_datetime(future)))

    assert seconds is not None
    assert 0 < seconds <= 31


def test_extract_retry_after_seconds_from_message_text():
    seconds = scan_safe.extract_retry_after_seconds(Exception("blocked; retry-after: 12"))

    assert seconds == 12
