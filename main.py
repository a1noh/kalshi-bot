"""Main run loop for the kalshi-bot.

Repeatedly scans Kalshi for candidate markets, asks Claude for a trading
signal on each, applies the risk gate, executes approved trades (or logs
them in dry-run mode), and records the outcome.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from dotenv import load_dotenv

from src.executor import place_order
from src.kalshi_client import KalshiClient
from src.logger import init_db, record_trade, setup_logging
from src.market_scanner import scan_markets
from src.risk import RiskManager
from src.signal import generate_signal

load_dotenv()

logger = logging.getLogger(__name__)


def run_once(client: KalshiClient, risk_manager: RiskManager) -> None:
    """Run a single scan -> signal -> risk -> execute -> log iteration.

    Args:
        client: Authenticated Kalshi client.
        risk_manager: Configured `RiskManager`.
    """
    candidates = scan_markets(client)
    logger.info("scan complete", extra={"extra_fields": {"candidate_count": len(candidates)}})

    for market in candidates:
        _process_candidate(client, risk_manager, market)


def _process_candidate(client: KalshiClient, risk_manager: RiskManager, market: dict[str, Any]) -> None:
    """Generate a signal for one candidate market and act on it.

    Args:
        client: Authenticated Kalshi client.
        risk_manager: Configured `RiskManager`.
        market: A candidate market dict (with an `"order_book"` key) from
            `scan_markets`.
    """
    ticker = market["ticker"]

    try:
        signal = generate_signal(market)
    except Exception:
        logger.exception("signal generation failed", extra={"extra_fields": {"ticker": ticker}})
        return

    if signal.skip:
        logger.info("signal skipped", extra={"extra_fields": {"ticker": ticker, "reason": signal.skip_reason}})
        record_trade(
            {
                "market_ticker": signal.market_ticker,
                "side": signal.side,
                "size_usd": 0.0,
                "confidence": signal.confidence,
                "edge": signal.edge,
                "status": "skipped",
                "reasoning": signal.skip_reason or signal.reasoning,
            }
        )
        return

    decision = risk_manager.evaluate(signal, client)
    if not decision.approved:
        logger.info(
            "signal rejected by risk manager",
            extra={"extra_fields": {"ticker": ticker, "reason": decision.reason}},
        )
        record_trade(
            {
                "market_ticker": signal.market_ticker,
                "side": signal.side,
                "size_usd": signal.size_usd,
                "confidence": signal.confidence,
                "edge": signal.edge,
                "status": "rejected",
                "reasoning": decision.reason,
            }
        )
        return

    try:
        result = place_order(client, signal, market["order_book"])
    except Exception:
        logger.exception("order placement failed", extra={"extra_fields": {"ticker": ticker}})
        return

    order_id = (result.get("response") or {}).get("order", {}).get("order_id")
    record_trade(
        {
            "market_ticker": signal.market_ticker,
            "side": signal.side,
            "size_usd": signal.size_usd,
            "confidence": signal.confidence,
            "edge": signal.edge,
            "status": "dry_run" if result["dry_run"] else "open",
            "reasoning": signal.reasoning,
            "order_id": order_id,
        }
    )


def main() -> None:
    """Entry point: set up logging/DB, then run the scan loop forever."""
    setup_logging()
    init_db()

    client = KalshiClient()
    risk_manager = RiskManager()
    interval_seconds = int(os.environ.get("SCAN_INTERVAL_SECONDS", "60"))

    logger.info(
        "kalshi-bot starting",
        extra={
            "extra_fields": {
                "dry_run": os.environ.get("DRY_RUN", "true"),
                "scan_interval_seconds": interval_seconds,
            }
        },
    )

    while True:
        try:
            run_once(client, risk_manager)
        except Exception:
            logger.exception("run_once failed")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
