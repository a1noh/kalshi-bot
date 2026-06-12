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

import anthropic as _anthropic
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
# Discovery helpers
# ---------------------------------------------------------------------------

_DISCOVER_SYSTEM = """\
You are an expert prediction market trader using Kalshi (kalshi.com).
Your task: find today's top 2-3 actionable Kalshi markets in under 4 web searches.

Do exactly this:
1. One search for today's big news events.
2. One or two searches to find real Kalshi market tickers for the best events \
   (e.g. "kalshi [topic] market ticker" or "site:kalshi.com [event]"). \
   Tickers look like: KXBTCD-26DEC3130, KXELECT-25NOV5-T, PRES-2024-DJT.
3. Call submit_opportunities immediately — do not do more searches.

Only include markets with real, verified tickers from your searches. \
If you cannot find a real ticker, skip that market. \
Pass an empty list if nothing has genuine edge.\
"""


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


def _discovery_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["opportunities"],
        "properties": {
            "opportunities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["ticker", "title", "side", "confidence", "size_usd", "edge", "reasoning", "sources"],
                    "properties": {
                        "ticker":     {"type": "string"},
                        "title":      {"type": "string"},
                        "side":       {"type": "string", "enum": ["yes", "no"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "size_usd":   {"type": "number", "minimum": 0},
                        "edge":       {"type": "number"},
                        "reasoning":  {"type": "string"},
                        "sources":    {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    }


def _run_discovery(max_bet_usd: float) -> list[dict[str, Any]]:
    """Call Claude to find today's hot Kalshi markets. Returns raw list from Claude."""
    import json as _json
    client = _anthropic.Anthropic(timeout=90.0)
    tools: list[Any] = [
        {"type": "web_search_20260209", "name": "web_search"},
        {
            "name": "submit_opportunities",
            "description": "Submit the list of discovered Kalshi market opportunities after completing research.",
            "input_schema": _discovery_schema(),
        },
    ]
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=_DISCOVER_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Max bet size per trade: ${max_bet_usd}. "
                "Do 3-4 web searches max, then immediately call submit_opportunities. "
                "Find today's top 2-3 Kalshi prediction market opportunities with real tickers."
            ),
        }],
        tools=tools,
    )
    for block in reversed(response.content):
        if block.type == "tool_use" and block.name == "submit_opportunities":
            data = block.input
            if isinstance(data, str):
                data = _json.loads(data)
            return data.get("opportunities", [])
    return []


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
    """Ask Claude to find hot markets via web search. Validates each ticker against Kalshi."""
    max_bet = float(os.environ.get("MAX_BET_USD", "10"))

    try:
        raw = _run_discovery(max_bet)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_anthropic_error_msg(exc)) from exc

    results = []
    for opp in raw:
        ticker = opp.get("ticker", "").strip()
        if not ticker:
            continue
        try:
            market = get_client().get_market(ticker)
            order_book = get_order_book_summary(get_client(), ticker)
        except Exception:
            continue  # ticker invalid or market closed — skip silently
        results.append({
            **opp,
            "ticker": ticker,
            "title": market.get("title") or opp.get("title", ticker),
            "size_usd": min(float(opp.get("size_usd", max_bet)), max_bet),
            "mid_price": order_book.mid_price,
            "best_bid": order_book.best_bid,
            "best_ask": order_book.best_ask,
        })

    return results


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
