"""Minimal local dashboard for kalshi-bot.

Serves a single static page plus two JSON endpoints showing recent trades
and a summary of account/risk state. Read-only - does not place or cancel
orders.

Auth: set DASHBOARD_PASSWORD in .env to enable HTTP Basic Auth (username:
admin). Leave unset to run without auth (fine for local dev).
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from src.kalshi_client import KalshiClient
from src.logger import get_daily_pnl, get_open_position_count, get_recent_trades

STATIC_DIR = Path(__file__).parent / "static"
_DASHBOARD_USER = "admin"
_security = HTTPBasic(auto_error=False)

app = FastAPI(title="kalshi-bot dashboard")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> None:
    """Enforce HTTP Basic Auth when DASHBOARD_PASSWORD is set."""
    password = os.environ.get("DASHBOARD_PASSWORD", "")
    if not password:
        return  # auth disabled when no password configured
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    user_ok = secrets.compare_digest(
        credentials.username.encode(), _DASHBOARD_USER.encode()
    )
    pass_ok = secrets.compare_digest(credentials.password.encode(), password.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


@lru_cache
def get_client() -> KalshiClient:
    """Return a cached `KalshiClient`, constructed lazily on first use."""
    return KalshiClient()


@app.get("/", dependencies=[Depends(_require_auth)])
def index() -> FileResponse:
    """Serve the dashboard's single HTML page."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/trades", dependencies=[Depends(_require_auth)])
def trades(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent trade log rows, newest first."""
    return get_recent_trades(limit=limit)


@app.get("/api/summary", dependencies=[Depends(_require_auth)])
def summary() -> dict[str, Any]:
    """Return account balance, risk-relevant counts, and DRY_RUN status."""
    result: dict[str, Any] = {
        "dry_run": os.environ.get("DRY_RUN", "true"),
        "daily_pnl": get_daily_pnl(),
        "open_positions_logged": get_open_position_count(),
        "balance_usd": None,
        "kalshi_open_positions": None,
    }
    try:
        balance = get_client().get_balance()
        result["balance_usd"] = balance.get("balance", 0) / 100
    except Exception:
        pass
    try:
        positions = get_client().get_positions()
        result["kalshi_open_positions"] = len(positions.get("market_positions", []))
    except Exception:
        pass
    return result
