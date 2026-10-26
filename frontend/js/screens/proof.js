import { getBacktest } from "../api.js";
import { enter, errorState, inr, num, pct, term } from "../ui.js";

const METRICS = [
  { key: "fill_rate_pct", label: "Fill rate", fmt: (v) => pct(v), better: "higher" },
  { key: "wastage_rate_pct", label: "Wastage", fmt: (v) => pct(v, 2), better: "lower" },
  { key: "freight_per_delivered_unit_inr", label: "Freight per unit delivered", fmt: (v) => `₹${v.toFixed(2)}`, better: "lower" },
  { key: "truck_utilization_pct", label: "Truck space used", fmt: (v) => pct(v), better: "higher" },
];

export async function renderProof(view) {
  view.innerHTML = `
    <div class="page-head" data-enter>
      <div class="eyebrow">For leadership review</div>
      <h1>Proof of results</h1>
      <p class="lede">Does OptiRoute actually beat today's method, and which part of it makes the difference? Four versions of the planner,
      from the spreadsheet method (A) to full OptiRoute (D), are tested on identical stock and the same simulated weeks of demand.</p>
    </div>
    <div id="pBody"><div class="panel panel-pad"><div class="skeleton"></div><div class="skeleton" style="margin-top:12px;width:60%"></div></div></div>`;
  enter(view);
  try {
    paint(view.querySelector("#pBody"), await getBacktest());
  } catch (err) {
    view.querySelector("#pBody").innerHTML = errorState({ title: "Backtest results are not available", message: err.message });
  }
}

function paint(body, bt) {
  const V = Object.fromEntries(bt.variants.map((v) => [v.id, v]));
  const m = (id, k) => V[id].metrics[k].mean;
  const regimeLoss = bt.regimes.find((r) => r.seeds_optiroute_better < r.seeds / 2);

  body.innerHTML = `
    <div class="panel panel-pad" data-enter>
      <h2>The short answer</h2>
      <p class="story" style="margin:6px 0 0">Full OptiRoute (D) serves <strong>${pct(m("D", "fill_rate_pct"))}</strong> of demand against
      <strong>${pct(m("A", "fill_rate_pct"))}</strong> for the pro-rata method (A), with
      <strong>${num(m("A", "fefo_violations"))} fewer</strong> batches shipped out of expiry order and
      ${m("D", "wastage_rate_pct") < m("A", "wastage_rate_pct") ? `<strong>less expired stock</strong> (${pct(m("D", "wastage_rate_pct"), 2)} against ${pct(m("A", "wastage_rate_pct"), 2)})` : "similar wastage"}.
      ${m("D", "freight_per_delivered_unit_inr") > m("A", "freight_per_delivered_unit_inr")
        ? `Serving that extra demand costs ₹${(m("D", "freight_per_delivered_unit_inr") - m("A", "freight_per_delivered_unit_inr")).toFixed(2)} more freight per delivered unit, because pro-rata only uses each store's cheapest lane.`
        : `Freight per delivered unit is also ₹${(m("A", "freight_per_delivered_unit_inr") - m("D", "freight_per_delivered_unit_inr")).toFixed(2)} lower.`}</p>
      <p class="muted small" style="margin-top:10px">${bt.method} ${term("ci", "What is a 95% interval?")}</p>
    </div>

    <div class="panel" style="margin-top:18px" data-enter>
      <div class="panel-head"><div><h2>Four planners compared</h2>
      <p class="muted small" style="margin:0">Dot is the average across ${bt.seeds} runs; the line is the 95% interval. Teal is full OptiRoute.</p></div>
      <select class="input" id="metric" style="width:auto">${METRICS.map((x) => `<option value="${x.key}">${x.label} (${x.better} is better)</option>`).join("")}</select></div>
      <div class="panel-pad" id="ci"></div>
    </div>

    <div class="panel" style="margin-top:18px" data-enter>
      <div class="panel-head"><h2>Full results table</h2></div>
      <div class="table-wrap"><table class="data">
        <thead><tr><th>Planner</th>${METRICS.map((x) => `<th class="r">${x.label}</th>`).join("")}<th class="r">Weekly freight</th><th class="r">FEFO breaks</th></tr></thead>
        <tbody>${bt.variants
          .map((v) => `<tr><td><b>${v.id}</b> ${v.label}</td>${METRICS.map((x) => {
            const c = v.metrics[x.key];
            return `<td class="r">${x.fmt(c.mean)}<br><span class="muted small">${x.fmt(c.ci95[0])} to ${x.fmt(c.ci95[1])}</span></td>`;
          }).join("")}<td class="r">${inr(v.metrics.freight_cost_inr.mean)}</td><td class="r">${num(v.metrics.fefo_violations.mean)}</td></tr>`)
          .join("")}</tbody>
      </table></div>
    </div>

    <div class="split" style="margin-top:18px">
      <div class="panel panel-pad" data-enter>
        <h2>Where the gains come from</h2>
        <p class="muted small" style="margin:0 0 8px">Each step adds one idea to the planner before it.</p>
        <ol class="steps-chain">${bt.attribution
          .map((a) => `<li><div><b>${a.step}</b> (${a.from} to ${a.to})<div class="small">
            Fill rate ${a.fill_rate_pp >= 0 ? "+" : ""}${a.fill_rate_pp.toFixed(1)} points · wastage ${a.wastage_pp >= 0 ? "+" : ""}${a.wastage_pp.toFixed(2)} points ·
            freight ${a.freight_inr >= 0 ? "+" : ""}${inr(a.freight_inr)}</div></div></li>`)
          .join("")}</ol>
        <p class="small muted">Step 3 trades a little fill rate for much less expired stock: the shelf-life rules refuse batches that would arrive too close to expiry. ${term("min_life", "Why?")}</p>
      </div>
      <div class="panel panel-pad" data-enter>
        <h2>Under pressure</h2>
        <p class="muted small" style="margin:0 0 8px">Full OptiRoute (D) against pro-rata (A) in harder weeks.</p>
        <div class="table-wrap"><table class="data"><thead><tr><th>Situation</th><th class="r">Fill rate A → D</th><th class="r">Wastage A → D</th><th class="r">D better in</th></tr></thead>
        <tbody>${bt.regimes
          .map((r) => `<tr><td>${r.regime}</td><td class="r">${pct(r.baseline_fill_rate_pct)} → <b>${pct(r.optiroute_fill_rate_pct)}</b></td>
            <td class="r">${pct(r.baseline_wastage_pct, 2)} → <b>${pct(r.optiroute_wastage_pct, 2)}</b></td><td class="r">${r.seeds_optiroute_better} of ${r.seeds} runs</td></tr>`)
          .join("")}</tbody></table></div>
        ${regimeLoss ? `<div class="callout warn" style="margin-top:12px"><b>Honest limit:</b> with ${regimeLoss.regime.replace("Warehouse ", "").replace(" offline", "")} offline,
          OptiRoute serves slightly less demand than pro-rata. The remaining lanes are long and expensive, and the optimiser judges some
          deliveries cost more in freight than the sales they save. It still wastes less stock. A planner can override this with "Why not?".</div>` : ""}
      </div>
    </div>`;

  enter(body);
  const draw = () => drawCi(body.querySelector("#ci"), bt, METRICS.find((x) => x.key === body.querySelector("#metric").value));
  body.querySelector("#metric").addEventListener("change", draw);
  draw();
}

function drawCi(el, bt, metric) {
  const vals = bt.variants.flatMap((v) => v.metrics[metric.key].ci95);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.15 || 1;
  lo -= pad; hi += pad;
  const x = (v) => ((v - lo) / (hi - lo)) * 100;
  el.innerHTML = bt.variants
    .map((v) => {
      const c = v.metrics[metric.key];
      return `<div class="ci-row"><span><b>${v.id}</b> ${v.label}</span>
        <div class="ci-track" aria-label="${metric.label} ${metric.fmt(c.mean)}">
          <span class="whisker" style="left:${x(c.ci95[0])}%;width:${Math.max(0.5, x(c.ci95[1]) - x(c.ci95[0]))}%"></span>
          <span class="dot ${v.id === "D" ? "d" : ""}" data-left="${x(c.mean)}" style="left:0"></span>
        </div>
        <span class="amt"><b>${metric.fmt(c.mean)}</b></span></div>`;
    })
    .join("");
  anime({ targets: el.querySelectorAll(".dot"), left: (d) => `${d.dataset.left}%`, delay: anime.stagger(80), duration: 700, easing: "easeOutCubic" });
}
