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
import { openTour, maybeShowTour } from "./tour.js";

// The weekly flow. Each step after "generate" needs a plan to exist.
const STEPS = [
  { id: "generate", title: "Generate", sub: "Build this week's plan", render: renderGenerate },
  { id: "review", title: "Review", sub: "Check results and shipments", render: renderReview },
  { id: "risk", title: "Stress-test", sub: "See how a bad week looks", render: renderRisk },
  { id: "whynot", title: "Ask \"Why not?\"", sub: "Test your own change", render: renderWhyNot },
  { id: "approve", title: "Approve & export", sub: "Lock and send to warehouses", render: renderApprove },
];
const LINKS = [
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
  const steps = document.getElementById("steps");
  steps.innerHTML =
    '<i class="progress-line"></i>' +
    STEPS.map((s, i) => {
      const locked = i > 0 && !state.plan;
      const done = stepDone(s.id);
      return `<li><button class="step ${done ? "done" : ""}" data-go="${s.id}"
        ${cur === s.id ? 'aria-current="step"' : ""} ${locked ? 'aria-disabled="true" title="Generate a plan first"' : ""}>
        <span class="step-dot">${done ? ICONS.check : i + 1}</span>
        <span class="step-text"><b>${s.title}</b><span>${s.sub}</span></span>
      </button></li>`;
    }).join("");

  // Progress line grows to the last completed step
  const doneCount = STEPS.filter((s) => stepDone(s.id)).length;
  const items = steps.querySelectorAll("li");
  const line = steps.querySelector(".progress-line");
  if (doneCount > 1) {
    const last = items[Math.min(doneCount, STEPS.length) - 1];
    const target = last.offsetTop + 22 - 22;
    anime({ targets: line, height: target, duration: 600, easing: "easeOutCubic" });
  }

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
  document.getElementById("crumb").innerHTML =
    index >= 0 ? `Step ${index + 1} of ${STEPS.length} · <b>${step.title}</b>` : `<b>${step.title}</b>`;
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
