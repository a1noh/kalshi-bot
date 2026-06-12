"""Minimal local dashboard for kalshi-bot.

Serves a single static page plus JSON endpoints for viewing trade history,
triggering on-demand market research, and placing manual bets. The continuous
scan loop (main.py) is a separate process; the dashboard is always-on and
executes actions only when the user clicks a button.

Auth: set DASHBOARD_PASSWORD in .env to enable HTTP Basic Auth (username:
admin). Leave unset to run without auth (fine for local dev).
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.executor import place_order
from src.kalshi_client import KalshiClient
from src.logger import get_daily_pnl, get_open_position_count, get_recent_trades, record_trade
from src.order_book import get_order_book_summary
from src.risk import RiskManager
from src.signal import TradeSignal, generate_signal

STATIC_DIR = Path(__file__).parent / "static"
_DASHBOARD_USER = "admin"
_security = HTTPBasic(auto_error=False)

app = FastAPI(title="kalshi-bot dashboard")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> None:
    """Enforce HTTP Basic Auth when DASHBOARD_PASSWORD is set."""
    password = os.environ.get("DASHBOARD_PASSWORD", "")
    if not password:
        return
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


# ---------------------------------------------------------------------------
# Kalshi client (lazy, cached)
# ---------------------------------------------------------------------------


@lru_cache
def get_client() -> KalshiClient:
    """Return a cached `KalshiClient`, constructed lazily on first use."""
    return KalshiClient()


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class ResearchRequest(BaseModel):
    ticker: str


class BetRequest(BaseModel):
    ticker: str
    side: Literal["yes", "no"]
    size_usd: float


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", dependencies=[Depends(_require_auth)])
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/trades", dependencies=[Depends(_require_auth)])
def trades(limit: int = 50) -> list[dict[str, Any]]:
    return get_recent_trades(limit=limit)


@app.get("/api/summary", dependencies=[Depends(_require_auth)])
def summary() -> dict[str, Any]:
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


@app.post("/api/research", dependencies=[Depends(_require_auth)])
def research(body: ResearchRequest) -> dict[str, Any]:
    """Run Claude market research on a ticker. Takes ~30s (web search)."""
    try:
        market = get_client().get_market(body.ticker)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Market not found: {exc}") from exc

    try:
        order_book = get_order_book_summary(get_client(), body.ticker)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Order book fetch failed: {exc}") from exc

    signal = generate_signal({**market, "order_book": order_book})
    return signal.model_dump()


@app.post("/api/bet", dependencies=[Depends(_require_auth)])
def bet(body: BetRequest) -> dict[str, Any]:
    """Place a manual bet. Runs through risk checks; respects DRY_RUN."""
    try:
        order_book = get_order_book_summary(get_client(), body.ticker)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Order book fetch failed: {exc}") from exc

    signal = TradeSignal(
        market_ticker=body.ticker,
        side=body.side,
        confidence=1.0,
        size_usd=body.size_usd,
        edge=0.0,
        reasoning="Manual bet placed via dashboard",
        sources=[],
        skip=False,
    )

    try:
        decision = RiskManager().evaluate(signal, get_client())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Risk check failed: {exc}") from exc

    if not decision.approved:
        raise HTTPException(status_code=400, detail=decision.reason)

    try:
        result = place_order(get_client(), signal, order_book)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Order placement failed: {exc}") from exc

    record_trade({
        "market_ticker": body.ticker,
        "side": body.side,
        "size_usd": body.size_usd,
        "confidence": 1.0,
        "edge": 0.0,
        "status": "dry_run" if result["dry_run"] else "open",
        "reasoning": "Manual bet via dashboard",
        "order_id": (result.get("response") or {}).get("order", {}).get("order_id"),
    })
    return result
