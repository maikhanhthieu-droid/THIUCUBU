from __future__ import annotations

from types import SimpleNamespace

import telegram_format as tf


def test_format_stock_card_formats_vietnamese_prices():
    result = SimpleNamespace(
        symbol="DIG",
        win_score=100,
        setup="DISCOUNT_BASE",
        sector="BDS",
        close=14950,
        discount_pct=40.4,
        target_discount_pct=43,
        vol_ratio=0.9,
        rsi=64,
        mfi=67,
        obv_up=True,
        near_break=True,
        failed_break=False,
        reason="test",
    )

    text = tf.format_stock_card(result)

    assert "Gia 14,950" in text
    assert "14950.00" not in text


def test_format_sector_line_preserves_markdown_backticks():
    line = "LEAD `Bank` avg 65"

    assert tf.format_sector_line(line) == line
