"""Structured logging and SQLite trade log.

Configures JSON-formatted stdlib logging for the whole application and
provides a small SQLite-backed trade log used by the risk manager and the
main run loop.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.environ.get("TRADE_LOG_DB", "trades.db")


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        """Render a log record as a JSON string.

        Args:
            record: The log record to format.

        Returns:
            A JSON-encoded string with `timestamp`, `level`, `logger`,
            `message`, and any `extra_fields` passed via `extra=`.
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields:
            payload.update(extra_fields)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str | None = None) -> None:
    """Configure the root logger for structured JSON output to stdout.

    Args:
        level: Logging level name (e.g. ``"INFO"``). Defaults to the
            ``LOG_LEVEL`` environment variable, or ``"INFO"``.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel((level or os.environ.get("LOG_LEVEL", "INFO")).upper())


@contextmanager
def _connect(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection to the trade log, closing it afterwards.

    Args:
        db_path: Path to the SQLite database file. Defaults to `DB_PATH`.

    Yields:
        An open `sqlite3.Connection`.
    """
    conn = sqlite3.connect(db_path or DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    """Create the `trades` table if it doesn't already exist.

    Args:
        db_path: Path to the SQLite database file. Defaults to `DB_PATH`.
    """
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                market_ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                size_usd REAL NOT NULL,
                confidence REAL NOT NULL,
                edge REAL NOT NULL,
                status TEXT NOT NULL,
                pnl_usd REAL,
                reasoning TEXT,
                order_id TEXT
            )
            """
        )
        conn.commit()


def record_trade(trade: dict[str, Any], db_path: str | None = None) -> int:
    """Insert a trade record into the trade log.

    Args:
        trade: A dict with keys ``market_ticker``, ``side``, ``size_usd``,
            ``confidence``, ``edge``, ``status`` (e.g. ``"open"``,
            ``"closed"``, ``"skipped"``, ``"rejected"``, ``"dry_run"``), and
            optionally ``pnl_usd``, ``reasoning``, ``order_id``.
        db_path: Path to the SQLite database file. Defaults to `DB_PATH`.

    Returns:
        The row id of the inserted trade.
    """
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO trades (
                created_at, market_ticker, side, size_usd, confidence, edge,
                status, pnl_usd, reasoning, order_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(UTC).isoformat(),
                trade["market_ticker"],
                trade["side"],
                trade["size_usd"],
                trade["confidence"],
                trade["edge"],
                trade["status"],
                trade.get("pnl_usd"),
                trade.get("reasoning"),
                trade.get("order_id"),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_daily_pnl(db_path: str | None = None, day: datetime | None = None) -> float:
    """Sum realized PnL for trades recorded on a given UTC day.

    Args:
        db_path: Path to the SQLite database file. Defaults to `DB_PATH`.
        day: The UTC day to sum. Defaults to today.

    Returns:
        The sum of `pnl_usd` for trades created on that day (0.0 if none).
    """
    target_day = (day or datetime.now(UTC)).date().isoformat()
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) FROM trades WHERE date(created_at) = ?",
            (target_day,),
        ).fetchone()
        return float(row[0])


def get_open_position_count(db_path: str | None = None) -> int:
    """Count trades currently recorded as open.

    Args:
        db_path: Path to the SQLite database file. Defaults to `DB_PATH`.

    Returns:
        The number of trades with `status = "open"`.
    """
    with _connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'open'").fetchone()
        return int(row[0])
