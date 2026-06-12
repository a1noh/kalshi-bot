# kalshi-bot

Production-grade automated trading bot for [Kalshi](https://kalshi.com)
prediction markets. Scans open markets, asks Claude for a trading signal
(with web search for current context), runs the signal through a risk gate,
and places orders via the Kalshi REST API. Everything is config-driven via
`.env`, structured-logged to JSON + SQLite, containerized, tested, and
deployed via CI/CD.

## Architecture

```
main.py                  # run loop: scan -> filter -> signal -> risk -> execute -> log -> sleep
src/
  kalshi_client.py        # authenticated Kalshi REST API wrapper (RSA-PSS signed requests, retries)
  market_scanner.py        # fetch open events/markets, filter by volume/spread/time-to-close
  order_book.py            # fetch orderbook, compute best_bid/best_ask/spread_pct/mid_price
  signal.py                 # ask Claude (claude-sonnet-4-6 + web search) for a structured TradeSignal
  risk.py                   # gate signals against MIN_CONFIDENCE/MAX_BET_USD/MAX_OPEN_POSITIONS/DAILY_LOSS_LIMIT_USD
  executor.py               # place/cancel orders via Kalshi API, respects DRY_RUN
  logger.py                 # structured JSON logging + SQLite trade log
tests/                      # pytest suite, no live network calls
.github/workflows/ci.yml     # ruff + pytest on push/PR
Dockerfile / docker-compose.yml
```

## Build status

- [x] Step 0 - repo scaffold (.gitignore, .env, requirements.txt, CLAUDE.md, dirs)
- [x] Step 1 - src/kalshi_client.py
- [x] Step 2 - src/market_scanner.py
- [x] Step 3 - src/order_book.py
- [x] Step 4 - src/signal.py
- [x] Step 5 - src/risk.py
- [x] Step 6 - src/executor.py
- [x] Step 7 - src/logger.py
- [x] Step 8 - main.py
- [x] Step 9 - tests/ (16 tests, `pytest -q` passes, no live network calls)
- [x] Step 10 - CI/CD & Docker (`.github/workflows/ci.yml` runs ruff + pytest;
      `Dockerfile` + `docker-compose.yml` for deployment; `ruff check .` passes)
- [x] Live smoke test - `python main.py` with `DRY_RUN=true` connects to the
      real Kalshi API (RSA-PSS auth verified) and scans markets concurrently
      with no crashes.

### Known characteristic: scan duration

`scan_markets` pages through *all* open Kalshi markets and fetches an order
book for every market closing more than `MIN_HOURS_TO_CLOSE` (24h) out, using
a bounded thread pool (`ORDER_BOOK_FETCH_WORKERS`, default 20). Kalshi
typically has thousands of such markets, so one full scan cycle can take
several minutes - longer than the default `SCAN_INTERVAL_SECONDS=60`. This
isn't a bug (the loop just runs back-to-back rather than sleeping), but if
faster cycles are needed later, consider narrowing `get_markets()` with a
`series_ticker`/category filter to a smaller universe of markets.

- [ ] Future - web UI (dashboard for positions, signals, trade history)
- [ ] Future - Railway CD: connect this repo to a Railway project, set the
      env vars from `.env.example` as Railway service variables, and deploy
      on push to `main` (Railway auto-builds from the `Dockerfile`)

## Environment variables (.env, see .env.example)

| Var | Purpose |
|---|---|
| `KALSHI_API_KEY_ID` | Kalshi API key ID (UUID) |
| `KALSHI_PRIVATE_KEY` | RSA private key PEM, paired with the API key |
| `KALSHI_BASE_URL` | API base URL (prod: `https://external-api.kalshi.com/trade-api/v2`, demo: `https://external-api.demo.kalshi.co/trade-api/v2`) |
| `ANTHROPIC_API_KEY` | Anthropic API key for signal generation |
| `MAX_BET_USD` | Max $ size per trade |
| `MIN_CONFIDENCE` | Minimum signal confidence (0-1) to act on |
| `MAX_OPEN_POSITIONS` | Cap on concurrent open positions |
| `SCAN_INTERVAL_SECONDS` | Delay between scan loop iterations |
| `DAILY_LOSS_LIMIT_USD` | Stop trading for the day once exceeded |
| `ORDER_BOOK_FETCH_WORKERS` | Concurrent order-book lookups during market scanning (default 20) |
| `DRY_RUN` | When `true`, log intended trades instead of placing real orders |
| `ENV` | `development` / `production` |
| `LOG_LEVEL` | Python logging level |

## Kalshi API v2 notes

- Auth: every request carries `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`
  (ms epoch), and `KALSHI-ACCESS-SIGNATURE`.
- Signature = base64(RSA-PSS-SHA256(private_key, f"{timestamp_ms}{METHOD}{path}")),
  with MGF1(SHA256) and salt_length=PSS.DIGEST_LENGTH. `path` excludes the
  query string and host.
- `GET /markets` - filter by `status`, `event_ticker`, `series_ticker`.
- `GET /events` - list events (groups of related markets).
- `GET /markets/{ticker}/orderbook` - returns `yes`/`no` bid levels only
  (price in cents + qty). Since yes_price + no_price = 100c:
  `best_yes_ask = 100 - best_no_bid` and `best_no_ask = 100 - best_yes_bid`.
- `POST /portfolio/orders` - create order. `DELETE /portfolio/orders/{order_id}` - cancel.
- `GET /portfolio/balance`, `GET /portfolio/positions`.

## TradeSignal schema (src/signal.py)

```python
class TradeSignal(BaseModel):
    market_ticker: str
    side: Literal["yes", "no"]
    confidence: float       # 0.0-1.0
    size_usd: float
    edge: float              # estimated edge vs market price
    reasoning: str
    sources: list[str]       # URLs from web search
    skip: bool
    skip_reason: str | None
```

## Conventions

- All config via env vars (python-dotenv), never hardcoded.
- All Kalshi API calls wrapped in `tenacity` retry with exponential backoff.
- Structured JSON logging (no `print`), plus SQLite trade log for risk checks.
- Type hints + docstrings on all functions.
- `DRY_RUN=true` by default - no real orders until explicitly disabled.
