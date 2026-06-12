"""Tests for src.logger."""

from src.logger import get_recent_trades, init_db, record_trade


def _trade(**overrides: object) -> dict:
    base = {
        "market_ticker": "TICKER-1",
        "side": "yes",
        "size_usd": 5.0,
        "confidence": 0.8,
        "edge": 0.1,
        "status": "dry_run",
    }
    base.update(overrides)
    return base


def test_get_recent_trades_empty(tmp_path):
    db = str(tmp_path / "trades.db")
    init_db(db)
    assert get_recent_trades(db_path=db) == []


def test_get_recent_trades_orders_newest_first(tmp_path):
    db = str(tmp_path / "trades.db")
    init_db(db)

    record_trade(_trade(market_ticker="FIRST"), db_path=db)
    record_trade(_trade(market_ticker="SECOND"), db_path=db)
    record_trade(_trade(market_ticker="THIRD"), db_path=db)

    rows = get_recent_trades(db_path=db)

    assert [r["market_ticker"] for r in rows] == ["THIRD", "SECOND", "FIRST"]


def test_get_recent_trades_respects_limit(tmp_path):
    db = str(tmp_path / "trades.db")
    init_db(db)

    for i in range(5):
        record_trade(_trade(market_ticker=f"T{i}"), db_path=db)

    rows = get_recent_trades(limit=2, db_path=db)
    assert len(rows) == 2


def test_get_recent_trades_returns_dicts(tmp_path):
    db = str(tmp_path / "trades.db")
    init_db(db)
    record_trade(_trade(), db_path=db)

    rows = get_recent_trades(db_path=db)

    assert isinstance(rows[0], dict)
    assert rows[0]["market_ticker"] == "TICKER-1"
    assert rows[0]["side"] == "yes"
    assert rows[0]["size_usd"] == 5.0
