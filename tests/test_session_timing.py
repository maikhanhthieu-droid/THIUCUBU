from __future__ import annotations

from datetime import time

import session_scan


def test_afternoon_split_starts_early_and_focuses_at_1400():
    window = session_scan.SESSION_WINDOWS["afternoon_split"]

    assert window["broad_after"] == time(13, 31)
    assert window["focus_after"] == time(14, 0)
    assert session_scan.session_deadline("afternoon_split") == time(14, 13)
    assert session_scan.SESSION_QUICK_LIMIT >= 50
