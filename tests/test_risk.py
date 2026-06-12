"""Tests for src.risk."""

from unittest.mock import MagicMock

import pytest

from src.risk import RiskManager
from src.signal import TradeSignal


def _signal(**overrides) -> TradeSignal:
    defaults = dict(
        market_ticker="TICKER",
        side="yes",
        confidence=0.8,
        size_usd=5.0,
        edge=0.1,
        reasoning="...",
        sources=[],
        skip=False,
        skip_reason=None,
    )
    defaults.update(overrides)
    return TradeSignal(**defaults)


def _client_with_open_positions(count: int) -> MagicMock:
    client = MagicMock()
    client.get_positions.return_value = {"market_positions": [{"position": 1} for _ in range(count)]}
    return client


@pytest.fixture
def risk_manager() -> RiskManager:
    return RiskManager(min_confidence=0.7, max_bet_usd=10, max_open_positions=2, daily_loss_limit_usd=50)


def test_skip_signal_rejected(risk_manager):
    decision = risk_manager.evaluate(_signal(skip=True, skip_reason="no edge"), _client_with_open_positions(0))

    assert decision.approved is False
    assert decision.reason == "no edge"


def test_low_confidence_rejected(risk_manager):
    decision = risk_manager.evaluate(_signal(confidence=0.5), _client_with_open_positions(0))

    assert decision.approved is False
    assert "confidence" in decision.reason


def test_oversized_bet_rejected(risk_manager):
    decision = risk_manager.evaluate(_signal(size_usd=20), _client_with_open_positions(0))

    assert decision.approved is False
    assert "MAX_BET_USD" in decision.reason


def test_too_many_open_positions_rejected(risk_manager):
    decision = risk_manager.evaluate(_signal(), _client_with_open_positions(2))

    assert decision.approved is False
    assert "open positions" in decision.reason


def test_daily_loss_limit_rejected(risk_manager, monkeypatch):
    monkeypatch.setattr("src.risk.get_daily_pnl", lambda: -60.0)

    decision = risk_manager.evaluate(_signal(), _client_with_open_positions(0))

    assert decision.approved is False
    assert "DAILY_LOSS_LIMIT_USD" in decision.reason


def test_signal_approved(risk_manager, monkeypatch):
    monkeypatch.setattr("src.risk.get_daily_pnl", lambda: 0.0)

    decision = risk_manager.evaluate(_signal(), _client_with_open_positions(0))

    assert decision.approved is True
    assert decision.reason is None
