// Topographic contour field: domain-warped noise drawn as isolines, like a map of
// demand pressure across a region. Index lines every fifth contour, a slow pulse runs
// along them, and the field bends gently toward the pointer.

const VERT = `attribute vec2 p; void main() { gl_Position = vec4(p, 0.0, 1.0); }`;

const FRAG = `
precision highp float;
uniform vec2 u_res;
uniform float u_time;
uniform vec2 u_mouse;
uniform float u_bias;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 5; i++) { v += a * noise(p); p = p * 2.03 + 11.7; a *= 0.5; }
  return v;
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_res.y;
  vec2 m = u_mouse / u_res.y;
  float t = u_time * 0.035;

  // Domain warp, nudged by the pointer
  vec2 q = vec2(fbm(uv * 1.3 + vec2(0.0, t)), fbm(uv * 1.3 + vec2(5.2, 1.3) - t));
  vec2 toM = uv - m;
  q += 0.18 * exp(-dot(toM, toM) * 5.0);
  float f = fbm(uv * 1.5 + 2.2 * q);

  // Isolines with constant on-screen width via derivatives
  float levels = 26.0;
  float v = f * levels;
  float w = fwidth(v);
  float minor = 1.0 - smoothstep(0.4 * w, 1.3 * w, 0.5 - abs(fract(v) - 0.5));
  float vi = f * levels / 5.0;
  float wi = fwidth(vi);
  float major = 1.0 - smoothstep(0.9 * wi, 2.0 * wi, 0.5 - abs(fract(vi) - 0.5));

  // Slow pulse travelling along index lines, like trucks on a route
  float pulse = smoothstep(0.92, 1.0, sin(f * 40.0 - u_time * 1.4) * 0.5 + 0.5);

  vec3 paper = vec3(0.957, 0.953, 0.937);
  vec3 teal = vec3(0.082, 0.369, 0.388);
  float side = mix(1.0, smoothstep(0.15, 0.85, gl_FragCoord.x / u_res.x), u_bias);
  float ink = (minor * 0.14 + major * 0.32 + major * pulse * 0.4) * (0.35 + 0.65 * side);
  gl_FragColor = vec4(mix(paper, teal, clamp(ink, 0.0, 1.0)), 1.0);
}`;

export function startField(canvas, { bias = 1, still = false } = {}) {
  const gl = canvas.getContext("webgl", { antialias: false, premultipliedAlpha: false });
  if (!gl || !gl.getExtension("OES_standard_derivatives")) return null;

  const compile = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  };
  const prog = gl.createProgram();
  gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
  gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, "#extension GL_OES_standard_derivatives : enable\n" + FRAG));
  gl.linkProgram(prog);
  gl.useProgram(prog);

  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, "p");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

  const u = (n) => gl.getUniformLocation(prog, n);
  const uRes = u("u_res"), uTime = u("u_time"), uMouse = u("u_mouse"), uBias = u("u_bias");
  gl.uniform1f(uBias, bias);

  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  const mouse = { x: 0, y: 0, tx: 0, ty: 0 };
  let visible = true, raf = 0;
  const t0 = performance.now();

  function resize() {
    const r = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    gl.viewport(0, 0, canvas.width, canvas.height);
    mouse.x = mouse.tx = canvas.width * 0.72;
    mouse.y = mouse.ty = canvas.height * 0.5;
  }
  function frame(now) {
    // Ease the pointer so the field drifts instead of snapping
    mouse.x += (mouse.tx - mouse.x) * 0.06;
    mouse.y += (mouse.ty - mouse.y) * 0.06;
    gl.uniform2f(uRes, canvas.width, canvas.height);
    gl.uniform1f(uTime, still ? 8.0 : (now - t0) / 1000);
    gl.uniform2f(uMouse, mouse.x, mouse.y);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    if (!still && visible) raf = requestAnimationFrame(frame);
  }

  resize();
  window.addEventListener("resize", () => { resize(); if (still) frame(0); });
  canvas.parentElement.addEventListener("pointermove", (e) => {
    const r = canvas.getBoundingClientRect();
    mouse.tx = (e.clientX - r.left) * dpr;
    mouse.ty = (r.bottom - e.clientY) * dpr;
  });
  // Only render while on screen and the tab is visible
  new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting && !document.hidden;
    cancelAnimationFrame(raf);
    if (visible) raf = requestAnimationFrame(frame);
  }).observe(canvas);
  document.addEventListener("visibilitychange", () => {
    visible = !document.hidden;
    cancelAnimationFrame(raf);
    if (visible) raf = requestAnimationFrame(frame);
  });
  raf = requestAnimationFrame(frame);
  return true;
}
