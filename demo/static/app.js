// Horus offline demo: Overview landing page, then the Live Radar console.
// Every request goes to this origin (enforced by the server's CSP).
"use strict";

const CLASS_CENTRES = [50, 130, 250, 420]; // preview weights only (uncalibrated)
const PIREP_SHOW_S = 1800;
const TRAIL_MIN = 10;
const WINDOW_SAMPLES = 60; // inspector chart: trailing 10 min at 10 s stride
const DEFAULT_SCENARIO = "jet-cat";
const SHORT = { "jet-cat": "Jet Stream", convective: "Convective", calm: "Calm Cruise", "pirep-replay": "PIREP Replay" };
const PLANE_D = "M0,-8 L1.3,-4.6 L1.3,-1.4 L8,2.6 L8,4 L1.3,2.2 L1.1,5.6 L3.4,7.2 L3.4,8.2 L0,7.4 L-3.4,8.2 L-3.4,7.2 L-1.1,5.6 L-1.3,2.2 L-8,4 L-8,2.6 L-1.3,-1.4 L-1.3,-4.6 Z";
const PLANE = new Path2D(PLANE_D);
const UI_FONT = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Helvetica Neue', sans-serif";
// monochrome line icons for the pipeline stepper (drawn here; SF Symbols may not be used on the web)
const ICONS = {
  antenna: '<path d="M12 21V11"/><circle cx="12" cy="9" r="2"/><path d="M7.8 13.2a6 6 0 0 1 0-8.4M16.2 4.8a6 6 0 0 1 0 8.4M5 16a10 10 0 0 1 0-14M19 2a10 10 0 0 1 0 14"/>',
  archive: '<rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v9.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V9M10 13h4"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9.5h18M3 14.5h18M9 9.5V20"/>',
  wave: '<path d="M2 12h3l2-6 3 12 3-9 2 6 2-3h5"/>',
  gauge: '<path d="M4.5 17a8.5 8.5 0 1 1 15 0"/><path d="M12 13l4-4"/><circle cx="12" cy="13" r="1.3"/>',
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 0, unit = "") => {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = Number(v).toFixed(d);
  return `${/^-0(\.0*)?$/.test(s) ? s.slice(1) : s}${unit}`;
};
const mmss = (s) => `+${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const sub = (s) => esc(s).replace(/σ_corr/g, "σ<sub>corr</sub>").replace(/σ²_corr/g, "σ²<sub>corr</sub>").replace(/σ²_raw/g, "σ²<sub>raw</sub>");

// ---------------------------------------------------------------- state
const S = {
  view: "overview", radarReady: false,
  basemap: null, index: [], sc: null, about: null, overview: null,
  t: 0, playing: false, speed: 60, flMin: 180, flMax: 450,
  folds: { more: false, weights: false, pireps: true },
  selected: -1, pinned: null, filterCs: "", showPireps: true, showField: false, showTrail: true,
  vp: { scale: 1, ox: 0, oy: 0, w: 0, h: 0, dpr: 1 }, baseLayer: null, hover: -1, lastJ: -1, lastListI: -1,
};
const canvas = $("map");
const ctx = canvas.getContext("2d");

// ---------------------------------------------------------------- theme (radar is always dark)
let COL = {};
const REDUCE = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
document.documentElement.classList.add("js");

function readColors() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n) => cs.getPropertyValue(n).trim();
  COL = {
    ocean: v("--ocean"), land: v("--land"), landStroke: v("--land-stroke"), grid: v("--grid"),
    label: v("--label"), label2: v("--label-2"), label3: v("--label-3"), accent: v("--accent"),
    surface: v("--glass-strong"), hairline: v("--hairline"), halo: v("--halo"), na: v("--na"),
    sev: [v("--sev0"), v("--sev1"), v("--sev2"), v("--sev3")],
    dark: true,
  };
  COL.tb = { NEG: COL.na, LGT: COL.sev[1], MOD: COL.sev[2], SEV: COL.sev[3] };
}
function applyTheme() { readColors(); } // dark only

function rgba(color, a) {
  if (color.startsWith("#")) {
    const v = parseInt(color.slice(1), 16);
    return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`;
  }
  const m = color.match(/rgba?\(([^)]+)\)/);
  if (!m) return color;
  const [r, g, b] = m[1].split(",").map((x) => x.trim());
  return `rgba(${r},${g},${b},${a})`;
}

function placeThumb(seg) {
  const thumb = seg.querySelector(".seg-thumb");
  const on = seg.querySelector('button[aria-checked="true"], button[aria-selected="true"]');
  if (!thumb || !on || !seg.offsetParent) return;
  thumb.style.width = `${on.offsetWidth}px`;
  thumb.style.transform = `translateX(${on.offsetLeft}px)`;
}
const placeAllThumbs = () => { for (const s of document.querySelectorAll(".segmented")) placeThumb(s); };

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------- views
async function setView(v, { push = true } = {}) {
  S.view = v;
  document.documentElement.dataset.view = v;
  for (const b of $("views").querySelectorAll("button")) b.setAttribute("aria-selected", String(b.dataset.view === v));
  applyTheme();
  placeAllThumbs();
  if (push) { try { history.replaceState(null, "", v === "radar" ? "#view=radar" : "#"); } catch { /* file: */ } }
  window.scrollTo(0, 0);
  if (v === "radar") {
    if (!S.radarReady) { S.radarReady = true; await initRadar(); }
    else { fitView(); draw(); S.lastJ = -1; S.lastListI = -1; updateInspectorLive(); renderFlightList(); }
    stopPreview();
  } else {
    setPlaying(false);
    startPreview();
  }
}

// ---------------------------------------------------------------- overview (landing)
function renderOverview() {
  const O = S.overview;
  $("kpis").innerHTML = O.kpis.map((k, i) => `
    <article class="card kpi reveal" data-delay="${i}">
      <span class="k-value${k.value.length > 6 ? " long" : ""}" data-count="${esc(k.value)}">${esc(k.value)}${k.unit ? `<span class="k-unit">${esc(k.unit)}</span>` : ""}</span>
      <span class="k-short">${sub(k.short || "")}</span>
      <span class="k-label">${sub(k.label)}</span>
      <details><summary>What this shows</summary>
        <p class="k-detail">${sub(k.detail)}</p><p class="k-caveat">${sub(k.caveat)}</p></details>
    </article>`).join("");
  const cls = { built: "built", "core built": "core", planned: "planned" };
  $("pipeline").innerHTML = O.pipeline.map((p) => `
    <li class="step">
      <div class="ic"><svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[p.icon] || ""}</svg></div>
      <div><h3>${esc(p.title)}</h3><p>${esc(p.detail)}</p>
        <span class="st ${cls[p.status]}">${esc(p.status)}</span>${p.note ? `<span class="st-note">${esc(p.note)}</span>` : ""}</div>
    </li>`).join("");
  $("maths").innerHTML = O.maths.map((m) => `
    <div class="math${m.highlight ? " hl" : ""}"><h3>${esc(m.title)}</h3>
      <div class="formula">${sub(m.formula)}</div><div class="m-note">${sub(m.note)}</div></div>`).join("");
  const B = O.benchmarks;
  $("bench").innerHTML = `<thead><tr><th>Row</th><th>Method</th><th class="num-c">PR-AUC</th><th class="num-c">Recall @ 1 FA / 1,000 h</th><th>When</th></tr></thead>
    <tbody>${B.rows.map((r) => `<tr><td class="row-id">${esc(r[0])}</td><td>${sub(r[1])}</td>
      <td class="num-c pending">${r[2] === null ? "—" : esc(r[2])}</td><td class="num-c pending">${r[3] === null ? "—" : esc(r[3])}</td>
      <td><span class="st planned">${esc(r[4])}</span></td></tr>`).join("")}</tbody>`;
  const A = S.about;
  $("attribs").innerHTML = A.attributions.map((x) => `<p class="attrib"><b>${esc(x.name)}</b>${esc(x.text)}
      <br><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.url)}</a></p>`).join("") +
    `<p class="attrib">${esc(A.disclaimer)}</p>`;
  for (const i of document.querySelectorAll(".device-legend i")) i.style.background = COL.sev[+i.dataset.sev];
  observeReveals();
  setupPlayground();
  setupTilt();
}

// reveal-on-scroll (fade + rise) and a staged count-up for KPI numbers
function countUp(el) {
  const target = el.dataset.count, m = /^(\d+(?:\.\d+)?)(%?)$/.exec(target);
  if (!m || REDUCE) return;
  const end = parseFloat(m[1]), dec = (m[1].split(".")[1] || "").length;
  const t0 = performance.now(), dur = 1200;
  const step = (now) => {
    const f = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - f, 3);
    el.firstChild.nodeValue = f < 1 ? (end * e).toFixed(dec) + m[2] : target;
    if (f < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function observeReveals() {
  const pending = new Set(document.querySelectorAll(".reveal, .stepper"));
  const show = (el) => {
    if (!pending.delete(el)) return;
    setTimeout(() => { el.classList.add("in"); const v = el.querySelector("[data-count]"); if (v) countUp(v); }, +(el.dataset.delay || 0) * 110);
  };
  if (REDUCE) { pending.forEach((e) => e.classList.add("in")); return; }
  // direct geometry check (on load and scroll) so content never depends on observer callbacks alone
  const check = () => {
    const vh = window.innerHeight;
    for (const el of [...pending]) { const r = el.getBoundingClientRect(); if (r.top < vh * 0.94 && r.bottom > 0) show(el); }
    if (!pending.size) window.removeEventListener("scroll", check);
  };
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => entries.forEach((en) => { if (en.isIntersecting) { show(en.target); io.unobserve(en.target); } }),
      { threshold: 0.15, rootMargin: "0px 0px -5% 0px" });
    pending.forEach((e) => io.observe(e));
  }
  window.addEventListener("scroll", check, { passive: true });
  requestAnimationFrame(check);
  check();
}

// quantisation playground: EXPECTED values from Sheppard's approximation (labelled on the page)
function setupPlayground() {
  const step = 64, q = (step * step) / 12, input = $("sigmaIn"), max = 420;
  const update = () => {
    const s = +input.value, raw = Math.sqrt(s * s + q), corr = Math.sqrt(Math.max(raw * raw - q, 0));
    $("sigmaOut").textContent = `${s} ft/min`;
    $("pbTrue").style.width = `${(s / max) * 100}%`; $("pvTrue").textContent = `${s.toFixed(0)} ft/min`;
    $("pbRaw").style.width = `${(raw / max) * 100}%`; $("pvRaw").textContent = `${raw.toFixed(0)} ft/min`;
    $("pbCorr").style.width = `${(corr / max) * 100}%`; $("pvCorr").textContent = `${corr.toFixed(0)} ft/min`;
    const over = ((raw / s - 1) * 100).toFixed(0);
    $("playNote").textContent = s < 40
      ? `Rounding alone adds ${over}% here. Below ~40 ft/min the turbulence is smaller than one 64 ft/min step, so the rounding error stops acting like random noise and the correction over-corrects (seen in our synthetic tests). Small values just mean "near zero".`
      : `Rounding inflates the measurement by ${over}%. Subtracting Δ²/12 = ${q.toFixed(1)} (ft/min)² removes that bias.`;
  };
  input.addEventListener("input", update);
  update();
}

// hero device: gentle 3D tilt toward the pointer
function setupTilt() {
  const d = $("device");
  if (REDUCE || !d) return;
  d.addEventListener("pointermove", (e) => {
    const r = d.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    d.style.transform = `rotateX(${(-y * 5).toFixed(2)}deg) rotateY(${(x * 6).toFixed(2)}deg)`;
  });
  d.addEventListener("pointerleave", () => { d.style.transform = ""; });
}

// hero mini-radar: the Jet Stream scenario replayed at 120×, trails coloured by detected severity
const PV = { data: null, t: 0, raf: 0, last: 0, vp: null, base: null, visible: true };
function previewProject() {
  const cv = $("preview"), r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  if (r.width < 2) return false;
  cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr);
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const st of S.basemap.states) for (const ring of st.rings) for (const [lon, lat] of ring) {
    const [x, y] = ALB(lon, lat); x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  const pad = 18, sc = Math.min((r.width - 2 * pad) / (x1 - x0), (r.height - 2 * pad) / (y1 - y0));
  const ox = (r.width - (x1 - x0) * sc) / 2 - x0 * sc, oy = (r.height + (y1 - y0) * sc) / 2 + y0 * sc;
  const P = (lon, lat) => { const [x, y] = ALB(lon, lat); return [ox + x * sc, oy - y * sc]; };
  PV.vp = { w: r.width, h: r.height, dpr, P };
  const off = document.createElement("canvas"); off.width = cv.width; off.height = cv.height;
  const c = off.getContext("2d"); c.scale(dpr, dpr);
  const g = c.createLinearGradient(0, 0, 0, r.height); g.addColorStop(0, "#030918"); g.addColorStop(1, "#0a1836");
  c.fillStyle = g; c.fillRect(0, 0, r.width, r.height);
  const land = new Path2D();
  for (const st of S.basemap.states) for (const ring of st.rings) {
    ring.forEach(([lon, lat], i) => { const [x, y] = P(lon, lat); i ? land.lineTo(x, y) : land.moveTo(x, y); }); land.closePath();
  }
  c.fillStyle = "rgba(30, 48, 84, 0.55)"; c.fill(land); c.strokeStyle = "rgba(120, 170, 240, 0.22)"; c.lineWidth = 0.6; c.stroke(land);
  PV.base = off;
  for (const a of PV.data.aircraft) {
    const n = a.cols.t.length; a.px = new Float32Array(n); a.py = new Float32Array(n);
    for (let i = 0; i < n; i++) { const [x, y] = P(a.cols.lon[i], a.cols.lat[i]); a.px[i] = x; a.py[i] = y; }
  }
  return true;
}
function drawPreview() {
  const cv = $("preview"), g = cv.getContext("2d"), { dpr } = PV.vp, D = PV.data;
  g.setTransform(1, 0, 0, 1, 0, 0); g.drawImage(PV.base, 0, 0); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const i = Math.min(D.aircraft[0].cols.t.length - 1, Math.floor(PV.t / D.stride_s)), trail = 36;
  g.lineCap = "round";
  for (const a of D.aircraft) {
    for (let j = Math.max(1, i - trail); j <= i; j++) {
      const s = a.cols.sev[j], col = s < 0 ? COL.na : COL.sev[s];
      g.strokeStyle = rgba(col, 0.15 + 0.85 * (1 - (i - j) / trail)); g.lineWidth = s >= 2 ? 2.6 : 1.8;
      g.beginPath(); g.moveTo(a.px[j - 1], a.py[j - 1]); g.lineTo(a.px[j], a.py[j]); g.stroke();
    }
    const s = a.cols.sev[i], col = s < 0 ? COL.na : COL.sev[s];
    if (s >= 2) { g.beginPath(); g.arc(a.px[i], a.py[i], 9, 0, 2 * Math.PI); g.fillStyle = rgba(col, 0.18); g.fill(); }
    g.save(); g.translate(a.px[i], a.py[i]); g.rotate((a.cols.hdg[i] * Math.PI) / 180); g.scale(0.8, 0.8);
    g.lineWidth = 2.4; g.strokeStyle = "rgba(2,5,13,.9)"; g.stroke(PLANE); g.fillStyle = col; g.fill(PLANE); g.restore();
  }
  $("previewClock").textContent = mmss(PV.t);
}
function previewTick(ts) {
  if (!PV.visible || S.view !== "overview") { PV.raf = 0; return; }
  if (PV.last) PV.t = (PV.t + ((ts - PV.last) / 1000) * 120) % (PV.data.duration_s - PV.data.stride_s);
  PV.last = ts; drawPreview();
  PV.raf = requestAnimationFrame(previewTick);
}
function startPreview() {
  if (!PV.data || PV.raf || !PV.vp) return;
  PV.last = 0;
  if (REDUCE) { drawPreview(); return; }
  PV.raf = requestAnimationFrame(previewTick);
}
function stopPreview() { if (PV.raf) cancelAnimationFrame(PV.raf); PV.raf = 0; }
async function initPreview() {
  PV.data = await getJSON(`/api/scenario/${DEFAULT_SCENARIO}`);
  PV.t = 1500;
  if (!previewProject()) return;
  drawPreview();
  new IntersectionObserver((es) => es.forEach((e) => { PV.visible = e.isIntersecting; if (PV.visible) startPreview(); })).observe($("preview"));
  startPreview();
}

// ---------------------------------------------------------------- projection (Albers equal-area conic, CONUS)
const ALB = (() => {
  const r = Math.PI / 180, p1 = 29.5 * r, p2 = 45.5 * r, p0 = 37.5 * r, l0 = -96 * r;
  const n = (Math.sin(p1) + Math.sin(p2)) / 2;
  const C = Math.cos(p1) ** 2 + 2 * n * Math.sin(p1);
  const rho0 = Math.sqrt(C - 2 * n * Math.sin(p0)) / n;
  return (lon, lat) => {
    const rho = Math.sqrt(C - 2 * n * Math.sin(lat * r)) / n;
    const th = n * (lon * r - l0);
    return [rho * Math.sin(th), rho0 - rho * Math.cos(th)];
  };
})();
const KM_PER_DEG = 111.195;
function haversineKm(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180, dlat = (lat2 - lat1) * r, dlon = (lon2 - lon1) * r;
  const a = Math.sin(dlat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dlon / 2) ** 2;
  return 2 * 6371.0088 * Math.asin(Math.sqrt(a));
}
function offset(lat, lon, eKm, nKm) {
  return [lat + nKm / KM_PER_DEG, lon + eKm / (KM_PER_DEG * Math.cos((lat * Math.PI) / 180))];
}

// ---------------------------------------------------------------- radar layout
function overlayRects() {
  const cr = canvas.getBoundingClientRect();
  const out = [];
  for (const el of [document.querySelector(".toolbar"), $("scenarios"), $("flightPane"), document.querySelector(".capsule"), $("inspector")]) {
    const pos = getComputedStyle(el).position;
    if (pos !== "absolute" && pos !== "fixed") continue;
    const r = el.getBoundingClientRect();
    out.push({ x: r.left - cr.left, y: r.top - cr.top, w: r.width, h: r.height, el });
  }
  return out;
}
function fitView() {
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 2) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const st of S.basemap.states) for (const ring of st.rings) for (const [lon, lat] of ring) {
    const [x, y] = ALB(lon, lat);
    x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  let L = 16, T = 16, R = rect.width - 16, B = rect.height - 16;
  for (const o of overlayRects()) {
    if (o.el.classList.contains("toolbar") || o.el.id === "scenarios") T = Math.max(T, o.y + o.h + 14);
    else if (o.el.id === "flightPane") L = Math.max(L, o.x + o.w + 16);
    else if (o.el.id === "inspector") R = Math.min(R, o.x - 16);
    else if (o.el.classList.contains("capsule")) B = Math.min(B, o.y - 12);
  }
  const scale = Math.min((R - L) / (x1 - x0), (B - T) / (y1 - y0));
  S.vp = {
    scale, dpr, w: rect.width, h: rect.height,
    ox: L + (R - L - (x1 - x0) * scale) / 2 - x0 * scale,
    oy: T + (B - T + (y1 - y0) * scale) / 2 + y0 * scale,
  };
  buildBaseLayer();
  if (S.sc) projectScenario();
}
function toScreen(lon, lat) {
  const [x, y] = ALB(lon, lat);
  return [S.vp.ox + x * S.vp.scale, S.vp.oy - y * S.vp.scale];
}
function buildBaseLayer() {
  const { w, h, dpr } = S.vp;
  const off = document.createElement("canvas");
  off.width = Math.max(1, Math.round(w * dpr)); off.height = Math.max(1, Math.round(h * dpr));
  const c = off.getContext("2d");
  c.scale(dpr, dpr);
  c.fillStyle = COL.ocean; c.fillRect(0, 0, w, h);
  c.strokeStyle = COL.grid; c.lineWidth = 1;
  for (let lat = 20; lat <= 55; lat += 5) {
    c.beginPath();
    for (let lon = -135; lon <= -55; lon += 1) { const [x, y] = toScreen(lon, lat); lon === -135 ? c.moveTo(x, y) : c.lineTo(x, y); }
    c.stroke();
  }
  for (let lon = -130; lon <= -60; lon += 5) {
    c.beginPath();
    for (let lat = 18; lat <= 56; lat += 1) { const [x, y] = toScreen(lon, lat); lat === 18 ? c.moveTo(x, y) : c.lineTo(x, y); }
    c.stroke();
  }
  const land = new Path2D();
  for (const st of S.basemap.states) for (const ring of st.rings) {
    ring.forEach(([lon, lat], i) => { const [x, y] = toScreen(lon, lat); i ? land.lineTo(x, y) : land.moveTo(x, y); });
    land.closePath();
  }
  c.fillStyle = COL.land; c.fill(land);
  c.strokeStyle = COL.landStroke; c.lineWidth = 0.6; c.stroke(land);
  S.baseLayer = off;
}

// ---------------------------------------------------------------- scenario
function projectScenario() {
  for (const a of S.sc.aircraft) {
    const c = a.cols, n = c.t.length;
    a.x = new Float32Array(n); a.y = new Float32Array(n);
    for (let i = 0; i < n; i++) { const [x, y] = toScreen(c.lon[i], c.lat[i]); a.x[i] = x; a.y[i] = y; }
  }
  for (const p of S.sc.pireps) { const [x, y] = toScreen(p.lon, p.lat); p.x = x; p.y = y; }
}
function mostTurbulent() {
  // the flight with the highest sustained σ_corr (70 s mean, ≥ 5 of 7 samples), and when:
  // a single 15 s estimate can spike, so one sample would overstate the turbulence
  let best = { k: 0, j: 0, v: -1 };
  S.sc.aircraft.forEach((a, k) => {
    const sc = a.cols.sc;
    for (let j = 3; j < sc.length - 3; j++) {
      let sum = 0, n = 0;
      for (let q = j - 3; q <= j + 3; q++) if (sc[q] !== null) { sum += sc[q]; n += 1; }
      if (n >= 5 && sum / n > best.v) best = { k, j, v: sum / n };
    }
  });
  return best;
}
async function loadScenario(id, { autoSelect = true } = {}) {
  S.sc = await getJSON(`/api/scenario/${encodeURIComponent(id)}`);
  S.selected = -1; S.pinned = null; S.filterCs = ""; S.lastJ = -1; S.lastListI = -1;
  projectScenario();
  $("callsign").innerHTML = '<option value="">All Flights</option>' + S.sc.aircraft.map((a, i) => ({ a, i }))
    .sort((p, q) => p.a.callsign.localeCompare(q.a.callsign))
    .map(({ a, i }) => `<option value="${i}">${esc(a.callsign)}</option>`).join("");
  $("scrub").max = String(S.sc.duration_s - S.sc.stride_s);
  for (const b of $("scenarios").querySelectorAll("button")) b.setAttribute("aria-selected", String(b.dataset.id === id));
  placeThumb($("scenarios"));
  renderScenarioCard();
  if (autoSelect) {
    const m = mostTurbulent();
    S.t = S.sc.aircraft[m.k].cols.t[m.j];
    select(m.k);
  } else {
    S.t = Math.min(1200, S.sc.duration_s - S.sc.stride_s);
    renderInspector(); draw();
  }
  renderFlightList();
}
function renderScenarioCard() {
  const sv = S.sc.severity;
  $("scenarioCard").innerHTML = `
    <h3>${esc(S.sc.title)}</h3>
    <p>${esc(S.sc.blurb)}</p>
    <div class="legend" title="σ_corr thresholds ${sv.edges.join(" / ")} ft/min — placeholders until M3">
      ${sv.labels.map((l, i) => `<span><i class="dot" data-sev="${i}"></i>${esc(l)}</span>`).join("")}
      <span><i class="dot" data-sev="na"></i>Climbing</span>
      <span><i class="ring" data-ring="1"></i>PIREP</span>
    </div>`;
  for (const d of $("scenarioCard").querySelectorAll("[data-sev]")) d.style.background = d.dataset.sev === "na" ? COL.na : COL.sev[+d.dataset.sev];
  for (const d of $("scenarioCard").querySelectorAll("[data-ring]")) { d.style.borderColor = COL.sev[2]; d.style.background = rgba(COL.sev[2], 0.18); }
}

// ---------------------------------------------------------------- flight strips
function renderFlightList() {
  if (!S.sc) return;
  const i = idxAt(S.t);
  if (i === S.lastListI && !renderFlightList.force) return;
  renderFlightList.force = false;
  S.lastListI = i;
  const rows = S.sc.aircraft.map((a, k) => ({ a, k, v: a.cols.sc[i], sev: a.cols.sev[i], alt: a.cols.alt[i] }))
    .sort((p, q) => (q.v ?? -1) - (p.v ?? -1) || p.a.callsign.localeCompare(q.a.callsign));
  $("flightList").innerHTML = rows.map((r) => `
    <li role="option" data-k="${r.k}" aria-selected="${r.k === S.selected}" class="${inFL(r.alt) ? "" : "out"}">
      <i class="dot" data-sev="${r.sev}"></i><span class="cs">${esc(r.a.callsign)}</span>
      <span class="fl">FL${fmt(r.alt / 100, 0)}</span><span class="word" data-sev="${r.sev}">${r.sev < 0 ? "Climbing" : esc(S.sc.severity.labels[r.sev])}</span></li>`).join("");
  for (const d of $("flightList").querySelectorAll(".dot")) d.style.background = sevColor(+d.dataset.sev);
  for (const w of $("flightList").querySelectorAll(".word")) if (+w.dataset.sev >= 1) w.style.color = sevColor(+w.dataset.sev);
}
$("inspector").addEventListener("toggle", (e) => {
  const f = e.target.dataset && e.target.dataset.fold;
  if (f) S.folds[f] = e.target.open;
}, true);
$("flightList").addEventListener("click", (e) => {
  const li = e.target.closest("li");
  if (!li) return;
  $("callsign").value = ""; S.filterCs = "";
  select(+li.dataset.k);
});

// ---------------------------------------------------------------- drawing
const nSamples = () => S.sc.aircraft[0].cols.t.length;
const idxAt = (t) => Math.min(nSamples() - 1, Math.max(0, Math.floor(t / S.sc.stride_s)));
const inFL = (alt) => alt !== null && alt / 100 >= S.flMin - 5 && alt / 100 <= S.flMax + 5;
const sevColor = (s) => (s < 0 ? COL.na : COL.sev[s]);

function drawField(t) {
  for (const p of S.sc.patches) {
    if (p.fl_hi < S.flMin || p.fl_lo > S.flMax) continue;
    const [clat, clon] = offset(p.lat, p.lon, (p.u_kmh * t) / 3600, (p.v_kmh * t) / 3600);
    const th = (p.angle_deg * Math.PI) / 180;
    const col = COL.sev[p.sigma >= 320 ? 3 : p.sigma >= 180 ? 2 : p.sigma >= 90 ? 1 : 0];
    for (const [k, alpha, stroke] of [[1.0, 0.08, true], [0.62, 0.1, false], [0.32, 0.12, false]]) {
      ctx.beginPath();
      for (let j = 0; j <= 56; j++) {
        const a = (j / 56) * 2 * Math.PI;
        const along = Math.cos(a) * p.a_km * k, across = Math.sin(a) * p.b_km * k;
        const [la, lo] = offset(clat, clon, along * Math.sin(th) + across * Math.cos(th), along * Math.cos(th) - across * Math.sin(th));
        const [x, y] = toScreen(lo, la);
        j ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.closePath();
      ctx.fillStyle = rgba(col, alpha); ctx.fill();
      if (stroke) { ctx.strokeStyle = rgba(col, 0.4); ctx.lineWidth = 1; ctx.stroke(); }
    }
  }
}
function roundRect(x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
const overlap = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

function drawCallouts(i, visible) {
  const order = [];
  if (S.selected >= 0 && visible.includes(S.selected)) order.push(S.selected);
  if (S.hover >= 0 && S.hover !== S.selected && visible.includes(S.hover)) order.push(S.hover);
  visible.filter((k) => !order.includes(k) && S.sc.aircraft[k].cols.sev[i] >= 2)
    .sort((p, q) => (S.sc.aircraft[q].cols.sc[i] ?? 0) - (S.sc.aircraft[p].cols.sc[i] ?? 0))
    .slice(0, 8).forEach((k) => order.push(k));
  const blocked = overlayRects().map(({ x, y, w, h }) => ({ x: x - 4, y: y - 4, w: w + 8, h: h + 8 }));
  const glyphs = visible.map((k) => ({ k, x: S.sc.aircraft[k].x[i] - 9, y: S.sc.aircraft[k].y[i] - 9, w: 18, h: 18 }));
  const placed = [];
  const bold = "600 11px 'SF Mono', ui-monospace, Menlo, monospace", reg = "500 11px 'SF Mono', ui-monospace, Menlo, monospace";
  for (const k of order) {
    const a = S.sc.aircraft[k], c = a.cols, x = a.x[i], y = a.y[i];
    const t1 = a.callsign, t2 = ` FL${Math.round(c.alt[i] / 100)}`;
    ctx.font = bold; const w1 = ctx.measureText(t1).width;
    ctx.font = reg; const w2 = ctx.measureText(t2).width;
    const w = 26 + w1 + w2, h = 20;
    const cands = [[14, -28], [14, 8], [-w - 14, -28], [-w - 14, 8], [-w / 2, -36], [-w / 2, 16]];
    let spot = null;
    for (const [dx, dy] of cands) {
      const r = { x: x + dx, y: y + dy, w, h };
      if (r.x < 4 || r.y < 4 || r.x + w > S.vp.w - 4 || r.y + h > S.vp.h - 4) continue;
      if (blocked.some((b) => overlap(r, b)) || placed.some((p) => overlap(r, p))) continue;
      if (glyphs.some((g) => g.k !== k && overlap(r, g))) continue;
      spot = r; break;
    }
    if (!spot && (k === S.selected || k === S.hover)) spot = { x: x + cands[0][0], y: y + cands[0][1], w, h };
    if (!spot) continue;
    placed.push(spot);
    const ax = Math.max(spot.x, Math.min(x, spot.x + w)), ay = Math.max(spot.y, Math.min(y, spot.y + h));
    ctx.strokeStyle = rgba(COL.label, 0.3); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(ax, ay); ctx.stroke();
    roundRect(spot.x, spot.y, w, h, 10); ctx.fillStyle = COL.surface; ctx.fill();
    ctx.strokeStyle = k === S.selected ? COL.accent : COL.hairline; ctx.lineWidth = k === S.selected ? 1.2 : 0.5; ctx.stroke();
    ctx.fillStyle = sevColor(c.sev[i]); ctx.beginPath(); ctx.arc(spot.x + 11, spot.y + h / 2, 3.5, 0, 2 * Math.PI); ctx.fill();
    ctx.textBaseline = "middle";
    ctx.font = bold; ctx.fillStyle = COL.label; ctx.fillText(t1, spot.x + 19, spot.y + h / 2 + 0.5);
    ctx.font = reg; ctx.fillStyle = COL.label2; ctx.fillText(t2, spot.x + 19 + w1, spot.y + h / 2 + 0.5);
    ctx.textBaseline = "alphabetic";
  }
}

function draw() {
  if (S.view !== "radar" || !S.basemap || !S.baseLayer) return;
  const { dpr } = S.vp;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.drawImage(S.baseLayer, 0, 0);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!S.sc) return;
  const i = idxAt(S.t);
  if (S.showField) drawField(S.t);

  const trailN = Math.round((TRAIL_MIN * 60) / S.sc.stride_s);
  const focus = S.filterCs === "" ? -1 : +S.filterCs;
  ctx.lineCap = "round"; ctx.lineJoin = "round";
  S.sc.aircraft.forEach((a, k) => {
    const c = a.cols, dim = focus >= 0 && k !== focus;
    if (k === S.selected || k === focus) {
      ctx.strokeStyle = rgba(COL.label, 0.2); ctx.lineWidth = 1.2; ctx.setLineDash([2, 4]); ctx.beginPath();
      for (let j = 0; j < c.t.length; j++) j ? ctx.lineTo(a.x[j], a.y[j]) : ctx.moveTo(a.x[j], a.y[j]);
      ctx.stroke(); ctx.setLineDash([]);
    }
    if (!S.showTrail && k !== S.selected) return;
    ctx.lineWidth = k === S.selected ? 3.4 : 2.4;
    for (let j = Math.max(1, i - trailN); j <= i; j++) {
      if (!inFL(c.alt[j])) continue;
      const fade = 0.25 + 0.75 * (1 - (i - j) / Math.max(1, trailN));
      ctx.strokeStyle = rgba(sevColor(c.sev[j]), dim ? 0.12 : fade);
      ctx.beginPath(); ctx.moveTo(a.x[j - 1], a.y[j - 1]); ctx.lineTo(a.x[j], a.y[j]); ctx.stroke();
    }
  });

  if (S.showPireps) {
    for (const p of S.sc.pireps) {
      const age = S.t - p.t;
      if (age < 0 || age > PIREP_SHOW_S || p.fl < S.flMin || p.fl > S.flMax) continue;
      const a = 1 - (age / PIREP_SHOW_S) * 0.65, col = COL.tb[p.tb];
      ctx.beginPath(); ctx.arc(p.x, p.y, 11, 0, 2 * Math.PI);
      ctx.fillStyle = rgba(col, 0.14 * a); ctx.fill();
      ctx.strokeStyle = rgba(col, 0.85 * a); ctx.lineWidth = 1.6; ctx.stroke();
      ctx.beginPath(); ctx.arc(p.x, p.y, 2.6, 0, 2 * Math.PI); ctx.fillStyle = rgba(col, a); ctx.fill();
    }
  }

  if (S.selected >= 0) {
    const a = S.sc.aircraft[S.selected], j = S.pinned ?? i;
    const lat = a.cols.lat[j], lon = a.cols.lon[j];
    const [x, y] = toScreen(lon, lat);
    const rpx = Math.abs(toScreen(offset(lat, lon, S.sc.match.dist_km, 0)[1], lat)[0] - x);
    ctx.beginPath(); ctx.arc(x, y, rpx, 0, 2 * Math.PI);
    ctx.fillStyle = rgba(COL.accent, 0.07); ctx.fill();
    ctx.setLineDash([4, 4]); ctx.strokeStyle = rgba(COL.accent, 0.85); ctx.lineWidth = 1.2; ctx.stroke(); ctx.setLineDash([]);
    if (S.pinned !== null) { ctx.beginPath(); ctx.arc(x, y, 3.5, 0, 2 * Math.PI); ctx.fillStyle = COL.accent; ctx.fill(); }
  }

  const visible = [];
  S.sc.aircraft.forEach((a, k) => {
    const c = a.cols;
    if (!inFL(c.alt[i])) return;
    visible.push(k);
    const dim = focus >= 0 && k !== focus, sel = k === S.selected;
    ctx.save(); ctx.translate(a.x[i], a.y[i]); ctx.rotate((c.hdg[i] * Math.PI) / 180);
    if (sel) { ctx.beginPath(); ctx.arc(0, 0, 15, 0, 2 * Math.PI); ctx.fillStyle = rgba(COL.accent, 0.22); ctx.fill(); ctx.scale(1.25, 1.25); }
    ctx.lineWidth = 2.4; ctx.strokeStyle = dim ? rgba(COL.halo, 0.3) : COL.halo; ctx.stroke(PLANE);
    ctx.fillStyle = rgba(sevColor(c.sev[i]), dim ? 0.3 : 1); ctx.fill(PLANE);
    ctx.restore();
  });
  drawCallouts(i, visible);

  $("clock").textContent = mmss(S.t);
  $("scrub").value = String(Math.round(S.t));
  renderFlightList();
  if (S.selected >= 0 && S.pinned === null) updateInspectorLive();
}

// ---------------------------------------------------------------- inspector
function classWeights(sigma) {
  if (sigma === null || !(sigma > 0)) return null;
  const w = CLASS_CENTRES.map((c) => Math.exp(-((Math.log(sigma) - Math.log(c)) ** 2) / (2 * 0.3 ** 2)));
  const s = w.reduce((p, q) => p + q, 0);
  return w.map((v) => v / s);
}
function matches(a, j) {
  const m = S.sc.match, c = a.cols, t = c.t[j], lat = c.lat[j], lon = c.lon[j], alt = c.alt[j];
  return S.sc.pireps.map((p) => ({ p, dt: p.t - t, d: haversineKm(lat, lon, p.lat, p.lon), dalt: p.fl * 100 - alt }))
    .filter((r) => Math.abs(r.dt) <= m.window_s && r.d <= m.dist_km && Math.abs(r.dalt) <= m.alt_ft)
    .sort((x, y) => x.d - y.d);
}
function renderInspector() {
  const el = $("inspector");
  S.lastJ = -1;
  if (S.selected < 0) {
    el.innerHTML = `<div class="empty">
      <svg viewBox="-10 -10 20 20" width="44" height="44" aria-hidden="true"><path d="${PLANE_D}"/></svg>
      <h3>No Flight Selected</h3>
      <p>Pick a flight from the list, click one on the map, or choose one in the playback bar.</p>
      <p><kbd>Space</kbd> play/pause · <kbd>←</kbd> <kbd>→</kbd> one minute · <kbd>Esc</kbd> deselect</p></div>`;
    return;
  }
  el.innerHTML = '<div id="insLive"></div>';
  updateInspectorLive();
}
function chartSVG(a, j, width) {
  const c = a.cols, sv = S.sc.severity;
  const j0 = Math.max(0, j - WINDOW_SAMPLES + 1);
  const H1 = 112, H2 = 40, gap = 10, padR = 52;
  const W = Math.max(200, Math.floor(width)), plotW = W - padR, Ht = H1 + gap + H2;
  const X = (k) => ((k - j0) / (WINDOW_SAMPLES - 1)) * plotW;
  const vals = [];
  for (let k = j0; k <= j; k++) { if (c.sc[k] !== null) vals.push(c.sc[k]); if (c.truth[k] !== null) vals.push(c.truth[k]); }
  const ymax = Math.max(360, ...vals) * 1.08;
  const Y = (v) => H1 - 4 - (v / ymax) * (H1 - 10);
  const path = (arr, yf) => {
    let d = "", pen = false;
    for (let k = j0; k <= j; k++) {
      const v = arr[k];
      if (v === null) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${X(k).toFixed(1)},${yf(v).toFixed(1)}`; pen = true;
    }
    return d;
  };
  let area = "", run = [];
  const flush = () => {
    if (run.length > 1) area += `M${run[0][0]},${H1 - 4}` + run.map(([x, y]) => `L${x},${y}`).join("") + `L${run[run.length - 1][0]},${H1 - 4}Z`;
    run = [];
  };
  for (let k = j0; k <= j; k++) { const v = c.sc[k]; if (v === null) flush(); else run.push([X(k).toFixed(1), Y(v).toFixed(1)]); }
  flush();
  const names = ["Light", "Mod", "Sev"];
  const thr = sv.edges.map((e, k) => (e < ymax
    ? `<line x1="0" x2="${plotW}" y1="${Y(e).toFixed(1)}" y2="${Y(e).toFixed(1)}" stroke="${COL.sev[k + 1]}" stroke-opacity=".7" stroke-width="1" stroke-dasharray="3 3"/>
       <text x="${plotW + 6}" y="${(Y(e) + 3.5).toFixed(1)}" font-size="10" font-weight="600" fill="${COL.sev[k + 1]}" font-family="${UI_FONT}">${names[k]} ${e}</text>`
    : "")).join("");
  const res = [];
  for (let k = j0; k <= j; k++) if (c.res[k] !== null) res.push(Math.abs(c.res[k]));
  const rmax = Math.max(200, ...res) * 1.1;
  const Yr = (v) => H1 + gap + H2 / 2 - (v / rmax) * (H2 / 2 - 2);
  const last = c.sc[j];
  return `<svg viewBox="0 0 ${W} ${Ht}" width="${W}" height="${Ht}" role="img" aria-label="Trailing 10-minute sigma and vertical-rate residual">
    <defs><linearGradient id="gArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${COL.accent}" stop-opacity=".34"/><stop offset="1" stop-color="${COL.accent}" stop-opacity="0"/></linearGradient></defs>
    <line x1="0" x2="${plotW}" y1="${H1 - 4}" y2="${H1 - 4}" stroke="${COL.hairline}" stroke-width="1"/>
    ${thr}
    <path d="${area}" fill="url(#gArea)"/>
    <path d="${path(c.truth, Y)}" fill="none" stroke="${COL.label2}" stroke-width="1.2" stroke-dasharray="3 3"/>
    <path d="${path(c.sc, Y)}" fill="none" stroke="${COL.accent}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    ${last !== null ? `<circle cx="${X(j).toFixed(1)}" cy="${Y(last).toFixed(1)}" r="3.5" fill="${COL.accent}" stroke="${COL.surface}" stroke-width="1.5"/>` : ""}
    <line x1="0" x2="${plotW}" y1="${Yr(0).toFixed(1)}" y2="${Yr(0).toFixed(1)}" stroke="${COL.hairline}" stroke-width="1"/>
    <path d="${path(c.res, Yr)}" fill="none" stroke="${COL.na}" stroke-width="1" stroke-linejoin="round"/>
    <text x="${plotW + 6}" y="${(Yr(0) + 3.5).toFixed(1)}" font-size="10" fill="${COL.label3}" font-family="${UI_FONT}">residual</text>
    <rect x="0" y="0" width="${plotW}" height="${Ht}" fill="transparent" data-hit="1"/>
  </svg>`;
}
function updateInspectorLive() {
  const live = $("insLive");
  if (S.selected < 0 || !live || !S.sc) return;
  const a = S.sc.aircraft[S.selected], c = a.cols;
  const j = S.pinned ?? idxAt(S.t);
  if (j === S.lastJ) return;
  S.lastJ = j;
  const sev = c.sev[j], sv = S.sc.severity, w = classWeights(c.sc[j]);
  const ms = matches(a, j), m = S.sc.match;
  const sevPill = sev < 0 ? '<span class="sev-pill na">Not Assessed</span>' : `<span class="sev-pill s${sev}">${esc(sv.labels[sev])}</span>`;
  const row = (k, v) => `<div class="row"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  const ns = c.lat[j] >= 0 ? "N" : "S", ew = c.lon[j] < 0 ? "W" : "E";
  const fl = Math.round(c.alt[j] / 100);
  const story = c.seg[j] !== 1
    ? `<b>${esc(a.callsign)}</b> is climbing or descending through FL${fl}. Horus only scores level flight, so it is <b>not assessed</b> right now.`
    : `<b>${esc(a.callsign)}</b> is cruising at FL${fl}. After the rounding correction its vertical rate jitters by <b>${fmt(c.sc[j], 0)} ft/min</b> — <b>${esc(sv.labels[sev])}</b> on the placeholder scale.` +
      (ms.length ? ` ${ms.length} synthetic pilot report${ms.length > 1 ? "s" : ""} nearby.` : "");
  const open = (k) => (S.folds[k] ? " open" : "");
  live.innerHTML = `
    <div class="hdr"><div><div class="callsign">${esc(a.callsign)}</div>
      <div class="subtitle">${a.size === "medium_jet" ? "Medium jet" : "Medium turboprop"} · Synthetic flight ·
        <span class="num">${fmt(Math.abs(c.lat[j]), 2)}°${ns} ${fmt(Math.abs(c.lon[j]), 2)}°${ew}</span></div></div>${sevPill}</div>
    <p class="story">${story}</p>
    <div class="meta-line"><span class="num">${mmss(c.t[j])} · ${S.pinned !== null ? "Pinned" : "Live"}</span>
      ${S.pinned !== null ? '<button id="unpin" class="text-btn" type="button">Follow Playhead</button>' : "<span>Click the chart to pin a moment</span>"}</div>
    <div class="tele">
      <div><b>FL${fmt(c.alt[j] / 100, 0)}</b><span>Altitude</span></div>
      <div><b>${fmt(c.gs[j], 0)} kt</b><span>Speed</span></div>
      <div><b>${fmt(c.hdg[j], 0)}°</b><span>Heading</span></div>
      <div><b>${fmt(c.vr[j], 0)}</b><span>VR ft/min</span></div>
    </div>
    <div class="chart-card">
      <div class="ch"><span>Roughness (σ<sub>corr</sub>) · last 10 min</span><b>${fmt(c.sc[j], 0)}<span> ft/min</span></b></div>
      <div id="chartHost"></div>
      <div class="chart-key"><span><i></i>σ<sub>corr</sub></span><span><i class="dash"></i>Injected σ (truth)</span><span><i class="res"></i>VR residual</span></div>
    </div>
    <div class="section-h">Detector <small>used by Horus</small></div>
    <div class="group">
      ${row("Level-flight segment", c.seg[j] === 1 ? "Yes" : "No · climb/descent")}
      ${row("σ<sub>corr</sub> <em>after Δ²/12</em>", fmt(c.sc[j], 0, " ft/min"))}
      ${row("σ<sub>raw</sub> <em>before Δ²/12</em>", fmt(c.sr[j], 0, " ft/min"))}
      ${row("Injected σ <em>synthetic truth</em>", fmt(c.truth[j], 0, " ft/min"))}
    </div>
    <details class="fold" data-fold="more"${open("more")}><summary>More metrics<small>flicker, B1, context</small></summary>
      <div class="group">
        ${row("Flicker samples <em>61 s</em>", fmt(c.flk[j], 0))}
        ${row("B1 vertical-accel RMS", fmt(c.b1[j], 3, " g"))}
        ${row("Altitude deviation <em>vs 2-min median</em>", fmt(c.adev[j], 0, " ft"))}
        ${row("Heading rate <em>descriptive</em>", fmt(c.hrate[j], 2, " °/s"))}
        ${row("GS variability <em>wind-gradient proxy</em>", fmt(c.gsd[j], 1, " kt"))}
      </div></details>
    <details class="fold" data-fold="weights"${open("weights")}><summary>Class weights<small>uncalibrated</small></summary>
      <div class="group">${w === null ? row("Not assessed outside level flight", "—")
        : sv.labels.map((l, k) => `<div class="row wbar"><span>${esc(l)}</span><span class="track"><span class="fill" data-k="${k}" data-w="${(w[k] * 100).toFixed(1)}"></span></span><span class="v">${Math.round(w[k] * 100)}%</span></div>`).join("")}</div></details>
    <details class="fold" data-fold="pireps"${open("pireps")}><summary>Pilot reports nearby<small>${ms.length} in window</small></summary>
      <div class="group">${ms.length === 0 ? row("None within ±10 min · 40 km · ±2,000 ft", "—") : ms.map((r) => `
        <div class="row pirep"><div class="row-top"><span class="k"><b data-tb="${esc(r.p.tb)}">${esc(r.p.tb)}</b> · ${esc(r.p.by)}</span>
          <span class="v">${r.d.toFixed(0)} km · ${r.dt >= 0 ? "+" : "−"}${Math.abs(r.dt / 60).toFixed(1)} min</span></div>
          <span class="txt">${esc(r.p.text)}</span></div>`).join("")}</div>
      <p class="footnote">Window ±${m.window_s / 60} min · ${m.dist_km} km · ±${m.alt_ft.toLocaleString()} ft from config.yaml (SPEC §6.1), drawn as the dashed ring on the map.</p></details>
    <p class="legal-line">Horus · MIT License · © 2026 Rohit Raj · Research prototype — not for operational use</p>`;
  for (const f of live.querySelectorAll(".fill")) { f.style.width = `${f.dataset.w}%`; f.style.background = COL.sev[+f.dataset.k]; }
  for (const b of live.querySelectorAll("[data-tb]")) b.style.color = COL.tb[b.dataset.tb];
  for (const r of live.querySelectorAll(".row-top")) { r.style.display = "flex"; r.style.justifyContent = "space-between"; r.style.gap = "10px"; }
  const host = $("chartHost");
  host.innerHTML = chartSVG(a, j, host.clientWidth || 360);
  host.querySelector("[data-hit]").addEventListener("click", (ev) => {
    const r = ev.currentTarget.getBoundingClientRect();
    const j0 = Math.max(0, j - WINDOW_SAMPLES + 1);
    S.pinned = Math.max(0, Math.min(j, j0 + Math.round(((ev.clientX - r.left) / r.width) * (WINDOW_SAMPLES - 1))));
    S.lastJ = -1; updateInspectorLive(); draw();
  });
  const up = $("unpin");
  if (up) up.addEventListener("click", () => { S.pinned = null; S.lastJ = -1; draw(); updateInspectorLive(); });
}

// ---------------------------------------------------------------- interaction
function pick(mx, my) {
  if (!S.sc) return null;
  const i = idxAt(S.t);
  let best = null, bd = 13 * 13;
  S.sc.aircraft.forEach((a, k) => {
    if (!inFL(a.cols.alt[i])) return;
    const d = (a.x[i] - mx) ** 2 + (a.y[i] - my) ** 2;
    if (d < bd) { bd = d; best = { k, j: null }; }
  });
  if (best) return best;
  bd = 7 * 7;
  const trailN = Math.round((TRAIL_MIN * 60) / S.sc.stride_s);
  S.sc.aircraft.forEach((a, k) => {
    const from = k === S.selected ? 0 : Math.max(0, i - trailN), to = k === S.selected ? a.x.length - 1 : i;
    for (let j = from; j <= to; j++) {
      const d = (a.x[j] - mx) ** 2 + (a.y[j] - my) ** 2;
      if (d < bd) { bd = d; best = { k, j }; }
    }
  });
  return best;
}
function select(k, j = null) {
  const changed = k !== S.selected;
  S.selected = k; S.pinned = j;
  if (changed) renderInspector(); else { S.lastJ = -1; updateInspectorLive(); }
  renderFlightList.force = true;
  draw();
}
canvas.addEventListener("click", (ev) => {
  const r = canvas.getBoundingClientRect();
  const hit = pick(ev.clientX - r.left, ev.clientY - r.top);
  if (hit) { $("callsign").value = ""; S.filterCs = ""; select(hit.k, hit.j); }
});
canvas.addEventListener("mousemove", (ev) => {
  const r = canvas.getBoundingClientRect();
  const hit = pick(ev.clientX - r.left, ev.clientY - r.top);
  const h = hit ? hit.k : -1;
  canvas.style.cursor = hit ? "pointer" : "default";
  if (h !== S.hover) { S.hover = h; if (!S.playing) draw(); }
});
canvas.addEventListener("mouseleave", () => { if (S.hover !== -1) { S.hover = -1; if (!S.playing) draw(); } });

let last = null;
function tick(ts) {
  if (!S.playing) { last = null; return; }
  if (last !== null) {
    S.t += ((ts - last) / 1000) * S.speed;
    const end = S.sc.duration_s - S.sc.stride_s;
    if (S.t >= end) { S.t = end; setPlaying(false); }
  }
  last = ts; draw();
  if (S.playing) requestAnimationFrame(tick);
}
function setPlaying(on) {
  S.playing = on;
  $("playIcon").setAttribute("d", on ? "M4 2.5h3v11H4zM9 2.5h3v11H9z" : "M4 2.5v11l9-5.5z");
  $("playBtn").setAttribute("aria-label", on ? "Pause" : "Play");
  if (on && S.sc) {
    if (S.t >= S.sc.duration_s - S.sc.stride_s) S.t = 0;
    requestAnimationFrame(tick);
  }
}
$("playBtn").addEventListener("click", () => setPlaying(!S.playing));
$("scrub").addEventListener("input", (e) => { S.t = +e.target.value; draw(); });
for (const b of $("speed").querySelectorAll("button")) {
  b.addEventListener("click", () => {
    S.speed = +b.dataset.speed;
    for (const x of $("speed").querySelectorAll("button")) x.setAttribute("aria-checked", String(x === b));
    placeThumb($("speed"));
  });
}
function syncFL(changed) {
  let lo = +$("flMin").value, hi = +$("flMax").value;
  if (lo > hi) { if (changed === "min") hi = lo; else lo = hi; $("flMin").value = lo; $("flMax").value = hi; }
  S.flMin = lo; S.flMax = hi;
  $("flLabel").textContent = `FL${lo}–FL${hi}`;
  renderFlightList.force = true;
  draw();
}
$("flMin").addEventListener("input", () => syncFL("min"));
$("flMax").addEventListener("input", () => syncFL("max"));
$("callsign").addEventListener("change", (e) => {
  S.filterCs = e.target.value;
  if (S.filterCs !== "") select(+S.filterCs); else draw();
});
function bindToggle(id, key) {
  $(id).addEventListener("click", () => {
    S[key] = !S[key];
    $(id).setAttribute("aria-pressed", String(S[key]));
    draw();
  });
}
bindToggle("tgPireps", "showPireps"); bindToggle("tgField", "showField"); bindToggle("tgTrail", "showTrail");
for (const b of $("views").querySelectorAll("button")) b.addEventListener("click", () => setView(b.dataset.view));
for (const b of document.querySelectorAll("[data-go]")) b.addEventListener("click", () => setView(b.dataset.go));
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  const sheetOpen = !$("sheet").classList.contains("hidden");
  if (e.key === "Escape") {
    if (sheetOpen) closeSheet();
    else if (S.view === "radar" && S.selected >= 0) { S.selected = -1; S.pinned = null; $("callsign").value = ""; S.filterCs = ""; renderInspector(); renderFlightList.force = true; draw(); }
    return;
  }
  if (S.view !== "radar" || !S.sc || sheetOpen) return;
  if (e.key === " ") { e.preventDefault(); setPlaying(!S.playing); }
  if (e.key === "ArrowRight") { S.t = Math.min(S.sc.duration_s - S.sc.stride_s, S.t + 60); draw(); }
  if (e.key === "ArrowLeft") { S.t = Math.max(0, S.t - 60); draw(); }
});

// ---------------------------------------------------------------- sources sheet (radar)
function openSheet() {
  const A = S.about, cls = { built: "built", "core built": "core", planned: "planned" };
  $("aboutContent").innerHTML = `
    ${A.stages.map((s) => `<div class="section-h">${esc(s.name)}</div><div class="group">${s.items.map((it) =>
      `<div class="row"><span class="k">${esc(it.label)}${it.note ? ` <em>${esc(it.note)}</em>` : ""}</span><span class="st ${cls[it.status]}">${esc(it.status)} · ${esc(it.m)}</span></div>`).join("")}</div>`).join("")}
    <div class="section-h">Attribution</div>
    ${A.attributions.map((x) => `<p class="attrib"><b>${esc(x.name)}</b>${esc(x.text)}
      <br><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.url)}</a></p>`).join("")}
    <p class="disclaimer">${esc(A.disclaimer)}</p>
    <p class="legal-line">${esc(A.license)}</p>`;
  $("sheet").classList.remove("hidden");
  $("closeSheet").focus();
}
function closeSheet() { $("sheet").classList.add("hidden"); $("aboutBtn").focus(); }
$("aboutBtn").addEventListener("click", openSheet);
$("closeSheet").addEventListener("click", closeSheet);
$("sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSheet(); });

// ---------------------------------------------------------------- boot
const H = new URLSearchParams(location.hash.slice(1)); // deep links: view, scenario, t, sel, pin, field, fl, about, theme

async function initRadar() {
  const seg = $("scenarios");
  seg.insertAdjacentHTML("beforeend", S.index.map((s) => `<button type="button" role="tab" data-id="${esc(s.id)}" aria-selected="false" title="${esc(s.title)}">${esc(SHORT[s.id] || s.title)}</button>`).join(""));
  for (const b of seg.querySelectorAll("button")) b.addEventListener("click", () => { setPlaying(false); loadScenario(b.dataset.id); });
  for (const b of $("speed").querySelectorAll("button")) b.setAttribute("aria-checked", String(+b.dataset.speed === S.speed));
  fitView();
  const sid = S.index.some((s) => s.id === H.get("scenario")) ? H.get("scenario") : DEFAULT_SCENARIO;
  const k = H.get("sel");
  await loadScenario(sid, { autoSelect: !k });
  if (H.has("t")) S.t = Math.max(0, Math.min(S.sc.duration_s - S.sc.stride_s, +H.get("t") || 0));
  if (H.get("field") === "1") { S.showField = true; $("tgField").setAttribute("aria-pressed", "true"); }
  if (H.has("fl")) {
    const [lo, hi] = H.get("fl").split("-").map(Number);
    if (lo && hi) { $("flMin").value = lo; $("flMax").value = hi; syncFL("max"); }
  }
  const sk = S.sc.aircraft.findIndex((a) => a.callsign === k);
  if (sk >= 0) select(sk, H.has("pin") ? idxAt(+H.get("pin")) : null);
  placeAllThumbs();
  renderFlightList.force = true;
  draw();
  if (H.get("about") === "1") openSheet();
}

async function boot() {
  applyTheme();
  [S.basemap, S.index, S.about, S.overview] = await Promise.all([
    getJSON("/api/basemap"), getJSON("/api/scenarios"), getJSON("/api/about"), getJSON("/api/overview"),
  ]);
  renderOverview();
  await setView(H.get("view") === "radar" ? "radar" : "overview", { push: false });
  initPreview().catch(() => {});
  new ResizeObserver(() => {
    placeAllThumbs();
    if (S.view === "radar" && S.radarReady) { fitView(); draw(); S.lastJ = -1; updateInspectorLive(); }
    if (S.view === "overview" && PV.data && previewProject()) drawPreview();
  }).observe(document.body);
}
boot().catch((err) => {
  document.body.insertAdjacentHTML("beforeend", `<p class="fine">Could not load the demo: ${esc(err.message)}</p>`);
});
