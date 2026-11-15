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
  folds: { more: false, pireps: false },
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
    <article class="card kpi reveal" data-delay="${i}" data-acc="${["green", "blue", "purple", "orange"][i % 4]}">
      <span class="k-value${k.value.length > 6 ? " long" : ""}" data-count="${esc(k.value)}">${esc(k.value)}${k.unit ? `<span class="k-unit">${esc(k.unit)}</span>` : ""}</span>
      <span class="k-short">${sub(k.short || "")}</span>
      <span class="k-label">${sub(k.label)}</span>
      <details><summary>What this shows</summary>
        <p class="k-detail">${sub(k.detail)}</p><p class="k-caveat">${sub(k.caveat)}</p></details>
    </article>`).join("");
  const cls = { built: "built", "core built": "core", planned: "planned" };
  const stLabel = { built: "Built", "core built": "Core built", planned: "Planned" };
  const stCls = { built: "built", "core built": "core", planned: "planned" };
  $("pipeline").innerHTML = '<span class="track-thumb" aria-hidden="true"></span>' + O.pipeline.map((p, i) => `
    <li class="gstep" role="tab" tabindex="0" data-i="${i}" data-acc="${esc(p.color || "blue")}" aria-selected="false" aria-controls="stageDetail">
      <span class="gi"><svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[p.icon] || ""}</svg></span>
      <span class="gt">${esc(p.title)}</span>
      <span class="gs"><i class="${stCls[p.status]}"></i>${stLabel[p.status]}</span>
    </li>`).join("");
  for (const li of $("pipeline").querySelectorAll(".gstep")) {
    li.addEventListener("click", () => selectStage(+li.dataset.i));
    li.addEventListener("keydown", (e) => {
      const n = O.pipeline.length, i = +li.dataset.i;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectStage(i); }
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const k = (i + (e.key === "ArrowRight" ? 1 : n - 1)) % n;
        selectStage(k); $("pipeline").querySelector(`[data-i="${k}"]`).focus();
      }
    });
  }
  selectStage(Math.min(O.pipeline.length - 1, Math.max(0, H.has("stage") ? +H.get("stage") : 3)));
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
  $("problem").innerHTML = O.problem.map((x, i) => `
    <article class="card reveal" data-delay="${i}" data-acc="${esc(x.color || "blue")}"><div class="ico">0${i + 1}</div><h3>${esc(x.title)}</h3><p>${esc(x.text)}</p></article>`).join("");
  $("scenGallery").innerHTML = O.scenarios.map((c, i) => `
    <button type="button" class="card scard reveal" data-delay="${i % 2}" data-open="${esc(c.id)}" data-acc="${SCEN_COL[c.id] || "blue"}" aria-label="Open ${esc(c.title)} in the radar">
      <canvas data-thumb="${esc(c.id)}" aria-hidden="true"></canvas>
      <div class="sc-body"><span class="sc-chip">${esc(SCEN_CHIP[c.id] || "Scenario")}</span><h3>${esc(c.title)}</h3><p>${esc(c.blurb)}</p>
        <div class="stats"><span><b>${c.aircraft}</b> flights</span><span><b>${c.patches}</b> turbulence patches</span>
          <span><b>${c.mod_plus}</b> / ${c.pireps} PIREPs MOD+</span>${c.recovery === null ? "" : `<span>σ recovered <b>${c.recovery.toFixed(2)}</b></span>`}</div>
        <span class="open">Open in radar ›</span></div>
    </button>`).join("");
  for (const b of $("scenGallery").querySelectorAll("[data-open]")) b.addEventListener("click", () => openScenario(b.dataset.open));
  $("glossary").innerHTML = O.glossary.map((g, i) => `
    <div class="gloss reveal" data-delay="${i % 2}" data-acc="${esc(g.color || "blue")}"><dt>${sub(g.term)}</dt><dd>${sub(g.text)}</dd></div>`).join("");
  setupThumbs();
  const done = { done: "Done", active: "In progress", planned: "Planned" };
  $("roadmapAsOf").textContent = `Setup plus seven weekly milestones, each closed by an automated gate. Status as of ${O.milestones_as_of}.`;
  $("roadmap").innerHTML = O.milestones.map((m, i) => `
    <li class="${m.status} reveal" data-delay="${i % 4}"><div class="node" title="${done[m.status]}">${m.status === "done" ? "✓" : esc(m.m)}</div>
      <h4>${esc(m.title)}</h4><p>${esc(m.note)}</p></li>`).join("");
  $("limits").innerHTML = O.limitations.map((x) => `<li>${sub(x)}</li>`).join("");
  observeReveals();
  setupHoverWeight();
  setupCarousel();
  setupPlayground();
  setupTilt();
}

const SCEN_COL = { "jet-cat": "orange", convective: "red", calm: "green", "pirep-replay": "purple" };
const SCEN_CHIP = { "jet-cat": "Clear-air turbulence", convective: "Thunderstorms", calm: "Control: no turbulence", "pirep-replay": "Validation" };

function placeStageThumb() {
  const track = $("pipeline"), on = track && track.querySelector('.gstep[aria-selected="true"]'), th = track && track.querySelector(".track-thumb");
  if (!on || !th || !track.offsetParent) return;
  th.style.width = `${on.offsetWidth}px`;
  th.style.transform = `translateX(${on.offsetLeft}px)`;
  $("glassStage").style.setProperty("--orb-x", `${((on.offsetLeft + on.offsetWidth / 2) / track.offsetWidth) * 100}%`);
}
function selectStage(i) {
  const p = S.overview.pipeline[i], n = S.overview.pipeline.length;
  for (const li of $("pipeline").querySelectorAll(".gstep")) li.setAttribute("aria-selected", String(+li.dataset.i === i));
  $("glassStage").dataset.acc = p.color || "blue";
  const dot = { built: "#fff", "core built": "var(--gold)", planned: "transparent" }[p.status];
  const word = { built: "Built", "core built": "Core built", planned: "Planned" }[p.status] + (p.note ? ` · ${p.note}` : "");
  $("stageDetail").innerHTML = `<div class="gp-fade">
    <div class="gp-head"><div><span class="gp-step">Stage ${i + 1} of ${n}</span><h3>${esc(p.title)}</h3><p class="gp-sub">${esc(p.detail)}</p></div>
      <span class="gp-status"><i data-dot="1"></i>${esc(word)}</span></div>
    <div id="lab" class="gp-lab"></div>
    <div class="gp-body">
      <div><h4>What it does</h4><p>${sub(p.what)}</p></div>
      <div><h4>Why it exists</h4><p>${sub(p.why)}</p></div>
      <div><h4>By the numbers</h4><ul>${p.facts.map((f) => `<li>${sub(f)}</li>`).join("")}</ul></div>
    </div></div>`;
  $("stageDetail").querySelector("[data-dot]").style.background = dot;
  placeStageThumb();
  renderLab(p.lab);
}

// ---------------------------------------------------------------- pipeline labs (one per stage)
// Every constant comes from /api/overview.lab (config.yaml or measured notes); the chain and
// threshold labs use the real detector output stored in the synthetic scenario files.
const SC_CACHE = {};
const getScenario = (id) => (SC_CACHE[id] ||= getJSON(`/api/scenario/${encodeURIComponent(id)}`));
let LAB = { token: 0, chainStep: 1, chainTimer: 0 };

function renderLab(kind) {
  const host = $("lab");
  if (!host) return;
  clearInterval(LAB.chainTimer);
  const token = ++LAB.token;
  ({ credits: labCredits, cadence: labCadence, units: labUnits, chain: labChain, threshold: labThreshold }[kind] || (() => {}))(host, token);
}
const labHead = (title, hint) => `<div class="lab-head"><span class="lab-badge">Try it</span><b>${title}</b><span class="lab-hint">${hint}</span></div>`;

function labCredits(host) {
  const L = S.overview.lab;
  host.innerHTML = `${labHead("Credit budget", "Drag to change how often the US-wide snapshot is requested.")}
    <div class="lab-row"><label for="lcIn">Snapshot every</label><input id="lcIn" class="slider" type="range" min="15" max="300" step="5" value="${L.poll_interval_s}"><b id="lcV" class="num"></b></div>
    <div class="meter"><i id="lcBar"></i><span class="meter-mark" title="Daily allowance"></span></div>
    <div class="lab-stats"><div><b id="lcCalls" class="num"></b><span>snapshots a day</span></div><div><b id="lcCred" class="num"></b><span>credits of ${L.daily_credits.toLocaleString()}</span></div><div><b id="lcLast" class="num"></b><span>credits last</span></div></div>
    <p id="lcNote" class="lab-note"></p>`;
  const upd = () => {
    const iv = +$("lcIn").value, calls = Math.ceil(86400 / iv), cred = calls * L.credit_cost, ok = cred <= L.daily_credits;
    const hours = Math.min(24, (L.daily_credits / L.credit_cost) * iv / 3600);
    $("lcV").textContent = `${iv} s`; $("lcCalls").textContent = calls.toLocaleString(); $("lcCred").textContent = cred.toLocaleString();
    $("lcLast").textContent = ok ? "24 h" : `${hours.toFixed(1)} h`;
    const bar = $("lcBar"); bar.style.width = `${Math.min(100, (cred / (2 * L.daily_credits)) * 100)}%`; bar.style.background = ok ? "#fff" : "var(--sev3)";
    $("lcNote").innerHTML = ok
      ? `Fits: ${L.credit_cost} credits per request × ${calls.toLocaleString()} requests stays inside the allowance. The recorder runs at ${L.poll_interval_s} s and also slows itself down using the credits-remaining header.`
      : `Over budget: the credits run out after ${hours.toFixed(1)} h and the recorder goes blind until the 00:00 UTC reset. This is why live data can't be 5–10 s.`;
  };
  $("lcIn").addEventListener("input", upd); upd();
}

function labCadence(host) {
  const L = S.overview.lab;
  const modes = [["trino", "Trino · 1 s"], ["adsblol", "adsb.lol · ~20 s"], ["live", "Live · 90 s"]];
  host.innerHTML = `${labHead("Samples per window", "Pick a feed: how many points fall inside the detector's windows?")}
    <div class="chips" role="radiogroup" aria-label="Feed">${modes.map(([k, l], i) => `<button type="button" role="radio" data-m="${k}" aria-checked="${i === 1}">${l}</button>`).join("")}</div>
    <div id="cdSvg" class="lab-svg"></div>
    <div class="lab-stats"><div><b id="cdStat" class="num"></b><span>points in the ${L.w_stat_s} s σ window</span></div><div><b id="cdTrend" class="num"></b><span>in the ${L.w_trend_s} s trend window</span></div><div><b class="num">${L.min_samples_stat}</b><span>needed for a σ estimate</span></div></div>
    <p id="cdNote" class="lab-note"></p>`;
  const gapsAdsb = [20, 19, 2, 20, 8, 20, 19, 20, 2, 20, 19, 8, 20, 20, 19];
  const times = (m) => {
    const out = []; let t = 0, k = 0;
    while (t <= 180) { out.push(t); t += m === "trino" ? 1 : m === "live" ? L.poll_interval_s : gapsAdsb[k++ % gapsAdsb.length]; }
    return out;
  };
  const draw = (m) => {
    const W = Math.max(280, $("cdSvg").clientWidth || 600), H = 70, X = (t) => 8 + (t / 180) * (W - 16);
    const c = 90, s0 = c - L.w_stat_s / 2, s1 = c + L.w_stat_s / 2, t0 = c - L.w_trend_s / 2, t1 = c + L.w_trend_s / 2;
    const ts = times(m), nS = ts.filter((t) => t >= s0 && t <= s1).length, nT = ts.filter((t) => t >= t0 && t <= t1).length;
    $("cdSvg").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Sample times over three minutes">
      <rect x="${X(t0)}" y="8" width="${X(t1) - X(t0)}" height="44" rx="8" fill="rgba(41,151,255,.10)" stroke="rgba(41,151,255,.45)"/>
      <rect x="${X(s0)}" y="14" width="${X(s1) - X(s0)}" height="32" rx="6" fill="rgba(255,159,10,.16)" stroke="rgba(255,159,10,.7)"/>
      <line x1="${X(0)}" x2="${X(180)}" y1="30" y2="30" stroke="rgba(255,255,255,.18)"/>
      ${ts.map((t) => `<circle cx="${X(t).toFixed(1)}" cy="30" r="${m === "trino" ? 1.6 : 3.4}" fill="${t >= s0 && t <= s1 ? "#ff9f0a" : "rgba(255,255,255,.75)"}"/>`).join("")}
      <text x="${X(0)}" y="66" font-size="10" fill="rgba(255,255,255,.45)" font-family="${UI_FONT}">0 s</text>
      <text x="${X(180) - 26}" y="66" font-size="10" fill="rgba(255,255,255,.45)" font-family="${UI_FONT}">180 s</text></svg>`;
    $("cdStat").textContent = nS; $("cdTrend").textContent = nT;
    $("cdNote").innerHTML = nS >= L.min_samples_stat
      ? `Enough points: σ can be measured every second. This is what OpenSky Trino would give.`
      : `Too few points for a ${L.w_stat_s} s window${m === "adsblol" ? " (spacing drawn from measured gaps: 10–90 % = " + L.adsblol_gap_percentiles_s.join(" / ") + " s)" : ""}. Longer, rate-specific windows are part of the M3 benchmark.`;
  };
  for (const b of host.querySelectorAll(".chips button")) b.addEventListener("click", () => {
    for (const x of host.querySelectorAll(".chips button")) x.setAttribute("aria-checked", String(x === b));
    draw(b.dataset.m);
  });
  draw("adsblol");
}

function labUnits(host) {
  const L = S.overview.lab, step = L.step_fpm;
  host.innerHTML = `${labHead("From m/s to the 64 ft/min grid", "Drag a true vertical rate and watch ADS-B round it.")}
    <div class="lab-row"><label for="unIn">True vertical rate</label><input id="unIn" class="slider" type="range" min="-8" max="8" step="0.05" value="1.3"><b id="unV" class="num"></b></div>
    <div id="unSvg" class="lab-svg"></div>
    <div class="lab-stats"><div><b id="unF" class="num"></b><span>ft/min (× ${L.fpm_per_mps.toFixed(3)})</span></div><div><b id="unB" class="num"></b><span>broadcast value</span></div><div><b id="unE" class="num"></b><span>rounding error</span></div></div>
    <p class="lab-note">Every ADS-B vertical rate lands on a multiple of ${step} ft/min, so the error is always within ±${step / 2}. Spread evenly, that error has variance Δ²/12 = ${(step * step / 12).toFixed(1)} (ft/min)²: the term stage 4 subtracts.</p>`;
  const upd = () => {
    const v = +$("unIn").value, f = v * L.fpm_per_mps, b = step * Math.round(f / step), e = f - b;
    $("unV").textContent = `${v.toFixed(2)} m/s`; $("unF").textContent = f.toFixed(1); $("unB").textContent = `${b.toFixed(0)}`; $("unE").textContent = `${e >= 0 ? "+" : ""}${e.toFixed(1)}`;
    const W = Math.max(280, $("unSvg").clientWidth || 600), H = 54, lo = Math.floor(f / step) * step - 3 * step, hi = lo + 7 * step;
    const X = (x) => 10 + ((x - lo) / (hi - lo)) * (W - 20);
    let ticks = "";
    for (let t = lo; t <= hi; t += step) ticks += `<line x1="${X(t)}" x2="${X(t)}" y1="16" y2="34" stroke="rgba(255,255,255,.35)"/><text x="${X(t) - 12}" y="50" font-size="10" fill="rgba(255,255,255,.45)" font-family="${UI_FONT}">${t}</text>`;
    $("unSvg").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Rounding to the 64 ft/min grid">
      <line x1="10" x2="${W - 10}" y1="25" y2="25" stroke="rgba(255,255,255,.15)"/>${ticks}
      <line x1="${X(f)}" x2="${X(b)}" y1="25" y2="25" stroke="#ff9f0a" stroke-width="2"/>
      <circle cx="${X(f)}" cy="25" r="5" fill="#2997ff"/><circle cx="${X(b)}" cy="25" r="6" fill="none" stroke="#30d158" stroke-width="2"/></svg>`;
  };
  $("unIn").addEventListener("input", upd); upd();
}

async function labChain(host, token) {
  host.innerHTML = `${labHead("Watch one flight go through the chain", "Step through what the detector does to the signal.")}<div class="lab-svg lab-loading">Loading a flight…</div>`;
  const D = await getScenario(DEFAULT_SCENARIO);
  if (token !== LAB.token) return;
  let best = { k: 0, j: 0, v: -1 };
  D.aircraft.forEach((a, k) => { const sc = a.cols.sc; for (let j = 3; j < sc.length - 3; j++) { let s = 0, n = 0; for (let q = j - 3; q <= j + 3; q++) if (sc[q] !== null) { s += sc[q]; n++; } if (n >= 5 && s / n > best.v) best = { k, j, v: s / n }; } });
  const a = D.aircraft[best.k], c = a.cols, j0 = Math.max(0, best.j - 90), j1 = Math.min(c.t.length - 1, best.j + 60);
  const steps = [
    ["Raw vertical rate", `What ${a.callsign} broadcasts: steps of ${S.overview.lab.step_fpm} ft/min, autopilot drift and turbulence mixed together.`],
    ["Trend (31 s median)", "A rolling median follows the slow autopilot drift but ignores fast jitter."],
    ["Residual", "Raw minus trend: only the fast jitter is left. Calm stretches hug zero; rough air swings wide."],
    ["σ_corr", "The spread of the residual over 15 s, minus the rounding variance Δ²/12. This number sets the colour."],
  ];
  host.innerHTML = `${labHead(`Watch ${esc(a.callsign)} go through the chain`, "Synthetic Jet Stream flight; every 10th second shown.")}
    <div class="chips" role="tablist">${steps.map(([l], i) => `<button type="button" role="tab" data-s="${i + 1}" aria-checked="${i + 1 === LAB.chainStep}">${i + 1}. ${sub(l)}</button>`).join("")}<button type="button" class="chip-play" id="chPlay">▶ Play</button></div>
    <div id="chSvg" class="lab-svg"></div><p id="chNote" class="lab-note"></p>`;
  const draw = () => {
    const st = LAB.chainStep, W = Math.max(300, $("chSvg").clientWidth || 640), H1 = 120, H2 = 70, G = 12;
    const X = (k) => 6 + ((k - j0) / (j1 - j0)) * (W - 60);
    let vmax = 200; for (let k = j0; k <= j1; k++) if (c.vr[k] !== null) vmax = Math.max(vmax, Math.abs(c.vr[k]));
    const Y = (v) => H1 / 2 - (v / vmax) * (H1 / 2 - 6);
    const line = (f) => { let d = "", pen = false; for (let k = j0; k <= j1; k++) { const v = f(k); if (v === null || Number.isNaN(v)) { pen = false; continue; } d += `${pen ? "L" : "M"}${X(k).toFixed(1)},${v.toFixed(1)}`; pen = true; } return d; };
    const raw = line((k) => (c.vr[k] === null ? null : Y(c.vr[k])));
    const trend = line((k) => (c.vr[k] === null || c.res[k] === null ? null : Y(c.vr[k] - c.res[k])));
    const resid = line((k) => (c.res[k] === null ? null : Y(c.res[k])));
    const smax = Math.max(360, ...c.sc.slice(j0, j1 + 1).filter((v) => v !== null)) * 1.05, Ys = (v) => H1 + G + H2 - 4 - (v / smax) * (H2 - 10);
    const sig = line((k) => (c.sc[k] === null ? null : Ys(c.sc[k])));
    const thr = st >= 4 ? D.severity.edges.map((e, i) => (e < smax ? `<line x1="6" x2="${W - 54}" y1="${Ys(e)}" y2="${Ys(e)}" stroke="${COL.sev[i + 1]}" stroke-opacity=".7" stroke-dasharray="3 3"/><text x="${W - 50}" y="${Ys(e) + 3}" font-size="9.5" fill="${COL.sev[i + 1]}" font-family="${UI_FONT}">${e}</text>` : "")).join("") : "";
    $("chSvg").innerHTML = `<svg viewBox="0 0 ${W} ${H1 + G + H2}" width="${W}" height="${H1 + G + H2}" role="img" aria-label="Signal processing chain">
      <line x1="6" x2="${W - 54}" y1="${Y(0)}" y2="${Y(0)}" stroke="rgba(255,255,255,.12)"/>
      <path d="${raw}" fill="none" stroke="rgba(255,255,255,${st === 1 ? 0.85 : 0.22})" stroke-width="1.2"/>
      ${st >= 2 ? `<path class="draw" pathLength="1" d="${trend}" fill="none" stroke="#2997ff" stroke-width="${st === 2 ? 2.4 : 1.4}" stroke-opacity="${st === 2 ? 1 : 0.4}"/>` : ""}
      ${st >= 3 ? `<path class="draw" pathLength="1" d="${resid}" fill="none" stroke="#ff9f0a" stroke-width="1.6" stroke-opacity="${st === 3 ? 1 : 0.55}"/>` : ""}
      <text x="${W - 50}" y="14" font-size="9.5" fill="rgba(255,255,255,.45)" font-family="${UI_FONT}">ft/min</text>
      ${st >= 4 ? `<line x1="6" x2="${W - 54}" y1="${H1 + G + H2 - 4}" y2="${H1 + G + H2 - 4}" stroke="rgba(255,255,255,.12)"/>${thr}<path class="draw" pathLength="1" d="${sig}" fill="none" stroke="#30d158" stroke-width="2"/>
        <text x="8" y="${H1 + G + 10}" font-size="9.5" fill="rgba(255,255,255,.55)" font-family="${UI_FONT}">σ corr</text>` : `<text x="8" y="${H1 + G + H2 / 2 + 3}" font-size="11" fill="rgba(255,255,255,.3)" font-family="${UI_FONT}">roughness (σ corr) appears at step 4</text>`}
    </svg>`;
    $("chNote").innerHTML = sub(steps[st - 1][1]);
    for (const b of host.querySelectorAll(".chips [data-s]")) b.setAttribute("aria-checked", String(+b.dataset.s === st));
  };
  for (const b of host.querySelectorAll(".chips [data-s]")) b.addEventListener("click", () => { clearInterval(LAB.chainTimer); LAB.chainStep = +b.dataset.s; draw(); });
  $("chPlay").addEventListener("click", () => {
    clearInterval(LAB.chainTimer); LAB.chainStep = 1; draw();
    LAB.chainTimer = setInterval(() => { if (LAB.chainStep >= 4) { clearInterval(LAB.chainTimer); return; } LAB.chainStep++; draw(); }, 1700);
  });
  draw();
}

async function labThreshold(host, token) {
  host.innerHTML = `${labHead("Where should the line go?", "Drag the threshold: catch rough air without flagging smooth air.")}<div class="lab-svg lab-loading">Loading samples…</div>`;
  const all = await Promise.all(["jet-cat", "convective", "pirep-replay", "calm"].map(getScenario));
  if (token !== LAB.token) return;
  const smooth = [], rough = [];
  for (const D of all) for (const a of D.aircraft) a.cols.sc.forEach((v, k) => {
    if (v === null || a.cols.seg[k] !== 1) return;
    if (a.cols.truth[k] <= 30) smooth.push(v); else if (a.cols.truth[k] >= 150) rough.push(v);
  });
  host.innerHTML = `${labHead("Where should the line go?", "Synthetic level-flight samples from all four scenarios.")}
    <div id="thSvg" class="lab-svg"></div>
    <div class="lab-row"><label for="thIn">Threshold</label><input id="thIn" class="slider" type="range" min="20" max="400" step="5" value="90"><b id="thV" class="num"></b></div>
    <div class="lab-stats"><div><b id="thCatch" class="num"></b><span>of rough samples caught</span></div><div><b id="thFa" class="num"></b><span>of smooth samples flagged</span></div><div><b class="num">${(smooth.length + rough.length).toLocaleString()}</b><span>samples (smooth ≤ 30, rough ≥ 150 ft/min injected)</span></div></div>
    <p class="lab-note">On real traffic, M3 chooses this line on December data for a target false-alarm rate per 1,000 flight-hours, then measures it on January. Here the answer key is the injected turbulence.</p>`;
  const B = 50, max = 500, hist = (arr) => { const h = new Array(B).fill(0); for (const v of arr) h[Math.min(B - 1, Math.floor((v / max) * B))]++; return h.map((x) => x / arr.length); };
  const hs = hist(smooth), hr = hist(rough), top = Math.max(...hs, ...hr);
  const upd = () => {
    const thr = +$("thIn").value, W = Math.max(300, $("thSvg").clientWidth || 640), H = 110;
    const X = (v) => 8 + (v / max) * (W - 16), Y = (d) => H - 18 - (d / top) * (H - 30);
    const area = (h) => `M${X(0)},${Y(0)}` + h.map((d, i) => `L${X((i * max) / B)},${Y(d)}L${X(((i + 1) * max) / B)},${Y(d)}`).join("") + `L${X(max)},${Y(0)}Z`;
    const caught = rough.filter((v) => v >= thr).length / rough.length, fa = smooth.filter((v) => v >= thr).length / smooth.length;
    $("thSvg").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="σ_corr distributions of smooth and rough samples">
      <path d="${area(hs)}" fill="rgba(48,209,88,.28)" stroke="#30d158" stroke-width="1"/>
      <path d="${area(hr)}" fill="rgba(255,159,10,.24)" stroke="#ff9f0a" stroke-width="1"/>
      <rect x="${X(thr)}" y="4" width="${X(max) - X(thr)}" height="${H - 22}" fill="rgba(255,69,58,.07)"/>
      <line x1="${X(thr)}" x2="${X(thr)}" y1="2" y2="${H - 18}" stroke="#fff" stroke-width="2"/>
      ${[0, 100, 200, 300, 400, 500].map((t) => `<text x="${X(t) - 8}" y="${H - 4}" font-size="9.5" fill="rgba(255,255,255,.45)" font-family="${UI_FONT}">${t}</text>`).join("")}
      <text x="${X(8)}" y="14" font-size="10.5" fill="#30d158" font-family="${UI_FONT}">smooth air</text>
      <text x="${X(260)}" y="14" font-size="10.5" fill="#ff9f0a" font-family="${UI_FONT}">rough air</text></svg>`;
    $("thV").textContent = `${thr} ft/min`; $("thCatch").textContent = `${(caught * 100).toFixed(1)}%`; $("thFa").textContent = `${(fa * 100).toFixed(2)}%`;
  };
  $("thIn").addEventListener("input", upd); upd();
}

// gallery thumbnails: the whole hour of every flight, coloured by detected severity (lazy)
function drawThumb(cv, D) {
  const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  if (r.width < 2) return;
  cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr);
  const g = cv.getContext("2d"); g.scale(dpr, dpr);
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const st of S.basemap.states) for (const ring of st.rings) for (const [lon, lat] of ring) {
    const [x, y] = ALB(lon, lat); x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  const pad = 10, sc = Math.min((r.width - 2 * pad) / (x1 - x0), (r.height - 2 * pad) / (y1 - y0));
  const ox = (r.width - (x1 - x0) * sc) / 2 - x0 * sc, oy = (r.height + (y1 - y0) * sc) / 2 + y0 * sc;
  const P = (lon, lat) => { const [x, y] = ALB(lon, lat); return [ox + x * sc, oy - y * sc]; };
  const bg = g.createLinearGradient(0, 0, 0, r.height); bg.addColorStop(0, "#050505"); bg.addColorStop(1, "#0b0b0c");
  g.fillStyle = bg; g.fillRect(0, 0, r.width, r.height);
  const land = new Path2D();
  for (const st of S.basemap.states) for (const ring of st.rings) {
    ring.forEach(([lon, lat], i) => { const [x, y] = P(lon, lat); i ? land.lineTo(x, y) : land.moveTo(x, y); }); land.closePath();
  }
  g.fillStyle = "rgba(255, 255, 255, 0.06)"; g.fill(land); g.strokeStyle = "rgba(255, 255, 255, 0.14)"; g.lineWidth = 0.5; g.stroke(land);
  g.lineCap = "round";
  for (const pass of [0, 1, 2, 3]) {           // draw rough segments last so they sit on top
    for (const a of D.aircraft) {
      const c = a.cols;
      for (let j = 1; j < c.t.length; j++) {
        const s = c.sev[j];
        if ((pass === 0 && s > 0) || (pass > 0 && s !== pass)) continue;
        const [xa, ya] = P(c.lon[j - 1], c.lat[j - 1]), [xb, yb] = P(c.lon[j], c.lat[j]);
        g.strokeStyle = s < 0 ? rgba(COL.na, 0.35) : rgba(COL.sev[Math.max(s, 0)], s >= 2 ? 0.95 : 0.55); g.lineWidth = s >= 2 ? 2 : 1.1;
        g.beginPath(); g.moveTo(xa, ya); g.lineTo(xb, yb); g.stroke();
      }
    }
  }
}
function setupThumbs() {
  const cvs = [...document.querySelectorAll("[data-thumb]")];
  const load = async (cv) => { try { drawThumb(cv, await getScenario(cv.dataset.thumb)); } catch { /* thumbnail is decoration */ } };
  // local files only: load shortly after first paint rather than depending on scroll callbacks
  setTimeout(() => cvs.forEach(load), 250);
}
async function openScenario(id) {
  S.pendingScenario = id;
  if (S.view === "radar" && S.radarReady) { setPlaying(false); await loadScenario(id); return; }
  await setView("radar");
  if (S.radarReady && S.sc && S.sc.id !== id) { setPlaying(false); await loadScenario(id); }
}

// reveal-on-scroll (fade + rise) and a staged count-up for KPI numbers
function countUp(el) {
  const target = el.dataset.count, m = /^(\d+(?:\.\d+)?)(%?)$/.exec(target);
  if (!m || REDUCE) return;
  const end = parseFloat(m[1]), dec = (m[1].split(".")[1] || "").length;
  const t0 = performance.now(), dur = 1200;
  const step = (now) => {
    const f = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - f, 3);
    el.firstChild.nodeValue = f < 1 ? Math.max(0, end * e).toFixed(dec) + m[2] : target;
    if (f < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function observeReveals() {
  const pending = new Set(document.querySelectorAll(".reveal"));
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

// hero headline: letters near the pointer get heavier (Geist is a variable font)
function setupHoverWeight() {
  const h = $("heroHeading");
  if (!h || REDUCE || h.dataset.split) return;
  h.dataset.split = "1";
  const wrap = (node) => {
    for (const n of [...node.childNodes]) {
      if (n.nodeType === 3) {
        const frag = document.createDocumentFragment();
        for (const word of n.textContent.split(/(\s+)/)) {
          if (!word) continue;
          if (/^\s+$/.test(word)) { frag.appendChild(document.createTextNode(word)); continue; }
          const w = document.createElement("span"); w.className = "w"; w.style.whiteSpace = "nowrap";
          for (const ch of word) { const c = document.createElement("span"); c.className = "ch"; c.textContent = ch; w.appendChild(c); }
          frag.appendChild(w);
        }
        n.replaceWith(frag);
      } else if (n.nodeType === 1 && n.tagName !== "EM") wrap(n);
    }
  };
  wrap(h);
  const chars = [...h.querySelectorAll(".ch")];
  let rects = [];
  const measure = () => { rects = chars.map((c) => { const r = c.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }); };
  h.addEventListener("pointerenter", measure);
  h.addEventListener("pointermove", (e) => {
    if (!rects.length) measure();
    chars.forEach((c, i) => {
      const d = Math.hypot(e.clientX - rects[i][0], e.clientY - rects[i][1]);
      c.style.fontVariationSettings = `"wght" ${Math.round(300 + 520 * Math.max(0, 1 - d / 170))}`;
    });
  });
  h.addEventListener("pointerleave", () => { for (const c of chars) c.style.fontVariationSettings = ""; rects = []; });
  window.addEventListener("scroll", () => { rects = []; }, { passive: true });
}

// scenario carousel: drag to scroll; a drag never counts as a click
function setupCarousel() {
  const el = $("scenCarousel");
  if (!el || el.dataset.ready) return;
  el.dataset.ready = "1";
  let down = false, startX = 0, startL = 0, moved = 0;
  el.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "mouse") return; // touch keeps native scrolling
    down = true; moved = 0; startX = e.clientX; startL = el.scrollLeft;
  });
  window.addEventListener("pointermove", (e) => {
    if (!down) return;
    const dx = e.clientX - startX; moved = Math.max(moved, Math.abs(dx));
    if (moved > 4) el.classList.add("dragging");
    el.scrollLeft = startL - dx;
  });
  window.addEventListener("pointerup", () => { down = false; el.classList.remove("dragging"); });
  el.addEventListener("click", (e) => { if (moved > 4) { e.stopPropagation(); e.preventDefault(); } moved = 0; }, true);
  el.addEventListener("dragstart", (e) => e.preventDefault());
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
  const L = r.width > 900 ? r.width * 0.36 : 16, R = r.width - 24, T = 70, B = r.height - 70;
  const sc = Math.min((R - L) / (x1 - x0), (B - T) / (y1 - y0));
  const ox = L + (R - L - (x1 - x0) * sc) / 2 - x0 * sc, oy = T + (B - T + (y1 - y0) * sc) / 2 + y0 * sc;
  const P = (lon, lat) => { const [x, y] = ALB(lon, lat); return [ox + x * sc, oy - y * sc]; };
  PV.vp = { w: r.width, h: r.height, dpr, P };
  const off = document.createElement("canvas"); off.width = cv.width; off.height = cv.height;
  const c = off.getContext("2d"); c.scale(dpr, dpr);
  c.fillStyle = "#000"; c.fillRect(0, 0, r.width, r.height);
  const land = new Path2D();
  for (const st of S.basemap.states) for (const ring of st.rings) {
    ring.forEach(([lon, lat], i) => { const [x, y] = P(lon, lat); i ? land.lineTo(x, y) : land.moveTo(x, y); }); land.closePath();
  }
  c.fillStyle = "rgba(255, 255, 255, 0.035)"; c.fill(land); c.strokeStyle = "rgba(255, 255, 255, 0.10)"; c.lineWidth = 0.6; c.stroke(land);
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
  PV.data = await getScenario(DEFAULT_SCENARIO);
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
    <h3 title="${esc(S.sc.blurb)}">${esc(S.sc.title)}</h3>
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
    .slice(0, 3).forEach((k) => order.push(k));
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
function profileSVG(a, j, width) {
  // altitude over the whole hour; level-flight (scored) samples shaded, coloured by severity
  const c = a.cols, n = c.t.length, W = Math.max(200, Math.floor(width)), H = 46;
  // at least a 4,000 ft span, so ±25 ft encoder jitter in level flight stays flat and climbs read as steps
  const alts = c.alt.filter((v) => v !== null), mid = (Math.min(...alts) + Math.max(...alts)) / 2;
  const half = Math.max(2000, (Math.max(...alts) - Math.min(...alts)) / 2 + 300), lo = mid - half, hi = mid + half;
  const X = (k) => (k / (n - 1)) * W, Y = (v) => H - 3 - ((v - lo) / Math.max(1, hi - lo)) * (H - 8);
  let bands = "";
  for (let k = 0; k < n; k++) {
    if (c.seg[k] !== 1) continue;
    const col = c.sev[k] < 0 ? COL.na : COL.sev[c.sev[k]];
    bands += `<rect x="${X(k).toFixed(1)}" y="0" width="${(W / n + 0.6).toFixed(2)}" height="${H}" fill="${col}" fill-opacity=".16"/>`;
  }
  let d = "";
  c.alt.forEach((v, k) => { if (v !== null) d += `${d ? "L" : "M"}${X(k).toFixed(1)},${Y(v).toFixed(1)}`; });
  const pi = idxAt(S.t);
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Altitude profile over the hour">
    ${bands}<path d="${d}" fill="none" stroke="${COL.label}" stroke-opacity=".75" stroke-width="1.4"/>
    <line x1="${X(pi).toFixed(1)}" x2="${X(pi).toFixed(1)}" y1="0" y2="${H}" stroke="${COL.label3}" stroke-width="1"/>
    <line x1="${X(j).toFixed(1)}" x2="${X(j).toFixed(1)}" y1="0" y2="${H}" stroke="${COL.accent}" stroke-width="1.5"/>
    <text x="3" y="11" font-size="9.5" fill="${COL.label3}" font-family="${UI_FONT}">FL${Math.round(hi / 100)}</text>
    <text x="3" y="${H - 4}" font-size="9.5" fill="${COL.label3}" font-family="${UI_FONT}">FL${Math.round(lo / 100)}</text>
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
  const row = (k, v, tip = "") => `<div class="row"${tip ? ` title="${esc(tip)}"` : ""}><span class="k">${k}</span><span class="v">${v}</span></div>`;
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
    <p class="story" data-sev="${sev}">${story}</p>
    <div class="meta-line"><span class="num">${mmss(c.t[j])} · ${S.pinned !== null ? "Pinned" : "Live"}</span>
      ${S.pinned !== null ? '<button id="unpin" class="text-btn" type="button">Follow Playhead</button>' : "<span>Click the chart to pin a moment</span>"}</div>
    <div class="tele">
      <div title="Flight level: barometric altitude in hundreds of feet"><b>FL${fmt(c.alt[j] / 100, 0)}</b><span>Altitude</span></div>
      <div title="Ground speed in knots"><b>${fmt(c.gs[j], 0)} kt</b><span>Speed</span></div>
      <div title="Direction of travel, degrees from true north"><b>${fmt(c.hdg[j], 0)}°</b><span>Heading</span></div>
      <div title="Vertical rate as broadcast, rounded to 64 ft/min steps"><b>${fmt(c.vr[j], 0)}</b><span>VR ft/min</span></div>
    </div>
    <div class="chart-card">
      <div class="ch"><span>Roughness (σ<sub>corr</sub>) · last 10 min</span><b>${fmt(c.sc[j], 0)}<span> ft/min</span></b></div>
      <div id="chartHost"></div>
      <div class="chart-key"><span><i></i>σ<sub>corr</sub></span><span><i class="dash"></i>Injected σ (truth)</span><span><i class="res"></i>VR residual</span></div>
      <div class="profile"><div class="ph"><span>Flight profile · full hour</span><span>shaded = level flight (scored)</span></div><div id="profileHost"></div></div>
    </div>
    <div class="section-h">Detector <small>used by Horus</small></div>
    <div class="group">
      ${row("Level-flight segment", c.seg[j] === 1 ? "Yes" : "No · climb/descent", "Only level flight is scored: at or above FL200, altitude within 300 ft over 2 minutes, median vertical rate within ±200 ft/min")}
      ${row("σ<sub>corr</sub> <em>after Δ²/12</em>", fmt(c.sc[j], 0, " ft/min"), "Jitter in vertical rate (15 s window) after removing the 31 s trend and the rounding variance Δ²/12. This is what sets the colour")}
      ${row("σ<sub>raw</sub> <em>before Δ²/12</em>", fmt(c.sr[j], 0, " ft/min"), "The same jitter before the rounding correction; always slightly larger")}
      ${row("Injected σ <em>synthetic truth</em>", fmt(c.truth[j], 0, " ft/min"), "The turbulence strength the simulation put here. Real data has no such answer key")}
    </div>
    <details class="fold" data-fold="more"${open("more")}><summary>More detail<small>flicker, B1, class weights</small></summary>
      <div class="group">
        ${row("Flicker samples <em>61 s</em>", fmt(c.flk[j], 0), "Single-sample ±64 ft/min toggles (encoder noise) in the last minute; they count half")}
        ${row("B1 vertical-accel RMS", fmt(c.b1[j], 3, " g"), "Baseline detector: RMS vertical acceleration with the rounding noise removed")}
        ${row("Altitude deviation <em>vs 2-min median</em>", fmt(c.adev[j], 0, " ft"), "Descriptive only: how far altitude sits from its 2-minute median")}
        ${row("Heading rate <em>descriptive</em>", fmt(c.hrate[j], 2, " °/s"), "Descriptive only: how fast the heading is changing")}
        ${row("GS variability <em>wind-gradient proxy</em>", fmt(c.gsd[j], 1, " kt"), "Descriptive only: ground-speed wobble over a minute, a rough hint of wind changes")}
      </div>
      <div class="group fold-gap">${w === null ? row("Class weights: not assessed outside level flight", "—")
        : sv.labels.map((l, k) => `<div class="row wbar"><span>${esc(l)}</span><span class="track"><span class="fill" data-k="${k}" data-w="${(w[k] * 100).toFixed(1)}"></span></span><span class="v">${Math.round(w[k] * 100)}%</span></div>`).join("")}</div>
      <p class="footnote">Class weights are an uncalibrated preview, not a probability.</p></details>
    <details class="fold" data-fold="pireps"${open("pireps")}><summary>Pilot reports nearby<small>${ms.length} in window</small></summary>
      <div class="group">${ms.length === 0 ? row("None within ±10 min · 40 km · ±2,000 ft", "—") : ms.map((r) => `
        <div class="row pirep"><div class="row-top"><span class="k"><b data-tb="${esc(r.p.tb)}">${esc(r.p.tb)}</b> · ${esc(r.p.by)}</span>
          <span class="v">${r.d.toFixed(0)} km · ${r.dt >= 0 ? "+" : "−"}${Math.abs(r.dt / 60).toFixed(1)} min</span></div>
          <span class="txt">${esc(r.p.text)}</span></div>`).join("")}</div>
      <p class="footnote">Window ±${m.window_s / 60} min · ${m.dist_km} km · ±${m.alt_ft.toLocaleString()} ft from config.yaml (SPEC §6.1), drawn as the dashed ring on the map.</p></details>
    <p class="legal-line">Horus · MIT License · © 2026 Rohit Raj · Research prototype — not for operational use</p>`;
  const st = live.querySelector(".story");
  if (st) st.style.setProperty("--story", sev < 0 ? COL.na : COL.sev[sev]);
  for (const f of live.querySelectorAll(".fill")) { f.style.width = `${f.dataset.w}%`; f.style.background = COL.sev[+f.dataset.k]; }
  for (const b of live.querySelectorAll("[data-tb]")) b.style.color = COL.tb[b.dataset.tb];
  for (const r of live.querySelectorAll(".row-top")) { r.style.display = "flex"; r.style.justifyContent = "space-between"; r.style.gap = "10px"; }
  const host = $("chartHost");
  host.innerHTML = chartSVG(a, j, host.clientWidth || 360);
  const ph = $("profileHost");
  ph.innerHTML = profileSVG(a, j, ph.clientWidth || 360);
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
const mapTip = document.createElement("div");
mapTip.className = "map-tip hidden";
$("radar").appendChild(mapTip);
function pickPirep(mx, my) {
  if (!S.sc || !S.showPireps) return null;
  let best = null, bd = 14 * 14;
  for (const p of S.sc.pireps) {
    const age = S.t - p.t;
    if (age < 0 || age > PIREP_SHOW_S || p.fl < S.flMin || p.fl > S.flMax) continue;
    const d = (p.x - mx) ** 2 + (p.y - my) ** 2;
    if (d < bd) { bd = d; best = p; }
  }
  return best;
}
canvas.addEventListener("mousemove", (ev) => {
  const r = canvas.getBoundingClientRect();
  const pp = pickPirep(ev.clientX - r.left, ev.clientY - r.top);
  if (pp && !pick(ev.clientX - r.left, ev.clientY - r.top)) {
    mapTip.innerHTML = `<b data-tb="${esc(pp.tb)}">${esc(pp.tb)}</b> pilot report · ${esc(pp.by)} · FL${pp.fl} · ${Math.round((S.t - pp.t) / 60)} min ago<span class="txt">${esc(pp.text)}</span>`;
    mapTip.querySelector("[data-tb]").style.color = COL.tb[pp.tb];
    mapTip.style.left = `${Math.min(ev.clientX - r.left + 14, r.width - 300)}px`;
    mapTip.style.top = `${ev.clientY - r.top + 14}px`;
    mapTip.classList.remove("hidden");
  } else mapTip.classList.add("hidden");
  const hit = pick(ev.clientX - r.left, ev.clientY - r.top);
  const h = hit ? hit.k : -1;
  canvas.style.cursor = hit ? "pointer" : "default";
  if (h !== S.hover) { S.hover = h; if (!S.playing) draw(); }
});
canvas.addEventListener("mouseleave", () => { mapTip.classList.add("hidden"); });
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
    <div class="section-h">How to read the radar</div>
    <div class="guide-grid">${S.overview.read_guide.map((g) => `<div class="guide-card" data-acc="${esc(g.color || "blue")}"><b>${esc(g.title)}</b><span>${sub(g.text)}</span></div>`).join("")}</div>
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

function maybeCoach() {
  let seen = false;
  try { seen = localStorage.getItem("horus-coach") === "1"; } catch { /* storage unavailable */ }
  if (seen || H.get("coach") === "0") return;
  const tip = document.createElement("div");
  tip.className = "coach"; tip.setAttribute("role", "status");
  tip.innerHTML = '<b>Flights are listed roughest first.</b> Click any flight, or a point on its trail, to see why Horus scored it. <b>Space</b> plays the hour.<br><button type="button">Got it</button>';
  $("radar").appendChild(tip);
  tip.querySelector("button").addEventListener("click", () => {
    tip.remove();
    try { localStorage.setItem("horus-coach", "1"); } catch { /* storage unavailable */ }
  });
}

async function initRadar() {
  const seg = $("scenarios");
  seg.insertAdjacentHTML("beforeend", S.index.map((s) => `<button type="button" role="tab" data-id="${esc(s.id)}" aria-selected="false" title="${esc(s.title)}">${esc(SHORT[s.id] || s.title)}</button>`).join(""));
  for (const b of seg.querySelectorAll("button")) b.addEventListener("click", () => { setPlaying(false); loadScenario(b.dataset.id); });
  for (const b of $("speed").querySelectorAll("button")) b.setAttribute("aria-checked", String(+b.dataset.speed === S.speed));
  fitView();
  const want = S.pendingScenario || H.get("scenario");
  const sid = S.index.some((s) => s.id === want) ? want : DEFAULT_SCENARIO;
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
  maybeCoach();
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
    if (S.view === "overview") placeStageThumb();
  }).observe(document.body);
}
boot().catch((err) => {
  document.body.insertAdjacentHTML("beforeend", `<p class="fine">Could not load the demo: ${esc(err.message)}</p>`);
});
