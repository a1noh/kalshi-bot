"""Minimal local dashboard for kalshi-bot.

Always-on FastAPI server. The continuous scan loop (main.py) is a separate
process. This dashboard executes actions only when the user clicks a button.

Auth: set DASHBOARD_PASSWORD in .env (username: admin). Leave unset for no auth.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from anthropic import BadRequestError as _BadRequestError
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

_DISCOVER_MIN_VOLUME = 50
_DISCOVER_MIN_MINUTES = 30
_DISCOVER_LIMIT = 8
# Known high-volume series to query for discover
_DISCOVER_SERIES = [
    "KXINX",   # S&P 500
    "KXBTCD",  # Bitcoin
    "KXFED",   # Federal Reserve rate
    "KXETH",   # Ethereum
    "KXNDAQ",  # Nasdaq
    "KXDOW",   # Dow Jones
    "KXGOLD",  # Gold
    "KXOIL",   # Oil
]

STATIC_DIR = Path(__file__).parent / "static"
_DASHBOARD_USER = "admin"
_security = HTTPBasic(auto_error=False)

app = FastAPI(title="kalshi-bot dashboard")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> None:
    password = os.environ.get("DASHBOARD_PASSWORD", "")
    if not password:
        return
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    user_ok = secrets.compare_digest(credentials.username.encode(), _DASHBOARD_USER.encode())
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
# Discovery helpers — pull real markets from Kalshi (no Claude, no API cost)
# ---------------------------------------------------------------------------


def _anthropic_error_msg(exc: Exception) -> str:
    """Return a human-readable message from an Anthropic API error."""
    if isinstance(exc, _BadRequestError):
        body = getattr(exc, "body", None) or {}
        msg = (body.get("error") or {}).get("message", "")
        if "credit balance" in msg.lower():
            return "Anthropic API credits depleted — add credits at console.anthropic.com"
        if msg:
            return f"Anthropic API error: {msg}"
    return str(exc)


def _market_display_title(market: dict[str, Any]) -> str:
    """Return a title that distinguishes markets in the same series."""
    title = market.get("title", market.get("ticker", ""))
    # For binary markets, append yes_sub_title if it contains a price/threshold
    yes_sub = market.get("yes_sub_title", "")
    no_sub = market.get("no_sub_title", "")
    sub = yes_sub or no_sub
    if sub and sub.lower() not in title.lower():
        return f"{title} — {sub}"
    return title


def _run_discovery(max_bet_usd: float) -> list[dict[str, Any]]:
    """Return the top open Kalshi markets by volume — no Claude call needed.

    Queries a curated set of high-volume series so we skip the parlay/multi-event
    markets that dominate the generic /markets endpoint.
    """
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    all_markets: list[dict[str, Any]] = []

    for series in _DISCOVER_SERIES:
        try:
            resp = get_client().get_markets(status="open", series_ticker=series, limit=50)
            all_markets.extend(resp.get("markets", []))
        except Exception:
            continue

    candidates: list[tuple[float, dict[str, Any]]] = []
    for m in all_markets:
        close_str = m.get("close_time", "")
        if not close_str:
            continue
        close = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
        if (close - now).total_seconds() < _DISCOVER_MIN_MINUTES * 60:
            continue
        vol = float(m.get("volume_fp") or m.get("volume") or 0)
        if vol < _DISCOVER_MIN_VOLUME:
            continue
        candidates.append((vol, m))

    candidates.sort(key=lambda x: x[0], reverse=True)

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for vol, m in candidates:
        if len(results) >= _DISCOVER_LIMIT:
            break
        ticker = m["ticker"]
        if ticker in seen:
            continue
        seen.add(ticker)
        try:
            ob = get_order_book_summary(get_client(), ticker)
        except Exception:
            continue
        results.append({
            "ticker": ticker,
            "title": _market_display_title(m),
            "size_usd": max_bet_usd,
            "mid_price": ob.mid_price,
            "best_bid": ob.best_bid,
            "best_ask": ob.best_ask,
            "volume": vol,
        })

    return results


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
        "max_bet_usd": float(os.environ.get("MAX_BET_USD", "10")),
        "balance_usd": None,
        "kalshi_open_positions": None,
    }
    try:
        result["balance_usd"] = get_client().get_balance().get("balance", 0) / 100
    except Exception:
        pass
    try:
        result["kalshi_open_positions"] = len(
            get_client().get_positions().get("market_positions", [])
        )
    except Exception:
        pass
    return result


@app.post("/api/discover", dependencies=[Depends(_require_auth)])
def discover() -> list[dict[str, Any]]:
    """Return top open Kalshi markets by volume. Fast — no Claude call."""
    max_bet = float(os.environ.get("MAX_BET_USD", "10"))
    try:
        return _run_discovery(max_bet)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/research", dependencies=[Depends(_require_auth)])
def research(body: ResearchRequest) -> dict[str, Any]:
    """Run Claude research on a specific ticker (~30 s)."""
    try:
        market = get_client().get_market(body.ticker)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Market not found: {exc}") from exc
    try:
        order_book = get_order_book_summary(get_client(), body.ticker)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Order book fetch failed: {exc}") from exc

    try:
        signal = generate_signal({**market, "order_book": order_book})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_anthropic_error_msg(exc)) from exc
    return signal.model_dump()


@app.post("/api/bet", dependencies=[Depends(_require_auth)])
def bet(body: BetRequest) -> dict[str, Any]:
    """Place a manual bet. Runs risk checks; respects DRY_RUN."""
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
