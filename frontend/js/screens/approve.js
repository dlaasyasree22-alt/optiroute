import { approvePlan } from "../api.js";
import { ICONS, REASONS, downloadCsv, enter, inrShort, num, pct, toast } from "../ui.js";
import { state, update } from "../state.js";

export function renderApprove(view) {
  const p = state.plan;
  const checks = [
    [true, "Plan generated", `${num(p.shipments.length)} lines on ${num(p.kpis.trucks_used)} trucks, ${pct(p.kpis.projected_fill_rate * 100)} of demand met.`],
    [state.visited.has("review"), "Results reviewed", "Headline results and shipments checked against the pro-rata baseline."],
    [!!state.risk, "Stress-tested", state.risk ? `Typical week ${pct(state.risk.median_fill_rate)}, bad week ${pct(state.risk.cvar95_fill_rate)}.` : "Optional, but recommended before approving."],
    [state.whyNot.length > 0, "Alternatives questioned", state.whyNot.length ? `${state.whyNot.length} "Why not?" question${state.whyNot.length > 1 ? "s" : ""} answered.` : "Optional. Useful when you expect to defend a decision."],
  ];

  view.innerHTML = `
    <div class="page-head" data-enter>
      <div class="eyebrow">Step 5</div>
      <h1>Approve and export</h1>
      <p class="lede">Approving locks this plan as the version sent to warehouses. Then download the manifest for the planning team
      and pick lists for each warehouse.</p>
    </div>
    <div class="split">
      <div class="panel panel-pad" data-enter>
        <h2>Before you approve</h2>
        <ul class="checklist">${checks
          .map(([ok, t, d]) => `<li><span class="check ${ok ? "ok" : ""}">${ok ? ICONS.check : ""}</span><div><b>${t}</b><div class="muted small">${d}</div></div></li>`)
          .join("")}</ul>
        <div id="approveArea" style="margin-top:20px"></div>
      </div>
      <div class="panel panel-pad" data-enter>
        <h2>Downloads</h2>
        <p class="muted small">CSV files open directly in Excel.</p>
        <div class="stack">
          <button class="btn btn-quiet" data-dl="manifest">${ICONS.download} Full dispatch manifest</button>
          ${[...new Set(p.shipments.map((s) => s.source_warehouse))].sort()
            .map((w) => `<button class="btn btn-quiet" data-dl="${w}">${ICONS.download} Pick list for ${w}</button>`).join("")}
          <button class="btn btn-quiet" data-dl="summary">${ICONS.download} One-page summary for review</button>
        </div>
      </div>
    </div>`;
  enter(view);
  paintApprove(view);

  view.querySelectorAll("[data-dl]").forEach((b) => b.addEventListener("click", () => download(b.dataset.dl)));
}

function paintApprove(view) {
  const area = view.querySelector("#approveArea");
  if (state.approved) {
    area.innerHTML = `<div class="stamp">${ICONS.check} Approved</div>
      <p class="muted small">Approved on ${state.approved.approved_at}. Plan reference ${state.plan.plan_id}.</p>`;
    anime({ targets: area.querySelector(".stamp"), scale: [1.6, 1], rotate: [-8, -2], opacity: [0, 1], duration: 500, easing: "easeOutBack" });
    return;
  }
  area.innerHTML = `<button class="btn btn-lg" id="approveBtn">${ICONS.check} Approve this plan</button>`;
  area.querySelector("#approveBtn").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      update({ approved: await approvePlan(state.plan.plan_id) });
      toast("Plan approved");
      paintApprove(view);
    } catch (err) {
      e.target.disabled = false;
      toast(`Could not approve: ${err.message}`);
    }
  });
}

function download(kind) {
  const p = state.plan;
  if (kind === "manifest") {
    return downloadCsv(`manifest_${p.plan_id}.csv`, [
      ["sku_id", "batch_id", "from_warehouse", "to_store", "cases", "units", "truck", "days_to_expiry_at_arrival", "reason"],
      ...p.shipments.map((s) => [s.sku_id, s.batch_id, s.source_warehouse, s.destination_store, s.cases_shipped, s.units_shipped, s.assigned_truck_id, s.days_to_expiry_at_arrival, REASONS[s.reason_code]?.long]),
    ]);
  }
  if (kind === "summary") {
    const k = p.kpis, b = p.baseline_kpis;
    return downloadCsv(`summary_${p.plan_id}.csv`, [
      ["measure", "this_plan", "pro_rata_baseline"],
      ["fill_rate_pct", (k.projected_fill_rate * 100).toFixed(2), (b.projected_fill_rate * 100).toFixed(2)],
      ["unmet_demand_pct", k.unmet_demand_pct, b.unmet_demand_pct],
      ["freight_cost_inr", k.total_freight_cost, b.total_freight_cost],
      ["freight_per_unit_inr", k.freight_per_unit_inr, b.freight_per_unit_inr],
      ["truck_utilization_pct", k.truck_utilization_pct, b.truck_utilization_pct],
      ["wastage_rate_pct", k.wastage_rate_pct, b.wastage_rate_pct],
      ["trucks_used", k.trucks_used, b.trucks_used],
      ["approved", state.approved ? state.approved.approved_at : "not yet", ""],
    ]);
  }
  const rows = p.shipments
    .filter((s) => s.source_warehouse === kind)
    .sort((a, b) => a.assigned_truck_id.localeCompare(b.assigned_truck_id) || a.days_to_expiry_at_arrival - b.days_to_expiry_at_arrival);
  downloadCsv(`pick_list_${kind}.csv`, [
    ["truck", "pick_order", "sku_id", "batch_id", "cases", "units", "days_to_expiry_at_arrival"],
    ...rows.map((s, i) => [s.assigned_truck_id, i + 1, s.sku_id, s.batch_id, s.cases_shipped, s.units_shipped, s.days_to_expiry_at_arrival]),
  ]);
}
