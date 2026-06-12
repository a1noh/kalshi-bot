"""Tests for src.signal."""

import json
from unittest.mock import MagicMock

from src.order_book import OrderBookSummary
from src.signal import TradeSignal, _build_prompt, _signal_schema, generate_signal


def _market() -> dict:
    return {
        "ticker": "TICKER",
        "title": "Will X happen?",
        "close_time": "2026-12-31T00:00:00Z",
        "order_book": OrderBookSummary(
            ticker="TICKER", best_bid=0.40, best_ask=0.45, spread_pct=0.05, mid_price=0.425
        ),
    }


def test_build_prompt_includes_market_details():
    prompt = _build_prompt(_market())

    assert "TICKER" in prompt
    assert "Will X happen?" in prompt
    assert "0.425" in prompt


def test_signal_schema_disables_additional_properties():
    schema = _signal_schema()

    assert schema["additionalProperties"] is False
    assert "market_ticker" in schema["properties"]
    assert "skip" in schema["properties"]


def test_generate_signal_parses_response():
    signal_data = {
        "market_ticker": "TICKER",
        "side": "yes",
        "confidence": 0.8,
        "size_usd": 5.0,
        "edge": 0.1,
        "reasoning": "Some reasoning",
        "sources": ["https://example.com"],
        "skip": False,
        "skip_reason": None,
    }

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "submit_signal"
    tool_block.input = signal_data

    response = MagicMock()
    response.content = [tool_block]

    client = MagicMock()
    client.messages.create.return_value = response

    signal = generate_signal(_market(), client=client)

    assert isinstance(signal, TradeSignal)
    assert signal.market_ticker == "TICKER"
    assert signal.side == "yes"
    assert signal.skip is False

    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    assert any(tool["type"] == "web_search_20260209" for tool in call_kwargs["tools"])
    assert any(t.get("name") == "submit_signal" for t in call_kwargs["tools"])
