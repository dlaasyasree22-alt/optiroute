import { generatePlan } from "../api.js";
import { ICONS, STATUS, enter, errorState, num, pct, term, toast } from "../ui.js";
import { resetPlan, state, update } from "../state.js";

const STAGES = [
  {
    id: "load",
    title: "Load stock and sales",
    desc: "Warehouse batches, store shelves, 60 weeks of sales and truck lanes.",
    result: (i) => `${num(i.counts.inventory_batches)} warehouse batches and ${num(i.counts.sales_history)} sales records loaded`,
  },
  {
    id: "forecast",
    title: "Forecast next week's demand",
    desc: "A likely and a cautious estimate for every store and product.",
    result: (i) => `${num(i.pairs)} store and product forecasts ready`,
  },
  {
    id: "optimise",
    title: "Choose the shipments",
    desc: "Warehouse, batch, quantity and truck for every store, at the lowest total cost.",
    result: (i) => `${STATUS[i.status] || i.status}, proven within ${pct(i.gap * 100, 2)} of the cheapest possible plan`,
  },
  {
    id: "fefo",
    title: "Oldest stock first",
    desc: "Swap in older batches wherever the shelf life allows, so nothing is left behind to expire.",
    result: (i) =>
      i.skipped ? "Skipped by your settings" : `${num(i.violations_before)} batch swaps made, ${num(i.violations_after)} rule breaks left`,
  },
  {
    id: "risk",
    title: "Stress-test the plan",
    desc: "Replay the plan against 500 possible weeks of demand.",
    result: (i) => `${num(i.scenarios)} weeks simulated`,
  },
];

let running = false;

export function renderGenerate(view) {
  const r = state.request || {
    cycle_date: "2026-10-26",
    planning_horizon_days: 7,
    optimality_gap_tolerance: 0.01,
    enable_post_solve_repair: true,
  };

  view.innerHTML = `
    <div class="page-head" data-enter>
      <div class="eyebrow">Step 1</div>
      <h1>Generate this week's plan</h1>
      <p class="lede">OptiRoute decides which products each store gets, from which warehouse and batch, and on which truck.
      Check the week below, then press the button. It takes about 10 seconds.</p>
    </div>

    <div class="split">
      <form class="panel panel-pad" id="genForm" data-enter>
        <h2>Planning settings</h2>
        <p class="muted small" style="margin:0 0 18px">The defaults suit a normal weekly cycle.</p>
        <div class="form-grid">
          <div class="field">
            <label for="cycle">Delivery week starting</label>
            <input class="input" id="cycle" type="date" value="${r.cycle_date}" required>
            <div class="hint">Stock and expiry dates are as of this day.</div>
          </div>
          <div class="field">
            <label for="horizon">Days covered</label>
            <select class="input" id="horizon">
              ${[5, 7, 10, 14].map((d) => `<option value="${d}" ${d === r.planning_horizon_days ? "selected" : ""}>${d} days${d === 7 ? " (standard week)" : ""}</option>`).join("")}
            </select>
            <div class="hint">${term("horizon")}</div>
          </div>
        </div>

        <details class="advanced">
          <summary>Advanced settings</summary>
          <div class="form-grid">
            <div class="field">
              <label for="gap">Accuracy target: within <span id="gapVal">${(r.optimality_gap_tolerance * 100).toFixed(0)}%</span> of the best plan</label>
              <input id="gap" type="range" min="1" max="5" step="1" value="${r.optimality_gap_tolerance * 100}">
              <div class="hint">Lower is more precise but can take longer. ${term("gap")}</div>
            </div>
            <div class="field">
              <label>Oldest stock first</label>
              <div class="toggle">
                <input id="fefo" type="checkbox" ${r.enable_post_solve_repair ? "checked" : ""}>
                <label for="fefo" style="font-weight:400">Swap in older batches after planning, so that within each warehouse the batch expiring soonest always ships first.</label>
              </div>
              <div class="hint">${term("fefo", "What is FEFO?")} Keep this on unless you are comparing results.</div>
            </div>
          </div>
        </details>

        <div style="margin-top:24px; display:flex; gap:14px; align-items:center; flex-wrap:wrap">
          <button class="btn btn-lg" id="genBtn" type="submit">${ICONS.play} ${state.plan ? "Generate a new plan" : "Generate weekly plan"}</button>
          ${state.plan ? '<button class="btn btn-quiet" type="button" data-go="review">Back to current plan</button>' : ""}
        </div>
      </form>

      <section class="panel panel-pad" aria-live="polite" data-enter>
        <h2>How this plan is made</h2>
        <p class="muted small" style="margin:0 0 10px">Each step lights up as the engine finishes it. Times are real.</p>
        <div class="progress-track"><div class="progress-fill" id="progress"></div></div>
        <ol class="pipeline" id="pipeline">
          ${STAGES.map(
            (s) => `<li class="pipe-step" data-stage="${s.id}">
              <span class="pipe-icon"></span>
              <div><b>${s.title}</b><div class="pipe-desc">${s.desc}</div><div class="pipe-result"></div></div>
              <span class="pipe-time"></span>
            </li>`
          ).join("")}
        </ol>
        <div id="genOutcome"></div>
      </section>
    </div>`;

  enter(view);
  if (state.stages) paintFinished(view);

  view.querySelector("#gap").addEventListener("input", (e) => (view.querySelector("#gapVal").textContent = `${e.target.value}%`));
  view.querySelector("#genForm").addEventListener("submit", (e) => {
    e.preventDefault();
    run(view);
  });
}

function stageEl(view, id) {
  return view.querySelector(`[data-stage="${id}"]`);
}

function markStage(view, event) {
  const el = stageEl(view, event.stage);
  if (!el) return;
  const def = STAGES.find((s) => s.id === event.stage);
  el.className = `pipe-step ${event.status}`;
  if (event.status === "done") {
    el.querySelector(".pipe-icon").innerHTML = ICONS.check;
    el.querySelector(".pipe-result").textContent = def.result(event.info);
    el.querySelector(".pipe-time").textContent = `${event.info.seconds.toFixed(1)} s`;
    anime({ targets: el.querySelector(".pipe-icon"), scale: [0.6, 1], duration: 400, easing: "easeOutBack" });
    anime({ targets: el.querySelector(".pipe-result"), opacity: [0, 1], translateX: [-6, 0], duration: 300, easing: "easeOutCubic" });
    const doneCount = view.querySelectorAll(".pipe-step.done").length;
    anime({ targets: view.querySelector("#progress"), width: `${(doneCount / STAGES.length) * 100}%`, duration: 500, easing: "easeOutCubic" });
  }
}

function paintFinished(view) {
  state.stages.forEach((e) => markStage(view, e));
  showOutcome(view);
}

function showOutcome(view) {
  const p = state.plan;
  const total = state.stages.reduce((a, e) => a + (e.info.seconds || 0), 0);
  view.querySelector("#genOutcome").innerHTML = `
    <div class="callout" style="margin-top:16px">
      <b>Plan ready in ${total.toFixed(1)} seconds.</b>
      ${num(p.shipments.length)} shipment lines on ${num(p.kpis.trucks_used)} trucks, meeting
      ${pct(p.kpis.projected_fill_rate * 100)} of expected demand.
    </div>
    <div style="margin-top:14px; display:flex; gap:10px; flex-wrap:wrap">
      <button class="btn" data-go="review">Review the plan ${ICONS.arrow}</button>
      <button class="btn btn-quiet" data-go="flow">See it on the flow map</button>
    </div>`;
  anime({ targets: view.querySelector("#genOutcome"), opacity: [0, 1], translateY: [8, 0], duration: 400, easing: "easeOutCubic" });
}

async function run(view) {
  if (running) return;
  running = true;
  const request = {
    cycle_date: view.querySelector("#cycle").value,
    planning_horizon_days: Number(view.querySelector("#horizon").value),
    optimality_gap_tolerance: Number(view.querySelector("#gap").value) / 100,
    enable_post_solve_repair: view.querySelector("#fefo").checked,
  };
  const btn = view.querySelector("#genBtn");
  btn.disabled = true;
  btn.textContent = "Planning...";
  resetPlan();
  view.querySelectorAll(".pipe-step").forEach((el) => {
    el.className = "pipe-step";
    el.querySelector(".pipe-icon").innerHTML = "";
    el.querySelector(".pipe-result").textContent = "";
    el.querySelector(".pipe-time").textContent = "";
  });
  view.querySelector("#progress").style.width = "0";
  view.querySelector("#genOutcome").innerHTML = "";

  const stages = [];
  try {
    const plan = await generatePlan(request, (event) => {
      if (event.status === "done") stages.push(event);
      markStage(view, event);
    });
    update({ request, plan, stages });
    showOutcome(view);
    toast("Plan ready. Review it next.");
  } catch (err) {
    const running = view.querySelector(".pipe-step.running");
    if (running) running.className = "pipe-step error";
    if (running) running.querySelector(".pipe-icon").innerHTML = ICONS.cross;
    const infeasible = err.kind === "infeasible";
    view.querySelector("#genOutcome").innerHTML = errorState({
      title: infeasible ? "No valid plan for these settings" : "The plan could not be finished",
      message: infeasible
        ? `${err.message} Try covering fewer days, or check that warehouse stock was loaded for this week.`
        : `${err.message} Check that the engine is running, then try again.`,
      action: "retry",
      actionLabel: "Try again",
    });
    view.querySelector('[data-action="retry"]').addEventListener("click", () => run(view));
  } finally {
    running = false;
    btn.disabled = false;
    btn.innerHTML = `${ICONS.play} Generate a new plan`;
  }
}
