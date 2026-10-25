// Shared formatting, plain-English text, icons and small UI helpers.

const inrFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const numFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export const inr = (n) => (n < 0 ? "-" : "") + "₹" + inrFmt.format(Math.abs(Math.round(n)));
export const num = (n) => numFmt.format(Math.round(n));
export const pct = (n, digits = 1) => `${Number(n).toFixed(digits)}%`;

// ₹9,24,904 reads as "₹9.25 lakh" for headline numbers
export function inrShort(n) {
  const a = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} crore`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} lakh`;
  return inr(n);
}

export const storeName = (id) => (id || "").replace("STORE_", "Store ");
export const whName = (id) => (id || "").replace("WH", "Warehouse ");
export const skuName = (id, meta) => {
  const s = meta?.skus?.find((x) => x.sku_id === id);
  return s ? `${id.replace("_", " ")} · ${s.category.replace("_", " ")}` : (id || "").replace("_", " ");
};
export const truckName = (id) => {
  const m = /TRUCK_(WH\d)_S(\d+)_(\d+)/.exec(id || "");
  return m ? `${m[1]} to Store ${m[2]}, truck ${m[3]}` : id;
};

export const REASONS = {
  STOCKOUT_PROTECTION: {
    short: "Store would run out",
    long: "Sent because this store was likely to run out of this product before the next delivery.",
  },
  EXPIRY_PRIORITY: {
    short: "Oldest stock moved first",
    long: "This batch is close to its minimum shelf life. Sending it now, to a store that sells it quickly, avoids writing it off at the warehouse.",
  },
  SAFETY_BUFFER: {
    short: "Cover for a busy week",
    long: "The store has enough for a normal week. This extra stock covers a busier than usual week.",
  },
  PLANNER_FORCED: {
    short: "Added by planner",
    long: "A planner asked for this shipment in a what-if question.",
  },
};

export const STATUS = {
  OPTIMAL: "Best plan found",
  TIME_LIMIT: "Good plan, stopped at time limit",
  INFEASIBLE: "No valid plan",
  HEURISTIC: "Rule-based plan",
};

export const TERMS = {
  fill_rate:
    "The share of customer demand that stores can meet from the shelf. 87% means 13 of every 100 units customers want are not available.",
  unmet:
    "Units customers would want next week that stores will not have. Some of this is unavoidable because the warehouses do not hold enough of certain products.",
  wastage:
    "Units expected to expire on store shelves before they sell, as a share of all stock at the stores.",
  truck_util:
    "How full the trucks are by volume. Each truck has room for 500 standard units. Fuller trucks mean a lower freight cost per unit.",
  freight: "Fixed cost per truck plus a per-unit handling cost on each lane, for the whole week.",
  fefo:
    "First Expiry, First Out. The batch that expires soonest leaves the warehouse first, so older stock is not left behind to expire.",
  gap:
    "How close the plan must be to the cheapest possible plan before the optimiser stops. At 1%, the plan's cost is proven to be within 1% of the best possible.",
  horizon: "How many days of store demand this plan covers. The standard weekly cycle is 7 days.",
  cvar:
    "CVaR95: the average fill rate in the worst 5% of the 500 simulated weeks. It answers the question: how bad does a bad week get?",
  p_stockout:
    "In a typical simulated week, the share of store and product combinations that run short at least once.",
  median: "Half of the 500 simulated weeks do better than this fill rate and half do worse.",
  monte_carlo:
    "The plan is replayed against 500 randomly generated weeks of demand, including region-wide shocks such as heat waves or festivals, to see how it holds up.",
  newsvendor:
    "For each product, the safety stock level comes from comparing the profit lost on a missed sale with the cost of an unsold or expired unit. Long-life, high-margin products get more buffer; perishables get less.",
  ci: "The range the true average falls in 95 times out of 100, based on 10 independent simulation runs.",
  stockout_cost:
    "Each unit a customer wanted but could not buy is costed at the lost margin plus a goodwill charge of half the unit cost.",
  writeoff: "The purchase cost of units that expire before they sell.",
  min_life:
    "Stores refuse stock that arrives with too little shelf life left. The optimiser never sends a batch that would arrive below this limit.",
};

export function term(key, label = "What is this?") {
  return `<details class="term"><summary>${label}</summary><span class="term-body">${TERMS[key]}</span></details>`;
}

export const ICONS = {
  check: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  cross: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
  up: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M6 10V2M2.5 5.5L6 2l3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  down: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M6 2v8M2.5 6.5L6 10l3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M8.5 12l2.5 2.5 4.5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  eye: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>',
  alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3L2 20h20z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M12 10v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="17" r="1.2" fill="currentColor"/></svg>',
  download: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7 10l5 5 5-5M5 20h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5l12 7-12 7z" fill="currentColor"/></svg>',
  arrow: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  flow: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="4" width="6" height="5" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/><rect x="2" y="15" width="6" height="5" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/><rect x="16" y="9.5" width="6" height="5" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 6.5c4 0 4 5.5 8 5.5M8 17.5c4 0 4-5.5 8-5.5" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  proof: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  book: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5a2 2 0 012-2h13v16H6a2 2 0 00-2 2zM4 5v16M8 7h7" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>',
  plug: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 01-12 0zM12 17v4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  empty: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="7" width="13" height="10" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M16 10h3l2 3v4h-5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><circle cx="7" cy="18" r="1.8" fill="var(--surface)" stroke="currentColor" stroke-width="1.6"/><circle cx="17.5" cy="18" r="1.8" fill="var(--surface)" stroke="currentColor" stroke-width="1.6"/></svg>',
};

// Compare our value against the baseline. higherIsBetter decides colour and wording.
export function deltaPill(ours, base, { higherIsBetter = true, unit = "pp", format = (d) => d.toFixed(1) } = {}) {
  const d = ours - base;
  if (Math.abs(d) < 1e-9) return `<span class="delta-pill flat">Same as baseline</span>`;
  const good = higherIsBetter ? d > 0 : d < 0;
  const icon = d > 0 ? ICONS.up : ICONS.down;
  return `<span class="delta-pill ${good ? "good" : "bad"}">${icon}${d > 0 ? "+" : ""}${format(d)}${unit} vs baseline</span>`;
}

export function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  anime.remove(el);
  anime
    .timeline({ targets: el, easing: "easeOutCubic" })
    .add({ opacity: [0, 1], translateY: [20, 0], translateX: ["-50%", "-50%"], duration: 250 })
    .add({ opacity: 0, translateY: 10, duration: 300, delay: 2200 });
}

// Counts a number up from 0 for headline figures
export function countUp(el, value, format, duration = 900) {
  const obj = { v: 0 };
  anime({
    targets: obj,
    v: value,
    duration,
    easing: "easeOutExpo",
    update: () => (el.textContent = format(obj.v)),
  });
}

export function enter(container) {
  anime({
    targets: container.querySelectorAll("[data-enter]"),
    opacity: [0, 1],
    translateY: [12, 0],
    delay: anime.stagger(60),
    duration: 450,
    easing: "easeOutCubic",
  });
}

export function downloadCsv(filename, rows) {
  const esc = (v) => {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = rows.map((r) => r.map(esc).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function errorState({ title, message, action, actionLabel }) {
  return `<div class="panel state error" data-enter>
    ${ICONS.alert}
    <h2>${title}</h2>
    <p>${message}</p>
    ${action ? `<button class="btn" data-action="${action}">${actionLabel}</button>` : ""}
  </div>`;
}

export function emptyState({ title, message, href, label }) {
  return `<div class="panel state" data-enter>
    ${ICONS.empty}
    <h2>${title}</h2>
    <p>${message}</p>
    ${href ? `<a class="btn" href="${href}">${label} ${ICONS.arrow}</a>` : ""}
  </div>`;
}
