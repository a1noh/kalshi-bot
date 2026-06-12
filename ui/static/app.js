const REFRESH_MS = 5000;
let maxBetUsd = 10; // updated from /api/summary

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatUsd(value) {
  if (value === null || value === undefined) return "-";
  return `$${Number(value).toFixed(2)}`;
}

function pct(value) {
  if (value === null || value === undefined) return "-";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)}%`;
}

// Compute 3 bet-size options: conservative, recommended, full
function betSizes(recommendedUsd) {
  const conservative = Math.max(1, Math.round((maxBetUsd * 0.25) * 100) / 100);
  const recommended  = Math.min(Math.max(1, Math.round(recommendedUsd * 100) / 100), maxBetUsd);
  const full         = maxBetUsd;
  // deduplicate and sort
  return [...new Set([conservative, recommended, full])].sort((a, b) => a - b);
}

// ---------------------------------------------------------------------------
// Summary polling
// ---------------------------------------------------------------------------

async function refreshSummary() {
  const res = await fetch("/api/summary");
  const data = await res.json();

  maxBetUsd = data.max_bet_usd ?? 10;

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
  const res = await fetch("/api/trades?limit=50");
  const trades = await res.json();
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
// Discover
// ---------------------------------------------------------------------------

document.getElementById("discover-btn").addEventListener("click", async () => {
  const btn = document.getElementById("discover-btn");
  const statusEl = document.getElementById("discover-status");
  const listEl = document.getElementById("opportunities-list");

  btn.disabled = true;
  statusEl.textContent = "Searching today's news and matching Kalshi markets… (~45 s)";
  statusEl.className = "action-status";
  listEl.replaceChildren();

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180000);

  try {
    const res = await fetch("/api/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }

    const opps = await res.json();

    if (opps.length === 0) {
      statusEl.textContent = "No clear opportunities found right now — try again later.";
      statusEl.className = "action-status";
    } else {
      statusEl.className = "action-status hidden";
      opps.forEach(opp => listEl.appendChild(buildOpportunityCard(opp)));
    }
  } catch (err) {
    clearTimeout(timeout);
    statusEl.textContent = `Error: ${err.message}`;
    statusEl.className = "action-status error";
  } finally {
    btn.disabled = false;
  }
});

function buildOpportunityCard(opp) {
  const card = document.createElement("div");
  card.className = "opp-card";

  // Header row: badge + ticker + meta
  const header = document.createElement("div");
  header.className = "opp-header";

  const badge = document.createElement("span");
  badge.className = `badge ${opp.side}`;
  badge.textContent = `BUY ${opp.side.toUpperCase()}`;
  header.appendChild(badge);

  const ticker = document.createElement("span");
  ticker.className = "opp-ticker";
  ticker.textContent = opp.ticker;
  header.appendChild(ticker);

  const meta = document.createElement("span");
  meta.className = "opp-meta";
  const midStr = opp.mid_price != null ? ` · mid ${(opp.mid_price * 100).toFixed(0)}¢` : "";
  meta.textContent = `${(opp.confidence * 100).toFixed(0)}% conf · edge ${pct(opp.edge)}${midStr}`;
  header.appendChild(meta);

  card.appendChild(header);

  // Title
  const title = document.createElement("p");
  title.className = "opp-title";
  title.textContent = opp.title;
  card.appendChild(title);

  // Reasoning (truncatable)
  const reasoning = document.createElement("p");
  reasoning.className = "opp-reasoning";
  reasoning.textContent = opp.reasoning;
  card.appendChild(reasoning);

  // Bet row
  const betRow = document.createElement("div");
  betRow.className = "opp-bet-row";

  const betLabel = document.createElement("span");
  betLabel.className = "opp-bet-label";
  betLabel.textContent = "Bet:";
  betRow.appendChild(betLabel);

  const sizes = betSizes(opp.size_usd);
  const recommendedSize = Math.min(Math.max(1, Math.round(opp.size_usd * 100) / 100), maxBetUsd);

  sizes.forEach(size => {
    const btn = document.createElement("button");
    btn.className = "bet-size-btn";
    btn.textContent = `$${size % 1 === 0 ? size : size.toFixed(2)}`;
    if (size === recommendedSize) {
      btn.classList.add("recommended");
      btn.title = "Claude's recommended size";
    }

    btn.addEventListener("click", async () => {
      // disable all bet buttons on this card
      card.querySelectorAll(".bet-size-btn").forEach(b => { b.disabled = true; });

      const resultEl = card.querySelector(".opp-result");
      resultEl.textContent = `Placing ${opp.side.toUpperCase()} ${opp.ticker} $${size}…`;
      resultEl.className = "opp-result";

      try {
        const res = await fetch("/api/bet", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticker: opp.ticker, side: opp.side, size_usd: size }),
        });

        if (!res.ok) {
          const errData = await res.json().catch(() => ({ detail: res.statusText }));
          throw new Error(errData.detail || res.statusText);
        }

        const data = await res.json();
        const order = data.order || {};
        const dryRun = data.dry_run;
        const parts = [
          dryRun ? "DRY RUN" : "PLACED",
          `${order.count ?? "?"} contract(s) @ ${order.yes_price_dollars ?? order.no_price_dollars ?? "?"}`,
        ].filter(Boolean);
        resultEl.textContent = parts.join(" · ");
        resultEl.className = `opp-result ${dryRun ? "dry-run" : "success"}`;

        refreshTrades().catch(console.error);
      } catch (err) {
        resultEl.textContent = `Rejected: ${err.message}`;
        resultEl.className = "opp-result error";
        card.querySelectorAll(".bet-size-btn").forEach(b => { b.disabled = false; });
      }
    });

    betRow.appendChild(btn);
  });

  card.appendChild(betRow);

  // Result line (hidden until bet placed)
  const resultEl = document.createElement("div");
  resultEl.className = "opp-result hidden";
  card.appendChild(resultEl);

  return card;
}

// ---------------------------------------------------------------------------
// Manual research
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

    // pre-fill manual bet form
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

  const badge = document.createElement("span");
  if (signal.skip) {
    badge.className = "badge skip";
    badge.textContent = "SKIP";
  } else {
    badge.className = `badge ${signal.side}`;
    badge.textContent = `BUY ${signal.side.toUpperCase()}`;
  }
  header.appendChild(badge);

  const meta = document.createElement("span");
  meta.className = "result-meta";
  meta.textContent = [
    `confidence ${(signal.confidence * 100).toFixed(0)}%`,
    `edge ${pct(signal.edge)}`,
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
// Manual bet
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

    refreshTrades().catch(console.error);
  } catch (err) {
    statusEl.textContent = `Rejected: ${err.message}`;
    statusEl.className = "action-status error";
  } finally {
    btn.disabled = false;
  }
});
