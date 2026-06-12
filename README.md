# kalshi-bot

Automated trading bot for [Kalshi](https://kalshi.com) prediction markets.
Scans open markets, generates trade signals via Claude (with web search),
gates them through a risk manager, and places orders. Includes a minimal
local dashboard for monitoring trades and account state.

> **`DRY_RUN=true` by default** — no real orders are placed until you
> explicitly set `DRY_RUN=false` in your `.env`.

## Setup

```bash
python -m venv .venv
# Windows:
.\.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # then fill in your credentials
```

## Run the bot

```bash
python main.py
```

## Run the dashboard

```bash
uvicorn ui.app:app --reload
```

Then open [http://localhost:8000](http://localhost:8000).

The dashboard shows live account summary (balance, PnL, open positions) and
a table of recent trades, polling every 5 seconds. It is read-only — it does
not place or cancel orders.

## Lint & tests

```bash
ruff check .
pytest -q
```

## Docker

```bash
cp .env.example .env   # fill in credentials
docker compose up --build
```

Starts both the bot and the dashboard (`http://localhost:8000`).

## Environment variables

See `.env.example` for the full list. Key ones:

| Var | Default | Purpose |
|---|---|---|
| `KALSHI_API_KEY_ID` | — | Kalshi API key ID |
| `KALSHI_PRIVATE_KEY` | — | RSA private key PEM |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `DRY_RUN` | `true` | Log trades without placing real orders |
| `MAX_BET_USD` | `5` | Max $ size per trade |
| `MIN_CONFIDENCE` | `0.7` | Min signal confidence to act on |
| `DAILY_LOSS_LIMIT_USD` | `50` | Stop trading once exceeded |
