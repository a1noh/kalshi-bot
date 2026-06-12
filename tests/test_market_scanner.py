"""Tests for src.market_scanner."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from src.market_scanner import (
    MIN_HOURS_TO_CLOSE,
    MIN_VOLUME_USD,
    _closes_after_min_window,
    _contract_volume,
    _meets_volume_threshold,
    scan_markets,
)
from src.order_book import OrderBookSummary


def _future_close(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def test_contract_volume_prefers_fp_field():
    assert _contract_volume({"volume_fp": "123.45", "volume": 1}) == 123.45
    assert _contract_volume({"volume": 50}) == 50.0
    assert _contract_volume({}) is None


def test_closes_after_min_window():
    now = datetime.now(UTC)

    assert _closes_after_min_window({"close_time": _future_close(MIN_HOURS_TO_CLOSE + 1)}, now) is True
    assert _closes_after_min_window({"close_time": _future_close(MIN_HOURS_TO_CLOSE - 1)}, now) is False
    assert _closes_after_min_window({}, now) is False


def test_meets_volume_threshold():
    order_book = OrderBookSummary(ticker="T", best_bid=0.5, best_ask=0.5, spread_pct=0.0, mid_price=0.5)

    big_market = {"volume_fp": str((MIN_VOLUME_USD / 0.5) + 1000)}
    small_market = {"volume_fp": "10"}

    assert _meets_volume_threshold(big_market, order_book) is True
    assert _meets_volume_threshold(small_market, order_book) is False


def test_scan_markets_applies_all_filters(monkeypatch):
    good_market = {
        "ticker": "GOOD",
        "close_time": _future_close(MIN_HOURS_TO_CLOSE + 10),
        "volume_fp": str((MIN_VOLUME_USD / 0.5) + 1000),
    }
    closes_too_soon = {
        "ticker": "SOON",
        "close_time": _future_close(1),
        "volume_fp": "1000000",
    }
    low_volume = {
        "ticker": "LOWVOL",
        "close_time": _future_close(MIN_HOURS_TO_CLOSE + 10),
        "volume_fp": "1",
    }

    client = MagicMock()
    client.get_markets.return_value = {
        "markets": [good_market, closes_too_soon, low_volume],
        "cursor": None,
    }

    tight_book = OrderBookSummary(ticker="x", best_bid=0.49, best_ask=0.51, spread_pct=0.039, mid_price=0.5)
    monkeypatch.setattr("src.market_scanner.get_order_book_summary", lambda client, ticker: tight_book)

    candidates = scan_markets(client)

    assert [market["ticker"] for market in candidates] == ["GOOD"]
    assert candidates[0]["order_book"] is tight_book
