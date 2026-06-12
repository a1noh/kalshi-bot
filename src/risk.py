"""Risk management gate for trade signals.

Applies pre-trade checks - minimum confidence, max bet size, max open
positions, and daily loss limit - before a `TradeSignal` is allowed to
execute.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from src.kalshi_client import KalshiClient
from src.logger import get_daily_pnl
from src.signal import TradeSignal

load_dotenv()


@dataclass
class RiskDecision:
    """The outcome of a risk evaluation."""

    approved: bool
    reason: str | None = None


class RiskManager:
    """Gates `TradeSignal`s against configured risk limits."""

    def __init__(
        self,
        min_confidence: float | None = None,
        max_bet_usd: float | None = None,
        max_open_positions: int | None = None,
        daily_loss_limit_usd: float | None = None,
    ) -> None:
        """Initialize the risk manager.

        Each limit defaults to its corresponding environment variable
        (`MIN_CONFIDENCE`, `MAX_BET_USD`, `MAX_OPEN_POSITIONS`,
        `DAILY_LOSS_LIMIT_USD`) if not provided explicitly.

        Args:
            min_confidence: Minimum `TradeSignal.confidence` to act on.
            max_bet_usd: Maximum `TradeSignal.size_usd` allowed.
            max_open_positions: Maximum number of concurrent open positions.
            daily_loss_limit_usd: Stop trading once today's realized loss
                (in dollars) reaches or exceeds this value.
        """
        self.min_confidence = (
            min_confidence if min_confidence is not None else float(os.environ.get("MIN_CONFIDENCE", "0.70"))
        )
        self.max_bet_usd = (
            max_bet_usd if max_bet_usd is not None else float(os.environ.get("MAX_BET_USD", "10"))
        )
        self.max_open_positions = (
            max_open_positions if max_open_positions is not None else int(os.environ.get("MAX_OPEN_POSITIONS", "5"))
        )
        self.daily_loss_limit_usd = (
            daily_loss_limit_usd
            if daily_loss_limit_usd is not None
            else float(os.environ.get("DAILY_LOSS_LIMIT_USD", "50"))
        )

    def evaluate(self, signal: TradeSignal, client: KalshiClient) -> RiskDecision:
        """Decide whether a trade signal is allowed to execute.

        Checks are applied in order; the first failing check determines the
        rejection reason.

        Args:
            signal: The `TradeSignal` to evaluate.
            client: Authenticated Kalshi client, used to check open positions.

        Returns:
            A `RiskDecision` with `approved=True` if all checks pass, or
            `approved=False` with a human-readable `reason` otherwise.
        """
        if signal.skip:
            return RiskDecision(approved=False, reason=signal.skip_reason or "signal marked skip")

        if signal.confidence < self.min_confidence:
            return RiskDecision(
                approved=False,
                reason=f"confidence {signal.confidence} below MIN_CONFIDENCE {self.min_confidence}",
            )

        if signal.size_usd <= 0:
            return RiskDecision(approved=False, reason="size_usd must be positive")

        if signal.size_usd > self.max_bet_usd:
            return RiskDecision(
                approved=False,
                reason=f"size_usd {signal.size_usd} exceeds MAX_BET_USD {self.max_bet_usd}",
            )

        open_positions = self._count_open_positions(client)
        if open_positions >= self.max_open_positions:
            return RiskDecision(
                approved=False,
                reason=f"open positions {open_positions} >= MAX_OPEN_POSITIONS {self.max_open_positions}",
            )

        daily_pnl = get_daily_pnl()
        if daily_pnl <= -abs(self.daily_loss_limit_usd):
            return RiskDecision(
                approved=False,
                reason=f"daily pnl {daily_pnl} breaches DAILY_LOSS_LIMIT_USD {self.daily_loss_limit_usd}",
            )

        return RiskDecision(approved=True)

    def _count_open_positions(self, client: KalshiClient) -> int:
        """Count currently open Kalshi positions.

        Args:
            client: Authenticated Kalshi client.

        Returns:
            The number of markets with a non-zero position.
        """
        positions = client.get_positions()
        market_positions = positions.get("market_positions", [])
        return sum(1 for position in market_positions if position.get("position", 0) != 0)
