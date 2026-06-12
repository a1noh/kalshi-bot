"""Tests for src.order_book."""

from unittest.mock import MagicMock

import pytest

from src.order_book import get_order_book_summary


def _client_with_response(response: dict) -> MagicMock:
    client = MagicMock()
    client.get_market_orderbook.return_value = response
    return client


def test_summary_from_dollar_format():
    client = _client_with_response(
        {
            "orderbook_fp": {
                "yes_dollars": [["0.40", "100.00"], ["0.38", "50.00"]],
                "no_dollars": [["0.55", "100.00"], ["0.50", "50.00"]],
            }
        }
    )

    summary = get_order_book_summary(client, "TICKER")

    assert summary.ticker == "TICKER"
    assert summary.best_bid == 0.40
    assert summary.best_ask == 0.45  # 1.00 - best_no_bid (0.55)
    assert summary.mid_price == pytest.approx(0.425)
    assert summary.spread_pct == pytest.approx((0.45 - 0.40) / 0.425, abs=1e-4)


def test_summary_from_legacy_cents_format():
    client = _client_with_response(
        {
            "orderbook": {
                "yes": [[40, 100], [38, 50]],
                "no": [[55, 100], [50, 50]],
            }
        }
    )

    summary = get_order_book_summary(client, "TICKER")

    assert summary.best_bid == 0.40
    assert summary.best_ask == 0.45
    assert summary.mid_price == pytest.approx(0.425)


def test_summary_with_empty_orderbook():
    client = _client_with_response({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})

    summary = get_order_book_summary(client, "TICKER")

    assert summary.best_bid is None
    assert summary.best_ask is None
    assert summary.mid_price is None
    assert summary.spread_pct is None
