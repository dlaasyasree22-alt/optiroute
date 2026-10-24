// Single shared state for the planning session. Screens read it and call update().

export const state = {
  meta: null,
  request: null,
  plan: null,
  stages: null, // pipeline stage results from the last run
  risk: null,
  whyNot: [], // questions asked this session: {request, response}
  visited: new Set(),
  approved: null,
};

const subscribers = new Set();
export const subscribe = (fn) => subscribers.add(fn);
export function update(patch = {}) {
  Object.assign(state, patch);
  subscribers.forEach((fn) => fn(state));
}

export function resetPlan() {
  update({ plan: null, stages: null, risk: null, whyNot: [], approved: null, visited: new Set(["generate"]) });
}
