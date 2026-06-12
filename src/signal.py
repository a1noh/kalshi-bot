"""Trading signal generation via Claude.

For each candidate market, asks Claude (with the web search tool enabled) to
research the market and return a structured `TradeSignal` describing whether
there's a tradeable edge.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from src.order_book import OrderBookSummary

load_dotenv()

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

SYSTEM_PROMPT = """\
You are a careful, risk-aware trading analyst evaluating Kalshi prediction \
market contracts.

For the market described in the user message, research it using web search \
to find current, relevant information (news, polls, data releases, official \
statements, etc.). Then form your own estimate of the true probability that \
the market resolves "yes".

Compare your estimate to the market's current implied probability (the mid \
price, where 1.00 = 100% likely yes). The "edge" is your_probability minus \
the market's yes mid price (positive edge favors "yes", negative favors "no").

Only recommend a trade when:
- You have a clear, well-sourced reason your estimate differs from the market.
- The edge is large enough to be worth the bid/ask spread and execution risk.
- Your confidence in your own estimate is genuinely high.

If you don't have a real edge, set "skip" to true and explain why in \
"skip_reason" - skipping is the correct default outcome for most markets.

After your research, call the submit_signal tool with your conclusion. \
"side" is the side you'd buy ("yes" or "no"). "size_usd" must not exceed \
the max bet size given in the prompt. "sources" must list the URLs you used.\
"""


class TradeSignal(BaseModel):
    """A structured trading recommendation for a single Kalshi market."""

    market_ticker: str
    side: Literal["yes", "no"]
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in this signal, 0-1.")
    size_usd: float = Field(ge=0.0, description="Recommended dollar amount to risk.")
    edge: float = Field(description="Estimated probability minus market mid price.")
    reasoning: str = Field(description="Explanation of the estimate and trade rationale.")
    sources: list[str] = Field(default_factory=list, description="URLs used to inform the estimate.")
    skip: bool = Field(description="True if no trade should be made on this market.")
    skip_reason: str | None = Field(default=None, description="Why this market was skipped, if skip is true.")


def generate_signal(
    market: dict[str, Any],
    client: anthropic.Anthropic | None = None,
) -> TradeSignal:
    """Ask Claude for a trading signal on a candidate market.

    Args:
        market: A market dict from `market_scanner.scan_markets`, including
            an `"order_book"` key with an `OrderBookSummary`.
        client: Optional Anthropic client. A new one is created (using
            `ANTHROPIC_API_KEY` from the environment) if not provided.

    Returns:
        A validated `TradeSignal`.

    Raises:
        anthropic.APIError: On API failures (subject to the SDK's built-in
            retry behavior for rate limits and server errors).
        pydantic.ValidationError: If Claude's response does not match the
            `TradeSignal` schema.
    """
    client = client or anthropic.Anthropic()

    tools: list[Any] = [
        {"type": "web_search_20260209", "name": "web_search"},
        {
            "name": "submit_signal",
            "description": "Submit the final trading signal after completing research.",
            "input_schema": _signal_schema(),
        },
    ]

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_prompt(market)}],
        tools=tools,
    )

    for block in reversed(response.content):
        if block.type == "tool_use" and block.name == "submit_signal":
            return TradeSignal.model_validate(block.input)

    raise ValueError("Claude did not call submit_signal — check model output")


def _build_prompt(market: dict[str, Any]) -> str:
    """Build the user prompt describing a candidate market.

    Args:
        market: A market dict from `market_scanner.scan_markets`, including
            an `"order_book"` key with an `OrderBookSummary`.

    Returns:
        The prompt text to send to Claude.
    """
    order_book: OrderBookSummary = market["order_book"]
    max_bet_usd = os.environ.get("MAX_BET_USD", "10")

    return (
        f"Market ticker: {market.get('ticker')}\n"
        f"Title: {market.get('title', '')}\n"
        f"Yes subtitle: {market.get('yes_sub_title', '')}\n"
        f"No subtitle: {market.get('no_sub_title', '')}\n"
        f"Rules: {market.get('rules_primary', '')}\n"
        f"Close time: {market.get('close_time', '')}\n"
        f"\n"
        f"Current order book (prices are dollars per contract, 0.00-1.00):\n"
        f"  Best bid (yes): {order_book.best_bid}\n"
        f"  Best ask (yes): {order_book.best_ask}\n"
        f"  Mid price (implied yes probability): {order_book.mid_price}\n"
        f"  Spread: {order_book.spread_pct}\n"
        f"\n"
        f"Maximum size_usd for this trade: {max_bet_usd}\n"
        f"\n"
        f"Research this market and respond with a TradeSignal JSON object."
    )


def _signal_schema() -> dict[str, Any]:
    """Build the JSON schema used to constrain Claude's response.

    Returns:
        A JSON schema derived from `TradeSignal`, with `additionalProperties`
        disabled.
    """
    schema = TradeSignal.model_json_schema()
    schema["additionalProperties"] = False
    return schema
