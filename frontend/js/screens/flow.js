import { getRisk } from "../api.js";
import { ICONS, enter, num, pct, storeName, whName } from "../ui.js";
import { state, update } from "../state.js";

// The whole idea on one page: how data becomes a plan (system flow), and where the plan
// sends stock (network map). Every node links to the screen that explains it in detail.

export async function renderFlow(view) {
  const p = state.plan;
  view.innerHTML = `
    <div class="page-head" data-enter>
      <div class="eyebrow">The whole picture</div>
      <h1>Flow map</h1>
      <p class="lede">How OptiRoute turns stock and sales data into a weekly plan, and where that plan sends every truck.
      Click any box to open that part of the plan. ${p ? "" : "Generate a plan to see live numbers flowing through it."}</p>
    </div>
    <div class="panel" data-enter>
      <div class="panel-head"><div><h2>From data to dispatch</h2>
      <p class="muted small" style="margin:0">${p ? "Moving dots show data flowing through each stage of this week's plan." : "Each box is one stage of the engine."}</p></div></div>
      <div class="flow-wrap" id="sysflow"></div>
    </div>
    <div class="panel" style="margin-top:18px" data-enter>
      <div class="panel-head"><div><h2>Where the stock goes this week</h2>
      <p class="muted small" style="margin:0">Warehouses on the left, stores on the right. Thicker lines carry more units. Hover a warehouse, store or line for details.</p></div>
      ${p ? `<button class="btn btn-quiet" data-go="review">See every shipment as a table</button>` : ""}</div>
      <div class="flow-wrap" id="netmap"></div>
    </div>
    <div class="flow-tip" id="flowTip" hidden></div>`;
  enter(view);

  if (p && !state.risk) {
    try { update({ risk: await getRisk(p.plan_id) }); } catch {}
  }
  drawSystem(view.querySelector("#sysflow"));
  drawNetwork(view.querySelector("#netmap"));
}

// ---------- System flow ----------

function stageInfo(id) {
  return state.stages?.find((s) => s.stage === id)?.info;
}

function drawSystem(el) {
  const p = state.plan;
  const load = stageInfo("load"), fc = stageInfo("forecast"), fefo = stageInfo("fefo");
  const risk = state.risk;
  const live = !!p;

  // x, y, w, h, title, subtitle, live metric, target screen
  const N = {
    sales: [10, 22, 200, 68, "Sales history", "60 weeks, every store", live && load ? `${num(load.counts.sales_history)} records` : "", "about"],
    batches: [10, 102, 200, 68, "Warehouse batches", "Stock and expiry dates", live && load ? `${num(load.counts.inventory_batches)} batches` : "", "about"],
    shelves: [10, 182, 200, 68, "Store shelves", "What stores hold today", live && load ? `${num(load.counts.store_inventory)} product lines` : "", "about"],
    lanes: [10, 262, 200, 68, "Truck lanes", "Cost and transit days", "60 lanes, 500-unit trucks", "about"],
    forecast: [270, 20, 190, 70, "Forecast demand", "Likely and cautious", live && fc ? `${num(fc.pairs)} forecasts` : "", "about"],
    optimise: [520, 120, 200, 104, "Choose shipments", "Lowest total cost", live ? `${num(p.kpis.units_shipped)} units` : "", "review", live ? `${num(p.kpis.trucks_used)} trucks` : ""],
    fefo: [780, 138, 170, 68, "Oldest stock first", "FEFO repair", live && fefo ? `${num(fefo.violations_before)} swaps, 0 breaks` : "", "review"],
    manifest: [1010, 58, 170, 68, "Dispatch manifest", "For planners", live ? `${num(p.shipments.length)} lines` : "", "review"],
    approve: [1010, 218, 170, 68, "Approve & send", "Pick lists by truck", state.approved ? "Approved" : live ? "Waiting for approval" : "", "approve"],
    risk: [780, 292, 170, 68, "Stress-test", "500 possible weeks", risk ? `Bad week ${pct(risk.cvar95_fill_rate)}` : "", "risk"],
    whynot: [520, 292, 200, 68, "Ask \"Why not?\"", "Your change, re-planned", state.whyNot.length ? `${state.whyNot.length} asked` : live ? "Try your own change" : "", "whynot"],
  };
  const right = (k) => [N[k][0] + N[k][2], N[k][1] + N[k][3] / 2];
  const left = (k) => [N[k][0], N[k][1] + N[k][3] / 2];
  const top = (k) => [N[k][0] + N[k][2] / 2, N[k][1]];
  const bottom = (k) => [N[k][0] + N[k][2] / 2, N[k][1] + N[k][3]];
  const curve = ([x1, y1], [x2, y2]) => {
    const mx = (x1 + x2) / 2;
    return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
  };
  const vert = ([x1, y1], [x2, y2]) => {
    const my = (y1 + y2) / 2;
    return `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`;
  };

  const optIn = (dy) => [N.optimise[0], N.optimise[1] + N.optimise[3] / 2 + dy];
  const edges = [
    { d: curve(right("sales"), left("forecast")) },
    { d: curve(right("forecast"), optIn(-36)), label: "target per store" },
    { d: curve(right("batches"), optIn(-12)) },
    { d: curve(right("shelves"), optIn(12)) },
    { d: curve(right("lanes"), optIn(36)) },
    { d: curve(right("optimise"), left("fefo")) },
    { d: curve(right("fefo"), left("manifest")) },
    { d: curve(right("fefo"), left("approve")) },
    { d: vert(bottom("fefo"), top("risk")) },
    { d: vert([N.whynot[0] + 70, N.whynot[1]], [N.optimise[0] + 70, N.optimise[1] + N.optimise[3]]), loop: true },
  ];

  const node = (key) => {
    const [x, y, w, h, title, sub, metric, go, metric2] = N[key];
    const big = key === "optimise";
    return `<g class="fnode ${live && metric ? "on" : ""}" data-go="${go}" tabindex="0" role="link" aria-label="${title}. ${sub}. ${metric || ""}">
      <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10"/>
      <text class="ft" x="${x + 14}" y="${y + 22}">${title}</text>
      <text class="fs" x="${x + 14}" y="${y + 39}">${sub}</text>
      ${metric ? `<text class="fm" x="${x + 14}" y="${y + (big ? 66 : 58)}">${metric}</text>` : ""}
      ${metric2 ? `<text class="fm" x="${x + 14}" y="${y + 88}">${metric2}</text>` : ""}
    </g>`;
  };

  el.innerHTML = `<svg viewBox="0 0 1190 372" class="sys" role="img" aria-label="System flow: sales history, warehouse batches, store shelves and truck lanes feed the forecast and the optimiser; the plan passes through oldest-stock-first repair to the dispatch manifest and approval, is stress-tested, and planner what-if questions loop back into the optimiser.">
    <defs>
      <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0,1 L9,5 L0,9" fill="none" stroke="var(--muted)" stroke-width="1.6" stroke-linecap="round"/>
      </marker>
    </defs>
    <text class="fcol" x="10" y="14">INPUTS</text>
    <text class="fcol" x="270" y="112">ENGINE</text>
    <text class="fcol" x="1010" y="46">OUTPUTS</text>
    ${edges.map((e, i) => `<path id="edge${i}" class="fedge ${e.loop ? "loop" : "draw"}" pathLength="1" style="animation-delay:${(0.2 + i * 0.06).toFixed(2)}s" d="${e.d}" marker-end="url(#arrow)"/>`).join("")}
    <text class="fs" x="${N.whynot[0] + 84}" y="${N.optimise[1] + N.optimise[3] + 38}">re-solves the plan</text>
    ${live ? edges.map((e, i) => [0, 1].map((k) => `<circle class="fdot" r="3.5"><animateMotion dur="${e.loop ? 3 : 2.4}s" begin="-${(i * 0.2 + k * 1.2).toFixed(1)}s" repeatCount="indefinite"><mpath href="#edge${i}"/></animateMotion></circle>`).join("")).join("") : ""}
    ${Object.keys(N).map(node).join("")}
  </svg>`;

  const svg = el.querySelector("svg");
  anime({ targets: svg.querySelectorAll(".fnode"), opacity: [0, 1], translateY: [8, 0], delay: anime.stagger(50), duration: 400, easing: "easeOutCubic" });
  svg.querySelectorAll(".fnode").forEach((g) => g.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); location.hash = `#/${g.dataset.go}`; }
  }));
}

// ---------- Network map ----------

function drawNetwork(el) {
  const p = state.plan;
  if (!p) {
    el.innerHTML = `<div class="state">${ICONS.empty}<h2>No plan yet</h2><p>Once a plan exists, every warehouse-to-store lane appears here, sized by the units it carries.</p>
      <a class="btn" href="#/generate">Generate weekly plan ${ICONS.arrow}</a></div>`;
    return;
  }

  const lanes = {}, whTot = {}, stTot = {}, whTrucks = {}, stTrucks = {};
  for (const s of p.shipments) {
    const k = `${s.source_warehouse}|${s.destination_store}`;
    const l = (lanes[k] ||= { wh: s.source_warehouse, st: s.destination_store, units: 0, trucks: new Set(), skus: new Set() });
    l.units += s.units_shipped;
    l.trucks.add(s.assigned_truck_id);
    l.skus.add(s.sku_id);
    whTot[s.source_warehouse] = (whTot[s.source_warehouse] || 0) + s.units_shipped;
    stTot[s.destination_store] = (stTot[s.destination_store] || 0) + s.units_shipped;
    (whTrucks[s.source_warehouse] ||= new Set()).add(s.assigned_truck_id);
    (stTrucks[s.destination_store] ||= new Set()).add(s.assigned_truck_id);
  }
  const whs = (state.meta?.warehouses || Object.keys(whTot)).slice().sort();
  const stores = (state.meta?.stores || Object.keys(stTot)).slice().sort();
  const atRisk = new Set((state.risk?.top_at_risk || []).map((r) => r.store_id));

  const W = 1190, rowH = 28, H = stores.length * rowH + 40;
  const wx = 230, sx = 900;
  const wy = (i) => 20 + ((i + 0.5) * (H - 40)) / whs.length;
  const sy = (i) => 20 + (i + 0.5) * rowH;
  const maxU = Math.max(...Object.values(lanes).map((l) => l.units));
  const width = (u) => 1.5 + (u / maxU) * 13;

  const laneList = Object.values(lanes).sort((a, b) => b.units - a.units);
  const pathFor = (l) => {
    const y1 = wy(whs.indexOf(l.wh)), y2 = sy(stores.indexOf(l.st));
    const mx = (wx + sx) / 2;
    return `M${wx},${y1} C${mx},${y1} ${mx},${y2} ${sx},${y2}`;
  };

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="net" role="img" aria-label="Network map: ${laneList.length} active lanes from ${whs.length} warehouses to ${stores.length} stores, carrying ${num(p.kpis.units_shipped)} units on ${num(p.kpis.trucks_used)} trucks.">
    <g class="lanes">${laneList
      .map((l, i) => `<path class="lane draw" pathLength="1" style="animation-delay:${(i * 0.006).toFixed(3)}s" data-i="${i}" data-wh="${l.wh}" data-st="${l.st}" d="${pathFor(l)}" stroke-width="${width(l.units).toFixed(1)}"/>`)
      .join("")}</g>
    <g class="lane-flows">${laneList.map((l) => `<path class="lane-flow" data-wh="${l.wh}" data-st="${l.st}" d="${pathFor(l)}"/>`).join("")}</g>
    <g class="lane-hits">${laneList
      .map((l, i) => `<path class="lane-hit" data-i="${i}" data-wh="${l.wh}" data-st="${l.st}" d="${pathFor(l)}" stroke-width="${Math.max(10, width(l.units) + 6)}"/>`)
      .join("")}</g>
    ${whs
      .map((w, i) => `<g class="wnode" data-wh="${w}" tabindex="0">
        <rect x="20" y="${wy(i) - 38}" width="${wx - 20}" height="76" rx="10"/>
        <text class="ft" x="36" y="${wy(i) - 14}">${whName(w)}</text>
        <text class="fm" x="36" y="${wy(i) + 8}">${num(whTot[w] || 0)} units out</text>
        <text class="fs" x="36" y="${wy(i) + 26}">${num(whTrucks[w]?.size || 0)} trucks</text>
      </g>`)
      .join("")}
    ${stores
      .map((s, i) => `<g class="snode" data-st="${s}" tabindex="0">
        <rect x="${sx}" y="${sy(i) - 11}" width="8" height="22" rx="3"/>
        <text class="ss" x="${sx + 18}" y="${sy(i) + 4}">${storeName(s)}</text>
        <text class="sv" x="${W - 20}" y="${sy(i) + 4}" text-anchor="end">${num(stTot[s] || 0)} units${atRisk.has(s) ? " · watch" : ""}</text>
      </g>`)
      .join("")}
  </svg>
  <p class="muted small" style="margin:10px 0 0">Stores marked "watch" have products the stress-test flagged as likely to run short.</p>`;

  const svg = el.querySelector("svg");
  const tip = document.getElementById("flowTip");
  anime({ targets: svg.querySelectorAll(".wnode, .snode"), opacity: [0, 1], delay: anime.stagger(20), duration: 300, easing: "linear" });

  const focus = (pred) => {
    svg.classList.add("focused");
    svg.querySelectorAll(".lane, .lane-flow").forEach((p) => p.classList.toggle("hot", pred(p.dataset)));
  };
  const clear = () => {
    svg.classList.remove("focused");
    svg.querySelectorAll(".hot").forEach((p) => p.classList.remove("hot"));
    tip.hidden = true;
  };
  const show = (e, html) => {
    tip.innerHTML = html;
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    tip.style.left = `${Math.min(window.innerWidth - r.width - 12, e.clientX + 14)}px`;
    tip.style.top = `${Math.max(8, e.clientY - r.height - 12)}px`;
  };

  svg.querySelectorAll(".lane-hit").forEach((h) => {
    const l = laneList[h.dataset.i];
    h.addEventListener("mousemove", (e) => {
      focus((d) => d.wh === l.wh && d.st === l.st);
      show(e, `<b>${whName(l.wh)} to ${storeName(l.st)}</b><br>${num(l.units)} units · ${l.trucks.size} truck${l.trucks.size > 1 ? "s" : ""} · ${l.skus.size} products`);
    });
    h.addEventListener("mouseleave", clear);
  });
  svg.querySelectorAll(".wnode").forEach((g) => {
    const on = (e) => {
      const w = g.dataset.wh;
      focus((d) => d.wh === w);
      const n = laneList.filter((l) => l.wh === w).length;
      show(e, `<b>${whName(w)}</b><br>${num(whTot[w] || 0)} units to ${n} stores on ${whTrucks[w]?.size || 0} trucks`);
    };
    g.addEventListener("mousemove", on);
    g.addEventListener("focus", () => focus((d) => d.wh === g.dataset.wh));
    g.addEventListener("mouseleave", clear);
    g.addEventListener("blur", clear);
  });
  svg.querySelectorAll(".snode").forEach((g) => {
    const on = (e) => {
      const s = g.dataset.st;
      focus((d) => d.st === s);
      const src = laneList.filter((l) => l.st === s).map((l) => `${l.wh} ${num(l.units)}`).join(", ");
      show(e, `<b>${storeName(s)}</b><br>${num(stTot[s] || 0)} units on ${stTrucks[s]?.size || 0} trucks<br><span class="muted">${src || "No deliveries this week"}</span>`);
    };
    g.addEventListener("mousemove", on);
    g.addEventListener("focus", () => focus((d) => d.st === g.dataset.st));
    g.addEventListener("mouseleave", clear);
    g.addEventListener("blur", clear);
  });
}
