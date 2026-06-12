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

// ---------------------------------------------------------------------------
// Research
// ---------------------------------------------------------------------------

let selectedSide = "yes";

function setSide(side) {
  selectedSide = side;
  document.getElementById("side-yes-btn").classList.toggle("active", side === "yes");
  document.getElementById("side-no-btn").classList.toggle("active", side === "no");
}

document.getElementById("side-yes-btn").addEventListener("click", () => setSide("yes"));
document.getElementById("side-no-btn").addEventListener("click", () => setSide("no"));

document.getElementById("research-btn").addEventListener("click", async () => {
  const ticker = document.getElementById("research-ticker").value.trim().toUpperCase();
  if (!ticker) return;

  const btn = document.getElementById("research-btn");
  const statusEl = document.getElementById("research-status");
  const resultEl = document.getElementById("research-result");

  btn.disabled = true;
  statusEl.textContent = "Researching… Claude is searching the web (~30 s)";
  statusEl.className = "action-status";
  resultEl.className = "result-card hidden";
  resultEl.replaceChildren();

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120000);

  try {
    const res = await fetch("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker }),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }

    const data = await res.json();
    renderResearchResult(resultEl, data);
    statusEl.className = "action-status hidden";
    resultEl.className = "result-card";

    // pre-fill bet form with this ticker
    document.getElementById("bet-ticker").value = data.market_ticker;
    if (!data.skip) setSide(data.side);
  } catch (err) {
    clearTimeout(timeout);
    statusEl.textContent = `Error: ${err.message}`;
    statusEl.className = "action-status error";
  } finally {
    btn.disabled = false;
  }
});

function renderResearchResult(el, signal) {
  el.replaceChildren();

  const header = document.createElement("div");
  header.className = "result-header";

  const label = document.createElement("span");
  if (signal.skip) {
    label.className = "badge skip";
    label.textContent = "SKIP";
  } else {
    label.className = `badge ${signal.side === "yes" ? "yes" : "no"}`;
    label.textContent = `BUY ${signal.side.toUpperCase()}`;
  }
  header.appendChild(label);

  const meta = document.createElement("span");
  meta.className = "result-meta";
  meta.textContent = [
    `confidence ${(signal.confidence * 100).toFixed(0)}%`,
    `edge ${(signal.edge * 100).toFixed(1)}%`,
    signal.skip_reason ? `skip: ${signal.skip_reason}` : null,
  ].filter(Boolean).join(" · ");
  header.appendChild(meta);
  el.appendChild(header);

  const reasoning = document.createElement("p");
  reasoning.className = "result-reasoning";
  reasoning.textContent = signal.reasoning;
  el.appendChild(reasoning);

  if (signal.sources && signal.sources.length > 0) {
    const sourcesLabel = document.createElement("p");
    sourcesLabel.className = "result-sources-label";
    sourcesLabel.textContent = "Sources";
    el.appendChild(sourcesLabel);

    const list = document.createElement("ul");
    list.className = "result-sources";
    for (const url of signal.sources) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = url;
      li.appendChild(a);
      list.appendChild(li);
    }
    el.appendChild(list);
  }
}

// ---------------------------------------------------------------------------
// Bet
// ---------------------------------------------------------------------------

document.getElementById("bet-btn").addEventListener("click", async () => {
  const ticker = document.getElementById("bet-ticker").value.trim().toUpperCase();
  const size_usd = parseFloat(document.getElementById("bet-size").value);
  if (!ticker || !size_usd || size_usd <= 0) return;

  const btn = document.getElementById("bet-btn");
  const statusEl = document.getElementById("bet-status");

  btn.disabled = true;
  statusEl.textContent = `Placing ${selectedSide.toUpperCase()} bet on ${ticker}…`;
  statusEl.className = "action-status";

  try {
    const res = await fetch("/api/bet", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker, side: selectedSide, size_usd }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }

    const data = await res.json();
    const order = data.order || {};
    const dryRun = data.dry_run;
    const parts = [
      dryRun ? "DRY RUN" : "PLACED",
      `${order.side?.toUpperCase()} ${order.ticker}`,
      `${order.count} contract(s)`,
      order.yes_price_dollars ? `@ $${order.yes_price_dollars}` : order.no_price_dollars ? `@ $${order.no_price_dollars}` : "",
    ].filter(Boolean);
    statusEl.textContent = parts.join(" · ");
    statusEl.className = `action-status ${dryRun ? "dry-run" : "success"}`;
  } catch (err) {
    statusEl.textContent = `Rejected: ${err.message}`;
    statusEl.className = "action-status error";
  } finally {
    btn.disabled = false;
  }
});
