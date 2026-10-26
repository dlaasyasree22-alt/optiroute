// First-run walkthrough of the weekly flow. Skippable; reopened from "How it works".

const KEY = "optiroute.tourSeen";

const ILLUS = {
  generate:
    '<svg viewBox="0 0 220 64"><rect x="10" y="22" width="40" height="20" rx="4" fill="#e3efee"/><rect x="70" y="22" width="40" height="20" rx="4" fill="#e3efee"/><rect x="130" y="22" width="40" height="20" rx="4" fill="#155e63"/><path d="M50 32h20M110 32h20" stroke="#155e63" stroke-width="2"/><circle cx="195" cy="32" r="12" fill="none" stroke="#155e63" stroke-width="2"/><path d="M189 32l4 4 8-8" fill="none" stroke="#155e63" stroke-width="2.4" stroke-linecap="round"/></svg>',
  review:
    '<svg viewBox="0 0 220 64"><rect x="10" y="10" width="200" height="10" rx="3" fill="#e1ded6"/><rect x="10" y="27" width="150" height="10" rx="3" fill="#155e63"/><rect x="10" y="44" width="110" height="10" rx="3" fill="#9aa1a8"/></svg>',
  risk:
    '<svg viewBox="0 0 220 64"><g fill="#155e63">' +
    [6, 10, 16, 24, 34, 44, 50, 46, 36, 24, 14, 8, 4]
      .map((h, i) => `<rect x="${14 + i * 15}" y="${58 - h}" width="11" height="${h}" rx="2" ${i < 2 ? 'fill="#b3261e"' : ""}/>`)
      .join("") +
    "</g></svg>",
  whynot:
    '<svg viewBox="0 0 220 64"><rect x="20" y="14" width="70" height="14" rx="3" fill="#b3261e"/><rect x="90" y="34" width="40" height="14" rx="3" fill="#1f7a4d"/><path d="M20 56h180" stroke="#4a5259"/><text x="150" y="46" font-size="14" fill="#1d2226" font-family="IBM Plex Sans, sans-serif">net ₹</text></svg>',
  approve:
    '<svg viewBox="0 0 220 64"><rect x="60" y="12" width="100" height="40" rx="6" fill="none" stroke="#1f7a4d" stroke-width="3"/><text x="78" y="38" font-size="15" font-weight="700" fill="#1f7a4d" font-family="IBM Plex Sans, sans-serif">APPROVED</text></svg>',
};

const SLIDES = [
  {
    key: "generate",
    title: "1. Generate the weekly plan",
    body: "Pick the week and press one button. OptiRoute forecasts demand for every store and product, then works out which warehouse sends what, in which batch, on which truck. You can watch each step as it runs.",
  },
  {
    key: "review",
    title: "2. Review what it decided",
    body: "See the headline results next to today's spreadsheet method, then browse every shipment with a plain reason for why it was sent. Warehouse teams get pick lists grouped by truck, oldest stock first.",
  },
  {
    key: "risk",
    title: "3. Stress-test it",
    body: "The plan is replayed against 500 possible weeks of demand. You get a simple verdict on how well it holds up in a bad week, and the stores most likely to run short.",
  },
  {
    key: "whynot",
    title: "4. Ask \"Why not?\"",
    body: "Disagree with a decision? Ask for a specific shipment. The engine re-plans with your change and tells you exactly what it would cost in freight, expired stock and lost sales.",
  },
  {
    key: "approve",
    title: "5. Approve and export",
    body: "When you are happy, approve the plan and download the dispatch manifest and warehouse pick lists.",
  },
];

let index = 0;
const root = () => document.getElementById("tour");

function render() {
  const s = SLIDES[index];
  root().innerHTML = `<div class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tourTitle">
    <div class="tour-flow">${SLIDES.map((_, i) => `<i class="${i <= index ? "on" : ""}"></i>`).join("")}</div>
    <div class="tour-illus">${ILLUS[s.key]}</div>
    <h2 id="tourTitle">${s.title}</h2>
    <p>${s.body}</p>
    <div class="tour-actions">
      <button class="link-btn" data-tour="skip">${index === SLIDES.length - 1 ? "Close" : "Skip the tour"}</button>
      <div>
        ${index > 0 ? '<button class="btn btn-quiet" data-tour="back">Back</button>' : ""}
        <button class="btn" data-tour="next">${index === SLIDES.length - 1 ? "Start planning" : "Next"}</button>
      </div>
    </div>
  </div>`;
  anime({ targets: root().querySelector(".tour-illus svg *"), opacity: [0, 1], translateY: [6, 0], delay: anime.stagger(40), duration: 400, easing: "easeOutCubic" });
  root().querySelector('[data-tour="next"]').focus();
}

function close() {
  try {
    localStorage.setItem(KEY, "1");
  } catch {}
  anime({ targets: root(), opacity: [1, 0], duration: 200, easing: "linear", complete: () => (root().hidden = true) });
}

export function openTour() {
  index = 0;
  root().hidden = false;
  root().style.opacity = 1;
  render();
  anime({ targets: root().querySelector(".tour-card"), scale: [0.96, 1], opacity: [0, 1], duration: 300, easing: "easeOutCubic" });
}

export function maybeShowTour() {
  let seen = false;
  try {
    seen = localStorage.getItem(KEY) === "1";
  } catch {}
  if (!seen) openTour();
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-tour]");
  if (!b) return;
  if (b.dataset.tour === "skip") return close();
  if (b.dataset.tour === "back") index = Math.max(0, index - 1);
  if (b.dataset.tour === "next") {
    if (index === SLIDES.length - 1) return close();
    index++;
  }
  render();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !root().hidden) close();
});
