const REFRESH_MS = 5000;

function formatUsd(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `$${Number(value).toFixed(2)}`;
}

async function refreshSummary() {
  const response = await fetch("/api/summary");
  const data = await response.json();

  const badge = document.getElementById("mode-badge");
  const isDryRun = String(data.dry_run).toLowerCase() !== "false";
  badge.textContent = isDryRun ? "DRY RUN" : "LIVE";
  badge.className = `badge ${isDryRun ? "dry-run" : "live"}`;

  document.getElementById("daily-pnl").textContent = formatUsd(data.daily_pnl);
  document.getElementById("open-positions-logged").textContent = data.open_positions_logged ?? "-";
  document.getElementById("balance").textContent = formatUsd(data.balance_usd);
  document.getElementById("kalshi-positions").textContent = data.kalshi_open_positions ?? "-";
}

async function refreshTrades() {
  const response = await fetch("/api/trades?limit=50");
  const trades = await response.json();

  const body = document.getElementById("trades-body");
  body.replaceChildren();

  if (trades.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 8;
    cell.textContent = "No trades yet.";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }

  for (const trade of trades) {
    const row = document.createElement("tr");
    const cells = [
      trade.created_at,
      trade.market_ticker,
      trade.side,
      formatUsd(trade.size_usd),
      trade.confidence?.toFixed(2) ?? "-",
      trade.edge?.toFixed(3) ?? "-",
      trade.status,
      trade.reasoning ?? "",
    ];

    cells.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === cells.length - 1) {
        cell.className = "reasoning";
        cell.title = value;
      }
      row.appendChild(cell);
    });

    body.appendChild(row);
  }
}

function refreshAll() {
  refreshSummary().catch(console.error);
  refreshTrades().catch(console.error);
}

refreshAll();
setInterval(refreshAll, REFRESH_MS);
