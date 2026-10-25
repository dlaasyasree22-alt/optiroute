import { getRisk } from "../api.js";
import { ICONS, enter, errorState, inrShort, num, pct, skuName, storeName, term } from "../ui.js";
import { state, update } from "../state.js";

// Verdict = how far a bad week falls below a typical week. It measures how fragile the plan is,
// separately from stock shortages that no plan can fix.
function verdict(r) {
  const drop = r.median_fill_rate - r.cvar95_fill_rate;
  if (drop <= 5) return { cls: "safe", label: "Safe", icon: ICONS.shield, drop,
    text: `In the worst weeks, fill rate falls only ${drop.toFixed(1)} points below a typical week. The plan holds up well.` };
  if (drop <= 10) return { cls: "watch", label: "Watch", icon: ICONS.eye, drop,
    text: `In the worst weeks, fill rate falls ${drop.toFixed(1)} points below a typical week. Keep an eye on the stores listed below.` };
  return { cls: "risk", label: "At risk", icon: ICONS.alert, drop,
    text: `In the worst weeks, fill rate falls ${drop.toFixed(1)} points below a typical week. Consider extra stock for the stores below.` };
}

export async function renderRisk(view) {
  view.innerHTML = `
    <div class="page-head" data-enter>
      <h1>Stress-test the plan</h1>
      <p class="lede">Demand never matches the forecast exactly. Here the plan is replayed against 500 possible weeks,
      including region-wide surges, to show how it holds up when things go wrong. ${term("monte_carlo", "How does this work?")}</p>
    </div>
    <div id="riskBody"><div class="panel panel-pad"><div class="skeleton" style="width:40%"></div><div class="skeleton" style="margin-top:12px"></div><div class="skeleton" style="margin-top:12px;width:70%"></div></div></div>`;
  enter(view);

  try {
    const r = state.risk || (await getRisk(state.plan.plan_id));
    update({ risk: r });
    paint(view.querySelector("#riskBody"), r);
  } catch (err) {
    view.querySelector("#riskBody").innerHTML = errorState({
      title: "Stress-test results are not available",
      message: `${err.message} Generate the plan again to rerun the stress-test.`,
    });
  }
}

function paint(body, r) {
  const v = verdict(r);
  body.innerHTML = `
    <div class="verdict ${v.cls}" data-enter>
      ${v.icon}
      <div><h2>${v.label}</h2><p>${v.text}</p></div>
    </div>

    <div class="split" style="margin-top:18px">
      <div class="panel panel-pad chart" data-enter>
        <h2>Fill rate across 500 simulated weeks</h2>
        <p class="muted small" style="margin:0 0 12px">Each bar counts how many simulated weeks ended with that fill rate. Weeks further left are worse.</p>
        ${histogram(r)}
        <div class="legend">
          <span><i style="background:var(--chart-d)"></i>Simulated weeks</span>
          <span><i style="background:var(--risk)"></i>Worst 5% of weeks</span>
          <span><i style="background:none;border-top:2px dashed var(--ink)"></i>Typical week (median)</span>
        </div>
      </div>

      <div class="panel panel-pad" data-enter>
        <h2>What the numbers say</h2>
        <div class="stat-list">
          <div class="stat"><div class="k"><b>Typical week</b><span>Median fill rate</span><br>${term("median")}</div><div class="v">${pct(r.median_fill_rate)}</div></div>
          <div class="stat"><div class="k"><b>Bad week</b><span>Average of the worst 5% of weeks (CVaR95)</span><br>${term("cvar")}</div><div class="v">${pct(r.cvar95_fill_rate)}</div></div>
          <div class="stat"><div class="k"><b>Store products running short</b><span>In a typical simulated week</span><br>${term("p_stockout")}</div><div class="v">${pct(r.p_stockout)}</div></div>
          <div class="stat"><div class="k"><b>Cost of lost sales in a bad week</b><span>Lost margin plus goodwill</span><br>${term("stockout_cost")}</div><div class="v">${inrShort(r.cvar_95_unmet_cost)}</div></div>
        </div>
      </div>
    </div>

    <div class="panel" style="margin-top:18px" data-enter>
      <div class="panel-head"><div><h2>Most likely to run short</h2>
      <p class="muted small" style="margin:0">Mostly products where the warehouses do not hold enough stock for every store. Ask "Why not?" to test sending more.</p></div></div>
      <div class="table-wrap"><table class="data">
        <thead><tr><th>Store</th><th>Product</th><th class="r">Chance of running short</th><th class="r">Stock after delivery</th><th class="r">Typical demand</th><th></th></tr></thead>
        <tbody>${r.top_at_risk
          .map((x) => `<tr>
            <td>${storeName(x.store_id)}</td><td>${skuName(x.sku_id, state.meta)}</td>
            <td class="r"><b>${pct(x.stockout_probability * 100, 0)}</b></td>
            <td class="r">${num(x.available_units)} units</td><td class="r">${num(x.median_demand_units)} units</td>
            <td><button class="link-btn small" data-ask='${JSON.stringify({ sku: x.sku_id, store: x.store_id })}'>Why not send more?</button></td>
          </tr>`)
          .join("")}</tbody>
      </table></div>
    </div>
    <div style="margin-top:18px"><button class="btn" data-go="whynot">Next: ask "Why not?" ${ICONS.arrow}</button></div>`;

  enter(body);
  anime({
    targets: body.querySelectorAll(".hist-bar"),
    scaleY: [0, 1],
    delay: anime.stagger(12),
    duration: 500,
    easing: "easeOutCubic",
  });
  body.addEventListener("click", (e) => {
    const b = e.target.closest("[data-ask]");
    if (!b) return;
    sessionStorage.setItem("optiroute.whyNotPrefill", b.dataset.ask);
    location.hash = "#/whynot";
  });
}

function histogram(r) {
  const xs = r.fill_rate_samples;
  const lo = Math.floor(Math.min(...xs)), hi = Math.ceil(Math.max(...xs));
  const bins = 24, w = (hi - lo) / bins || 1;
  const counts = Array(bins).fill(0);
  xs.forEach((x) => counts[Math.min(bins - 1, Math.floor((x - lo) / w))]++);
  const cutoff = [...xs].sort((a, b) => a - b)[Math.floor(xs.length * 0.05)];
  const W = 560, H = 256, P = { l: 36, r: 10, t: 30, b: 34 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b, max = Math.max(...counts);
  const bw = iw / bins;
  const xpos = (v) => P.l + ((v - lo) / (hi - lo)) * iw;
  const ticks = Array.from({ length: 5 }, (_, i) => lo + ((hi - lo) * i) / 4);
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Histogram of simulated fill rates, median ${pct(r.median_fill_rate)}, worst 5% average ${pct(r.cvar95_fill_rate)}">
    <line x1="${P.l}" x2="${W - P.r}" y1="${H - P.b}" y2="${H - P.b}" stroke="var(--line)"/>
    ${counts
      .map((c, i) => {
        const h = (c / max) * ih, x = P.l + i * bw;
        const worst = lo + (i + 1) * w <= cutoff + w * 0.5;
        return `<rect class="hist-bar" style="transform-origin: 0 ${H - P.b}px" x="${x + 1}" y="${H - P.b - h}" width="${bw - 2}" height="${h}" rx="2" fill="${worst ? "var(--risk)" : "var(--chart-d)"}"><title>${c} weeks between ${(lo + i * w).toFixed(1)}% and ${(lo + (i + 1) * w).toFixed(1)}%</title></rect>`;
      })
      .join("")}
    <line x1="${xpos(r.median_fill_rate)}" x2="${xpos(r.median_fill_rate)}" y1="${P.t}" y2="${H - P.b}" stroke="var(--ink)" stroke-width="2" stroke-dasharray="4 4"/>
    <text x="${xpos(r.median_fill_rate)}" y="${P.t - 10}" text-anchor="middle" style="fill:var(--ink);font-weight:600">Typical week ${pct(r.median_fill_rate)}</text>
    ${ticks.map((t) => `<text x="${xpos(t)}" y="${H - P.b + 18}" text-anchor="middle">${t.toFixed(0)}%</text>`).join("")}
    <text x="${P.l + iw / 2}" y="${H - 2}" text-anchor="middle">Fill rate in the simulated week</text>
  </svg>`;
}
