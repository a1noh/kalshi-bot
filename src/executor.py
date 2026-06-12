"""Order execution for Kalshi trades.

Translates an approved `TradeSignal` into a Kalshi order and places (or
cancels) it via `KalshiClient`. When `DRY_RUN` is true (the default), orders
are logged but never sent to the API.
"""

from __future__ import annotations

import logging
import math
import os
import uuid
from typing import Any

from dotenv import load_dotenv

from src.kalshi_client import KalshiClient
from src.order_book import OrderBookSummary
from src.signal import TradeSignal

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_TIME_IN_FORCE = "fill_or_kill"


def is_dry_run() -> bool:
    """Check whether the bot is running in dry-run mode.

    Returns:
        True unless the `DRY_RUN` environment variable is explicitly set to
        a falsy value (`"false"`, `"0"`, `"no"`).
    """
    return os.environ.get("DRY_RUN", "true").strip().lower() not in ("false", "0", "no")


def place_order(
    client: KalshiClient,
    signal: TradeSignal,
    order_book: OrderBookSummary,
) -> dict[str, Any]:
    """Place an order for an approved trade signal.

    Args:
        client: Authenticated Kalshi client.
        signal: An approved `TradeSignal`.
        order_book: The market's current `OrderBookSummary`, used to convert
            `signal.size_usd` into a contract count and limit price.

    Returns:
        A dict with `"dry_run"`, the `"order"` payload that was (or would be)
        sent, and - when not in dry-run mode - the API `"response"`.

    Raises:
        ValueError: If no price is available for `signal.side` (e.g. an
            empty order book).
    """
    payload = _build_order_payload(signal, order_book)

    if is_dry_run():
        logger.info("DRY_RUN: would place order", extra={"extra_fields": {"order": payload}})
        return {"dry_run": True, "order": payload}

    response = client.create_order(payload)
    logger.info("placed order", extra={"extra_fields": {"order": payload, "response": response}})
    return {"dry_run": False, "order": payload, "response": response}


def cancel_order(client: KalshiClient, order_id: str) -> dict[str, Any]:
    """Cancel an open order.

    Args:
        client: Authenticated Kalshi client.
        order_id: The ID of the order to cancel.

    Returns:
        A dict with `"dry_run"` and `"order_id"`, plus - when not in
        dry-run mode - the API `"response"`.
    """
    if is_dry_run():
        logger.info("DRY_RUN: would cancel order", extra={"extra_fields": {"order_id": order_id}})
        return {"dry_run": True, "order_id": order_id}

    response = client.cancel_order(order_id)
    logger.info("cancelled order", extra={"extra_fields": {"order_id": order_id, "response": response}})
    return {"dry_run": False, "order_id": order_id, "response": response}


def _build_order_payload(signal: TradeSignal, order_book: OrderBookSummary) -> dict[str, Any]:
    """Build a Kalshi `POST /portfolio/orders` payload from a trade signal.

    The order is a limit buy for `signal.side` ("yes" or "no"), priced at
    the current best ask for that side, sized to spend approximately
    `signal.size_usd`, and submitted `fill_or_kill` so it either fills near
    the expected price or doesn't execute at all.

    Args:
        signal: The trade signal to convert.
        order_book: The market's current `OrderBookSummary`.

    Returns:
        A dict suitable for `KalshiClient.create_order`.

    Raises:
        ValueError: If the order book has no price available for
            `signal.side`.
    """
    price = _ask_price_for_side(signal.side, order_book)
    if price is None:
        raise ValueError(f"no ask price available for side={signal.side!r} on {signal.market_ticker}")

    count = max(1, math.floor(signal.size_usd / price))

    payload: dict[str, Any] = {
        "ticker": signal.market_ticker,
        "side": signal.side,
        "action": "buy",
        "count": count,
        "time_in_force": DEFAULT_TIME_IN_FORCE,
        "client_order_id": str(uuid.uuid4()),
    }

    price_field = "yes_price_dollars" if signal.side == "yes" else "no_price_dollars"
    payload[price_field] = f"{price:.4f}"

    return payload


def _ask_price_for_side(side: str, order_book: OrderBookSummary) -> float | None:
    """Get the ask price (dollars per contract) for a given side.

    Args:
        side: `"yes"` or `"no"`.
        order_book: The market's current `OrderBookSummary`.

    Returns:
        The ask price in dollars, or `None` if unavailable. For `"yes"`
        this is `order_book.best_ask`; for `"no"` it is derived as
        `1.00 - order_book.best_bid` since yes + no = $1.00.
    """
    if side == "yes":
        return order_book.best_ask
    if order_book.best_bid is None:
        return None
    return round(1.0 - order_book.best_bid, 4)
