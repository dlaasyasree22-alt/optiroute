// Thin API client. One place decides live vs recorded data, so screens never care.
// Live: talks to the FastAPI engine. Recorded: replays a saved real run from /recorded
// when the engine cannot be reached, and the UI shows a banner saying so.

const BASE = window.OPTIROUTE_API_BASE || "/api/v1";
const listeners = new Set();
let recorded = false;

export class ApiError extends Error {
  constructor(message, kind = "server") {
    super(message);
    this.kind = kind; // "server" | "offline" | "infeasible" | "recorded"
  }
}

export const isRecorded = () => recorded;
export const onModeChange = (fn) => listeners.add(fn);

function setRecorded(value) {
  if (recorded === value) return;
  recorded = value;
  listeners.forEach((fn) => fn(recorded));
}

async function fetchJson(path, options = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      headers: { "content-type": "application/json" },
      ...options,
    });
  } catch {
    throw new ApiError("The planning engine could not be reached.", "offline");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : "The planning engine returned an error.";
    throw new ApiError(detail, res.status === 422 ? "infeasible" : "server");
  }
  return res.json();
}

const recordedCache = {};
async function loadRecorded(name) {
  if (!recordedCache[name]) {
    const res = await fetch(`/recorded/${name}.json`);
    recordedCache[name] = await res.json();
  }
  return recordedCache[name];
}

// Calls live; if the engine is unreachable, switches to recorded mode and retries from disk.
async function withFallback(liveCall, recordedCall) {
  if (!recorded) {
    try {
      return await liveCall();
    } catch (err) {
      if (err.kind !== "offline") throw err;
      setRecorded(true);
    }
  }
  return recordedCall();
}

export async function checkHealth() {
  try {
    await fetchJson("/health");
    setRecorded(false);
  } catch {
    setRecorded(true);
  }
  return !recorded;
}

export const getMeta = () => withFallback(() => fetchJson("/meta"), () => loadRecorded("meta"));
export const getBacktest = () => withFallback(() => fetchJson("/backtest"), () => loadRecorded("backtest"));
export const getRisk = (planId) =>
  withFallback(() => fetchJson(`/plan/${planId}/risk`), () => loadRecorded("risk"));

export const approvePlan = (planId) =>
  withFallback(
    () => fetchJson(`/plan/${planId}/approve`, { method: "POST" }),
    async () => ({ plan_id: planId, approved: true, approved_at: new Date().toLocaleString("en-IN") })
  );

export async function askWhyNot(planId, forced) {
  const body = { base_plan_id: planId, forced_constraint: { type: "FORCE_SHIPMENT", ...forced } };
  return withFallback(
    () => fetchJson("/plan/counterfactual", { method: "POST", body: JSON.stringify(body) }),
    async () => {
      const saved = await loadRecorded("counterfactual");
      const same = Object.keys(saved.request).every((k) => String(saved.request[k]) === String(forced[k]));
      if (!same) {
        const r = saved.request;
        throw new ApiError(
          `The engine is offline, so only the saved question can be replayed: ${r.min_units} units of ` +
            `${r.sku_id} from ${r.source_warehouse} to ${r.destination_store}.`,
          "recorded"
        );
      }
      await new Promise((ok) => setTimeout(ok, 1200));
      return saved.response;
    }
  );
}

// Streams pipeline stages. onEvent({stage, status, info}) fires as each real stage finishes.
export async function generatePlan(request, onEvent) {
  if (!recorded) {
    let res;
    try {
      res = await fetch(BASE + "/plan/generate/stream", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      });
    } catch {
      setRecorded(true);
    }
    if (res && !res.ok) throw new ApiError("The planning engine rejected the request.", "server");
    if (res) return readStream(res, onEvent);
  }
  return replayRecorded(onEvent);
}

async function readStream(res, onEvent) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop();
    for (const chunk of chunks) {
      if (!chunk.startsWith("data: ")) continue;
      const event = JSON.parse(chunk.slice(6));
      if (event.status === "error") throw new ApiError(event.info.message, event.stage === "optimise" ? "infeasible" : "server");
      if (event.stage === "result") result = event.info;
      else onEvent(event);
    }
  }
  if (!result) throw new ApiError("The engine stopped before finishing the plan.", "server");
  return result;
}

async function replayRecorded(onEvent) {
  const events = await loadRecorded("stream");
  let result = null;
  for (const event of events) {
    if (event.stage === "result") {
      result = event.info;
      continue;
    }
    // Replay at the recorded pace, capped so the demo stays snappy
    const wait = event.status === "done" ? Math.min(1800, 300 + (event.info.seconds || 0) * 250) : 120;
    await new Promise((ok) => setTimeout(ok, wait));
    onEvent(event);
  }
  return result;
}
