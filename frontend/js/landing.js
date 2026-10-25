import { startField } from "./shader.js";

const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
if (reduce) document.documentElement.classList.add("no-motion");

// ---------- Haptics: a short tick on supported phones, a pressed feel everywhere ----------
function haptic(pattern = 12) {
  if (!reduce && "vibrate" in navigator) {
    try { navigator.vibrate(pattern); } catch {}
  }
}
document.querySelectorAll("[data-haptic]").forEach((el) => {
  el.addEventListener("pointerdown", () => {
    haptic(12);
    el.classList.add("pressed");
    anime.remove(el);
    anime({ targets: el, scale: 0.95, duration: 120, easing: "easeOutQuad" });
  });
  const release = () => {
    el.classList.remove("pressed");
    anime({ targets: el, scale: 1, duration: 500, easing: "easeOutElastic(1, .5)" });
  };
  el.addEventListener("pointerup", release);
  el.addEventListener("pointerleave", release);
});

// ---------- Magnetic buttons: pulled slightly toward the pointer ----------
if (!reduce && window.matchMedia("(pointer: fine)").matches) {
  document.querySelectorAll(".magnetic").forEach((el) => {
    el.addEventListener("pointermove", (e) => {
      const r = el.getBoundingClientRect();
      const x = (e.clientX - r.left - r.width / 2) * 0.25;
      const y = (e.clientY - r.top - r.height / 2) * 0.35;
      anime({ targets: el, translateX: x, translateY: y, duration: 300, easing: "easeOutQuad" });
    });
    el.addEventListener("pointerleave", () =>
      anime({ targets: el, translateX: 0, translateY: 0, duration: 700, easing: "easeOutElastic(1, .45)" })
    );
  });
}

// ---------- WebGL fields ----------
startField(document.getElementById("field"), { bias: 1, still: reduce });
startField(document.getElementById("field2"), { bias: 1, still: reduce });

// ---------- Kinetic type ----------
function splitChars(el) {
  const text = el.textContent;
  el.textContent = "";
  text.split(" ").forEach((word, wi, arr) => {
    const w = document.createElement("span");
    w.className = "word";
    for (const ch of word) {
      const c = document.createElement("span");
      c.className = "char";
      c.textContent = ch;
      w.appendChild(c);
    }
    el.appendChild(w);
    if (wi < arr.length - 1) el.appendChild(document.createTextNode(" "));
  });
  return el.querySelectorAll(".char");
}

function splitWords(el) {
  el.innerHTML = el.textContent
    .split(" ")
    .map((w) => `<span class="w"><span>${w}</span></span>`)
    .join(" ");
}

const heroChars = [...document.querySelectorAll("[data-split]")].flatMap((el) => [...splitChars(el)]);
document.querySelectorAll("[data-words]").forEach(splitWords);

if (!reduce) {
  anime
    .timeline({ easing: "easeOutExpo" })
    .add({ targets: heroChars, translateY: ["110%", "0%"], rotate: [12, 0], duration: 1100, delay: anime.stagger(22) })
    .add({ targets: "[data-reveal]", opacity: [0, 1], translateY: [16, 0], duration: 900, delay: anime.stagger(120) }, "-=700");

  // Letters lean toward the pointer, then settle: the headline reacts like type set in motion
  const hero = document.getElementById("hero");
  hero.addEventListener("pointermove", (e) => {
    heroChars.forEach((c) => {
      const r = c.getBoundingClientRect();
      const dx = e.clientX - (r.left + r.width / 2);
      const dy = e.clientY - (r.top + r.height / 2);
      const d = Math.hypot(dx, dy);
      const pull = Math.max(0, 1 - d / 180);
      c.style.transform = `translate(${(-dx * pull * 0.08).toFixed(1)}px, ${(-dy * pull * 0.12).toFixed(1)}px)`;
    });
  });
  hero.addEventListener("pointerleave", () =>
    anime({ targets: heroChars, translateX: 0, translateY: 0, duration: 600, easing: "easeOutElastic(1, .6)" })
  );
}
// Safety net: never leave hero content hidden if an animation is interrupted
setTimeout(() => {
  document.querySelectorAll("[data-reveal]").forEach((el) => (el.style.opacity = 1));
  anime.remove(heroChars);
  heroChars.forEach((c) => (c.style.transform = "none"));
}, reduce ? 0 : 3200);

// Cycling phrase, swapped letter by letter
const phrases = ["fewer stockouts.", "fuller trucks.", "less expired stock.", "decisions you can defend."];
const cycler = document.getElementById("cycler");
let pi = 0;
function setPhrase(text, animateIn) {
  cycler.innerHTML = [...text].map((ch) => `<span class="char">${ch}</span>`).join("");
  if (animateIn && !reduce) {
    anime({ targets: cycler.querySelectorAll(".char"), translateY: ["100%", "0%"], opacity: [0, 1], duration: 600, delay: anime.stagger(18), easing: "easeOutExpo" });
  }
}
setPhrase(phrases[0], false);
if (!reduce) {
  setInterval(() => {
    anime({
      targets: cycler.querySelectorAll(".char"),
      translateY: ["0%", "-100%"],
      opacity: [1, 0],
      duration: 380,
      delay: anime.stagger(12),
      easing: "easeInQuad",
      complete: () => setPhrase(phrases[(pi = (pi + 1) % phrases.length)], true),
    });
  }, 2800);
}

// Word reveal when headings scroll into view
const wordObserver = new IntersectionObserver(
  (entries) =>
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      wordObserver.unobserve(e.target);
      if (reduce) return;
      anime({ targets: e.target.querySelectorAll(".w > span"), translateY: ["105%", "0%"], duration: 900, delay: anime.stagger(55), easing: "easeOutExpo" });
    }),
  { threshold: 0.4 }
);
document.querySelectorAll("[data-words]").forEach((el) => wordObserver.observe(el));

// ---------- Scroll engine: parallax, pinned route, marquee velocity, nav state ----------
const nav = document.getElementById("nav");
const heroInner = document.querySelector("[data-parallax]");
const route = document.getElementById("route");
const layers = [...document.querySelectorAll("#scene .layer")];
const trucks = [...document.querySelectorAll("#trucks .truck")];
const captions = [...document.querySelectorAll("#captions li")];
const routeBar = document.getElementById("routeBar");
const marquee = document.getElementById("marquee");
let lastY = window.scrollY, velocity = 0, marqueeX = 0, skew = 0;

function onScroll() {
  const y = window.scrollY;
  nav.classList.toggle("scrolled", y > 40);
  if (reduce) return;

  // Hero text drifts up slower than the page
  heroInner.style.transform = `translate3d(0, ${y * Number(heroInner.dataset.parallax)}px, 0)`;

  // Pinned route: progress 0..1 across the section's scroll length
  const r = route.getBoundingClientRect();
  const span = route.offsetHeight - window.innerHeight;
  const prog = Math.min(1, Math.max(0, -r.top / span));
  const w = window.innerWidth;
  layers.forEach((l) => (l.style.transform = `translate3d(${-prog * w * 1.3 * Number(l.dataset.depth)}px, 0, 0)`));
  trucks.forEach((t, i) => {
    const p = Math.min(1, Math.max(0, prog * 1.15 - i * 0.12));
    const bounce = Math.sin(p * 60) * 1.5;
    t.style.transform = `translate3d(${-200 + p * (w + 260)}px, ${bounce}px, 0)`;
    // Load empties as the truck reaches stores along the way
    t.querySelector(".load").setAttribute("width", (84 * (1 - Math.max(0, p - 0.35) / 0.65)).toFixed(1));
  });
  const step = Math.min(captions.length - 1, Math.floor(prog * captions.length));
  captions.forEach((c, i) => c.classList.toggle("on", i === step));
  routeBar.style.width = `${prog * 100}%`;
}

function tick() {
  const y = window.scrollY;
  velocity = velocity * 0.85 + (y - lastY) * 0.15;
  lastY = y;
  if (!reduce) {
    // Marquee drifts constantly and speeds up, and skews, with scroll velocity
    marqueeX -= 0.6 + Math.abs(velocity) * 0.5;
    const half = marquee.scrollWidth / 2;
    if (-marqueeX > half) marqueeX += half;
    skew += (Math.max(-12, Math.min(12, velocity * 0.4)) - skew) * 0.15;
    marquee.style.transform = `translate3d(${marqueeX}px, 0, 0) skewX(${-skew}deg)`;
  }
  requestAnimationFrame(tick);
}
window.addEventListener("scroll", onScroll, { passive: true });
window.addEventListener("resize", onScroll);
onScroll();
requestAnimationFrame(tick);

// ---------- How it works: sticky stage word swaps as each stage scrolls past ----------
const howWord = document.getElementById("howWord");
const howMeter = document.getElementById("howMeter");
const stages = [...document.querySelectorAll(".how-list li")];
let currentStage = "";
const stageObserver = new IntersectionObserver(
  (entries) =>
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      const name = e.target.dataset.stage;
      stages.forEach((s) => s.classList.toggle("active", s === e.target));
      howMeter.style.width = `${((stages.indexOf(e.target) + 1) / stages.length) * 100}%`;
      if (name === currentStage) return;
      currentStage = name;
      if (reduce) return (howWord.textContent = name);
      anime({
        targets: howWord, translateY: ["0%", "-110%"], duration: 220, easing: "easeInQuad",
        complete: () => {
          howWord.textContent = name;
          anime({ targets: howWord, translateY: ["110%", "0%"], duration: 520, easing: "easeOutExpo" });
        },
      });
      haptic(6);
    }),
  { rootMargin: "-45% 0px -45% 0px" }
);
stages.forEach((s) => stageObserver.observe(s));

// ---------- Results: real figures from a recorded engine run, counted up on view ----------
const fmtPct = (v) => `${v.toFixed(1)}%`;
const fmtInr = (v) => `₹${v.toFixed(2)}`;
const fmtInt = (v) => `${Math.round(v)}`;

async function loadResults() {
  const rows = document.getElementById("resultRows");
  const note = document.getElementById("resultsNote");
  try {
    const [stream, backtest] = await Promise.all([
      fetch("/recorded/stream.json").then((r) => r.json()),
      fetch("/recorded/backtest.json").then((r) => r.json()),
    ]);
    const plan = stream.at(-1).info;
    const k = plan.kpis, b = plan.baseline_kpis;
    const A = backtest.variants.find((v) => v.id === "A").metrics;
    const D = backtest.variants.find((v) => v.id === "D").metrics;
    const data = [
      { value: k.projected_fill_rate * 100, base: b.projected_fill_rate * 100, fmt: fmtPct, max: 100,
        label: "of expected demand met", sub: `${Math.round(b.unmet_demand_units - k.unmet_demand_units).toLocaleString("en-IN")} more units on shelves this week` },
      { value: k.freight_per_unit_inr, base: b.freight_per_unit_inr, fmt: fmtInr, max: Math.max(k.freight_per_unit_inr, b.freight_per_unit_inr),
        label: "freight per unit delivered", sub: "Fuller trucks on better lanes" },
      { value: D.fefo_violations.mean, base: A.fefo_violations.mean, fmt: fmtInt, max: A.fefo_violations.mean,
        label: "batches shipped out of expiry order", sub: `Pro-rata ships ${A.fefo_violations.mean} out of order across the network` },
    ];
    note.textContent = "Figures from a recorded run of the engine on the simulated network. Pro-rata gets the same stock and forecasts.";
    rows.innerHTML = data
      .map((d, i) => `<div class="result-row" data-i="${i}">
        <div><div class="result-num" data-count>${d.fmt(0)}</div><div class="result-label">${d.label}</div><div class="result-sub">${d.sub}</div></div>
        <div class="compare">
          <div class="cmp us"><span>OptiRoute</span><span class="bar"><i data-w="${Math.max(2, (d.value / d.max) * 100)}"></i></span><b>${d.fmt(d.value)}</b></div>
          <div class="cmp"><span>Pro-rata</span><span class="bar"><i data-w="${Math.max(2, (d.base / d.max) * 100)}"></i></span><b>${d.fmt(d.base)}</b></div>
        </div>
      </div>`)
      .join("");

    const obs = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (!e.isIntersecting) return;
          obs.unobserve(e.target);
          const d = data[e.target.dataset.i];
          const num = e.target.querySelector("[data-count]");
          if (reduce) {
            num.textContent = d.fmt(d.value);
            e.target.querySelectorAll(".bar i").forEach((b) => (b.style.width = `${b.dataset.w}%`));
            return;
          }
          const o = { v: d.base };
          anime({ targets: o, v: d.value, duration: 1600, easing: "easeOutExpo", update: () => (num.textContent = d.fmt(o.v)) });
          anime({ targets: e.target.querySelectorAll(".bar i"), width: (b) => `${b.dataset.w}%`, duration: 1200, delay: anime.stagger(150), easing: "easeOutCubic" });
        }),
      { threshold: 0.5 }
    );
    rows.querySelectorAll(".result-row").forEach((r) => obs.observe(r));
  } catch {
    note.textContent = "Results are available inside the planner.";
  }
}
loadResults();
