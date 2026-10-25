import {
  ICONS, REASONS, STATUS, countUp, deltaPill, downloadCsv, enter, inr, inrShort, num, pct, skuName, storeName,
  term, truckName, whName,
} from "../ui.js";
import { state } from "../state.js";

let tab = "summary";
const table = { q: "", wh: "", store: "", reason: "", sort: "units_shipped", dir: -1, page: 0 };
const PAGE = 40;

export function renderReview(view) {
  const p = state.plan;
  view.innerHTML = `
    <div class="page-head" data-enter>
      <h1>Review the plan</h1>
      <p class="lede">The headline results first, each next to today's pro-rata spreadsheet method on the same stock.
      Then every shipment with the reason it was sent.</p>
    </div>
    <div class="trust" data-enter>
      ${ICONS.shield}
      <span><b>${STATUS[p.solver_status] || p.solver_status}</b></span>
      <span>Within ${pct(p.mip_gap * 100, 2)} of the cheapest plan</span>
      <span>Solved in ${p.solve_time_seconds.toFixed(1)} s</span>
      <span>${num(p.fefo_violations_repaired || 0)} older batches swapped in, 0 FEFO breaks</span>
      ${term("gap", "What does this mean?")}
    </div>
    <div class="tabs" role="tablist" style="margin-top:18px" data-enter>
      ${[["summary", "Summary"], ["manifest", "Dispatch manifest"], ["trucks", "Truck loading (warehouse)"]]
        .map(([id, label]) => `<button class="tab" role="tab" data-tab="${id}" aria-selected="${tab === id}">${label}</button>`)
        .join("")}
    </div>
    <div id="tabBody"></div>`;

  view.querySelectorAll("[data-tab]").forEach((b) =>
    b.addEventListener("click", () => {
      tab = b.dataset.tab;
      view.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", x.dataset.tab === tab));
      paintTab(view);
    })
  );
  enter(view);
  paintTab(view);
}

function paintTab(view) {
  const body = view.querySelector("#tabBody");
  if (tab === "summary") summary(body);
  if (tab === "manifest") manifest(body);
  if (tab === "trucks") trucks(body);
  anime({ targets: body, opacity: [0, 1], translateY: [6, 0], duration: 300, easing: "easeOutCubic" });
}

function summary(body) {
  const k = state.plan.kpis;
  const b = state.plan.baseline_kpis;
  const fill = k.projected_fill_rate * 100;
  const bfill = b.projected_fill_rate * 100;
  const extraUnits = b.unmet_demand_units - k.unmet_demand_units;

  const rows = [
    {
      name: "Unmet demand", meaning: "Units customers want that stores will not have", key: "unmet",
      ours: k.unmet_demand_pct, base: b.unmet_demand_pct, fmt: (v) => pct(v), max: Math.max(k.unmet_demand_pct, b.unmet_demand_pct), higher: false,
    },
    {
      name: "Freight cost per unit delivered", meaning: `Total freight ${inrShort(k.total_freight_cost)} for the week`, key: "freight",
      ours: k.freight_per_unit_inr, base: b.freight_per_unit_inr, fmt: (v) => `₹${v.toFixed(2)}`, max: Math.max(k.freight_per_unit_inr, b.freight_per_unit_inr),
      higher: false, unit: "", dfmt: (d) => `₹${Math.abs(d).toFixed(2)}`,
    },
    {
      name: "Truck space used", meaning: `${num(k.trucks_used)} trucks, each with room for 500 units`, key: "truck_util",
      ours: k.truck_utilization_pct, base: b.truck_utilization_pct, fmt: (v) => pct(v), max: 100, higher: true,
    },
    {
      name: "Expected wastage", meaning: `${num(k.wastage_units)} units may expire on shelves (${inr(k.writeoff_cost_inr)} write-off)`, key: "wastage",
      ours: k.wastage_rate_pct, base: b.wastage_rate_pct, fmt: (v) => pct(v, 2), max: Math.max(k.wastage_rate_pct, b.wastage_rate_pct), higher: false, dfmt: (d) => Math.abs(d).toFixed(2),
    },
  ];

  body.innerHTML = `
    <div class="panel panel-pad">
      <div class="headline">
        <div>
          <div class="big-number" id="bigFill">0%</div>
          <div class="big-label">of expected demand met</div>
        </div>
        <div>
          <h2>Stores can serve ${pct(fill)} of what customers will want next week</h2>
          <p class="muted" style="margin:4px 0 10px">The pro-rata method would serve ${pct(bfill)} from the same warehouse stock.
          That is <b>${num(extraUnits)} more units on shelves</b> this week. ${term("fill_rate")}</p>
          ${deltaPill(fill, bfill)}
        </div>
      </div>
    </div>

    <div class="panel" style="margin-top:18px">
      <div class="panel-head"><div><h2>Other results</h2><p class="muted small" style="margin:0">Teal bar is this plan, grey is the pro-rata baseline.</p></div></div>
      <div class="table-wrap">
      <table class="ledger">
        <thead><tr><th>Measure</th><th>This plan</th><th>Baseline</th><th>Comparison</th><th>Change</th></tr></thead>
        <tbody>
          ${rows
            .map(
              (r) => `<tr>
              <td class="metric"><b>${r.name}</b><span>${r.meaning}</span><br>${term(r.key)}</td>
              <td class="val">${r.fmt(r.ours)}</td>
              <td class="base">${r.fmt(r.base)}</td>
              <td><div class="compare">
                <div class="compare-row"><span>This plan</span><div class="bar us"><i data-w="${(r.ours / (r.max || 1)) * 100}"></i></div></div>
                <div class="compare-row"><span>Baseline</span><div class="bar"><i data-w="${(r.base / (r.max || 1)) * 100}"></i></div></div>
              </div></td>
              <td>${deltaPill(r.ours, r.base, { higherIsBetter: r.higher, unit: r.unit ?? "pp", format: r.dfmt || ((d) => Math.abs(d).toFixed(1)) })}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>
      </div>
    </div>

    <div class="callout" style="margin-top:18px">
      <b>Why total freight is higher than the baseline:</b> this plan ships ${num(k.units_shipped - b.units_shipped)} more units.
      Each unit delivered costs less to move, and the extra freight is far smaller than the lost sales it prevents
      (${inrShort(b.stockout_cost_inr - k.stockout_cost_inr)} of lost margin and goodwill avoided). ${term("stockout_cost")}
    </div>
    <div style="margin-top:18px; display:flex; gap:12px; flex-wrap:wrap">
      <button class="btn" data-go="risk">Next: stress-test the plan ${ICONS.arrow}</button>
      <button class="btn btn-quiet" data-tab-jump="manifest">See every shipment</button>
    </div>`;

  countUp(body.querySelector("#bigFill"), fill, (v) => pct(v));
  anime({
    targets: body.querySelectorAll(".bar i"),
    width: (el) => `${Math.max(2, Number(el.dataset.w))}%`,
    delay: anime.stagger(50),
    duration: 700,
    easing: "easeOutCubic",
  });
  body.querySelector("[data-tab-jump]").addEventListener("click", () => document.querySelector('[data-tab="manifest"]').click());
}

function filtered() {
  const q = table.q.toLowerCase();
  const rows = state.plan.shipments.filter(
    (s) =>
      (!table.wh || s.source_warehouse === table.wh) &&
      (!table.store || s.destination_store === table.store) &&
      (!table.reason || s.reason_code === table.reason) &&
      (!q || `${s.sku_id} ${s.batch_id} ${s.assigned_truck_id} ${skuName(s.sku_id, state.meta)}`.toLowerCase().includes(q))
  );
  return rows.sort((a, b) => {
    const x = a[table.sort], y = b[table.sort];
    return (x > y ? 1 : x < y ? -1 : 0) * table.dir;
  });
}

const COLS = [
  ["sku_id", "Product"],
  ["batch_id", "Batch"],
  ["source_warehouse", "From"],
  ["destination_store", "To"],
  ["units_shipped", "Units", "r"],
  ["cases_shipped", "Cases", "r"],
  ["assigned_truck_id", "Truck"],
  ["days_to_expiry_at_arrival", "Shelf life on arrival", "r"],
  ["reason_code", "Why it was sent"],
];

function minLife(sku) {
  return state.meta?.skus?.find((x) => x.sku_id === sku)?.min_life_at_delivery ?? 0;
}

function manifest(body) {
  const ships = state.plan.shipments;
  const uniq = (k) => [...new Set(ships.map((s) => s[k]))].sort();
  body.innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div><h2>Dispatch manifest</h2>
        <p class="muted small" style="margin:0">Every shipment line. Hover a reason to read it in full. Click a column to sort.</p></div>
        <button class="btn btn-quiet" id="csv">${ICONS.download} Download CSV</button>
      </div>
      <div class="toolbar">
        <input class="input grow" id="q" placeholder="Search product, batch or truck" value="${table.q}" aria-label="Search">
        <select class="input" id="fwh" aria-label="Warehouse"><option value="">All warehouses</option>${uniq("source_warehouse").map((w) => `<option ${w === table.wh ? "selected" : ""} value="${w}">${whName(w)}</option>`).join("")}</select>
        <select class="input" id="fst" aria-label="Store"><option value="">All stores</option>${uniq("destination_store").map((w) => `<option ${w === table.store ? "selected" : ""} value="${w}">${storeName(w)}</option>`).join("")}</select>
        <select class="input" id="frs" aria-label="Reason"><option value="">All reasons</option>${Object.entries(REASONS).map(([k, v]) => `<option ${k === table.reason ? "selected" : ""} value="${k}">${v.short}</option>`).join("")}</select>
      </div>
      <div class="table-wrap"><table class="data" id="mtable"></table></div>
      <div class="pager" id="pager"></div>
    </div>`;

  const paint = () => {
    const rows = filtered();
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    table.page = Math.min(table.page, pages - 1);
    const slice = rows.slice(table.page * PAGE, (table.page + 1) * PAGE);
    body.querySelector("#mtable").innerHTML = `
      <thead><tr>${COLS.map(([k, label, cls]) => `<th class="${cls || ""}" ${table.sort === k ? `aria-sort="${table.dir > 0 ? "ascending" : "descending"}"` : ""}>
        <button data-sort="${k}">${label}${table.sort === k ? (table.dir > 0 ? ICONS.up : ICONS.down) : ""}</button></th>`).join("")}<th></th></tr></thead>
      <tbody>${slice
        .map((s) => {
          const low = s.days_to_expiry_at_arrival <= minLife(s.sku_id) * 1.5 && minLife(s.sku_id) < 30;
          return `<tr>
          <td>${skuName(s.sku_id, state.meta)}</td>
          <td class="muted">${s.batch_id}</td>
          <td>${s.source_warehouse}</td>
          <td>${storeName(s.destination_store)}</td>
          <td class="r">${num(s.units_shipped)}</td>
          <td class="r">${num(s.cases_shipped)}</td>
          <td class="muted">${truckName(s.assigned_truck_id)}</td>
          <td class="r"><span class="life ${low ? "low" : ""}">${s.days_to_expiry_at_arrival} days${low ? " (short)" : ""}</span></td>
          <td><span class="reason" data-code="${s.reason_code}" title="${REASONS[s.reason_code]?.long || ""}"><i></i>${REASONS[s.reason_code]?.short || s.reason_code}</span></td>
          <td><button class="link-btn small" data-ask='${JSON.stringify({ sku: s.sku_id, wh: s.source_warehouse, store: s.destination_store })}'>Why not another warehouse?</button></td>
        </tr>`;
        })
        .join("") || `<tr><td colspan="10" class="muted" style="text-align:center;padding:32px">No shipments match these filters. Clear a filter to see more.</td></tr>`}
      </tbody>`;
    body.querySelector("#pager").innerHTML = `
      <span>Showing ${rows.length ? table.page * PAGE + 1 : 0} to ${Math.min(rows.length, (table.page + 1) * PAGE)} of ${num(rows.length)} lines · ${num(rows.reduce((a, s) => a + s.units_shipped, 0))} units</span>
      <span><button class="btn btn-quiet" id="prev" ${table.page === 0 ? "disabled" : ""}>Previous</button>
      <button class="btn btn-quiet" id="next" ${table.page >= pages - 1 ? "disabled" : ""}>Next</button></span>`;
    body.querySelector("#prev").onclick = () => { table.page--; paint(); };
    body.querySelector("#next").onclick = () => { table.page++; paint(); };
    body.querySelectorAll("[data-sort]").forEach((b) =>
      (b.onclick = () => {
        table.dir = table.sort === b.dataset.sort ? -table.dir : -1;
        table.sort = b.dataset.sort;
        paint();
      })
    );
    anime({ targets: body.querySelectorAll("tbody tr"), opacity: [0, 1], delay: anime.stagger(8), duration: 200, easing: "linear" });
  };

  const bind = (id, key) =>
    body.querySelector(id).addEventListener("input", (e) => {
      table[key] = e.target.value;
      table.page = 0;
      paint();
    });
  bind("#q", "q"); bind("#fwh", "wh"); bind("#fst", "store"); bind("#frs", "reason");
  body.querySelector("#csv").addEventListener("click", () => {
    downloadCsv(`optiroute_manifest_${state.plan.plan_id}.csv`, [
      ["sku_id", "batch_id", "from_warehouse", "to_store", "cases", "units", "truck", "days_to_expiry_at_arrival", "reason"],
      ...filtered().map((s) => [s.sku_id, s.batch_id, s.source_warehouse, s.destination_store, s.cases_shipped, s.units_shipped, s.assigned_truck_id, s.days_to_expiry_at_arrival, REASONS[s.reason_code]?.long || s.reason_code]),
    ]);
  });
  body.addEventListener("click", (e) => {
    const b = e.target.closest("[data-ask]");
    if (!b) return;
    sessionStorage.setItem("optiroute.whyNotPrefill", b.dataset.ask);
    location.hash = "#/whynot";
  });
  paint();
}

function trucks(body) {
  const groups = {};
  for (const s of state.plan.shipments) (groups[s.assigned_truck_id] ||= []).push(s);
  const vol = (s) => s.units_shipped * (state.meta?.skus?.find((x) => x.sku_id === s.sku_id)?.volume ?? 1);
  const whs = [...new Set(state.plan.shipments.map((s) => s.source_warehouse))].sort();
  let wh = whs[0];

  body.innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div><h2>Truck loading and pick lists</h2>
        <p class="muted small" style="margin:0">For warehouse teams. Each truck's list is in pick order: the batch expiring soonest first. ${term("fefo", "Why this order?")}</p></div>
        <div style="display:flex; gap:10px; flex-wrap:wrap">
          <select class="input" id="twh" aria-label="Warehouse">${whs.map((w) => `<option value="${w}">${whName(w)}</option>`).join("")}</select>
          <button class="btn btn-quiet" id="pickCsv">${ICONS.download} Pick list CSV</button>
        </div>
      </div>
      <div class="truck-list" id="tlist"></div>
    </div>`;

  const paint = () => {
    const ids = Object.keys(groups).filter((id) => groups[id][0].source_warehouse === wh).sort();
    body.querySelector("#tlist").innerHTML = ids
      .map((id) => {
        const lines = groups[id].slice().sort((a, b) => a.days_to_expiry_at_arrival - b.days_to_expiry_at_arrival);
        const units = lines.reduce((a, s) => a + s.units_shipped, 0);
        const load = Math.min(100, (lines.reduce((a, s) => a + vol(s), 0) / 500) * 100);
        return `<details class="truck">
          <summary>
            <span><b>${truckName(id)}</b><br><span class="muted small">${lines.length} lines · ${num(units)} units</span></span>
            <span class="truck-load"><span>About ${Math.round(load)}% full</span><span class="bar us"><i data-w="${load}"></i></span></span>
            <span class="muted small">Show pick list</span>
          </summary>
          <div class="pick"><ol>${lines
            .map((s) => `<li><b>${num(s.cases_shipped)} cases</b> of ${skuName(s.sku_id, state.meta)} from batch ${s.batch_id}
              <span class="muted">(${s.days_to_expiry_at_arrival} days left on arrival)</span></li>`)
            .join("")}</ol></div>
        </details>`;
      })
      .join("");
    anime({ targets: body.querySelectorAll(".truck-load .bar i"), width: (el) => `${el.dataset.w}%`, delay: anime.stagger(15), duration: 600, easing: "easeOutCubic" });
  };
  body.querySelector("#twh").addEventListener("change", (e) => { wh = e.target.value; paint(); });
  body.querySelector("#pickCsv").addEventListener("click", () => {
    const rows = state.plan.shipments
      .filter((s) => s.source_warehouse === wh)
      .sort((a, b) => a.assigned_truck_id.localeCompare(b.assigned_truck_id) || a.days_to_expiry_at_arrival - b.days_to_expiry_at_arrival);
    downloadCsv(`pick_list_${wh}.csv`, [
      ["truck", "pick_order", "sku_id", "batch_id", "cases", "units", "days_to_expiry_at_arrival"],
      ...rows.map((s, i) => [s.assigned_truck_id, i + 1, s.sku_id, s.batch_id, s.cases_shipped, s.units_shipped, s.days_to_expiry_at_arrival]),
    ]);
  });
  paint();
}
