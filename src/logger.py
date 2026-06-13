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
    """Configure the root logger for structured JSON output to stdout."""
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel((level or os.environ.get("LOG_LEVEL", "INFO")).upper())


@contextmanager
def _connect(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path or DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    """Create / migrate the trades table."""
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
                order_id TEXT,
                series TEXT,
                full_reasoning TEXT,
                sources TEXT,
                skip_reason TEXT,
                outcome TEXT
            )
            """
        )
        # Migrate existing DBs that predate the new columns
        existing = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        for col, defn in [
            ("series", "TEXT"),
            ("full_reasoning", "TEXT"),
            ("sources", "TEXT"),
            ("skip_reason", "TEXT"),
            ("outcome", "TEXT"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {defn}")
        conn.commit()


def record_trade(trade: dict[str, Any], db_path: str | None = None) -> int:
    """Insert a trade/research record.

    Accepts all original fields plus:
      series, full_reasoning, sources (list[str]), skip_reason, outcome.
    """
    sources = trade.get("sources")
    if isinstance(sources, list):
        sources = json.dumps(sources)

    ticker = trade["market_ticker"]
    series = trade.get("series") or ticker.split("-")[0]

    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO trades (
                created_at, market_ticker, side, size_usd, confidence, edge,
                status, pnl_usd, reasoning, order_id,
                series, full_reasoning, sources, skip_reason, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(UTC).isoformat(),
                ticker,
                trade["side"],
                trade["size_usd"],
                trade["confidence"],
                trade["edge"],
                trade["status"],
                trade.get("pnl_usd"),
                trade.get("reasoning"),
                trade.get("order_id"),
                series,
                trade.get("full_reasoning"),
                sources,
                trade.get("skip_reason"),
                trade.get("outcome"),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_outcome(trade_id: int, outcome: str, pnl_usd: float | None = None,
                   db_path: str | None = None) -> None:
    """Set the win/loss outcome on a previously recorded trade."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE trades SET outcome = ?, pnl_usd = COALESCE(?, pnl_usd) WHERE id = ?",
            (outcome, pnl_usd, trade_id),
        )
        conn.commit()


def get_trade_history(series: str | None = None, limit: int = 8,
                      db_path: str | None = None) -> list[dict[str, Any]]:
    """Return recent trades, optionally filtered by series (e.g. 'KXBTCD')."""
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if series:
            rows = conn.execute(
                "SELECT * FROM trades WHERE series = ? ORDER BY created_at DESC LIMIT ?",
                (series, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("sources") and isinstance(d["sources"], str):
                try:
                    d["sources"] = json.loads(d["sources"])
                except Exception:
                    d["sources"] = []
            result.append(d)
        return result


def format_history_for_claude(trades: list[dict[str, Any]]) -> str:
    """Format past trades as a compact context block for Claude."""
    if not trades:
        return ""
    lines = ["Past trades on similar markets (use as reference — learn from wins/losses):"]
    for t in trades:
        outcome_str = f" → {t['outcome'].upper()}" if t.get("outcome") else ""
        skip_str = " [SKIPPED]" if t.get("status") == "skipped" else ""
        lines.append(
            f"- {t['market_ticker']} | {t['side'].upper()}{skip_str} | "
            f"conf={t['confidence']:.0%} | edge={t['edge']:+.1%}{outcome_str}"
        )
        reason = t.get("full_reasoning") or t.get("reasoning") or ""
        if reason:
            lines.append(f"  Analysis: {reason[:300]}")
        if t.get("skip_reason"):
            lines.append(f"  Skip reason: {t['skip_reason'][:150]}")
    return "\n".join(lines)


def get_daily_pnl(db_path: str | None = None, day: datetime | None = None) -> float:
    target_day = (day or datetime.now(UTC)).date().isoformat()
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) FROM trades WHERE date(created_at) = ?",
            (target_day,),
        ).fetchone()
        return float(row[0])


def get_recent_trades(limit: int = 50, db_path: str | None = None) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("sources") and isinstance(d["sources"], str):
                try:
                    d["sources"] = json.loads(d["sources"])
                except Exception:
                    d["sources"] = []
            result.append(d)
        return result


def get_open_position_count(db_path: str | None = None) -> int:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'open'").fetchone()
        return int(row[0])
