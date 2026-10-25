import { checkHealth, getMeta, isRecorded, onModeChange } from "./api.js";
import { ICONS } from "./ui.js";
import { state, subscribe, update } from "./state.js";
import { renderGenerate } from "./screens/generate.js";
import { renderReview } from "./screens/review.js";
import { renderRisk } from "./screens/risk.js";
import { renderWhyNot } from "./screens/whynot.js";
import { renderApprove } from "./screens/approve.js";
import { renderProof } from "./screens/proof.js";
import { renderAbout } from "./screens/about.js";
import { renderFlow } from "./screens/flow.js";
import { openTour, maybeShowTour } from "./tour.js";

const svg = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const STEP_ICONS = {
  generate: svg('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'),
  review: svg('<path d="M8 6h12M8 12h12M8 18h12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="4" cy="6" r="1.4" fill="currentColor"/><circle cx="4" cy="12" r="1.4" fill="currentColor"/><circle cx="4" cy="18" r="1.4" fill="currentColor"/>'),
  risk: svg('<path d="M2 12h4l2.5-6 4.5 12 2.5-6H22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'),
  whynot: svg('<path d="M6 4v6a4 4 0 004 4h8M15 10l4 4-4 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'),
  approve: svg('<rect x="4" y="4" width="16" height="16" rx="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 12.5l2.5 2.5L16 9.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'),
};

// The weekly plan's parts. Everything after "generate" needs a plan to exist.
const STEPS = [
  { id: "generate", title: "Generate", icon: STEP_ICONS.generate, render: renderGenerate },
  { id: "review", title: "Review", icon: STEP_ICONS.review, render: renderReview },
  { id: "risk", title: "Stress-test", icon: STEP_ICONS.risk, render: renderRisk },
  { id: "whynot", title: "Why not?", icon: STEP_ICONS.whynot, render: renderWhyNot },
  { id: "approve", title: "Approve", icon: STEP_ICONS.approve, render: renderApprove },
];
const LINKS = [
  { id: "flow", title: "Flow map (whole picture)", icon: ICONS.flow, render: renderFlow },
  { id: "proof", title: "Proof of results", icon: ICONS.proof, render: renderProof },
  { id: "about", title: "Data & assumptions", icon: ICONS.book, render: renderAbout },
];
const ALL = [...STEPS, ...LINKS];

const view = document.getElementById("view");
const rail = document.querySelector(".rail");

function currentId() {
  const id = location.hash.replace("#/", "") || "generate";
  return ALL.some((s) => s.id === id) ? id : "generate";
}

function stepDone(id) {
  if (id === "generate") return !!state.plan;
  if (id === "whynot") return state.whyNot.length > 0;
  if (id === "approve") return !!state.approved;
  return !!state.plan && state.visited.has(id);
}

function renderRail() {
  const cur = currentId();
  document.getElementById("crumb").innerHTML = state.plan
    ? `Week of <b>${new Date((state.request?.cycle_date || "2026-10-26") + "T00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}</b> · ${state.approved ? "Approved" : "Draft plan"}`
    : "No plan yet";
  const steps = document.getElementById("steps");
  steps.innerHTML = STEPS.map((s, i) => {
    const locked = i > 0 && !state.plan;
    const done = stepDone(s.id);
    return `<li><button class="step ${done ? "done" : ""}" data-go="${s.id}"
      ${cur === s.id ? 'aria-current="page"' : ""} ${locked ? 'aria-disabled="true" title="Generate a plan first"' : ""}>
      <span class="step-icon">${s.icon}</span><b>${s.title}</b>
      ${done ? `<span class="step-check" title="Done">${ICONS.check}<span class="sr-only">done</span></span>` : ""}
    </button></li>`;
  }).join("");

  document.getElementById("links").innerHTML = LINKS.map(
    (l) => `<a href="#/${l.id}" ${cur === l.id ? 'aria-current="page"' : ""}>${l.icon}${l.title}</a>`
  ).join("");
}

function renderMode() {
  const mode = document.getElementById("mode");
  const banner = document.getElementById("banner");
  if (isRecorded()) {
    mode.className = "mode recorded";
    mode.innerHTML = "<i></i><span><b>Recorded run</b><br>Engine offline, replaying a saved real run</span>";
    banner.hidden = false;
    banner.textContent =
      "Recorded run: the planning engine is not reachable, so you are seeing a saved result from a real engine run. Numbers will not change if you edit settings.";
  } else {
    mode.className = "mode";
    mode.innerHTML = "<i></i><span><b>Live engine</b><br>Results computed on request</span>";
    banner.hidden = true;
  }
}

function route() {
  const id = currentId();
  const step = ALL.find((s) => s.id === id);
  const index = STEPS.findIndex((s) => s.id === id);
  if (index > 0 && !state.plan) {
    location.hash = "#/generate";
    return;
  }
  state.visited.add(id);
  renderRail();
  view.innerHTML = "";
  step.render(view);
  view.focus({ preventScroll: true });
  window.scrollTo({ top: 0 });
  rail.classList.remove("open");
}

export const go = (id) => (location.hash = `#/${id}`);

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-go]");
  if (!btn || btn.getAttribute("aria-disabled") === "true") return;
  go(btn.dataset.go);
});
document.getElementById("tourBtn").addEventListener("click", openTour);
document.getElementById("menuBtn").addEventListener("click", () => rail.classList.toggle("open"));
window.addEventListener("hashchange", route);
subscribe(renderRail);
onModeChange(renderMode);

(async function start() {
  await checkHealth();
  renderMode();
  update({ meta: await getMeta().catch(() => null) });
  route();
  maybeShowTour();
})();
