"""Market scanner for Kalshi.

Fetches open markets and filters them down to a list of candidates worth
evaluating for a trading signal: sufficient trading volume, a tight bid/ask
spread, and enough time left before the market closes.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from src.kalshi_client import KalshiClient
from src.order_book import get_order_book_summary

MIN_VOLUME_USD = 5_000
MAX_SPREAD_PCT = 0.05
MIN_HOURS_TO_CLOSE = 24
MARKETS_PAGE_SIZE = 200
ORDER_BOOK_FETCH_WORKERS = int(os.environ.get("ORDER_BOOK_FETCH_WORKERS", "20"))


def scan_markets(client: KalshiClient) -> list[dict[str, Any]]:
    """Scan open Kalshi markets and return actionable candidates.

    Fetches every open market (paginating through the full result set), then
    keeps only markets that meet all of:

    - Estimated dollar volume above `MIN_VOLUME_USD`.
    - Order book spread below `MAX_SPREAD_PCT`.
    - More than `MIN_HOURS_TO_CLOSE` hours remaining until `close_time`.

    Args:
        client: Authenticated Kalshi client.

    Order books for markets that pass the time-to-close filter are fetched
    concurrently (bounded by `ORDER_BOOK_FETCH_WORKERS`), since a single
    Kalshi scan can involve thousands of open markets and sequential
    per-market order book requests would make the scan far too slow for
    `SCAN_INTERVAL_SECONDS`.

    Args:
        client: Authenticated Kalshi client.

    Returns:
        A list of market dicts, each with an added `"order_book"` key
        containing the market's `OrderBookSummary`.
    """
    candidates: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    cursor: str | None = None

    with ThreadPoolExecutor(max_workers=ORDER_BOOK_FETCH_WORKERS) as executor:
        while True:
            params: dict[str, Any] = {"status": "open", "limit": MARKETS_PAGE_SIZE}
            if cursor:
                params["cursor"] = cursor

            response = client.get_markets(**params)
            markets = response.get("markets", [])

            eligible = [market for market in markets if _closes_after_min_window(market, now)]
            order_books = executor.map(lambda market: get_order_book_summary(client, market["ticker"]), eligible)

            for market, order_book in zip(eligible, order_books, strict=True):
                if order_book.spread_pct is None or order_book.spread_pct >= MAX_SPREAD_PCT:
                    continue

                if not _meets_volume_threshold(market, order_book):
                    continue

                candidates.append({**market, "order_book": order_book})

            cursor = response.get("cursor")
            if not cursor or not markets:
                break

    return candidates


def _meets_volume_threshold(market: dict[str, Any], order_book: Any) -> bool:
    """Check whether a market's estimated dollar volume exceeds `MIN_VOLUME_USD`.

    Kalshi reports volume as a contract count rather than a dollar amount, so
    dollar volume is estimated as `contract_volume * mid_price` (mid price is
    in dollars per contract, where a contract pays out $1.00).

    Args:
        market: A market dict from `GET /markets`.
        order_book: The market's `OrderBookSummary`.

    Returns:
        True if estimated dollar volume exceeds `MIN_VOLUME_USD`.
    """
    contract_volume = _contract_volume(market)
    mid_price = order_book.mid_price
    if contract_volume is None or mid_price is None:
        return False
    return (contract_volume * mid_price) > MIN_VOLUME_USD


def _contract_volume(market: dict[str, Any]) -> float | None:
    """Extract the traded contract volume from a market dict.

    Supports both the current `volume_fp` (string) field and the legacy
    `volume` (int) field.

    Args:
        market: A market dict from `GET /markets`.

    Returns:
        The contract volume, or `None` if neither field is present.
    """
    if "volume_fp" in market:
        return float(market["volume_fp"])
    if "volume" in market:
        return float(market["volume"])
    return None


def _closes_after_min_window(market: dict[str, Any], now: datetime) -> bool:
    """Check whether a market closes more than `MIN_HOURS_TO_CLOSE` hours from now.

    Args:
        market: A market dict from `GET /markets`.
        now: The current UTC time.

    Returns:
        True if `close_time` is more than `MIN_HOURS_TO_CLOSE` hours away.
    """
    close_time_str = market.get("close_time")
    if not close_time_str:
        return False

    close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
    hours_to_close = (close_time - now).total_seconds() / 3600
    return hours_to_close > MIN_HOURS_TO_CLOSE
