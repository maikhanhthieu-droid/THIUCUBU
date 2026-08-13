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

    assert "Giá 14,950" in text
    assert "14950.00" not in text
    assert "100/97" not in text
    assert "97/97" in text
    assert "💎" in text


def test_format_sector_line_preserves_markdown_backticks():
    line = "LEAD `Bank` avg 65"

    assert tf.format_sector_line(line) == line


def test_format_stock_card_puts_multi_timeframe_state_near_top():
    result = SimpleNamespace(
        symbol="AAA",
        win_score=74,
        setup="REACCUMULATION_WATCH",
        sector="Test",
        close=10.2,
        discount_pct=20,
        target_discount_pct=25,
        vol_ratio=0.7,
        rsi=50,
        mfi=52,
        obv_up=True,
        near_break=True,
        failed_break=False,
        reason="test",
        market_state="ACCUMULATION",
        daily_phase="REACCUMULATION",
        weekly_phase="MARKUP",
        monthly_phase="ACCUMULATION",
        breakout_state="REACCUMULATION",
    )

    lines = tf.format_stock_card(result).splitlines()

    assert lines[1].startswith("TT TÍCH LŨY")
    assert "D TÁI TÍCH LŨY" in lines[1]
    assert "W CƠ HỘI" in lines[1]
