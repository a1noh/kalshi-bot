const REFRESH_MS = 5000;
let maxBetUsd = 10;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatUsd(v) {
  return v == null ? "-" : `$${Number(v).toFixed(2)}`;
}

function pct(v) {
  if (v == null) return "-";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
}

function calcBetSizes(recommendedUsd) {
  const conservative = Math.max(1, Math.floor(maxBetUsd * 0.25));
  const recommended  = Math.min(Math.max(1, Math.round(recommendedUsd * 100) / 100), maxBetUsd);
  const full         = maxBetUsd;
  const unique = [...new Set([conservative, recommended, full])].sort((a, b) => a - b);
  return unique.map(amount => ({
    amount,
    recommended: amount === recommended,
    label: amount === recommended ? "recommended ★"
         : amount === full        ? "max"
         :                         "conservative",
  }));
}

async function apiPost(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Summary + trade history polling
// ---------------------------------------------------------------------------

async function refreshSummary() {
  const data = await fetch("/api/summary").then(r => r.json());
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
  const trades = await fetch("/api/trades?limit=50").then(r => r.json());
  const body = document.getElementById("trades-body");
  body.replaceChildren();

  if (!trades.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 8;
    cell.textContent = "No trades yet.";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }

  for (const t of trades) {
    const row = document.createElement("tr");

    // outcome badge
    const outcomeEl = document.createElement("td");
    if (t.outcome) {
      const b = document.createElement("span");
      b.className = `badge ${t.outcome === "win" ? "yes" : t.outcome === "loss" ? "no" : "skip"}`;
      b.textContent = t.outcome.toUpperCase();
      outcomeEl.appendChild(b);
    } else {
      outcomeEl.textContent = "-";
    }

    // sources tooltip on reasoning cell
    const sources = Array.isArray(t.sources) ? t.sources : [];
    const fullReason = t.full_reasoning || t.reasoning || "";
    const reasonEl = document.createElement("td");
    reasonEl.className = "reasoning";
    reasonEl.textContent = fullReason;
    reasonEl.title = sources.length
      ? `${fullReason}\n\nSources:\n${sources.join("\n")}`
      : fullReason;

    [
      t.created_at?.slice(0, 16), t.market_ticker, t.side, formatUsd(t.size_usd),
      t.confidence?.toFixed(2) ?? "-", t.edge?.toFixed(3) ?? "-", t.status,
    ].forEach(val => {
      const td = document.createElement("td");
      td.textContent = val ?? "-";
      row.appendChild(td);
    });

    row.appendChild(outcomeEl);
    row.appendChild(reasonEl);
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
// Wizard helpers
// ---------------------------------------------------------------------------

function createStep(wizard) {
  const div = document.createElement("div");
  div.className = "step";
  wizard.appendChild(div);
  setTimeout(() => div.scrollIntoView({ behavior: "smooth", block: "nearest" }), 50);
  return div;
}

function createLoadingStep(wizard, msg) {
  const step = createStep(wizard);
  const p = document.createElement("p");
  p.className = "step-loading";
  p.textContent = msg;
  step.appendChild(p);
  return step;
}

function setStepError(step, msg) {
  step.replaceChildren();
  const p = document.createElement("p");
  p.className = "step-error";
  p.textContent = `Error: ${msg}`;
  step.appendChild(p);
}

function makeRestartBtn(wizard) {
  const btn = document.createElement("button");
  btn.className = "step-btn";
  btn.textContent = "Find More Markets";
  btn.addEventListener("click", () => initWizard());
  return btn;
}

// ---------------------------------------------------------------------------
// Step 0 — initWizard
// ---------------------------------------------------------------------------

function initWizard() {
  const wizard = document.getElementById("wizard");
  wizard.replaceChildren();

  const step = createStep(wizard);
  const btn = document.createElement("button");
  btn.className = "big-btn";
  btn.textContent = "Find Hot Markets";
  btn.addEventListener("click", () => handleDiscover(wizard, btn));
  step.appendChild(btn);
}

// ---------------------------------------------------------------------------
// Step 1 — discover
// ---------------------------------------------------------------------------

async function handleDiscover(wizard, findBtn) {
  findBtn.disabled = true;

  const step = createLoadingStep(wizard, "Loading top Kalshi markets…");

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 600000);

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
    step.replaceChildren(buildOppsContent(wizard, opps));
  } catch (err) {
    clearTimeout(timeout);
    setStepError(step, err.message);
    findBtn.disabled = false;
  }
}

function buildOppsContent(wizard, opps) {
  const div = document.createElement("div");

  if (!opps.length) {
    const note = document.createElement("p");
    note.className = "step-note";
    note.textContent = "No clear opportunities found right now — try again in a bit.";
    div.appendChild(note);
    div.appendChild(makeRestartBtn(wizard));
    return div;
  }

  const label = document.createElement("p");
  label.className = "step-label";
  label.textContent = `${opps.length} active market${opps.length !== 1 ? "s" : ""} — pick one to research:`;
  div.appendChild(label);

  const list = document.createElement("div");
  list.className = "opp-list";

  opps.forEach((opp, i) => {
    const btn = document.createElement("button");
    btn.className = "opp-row-btn";

    const lbl = document.createElement("span");
    lbl.className = "opp-row-lbl";
    lbl.textContent = String.fromCharCode(65 + i); // A, B, C…

    const title = document.createElement("span");
    title.className = "opp-row-title";
    title.textContent = opp.title;

    const midCents = opp.mid_price != null ? `${(opp.mid_price * 100).toFixed(0)}¢` : "?¢";
    const volK = opp.volume != null ? `$${(opp.volume / 1000).toFixed(0)}k vol` : "";

    const meta = document.createElement("span");
    meta.className = "opp-row-meta";
    meta.textContent = [midCents, volK].filter(Boolean).join(" · ");

    btn.append(lbl, title, meta);

    btn.addEventListener("click", () => {
      list.querySelectorAll(".opp-row-btn").forEach(b => { b.disabled = true; });
      btn.classList.add("selected");
      handleResearch(wizard, opp);
    });

    list.appendChild(btn);
  });

  div.appendChild(list);
  return div;
}

// ---------------------------------------------------------------------------
// Step 2 — research
// ---------------------------------------------------------------------------

async function handleResearch(wizard, opp) {
  const step = createStep(wizard);

  // Live thinking area
  const thinkingEl = document.createElement("div");
  thinkingEl.className = "stream-log";
  step.appendChild(thinkingEl);

  function addLine(text, cls) {
    const p = document.createElement("p");
    p.className = cls || "step-loading";
    p.textContent = text;
    thinkingEl.appendChild(p);
    step.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  addLine(`Researching ${opp.ticker}…`);

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 300000);

  try {
    const res = await fetch("/api/research/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker: opp.ticker }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let signal = null;
    let thinkingText = "";
    let thinkingP = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      const chunks = buf.split("\n\n");
      buf = chunks.pop();

      for (const chunk of chunks) {
        if (!chunk.startsWith("data: ")) continue;
        let evt;
        try { evt = JSON.parse(chunk.slice(6)); } catch { continue; }

        if (evt.type === "progress") {
          addLine(evt.text, "step-loading");
          thinkingP = null; thinkingText = "";
        } else if (evt.type === "thinking") {
          thinkingText += evt.text;
          if (!thinkingP) {
            thinkingP = document.createElement("p");
            thinkingP.className = "stream-thinking";
            thinkingEl.appendChild(thinkingP);
          }
          thinkingP.textContent = thinkingText;
          step.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } else if (evt.type === "signal") {
          signal = evt.signal;
        } else if (evt.type === "error") {
          throw new Error(evt.text);
        }
      }
    }

    clearTimeout(timeout);
    if (signal) {
      step.replaceChildren(buildSignalContent(wizard, opp, signal));
    } else {
      setStepError(step, "No signal returned — try again");
    }
  } catch (err) {
    clearTimeout(timeout);
    setStepError(step, err.message);
  }
}

function buildSignalContent(wizard, opp, signal) {
  const div = document.createElement("div");

  // Header: badge + ticker + meta
  const header = document.createElement("div");
  header.className = "signal-header";

  const badge = document.createElement("span");
  badge.className = `badge ${signal.skip ? "skip" : signal.side}`;
  badge.textContent = signal.skip ? "SKIP" : `BUY ${signal.side.toUpperCase()}`;

  const ticker = document.createElement("span");
  ticker.className = "signal-ticker";
  ticker.textContent = opp.ticker;

  const meta = document.createElement("span");
  meta.className = "signal-meta";
  const midStr = opp.mid_price != null ? ` · mid ${(opp.mid_price * 100).toFixed(0)}¢` : "";
  meta.textContent = `${(signal.confidence * 100).toFixed(0)}% conf · edge ${pct(signal.edge)}${midStr}`;

  header.append(badge, ticker, meta);
  div.appendChild(header);

  // Reasoning
  const reasoning = document.createElement("p");
  reasoning.className = "signal-reasoning";
  reasoning.textContent = signal.reasoning;
  div.appendChild(reasoning);

  if (signal.skip) {
    const note = document.createElement("p");
    note.className = "step-note";
    note.textContent = signal.skip_reason || "No trade recommended on this market.";
    div.appendChild(note);
    div.appendChild(makeRestartBtn(wizard));
    return div;
  }

  // Bet size tiles
  const betLabel = document.createElement("p");
  betLabel.className = "step-label";
  betLabel.textContent = "How much to bet?";
  div.appendChild(betLabel);

  const betOptions = document.createElement("div");
  betOptions.className = "bet-options";

  calcBetSizes(signal.size_usd).forEach(({ amount, label, recommended }) => {
    const btn = document.createElement("button");
    btn.className = recommended ? "bet-opt-btn recommended" : "bet-opt-btn";

    const amt = document.createElement("span");
    amt.className = "bet-opt-amount";
    amt.textContent = `$${amount % 1 === 0 ? amount : amount.toFixed(2)}`;

    const lbl = document.createElement("span");
    lbl.className = "bet-opt-label";
    lbl.textContent = label;

    btn.append(amt, lbl);

    btn.addEventListener("click", () => {
      betOptions.querySelectorAll(".bet-opt-btn").forEach(b => { b.disabled = true; });
      handleBet(wizard, opp.ticker, signal.side, amount);
    });

    betOptions.appendChild(btn);
  });

  div.appendChild(betOptions);
  return div;
}

// ---------------------------------------------------------------------------
// Step 3 — place bet
// ---------------------------------------------------------------------------

async function handleBet(wizard, ticker, side, size_usd) {
  const step = createLoadingStep(wizard, `Placing ${side.toUpperCase()} bet on ${ticker}…`);

  try {
    const result = await apiPost("/api/bet", { ticker, side, size_usd });
    const order = result.order || {};
    const dryRun = result.dry_run;

    step.replaceChildren();

    const resultLine = document.createElement("p");
    resultLine.className = `bet-result ${dryRun ? "dry-run" : "success"}`;
    const price = order.yes_price_dollars ?? order.no_price_dollars;
    resultLine.textContent = [
      dryRun ? "DRY RUN" : "✓ PLACED",
      `${order.count ?? "?"} contract(s)`,
      price ? `@ $${price}` : null,
    ].filter(Boolean).join(" · ");
    step.appendChild(resultLine);
    step.appendChild(makeRestartBtn(wizard));

    refreshTrades().catch(console.error);
  } catch (err) {
    step.replaceChildren();
    const errLine = document.createElement("p");
    errLine.className = "bet-result error";
    errLine.textContent = `Rejected: ${err.message}`;
    step.appendChild(errLine);
    step.appendChild(makeRestartBtn(wizard));
  }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

initWizard();
