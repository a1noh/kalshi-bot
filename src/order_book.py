"""Order book utilities for Kalshi markets.

Fetches the order book for a market and reduces it to the summary stats
(`best_bid`, `best_ask`, `spread_pct`, `mid_price`) used by the market
scanner and signal generator.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.kalshi_client import KalshiClient

PriceLevel = tuple[float, float]


@dataclass
class OrderBookSummary:
    """Summary statistics for a market's order book.

    All prices are expressed in dollars per contract (0.00-1.00), where a
    "yes" contract pays $1.00 if the market resolves yes.
    """

    ticker: str
    best_bid: float | None
    best_ask: float | None
    spread_pct: float | None
    mid_price: float | None


def get_order_book_summary(client: KalshiClient, ticker: str) -> OrderBookSummary:
    """Fetch a market's order book and compute summary statistics.

    Kalshi order books only list resting *bid* orders for the "yes" and "no"
    sides of a binary market. Because a yes contract and a no contract always
    sum to $1.00, the best "yes ask" is derived as ``1.00 - best_no_bid`` (and
    symmetrically for the best "no ask").

    Args:
        client: Authenticated Kalshi client.
        ticker: The market ticker symbol.

    Returns:
        An `OrderBookSummary`. Fields are `None` if there are no resting
        orders on the relevant side.
    """
    response = client.get_market_orderbook(ticker)
    book = response.get("orderbook_fp") or response.get("orderbook") or {}

    yes_levels = _extract_levels(book, "yes_dollars", "yes")
    no_levels = _extract_levels(book, "no_dollars", "no")

    best_yes_bid = max((price for price, _ in yes_levels), default=None)
    best_no_bid = max((price for price, _ in no_levels), default=None)

    best_bid = best_yes_bid
    best_ask = round(1.0 - best_no_bid, 4) if best_no_bid is not None else None

    spread_pct: float | None = None
    mid_price: float | None = None
    if best_bid is not None and best_ask is not None:
        mid_price = round((best_bid + best_ask) / 2, 4)
        if mid_price > 0:
            spread_pct = round((best_ask - best_bid) / mid_price, 4)

    return OrderBookSummary(
        ticker=ticker,
        best_bid=best_bid,
        best_ask=best_ask,
        spread_pct=spread_pct,
        mid_price=mid_price,
    )


def _extract_levels(book: dict, fp_key: str, legacy_key: str) -> list[PriceLevel]:
    """Normalize an order book side to a list of (price_dollars, quantity).

    Supports both the current fixed-point dollar format (e.g. `yes_dollars`
    with string prices like `"0.15"`) and the legacy integer-cents format
    (e.g. `yes` with prices like `15`).

    Args:
        book: The `orderbook_fp`/`orderbook` payload.
        fp_key: Key for the current dollar-denominated format.
        legacy_key: Key for the legacy cents-denominated format.

    Returns:
        A list of (price_in_dollars, quantity) tuples.
    """
    levels = book.get(fp_key)
    if levels is not None:
        return [(float(price), float(qty)) for price, qty in levels]

    levels = book.get(legacy_key) or []
    return [(float(price) / 100, float(qty)) for price, qty in levels]
