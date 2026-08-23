from __future__ import annotations

from pathlib import Path


def test_vnstock_chart_adapter_is_pinned_to_compatible_release() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8")

    assert "vnstock==4.0.2" in requirements
    assert "vnstock_ezchart==0.0.3" in requirements
