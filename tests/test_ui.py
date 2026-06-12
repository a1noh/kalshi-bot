"""Tests for ui.app."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.logger import init_db, record_trade
from ui.app import app


def _trade(**overrides: object) -> dict:
    base = {
        "market_ticker": "TEST-TICK",
        "side": "yes",
        "size_usd": 5.0,
        "confidence": 0.8,
        "edge": 0.1,
        "status": "dry_run",
    }
    base.update(overrides)
    return base


def test_index_serves_html(tmp_path, monkeypatch):
    db = str(tmp_path / "trades.db")
    monkeypatch.setattr("src.logger.DB_PATH", db)
    init_db(db)

    with TestClient(app) as tc:
        response = tc.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "kalshi-bot" in response.text


def test_api_trades_empty(tmp_path, monkeypatch):
    db = str(tmp_path / "trades.db")
    monkeypatch.setattr("src.logger.DB_PATH", db)
    init_db(db)

    with TestClient(app) as tc:
        response = tc.get("/api/trades")

    assert response.status_code == 200
    assert response.json() == []


def test_api_trades_returns_records(tmp_path, monkeypatch):
    db = str(tmp_path / "trades.db")
    monkeypatch.setattr("src.logger.DB_PATH", db)
    init_db(db)
    record_trade(_trade(market_ticker="KXTEST-A"), db_path=db)

    with TestClient(app) as tc:
        response = tc.get("/api/trades?limit=10")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["market_ticker"] == "KXTEST-A"


def test_api_summary_contains_expected_keys(tmp_path, monkeypatch):
    db = str(tmp_path / "trades.db")
    monkeypatch.setattr("src.logger.DB_PATH", db)
    init_db(db)

    mock_client = MagicMock()
    mock_client.get_balance.return_value = {"balance": 12345}
    mock_client.get_positions.return_value = {"market_positions": [{"x": 1}, {"x": 2}]}
    monkeypatch.setattr("ui.app.get_client", lambda: mock_client)

    with TestClient(app) as tc:
        response = tc.get("/api/summary")

    assert response.status_code == 200
    data = response.json()
    assert {"dry_run", "daily_pnl", "open_positions_logged", "balance_usd", "kalshi_open_positions"} <= set(
        data.keys()
    )
    assert data["balance_usd"] == pytest.approx(123.45)
    assert data["kalshi_open_positions"] == 2


def test_auth_blocks_unauthenticated_requests(tmp_path, monkeypatch):
    db = str(tmp_path / "trades.db")
    monkeypatch.setattr("src.logger.DB_PATH", db)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret")
    init_db(db)

    with TestClient(app, raise_server_exceptions=False) as tc:
        assert tc.get("/").status_code == 401
        assert tc.get("/api/trades").status_code == 401
        assert tc.get("/api/summary").status_code == 401


def test_auth_accepts_correct_credentials(tmp_path, monkeypatch):
    db = str(tmp_path / "trades.db")
    monkeypatch.setattr("src.logger.DB_PATH", db)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret")
    init_db(db)

    with TestClient(app) as tc:
        assert tc.get("/", auth=("admin", "secret")).status_code == 200
        assert tc.get("/api/trades", auth=("admin", "secret")).status_code == 200


def test_auth_rejects_wrong_password(tmp_path, monkeypatch):
    db = str(tmp_path / "trades.db")
    monkeypatch.setattr("src.logger.DB_PATH", db)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret")
    init_db(db)

    with TestClient(app, raise_server_exceptions=False) as tc:
        assert tc.get("/", auth=("admin", "wrong")).status_code == 401


def test_api_summary_tolerates_kalshi_failure(tmp_path, monkeypatch):
    db = str(tmp_path / "trades.db")
    monkeypatch.setattr("src.logger.DB_PATH", db)
    init_db(db)

    def _boom() -> None:
        raise RuntimeError("api down")

    monkeypatch.setattr("ui.app.get_client", _boom)

    with TestClient(app) as tc:
        response = tc.get("/api/summary")

    assert response.status_code == 200
    data = response.json()
    assert data["balance_usd"] is None
    assert data["kalshi_open_positions"] is None
