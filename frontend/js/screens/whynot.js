import { askWhyNot } from "../api.js";
import { ICONS, enter, errorState, inr, num, skuName, storeName, term, whName } from "../ui.js";
import { state, update } from "../state.js";

export function renderWhyNot(view) {
  const meta = state.meta || { skus: [], stores: [], warehouses: [] };
  let pre = {};
  try {
    pre = JSON.parse(sessionStorage.getItem("optiroute.whyNotPrefill") || "{}");
    sessionStorage.removeItem("optiroute.whyNotPrefill");
  } catch {}
  const last = state.whyNot.at(-1)?.request;
  const sku = pre.sku || last?.sku_id || meta.skus[0]?.sku_id;
  const store = pre.store || last?.destination_store || meta.stores[0];
  // When coming from a manifest line, suggest a different warehouse than the one the plan chose
  const wh = pre.wh ? meta.warehouses.find((w) => w !== pre.wh) : last?.source_warehouse || meta.warehouses[0];

  view.innerHTML = `
    <div class="page-head" data-enter>
      <h1>Ask "Why not?"</h1>
      <p class="lede">Think a store should get a shipment the plan did not give it? Describe it below. The engine re-plans the whole
      network with your shipment locked in, then shows exactly what changes and what it costs.</p>
    </div>
    <div class="split">
      <form class="panel panel-pad" id="wnForm" data-enter>
        <h2>Your shipment</h2>
        <p class="muted small" style="margin:0 0 16px">Read it as a sentence: send at least this many units of this product, from this warehouse, to this store.</p>
        <div class="form-grid">
          <div class="field"><label for="wsku">Product</label>
            <select class="input" id="wsku">${meta.skus.map((s) => `<option value="${s.sku_id}" ${s.sku_id === sku ? "selected" : ""}>${skuName(s.sku_id, meta)}</option>`).join("")}</select></div>
          <div class="field"><label for="wunits">At least (units)</label>
            <input class="input" id="wunits" type="number" min="1" step="1" value="${last?.min_units || 200}" required>
            <div class="hint" id="wpack"></div></div>
          <div class="field"><label for="wwh">From warehouse</label>
            <select class="input" id="wwh">${meta.warehouses.map((w) => `<option value="${w}" ${w === wh ? "selected" : ""}>${whName(w)}</option>`).join("")}</select></div>
          <div class="field"><label for="wstore">To store</label>
            <select class="input" id="wstore">${meta.stores.map((s) => `<option value="${s}" ${s === store ? "selected" : ""}>${storeName(s)}</option>`).join("")}</select></div>
        </div>
        <div class="callout" id="today" style="margin-top:16px"></div>
        <button class="btn btn-lg" style="margin-top:18px" type="submit" id="wnBtn">Re-plan with my shipment</button>
      </form>
      <section class="panel panel-pad" id="wnResult" aria-live="polite" data-enter>
        <h2>The answer</h2>
        <p class="muted">Your result appears here as a short story with the cost broken down: freight, expired stock and lost sales elsewhere.</p>
      </section>
    </div>
    <div id="history"></div>`;
  enter(view);

  const form = view.querySelector("#wnForm");
  const today = () => {
    const s = form.wsku.value, st = form.wstore.value, w = form.wwh.value;
    const lines = state.plan.shipments.filter((x) => x.sku_id === s && x.destination_store === st);
    const pack = state.meta?.skus?.find((x) => x.sku_id === s)?.case_pack_size;
    view.querySelector("#wpack").textContent = pack ? `Ships in cases of ${pack}, so this is rounded up to whole cases.` : "";
    const fromWh = lines.filter((x) => x.source_warehouse === w).reduce((a, x) => a + x.units_shipped, 0);
    const total = lines.reduce((a, x) => a + x.units_shipped, 0);
    const sources = [...new Set(lines.map((x) => x.source_warehouse))];
    view.querySelector("#today").innerHTML = total
      ? `<b>Today's plan:</b> ${storeName(st)} gets ${num(total)} units of this product from ${sources.map(whName).join(" and ")}${
          fromWh && sources.length > 1 ? `, including ${num(fromWh)} from ${whName(w)}` : ""}.`
      : `<b>Today's plan:</b> ${storeName(st)} gets none of this product this week.`;
  };
  ["wsku", "wstore", "wwh"].forEach((id) => form[id].addEventListener("change", today));
  today();
  paintHistory(view);
  const prev = state.whyNot.at(-1);
  if (prev && !pre.sku) paintAnswer(view.querySelector("#wnResult"), prev.request, prev.response);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const request = {
      sku_id: form.wsku.value,
      source_warehouse: form.wwh.value,
      destination_store: form.wstore.value,
      min_units: Number(form.wunits.value),
    };
    const btn = view.querySelector("#wnBtn");
    btn.disabled = true;
    btn.textContent = "Re-planning the network...";
    const out = view.querySelector("#wnResult");
    out.innerHTML = `<h2>Re-planning</h2><p class="muted">Locking in your shipment and re-solving every store, warehouse and truck. This takes a few seconds.</p>
      <div class="progress-track"><div class="progress-fill" id="wnProg"></div></div>`;
    anime({ targets: "#wnProg", width: ["0%", "90%"], duration: 4000, easing: "easeOutQuad" });
    try {
      const response = await askWhyNot(state.plan.plan_id, request);
      update({ whyNot: [...state.whyNot, { request, response }] });
      paintAnswer(out, request, response);
      paintHistory(view);
    } catch (err) {
      out.innerHTML = errorState({
        title: err.kind === "recorded" ? "Not available offline" : "Could not answer this question",
        message: err.message,
      });
    } finally {
      btn.disabled = false;
      btn.textContent = "Re-plan with my shipment";
    }
  });
}

function story(req, res) {
  const d = res.delta_kpis;
  const what = `Sending ${num(req.min_units)} units of ${skuName(req.sku_id, state.meta)} from ${whName(req.source_warehouse)} to ${storeName(req.destination_store)}`;
  const parts = [];
  if (Math.abs(d.delta_freight_cost) >= 1)
    parts.push(`${d.delta_freight_cost > 0 ? "adds" : "saves"} <strong>${inr(Math.abs(d.delta_freight_cost))}</strong> in freight${d.delta_trucks ? ` (${d.delta_trucks > 0 ? "+" : ""}${d.delta_trucks} truck${Math.abs(d.delta_trucks) > 1 ? "s" : ""})` : ""}`);
  if (Math.abs(d.delta_stockout_units) >= 1)
    parts.push(d.delta_stockout_units > 0
      ? `takes stock other stores needed, leaving <strong>${num(d.delta_stockout_units)} more units</strong> of demand unmet elsewhere`
      : `fills <strong>${num(-d.delta_stockout_units)} more units</strong> of demand overall`);
  if (Math.abs(d.delta_waste_units) >= 1)
    parts.push(`${d.delta_waste_units > 0 ? "lets" : "saves"} <strong>${num(Math.abs(d.delta_waste_units))} units</strong> ${d.delta_waste_units > 0 ? "expire" : "from expiring"}`);
  const verdict = d.net_financial_delta_inr > 0
    ? `Overall your version costs <strong>${inr(d.net_financial_delta_inr)} more</strong> this week, which is why the plan did not do it.`
    : `Overall your version is <strong>${inr(-d.net_financial_delta_inr)} cheaper</strong>. The difference is within the optimiser's accuracy target, so both plans are reasonable.`;
  return `${what} ${parts.join(", ")}. ${verdict}`;
}

function paintAnswer(out, req, res) {
  if (res.counterfactual_status !== "FEASIBLE") {
    out.innerHTML = `<div class="verdict risk">${ICONS.alert}<div><h2>Not possible</h2><p>${res.root_cause_explanation}</p></div></div>
      <p class="muted" style="margin-top:14px">Try fewer units, or a different warehouse. ${term("min_life", "Why can stock be ineligible?")}</p>`;
    return;
  }
  const d = res.delta_kpis;
  const items = [
    ["Freight", d.delta_freight_cost, "freight"],
    ["Expired stock (write-off)", d.delta_writeoff_inr, "writeoff"],
    ["Lost sales elsewhere", d.delta_stockout_inr, "stockout_cost"],
  ];
  const scale = Math.max(...items.map((i) => Math.abs(i[1])), Math.abs(d.net_financial_delta_inr), 1);
  const up = d.net_financial_delta_inr > 0;
  out.innerHTML = `
    <div class="verdict ${up ? "watch" : "safe"}">${up ? ICONS.eye : ICONS.check}
      <div><h2>${up ? `Costs ${inr(d.net_financial_delta_inr)} more` : `Saves ${inr(-d.net_financial_delta_inr)}`}</h2>
      <p>Answered in ${res.solve_time_seconds.toFixed(1)} s by re-solving the full plan.</p></div></div>
    <p class="story" style="margin:18px 0">${story(req, res)}</p>
    <h3>Where the money goes</h3>
    <p class="muted small" style="margin:0 0 8px">Red adds cost, green saves cost. The line marks zero.</p>
    ${items
      .map(([label, v, key]) => `<div class="waterfall-row"><span>${label}<br>${term(key)}</span>
        <div class="wbar"><span class="axis" style="left:50%"></span><i class="${v > 0 ? "cost-up" : "cost-down"}" data-v="${v}" style="${v > 0 ? "left:50%" : "right:50%"}"></i></div>
        <span class="amt">${v > 0 ? "+" : ""}${inr(v)}</span></div>`)
      .join("")}
    <div class="waterfall-row total"><span>Net change</span>
      <div class="wbar"><span class="axis" style="left:50%"></span><i class="${up ? "cost-up" : "cost-down"}" data-v="${d.net_financial_delta_inr}" style="${up ? "left:50%" : "right:50%"}"></i></div>
      <span class="amt">${up ? "+" : ""}${inr(d.net_financial_delta_inr)}</span></div>
    <div style="margin-top:18px; display:flex; gap:12px; flex-wrap:wrap">
      <button class="btn" data-go="approve">Keep the recommended plan ${ICONS.arrow}</button>
    </div>`;
  anime({
    targets: out.querySelectorAll(".wbar i"),
    width: (el) => `${(Math.abs(Number(el.dataset.v)) / scale) * 50}%`,
    delay: anime.stagger(150),
    duration: 700,
    easing: "easeOutCubic",
  });
  anime({ targets: out.querySelector(".story"), opacity: [0, 1], duration: 500, easing: "linear" });
}

function paintHistory(view) {
  const h = view.querySelector("#history");
  if (state.whyNot.length < 2) return (h.innerHTML = "");
  h.innerHTML = `<div class="panel" style="margin-top:18px"><div class="panel-head"><h2>Questions asked this session</h2></div>
    <div class="table-wrap"><table class="data"><thead><tr><th>Shipment asked for</th><th>Answer</th><th class="r">Net change</th></tr></thead><tbody>
    ${state.whyNot
      .map(({ request: r, response: s }) => `<tr><td>${num(r.min_units)} × ${r.sku_id} from ${r.source_warehouse} to ${storeName(r.destination_store)}</td>
        <td>${s.counterfactual_status === "FEASIBLE" ? "Possible" : "Not possible"}</td>
        <td class="r">${s.delta_kpis ? inr(s.delta_kpis.net_financial_delta_inr) : "-"}</td></tr>`)
      .reverse()
      .join("")}</tbody></table></div></div>`;
}
