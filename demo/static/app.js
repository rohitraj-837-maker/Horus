// Horus demo front end. Design brief: docs/design-brief.md. Every request goes to this origin.
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sub = (s) => esc(s).replace(/σ²_corr/g, "σ²<sub>corr</sub>").replace(/σ²_raw/g, "σ²<sub>raw</sub>").replace(/σ_corr/g, "σ<sub>corr</sub>");
const fmt = (v, d = 0, unit = "") => {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = Number(v).toFixed(d);
  return `${/^-0(\.0*)?$/.test(s) ? s.slice(1) : s}${unit}`;
};
const tplus = (s) => `T+${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const REDUCE = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
document.documentElement.classList.add("js");
const UI = "Geist, -apple-system, BlinkMacSystemFont, sans-serif";
const MONO = "'SF Mono', ui-monospace, Menlo, monospace";
const DEFAULT_SCENARIO = "jet-cat";
const PLANE = new Path2D("M0,-8 L1.3,-4.6 L1.3,-1.4 L8,2.6 L8,4 L1.3,2.2 L1.1,5.6 L3.4,7.2 L3.4,8.2 L0,7.4 L-3.4,8.2 L-3.4,7.2 L-1.1,5.6 L-1.3,2.2 L-8,4 L-8,2.6 L-1.3,-1.4 L-1.3,-4.6 Z");
const H = new URLSearchParams(location.hash.slice(1)); // deep links: view, scenario, t, sel, pin, stage

// colours come from the stylesheet so there is one source of truth
const css = getComputedStyle(document.documentElement);
const C = {
  text: css.getPropertyValue("--text").trim(), text2: css.getPropertyValue("--text-2").trim(), text3: css.getPropertyValue("--text-3").trim(),
  cls: ["--smooth", "--light", "--moderate", "--severe"].map((v) => css.getPropertyValue(v).trim()),
  unscored: css.getPropertyValue("--unscored").trim(),
};
C.tb = { NEG: C.cls[0], LGT: C.cls[1], MOD: C.cls[2], SEV: C.cls[3] };
const sevColor = (s) => (s < 0 ? C.unscored : C.cls[s]);
const rgba = (hex, a) => { const v = parseInt(hex.slice(1), 16); return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`; };

const S = {
  view: "overview", overview: null, about: null, basemap: null, index: [],
  sc: null, radarReady: false, t: 0, playing: false, speed: 60, flMin: 180, flMax: 450,
  sel: -1, pinned: null, hover: -1, lastJ: -1, lastListI: -1,
  layers: { trails: true, pireps: true, field: false, labels: true },
  vp: null, base: null, pending: null,
};

async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json(); }
const CACHE = {};
const getScenario = (id) => (CACHE[id] ||= getJSON(`/api/scenario/${encodeURIComponent(id)}`));

// ---------------------------------------------------------------- geometry
const ALB = (() => { // Albers equal-area conic for the continental US
  const r = Math.PI / 180, p1 = 29.5 * r, p2 = 45.5 * r, p0 = 37.5 * r, l0 = -96 * r;
  const n = (Math.sin(p1) + Math.sin(p2)) / 2, C2 = Math.cos(p1) ** 2 + 2 * n * Math.sin(p1), rho0 = Math.sqrt(C2 - 2 * n * Math.sin(p0)) / n;
  return (lon, lat) => { const rho = Math.sqrt(C2 - 2 * n * Math.sin(lat * r)) / n, th = n * (lon * r - l0); return [rho * Math.sin(th), rho0 - rho * Math.cos(th)]; };
})();
const KM = 111.195;
const offset = (lat, lon, e, n) => [lat + n / KM, lon + e / (KM * Math.cos((lat * Math.PI) / 180))];
function haversine(a1, o1, a2, o2) {
  const r = Math.PI / 180, h = Math.sin(((a2 - a1) * r) / 2) ** 2 + Math.cos(a1 * r) * Math.cos(a2 * r) * Math.sin(((o2 - o1) * r) / 2) ** 2;
  return 2 * 6371.0088 * Math.asin(Math.sqrt(h));
}
function fitter(L, T, R, B) { // returns lon/lat -> screen within the box
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const st of S.basemap.states) for (const ring of st.rings) for (const [lo, la] of ring) {
    const [x, y] = ALB(lo, la); x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  const sc = Math.min((R - L) / (x1 - x0), (B - T) / (y1 - y0));
  const ox = L + (R - L - (x1 - x0) * sc) / 2 - x0 * sc, oy = T + (B - T + (y1 - y0) * sc) / 2 + y0 * sc;
  return (lon, lat) => { const [x, y] = ALB(lon, lat); return [ox + x * sc, oy - y * sc]; };
}
function landPath(P) {
  const p = new Path2D();
  for (const st of S.basemap.states) for (const ring of st.rings) { ring.forEach(([lo, la], i) => { const [x, y] = P(lo, la); i ? p.lineTo(x, y) : p.moveTo(x, y); }); p.closePath(); }
  return p;
}
function sizeCanvas(cv) {
  const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  cv.width = Math.max(1, Math.round(r.width * dpr)); cv.height = Math.max(1, Math.round(r.height * dpr));
  const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { g, w: r.width, h: r.height, dpr };
}
function mostTurbulent(D) { // highest sustained σ_corr (70 s mean): a single 15 s estimate can spike
  let best = { k: 0, j: 0, v: -1 };
  D.aircraft.forEach((a, k) => { const s = a.cols.sc; for (let j = 3; j < s.length - 3; j++) { let t = 0, n = 0; for (let q = j - 3; q <= j + 3; q++) if (s[q] !== null) { t += s[q]; n++; } if (n >= 5 && t / n > best.v) best = { k, j, v: t / n }; } });
  return best;
}

// ---------------------------------------------------------------- header + views
function tickUTC() { const d = new Date(); $("utc").textContent = `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}Z`; }
tickUTC(); setInterval(tickUTC, 15000);

async function setView(v) {
  S.view = v;
  document.documentElement.dataset.view = v;
  for (const b of document.querySelectorAll(".views button")) b.setAttribute("aria-selected", String(b.dataset.view === v));
  try { history.replaceState(null, "", v === "radar" ? "#view=radar" : location.pathname); } catch { /* file: */ }
  window.scrollTo(0, 0);
  if (v === "radar") {
    SCENE.stop();
    if (!S.radarReady) { S.radarReady = true; await initRadar(); }
    else { fitMap(); draw(); }
    if (S.pending && S.sc && S.sc.id !== S.pending) await loadScenario(S.pending);
    S.pending = null;
  } else { setPlaying(false); hideLegend(); SCENE.start(); }
}
for (const b of document.querySelectorAll(".views button")) b.addEventListener("click", () => setView(b.dataset.view));
for (const b of document.querySelectorAll("[data-go]")) b.addEventListener("click", () => setView(b.dataset.go));
document.querySelector(".brand").addEventListener("click", (e) => { e.preventDefault(); setView("overview"); });

// ---------------------------------------------------------------- overview
function renderOverview() {
  const O = S.overview;
  $("problem").innerHTML = O.problem.map((x, i) => `<div><span class="n">0${i + 1}</span><h3>${esc(x.title)}</h3><p>${esc(x.text)}</p></div>`).join("");

  const word = { built: "Built", "core built": "Core built", planned: "Planned" };
  $("steps").innerHTML = O.pipeline.map((p, i) => `<li role="tab" tabindex="0" data-i="${i}" aria-selected="false" aria-controls="stepPanel"><span class="t">${esc(p.title)}</span><span class="st">${word[p.status]}</span></li>`).join("");
  for (const li of $("steps").querySelectorAll("li")) {
    li.addEventListener("click", () => selectStep(+li.dataset.i));
    li.addEventListener("keydown", (e) => {
      const n = O.pipeline.length, i = +li.dataset.i;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectStep(i); }
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); const k = (i + (e.key === "ArrowRight" ? 1 : n - 1)) % n; selectStep(k); $("steps").children[k].focus(); }
    });
  }
  selectStep(Math.min(O.pipeline.length - 1, Math.max(0, H.has("stage") ? +H.get("stage") : 3)));

  $("kpis").innerHTML = O.kpis.map((k) => `<div class="kpi">
      <span class="v" data-count="${esc(k.value)}">${esc(k.value)}${k.unit ? `<small>${esc(k.unit)}</small>` : ""}</span>
      <span class="s">${sub(k.short || "")}</span><span class="k">${sub(k.label)}</span>
      <details><summary>What this shows</summary><p class="d">${sub(k.detail)}</p><p class="c">${sub(k.caveat)}</p></details></div>`).join("");

  const chip = { "jet-cat": "Clear-air turbulence", convective: "Thunderstorms", calm: "Control: no turbulence", "pirep-replay": "Validation" };
  $("skies").innerHTML = O.scenarios.map((c) => `<button type="button" class="sky" data-open="${esc(c.id)}">
      <canvas data-thumb="${esc(c.id)}" aria-hidden="true"></canvas>
      <span class="k">${esc(chip[c.id] || "Scenario")}</span><h3>${esc(c.title)}</h3><p>${esc(c.blurb)}</p>
      <span class="m"><b>${c.aircraft}</b> flights · <b>${c.mod_plus}</b>/${c.pireps} MOD+ reports${c.recovery === null ? "" : ` · σ recovered <b>${c.recovery.toFixed(2)}</b>`}</span><br>
      <span class="o">Open in radar</span></button>`).join("");
  for (const b of $("skies").querySelectorAll("[data-open]")) b.addEventListener("click", () => openScenario(b.dataset.open));
  setTimeout(() => { for (const cv of $("skies").querySelectorAll("canvas")) getScenario(cv.dataset.thumb).then((D) => drawThumb(cv, D)).catch(() => {}); }, 200);

  const cmp = O.compare;
  $("compareNote").textContent = `What each source says about itself on its public pages, checked ${cmp.checked}. PIREP facts come from the Horus spec and data notes.`;
  $("compare").innerHTML = `<thead><tr><th></th>${cmp.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${cmp.rows.map((r) => `<tr class="${r.self ? "self" : ""}"><td>${esc(r.name)}<small>${esc(r.kind)}</small></td>${r.cells.map((c) => `<td>${sub(c)}</td>`).join("")}</tr>`).join("")}</tbody>`;

  $("roadmapNote").textContent = `Setup plus seven weekly milestones, each closed by an automated test gate. Status as of ${O.milestones_as_of}.`;
  $("roadmap").innerHTML = O.milestones.map((m) => `<li class="${m.status}"><span class="m">${esc(m.m)}</span><h4>${esc(m.title)}</h4><p>${esc(m.note)}</p></li>`).join("");
  const B = O.benchmarks;
  $("bench").innerHTML = `<thead><tr><th>Row</th><th>Method</th><th class="num-c">PR-AUC</th><th class="num-c">Recall @ 1 FA / 1,000 h</th><th>When</th></tr></thead><tbody>${B.rows.map((r) => `<tr><td class="id">${esc(r[0])}</td><td>${sub(r[1])}</td><td class="num-c">${r[2] === null ? "—" : esc(r[2])}</td><td class="num-c">${r[3] === null ? "—" : esc(r[3])}</td><td>${esc(r[4])}</td></tr>`).join("")}</tbody>`;
  $("maths").innerHTML = O.maths.map((m) => `<div class="m"><h5>${esc(m.title)}</h5><code>${sub(m.formula)}</code><p>${sub(m.note)}</p></div>`).join("");
  $("glossary").innerHTML = O.glossary.map((g) => `<div><dt>${sub(g.term)}</dt><dd>${sub(g.text)}</dd></div>`).join("");
  $("limits").innerHTML = O.limitations.map((x) => `<li>${sub(x)}</li>`).join("");
  const A = S.about;
  $("sources").innerHTML = A.attributions.map((x) => `<p><b>${esc(x.name)}</b>${esc(x.text)}<br><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.url)}</a></p>`).join("") + `<p>${esc(A.disclaimer)}</p>`;
  reveal();
}

function reveal() { // slow fade for text blocks; content never depends on observer callbacks alone
  const pending = new Set(document.querySelectorAll(".fade"));
  const show = (el) => { if (!pending.delete(el)) return; el.classList.add("in"); const v = el.querySelector("[data-count]"); if (v) countUpAll(el); };
  if (REDUCE) { pending.forEach((e) => e.classList.add("in")); return; }
  const check = () => { const vh = innerHeight; for (const el of [...pending]) { const r = el.getBoundingClientRect(); if (r.top < vh * 0.92 && r.bottom > 0) show(el); } };
  if ("IntersectionObserver" in window) { const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { show(e.target); io.unobserve(e.target); } }), { threshold: 0.12 }); pending.forEach((e) => io.observe(e)); }
  addEventListener("scroll", check, { passive: true }); requestAnimationFrame(check); check();
}
function countUpAll(root) {
  for (const el of root.querySelectorAll("[data-count]")) {
    const target = el.dataset.count, m = /^(\d+(?:\.\d+)?)(%?)$/.exec(target);
    if (!m || REDUCE) continue;
    const end = parseFloat(m[1]), dec = (m[1].split(".")[1] || "").length, t0 = performance.now();
    const step = (now) => { const f = Math.min(1, (now - t0) / 1400), e = 1 - (1 - f) ** 3; el.firstChild.nodeValue = f < 1 ? Math.max(0, end * e).toFixed(dec) + m[2] : target; if (f < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }
}

function selectStep(i) {
  const p = S.overview.pipeline[i], n = S.overview.pipeline.length;
  for (const li of $("steps").children) li.setAttribute("aria-selected", String(+li.dataset.i === i));
  const word = { built: "Built", "core built": "Core built", planned: "Planned" }[p.status] + (p.note ? ` · ${p.note}` : "");
  $("stepPanel").innerHTML = `<div class="step-in">
    <div class="lab" id="lab"></div>
    <div class="step-grid">
      <div><h4>Step ${i + 1} of ${n} · ${esc(word)}</h4><p>${sub(p.what)}</p></div>
      <div><h4>Why it exists</h4><p>${sub(p.why)}</p></div>
      <div><h4>By the numbers</h4><ul>${p.facts.map((f) => `<li>${sub(f)}</li>`).join("")}</ul></div>
    </div></div>`;
  LAB.run(p.lab);
}

// ---------------------------------------------------------------- labs (constants from /api/overview.lab or the scenario data)
const LAB = {
  token: 0, timer: 0, chainStep: 1,
  run(kind) { clearInterval(this.timer); const tok = ++this.token; const f = { credits: labCredits, cadence: labCadence, units: labUnits, chain: labChain, threshold: labThreshold }[kind]; if (f) f($("lab"), tok); },
};
const labTop = (t, h) => `<div class="lab-top"><b>${t}</b><span>${h}</span></div>`;
const svgText = (x, y, s, fill = "rgba(236,235,232,.45)", size = 10) => `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" font-family="${UI}">${s}</text>`;

function labCredits(el) {
  const L = S.overview.lab;
  el.innerHTML = `${labTop("Credit budget", "How often can a free OpenSky account ask for the whole US?")}
    <div class="lab-row"><label for="lcIn">Snapshot every</label><input id="lcIn" type="range" min="15" max="300" step="5" value="${L.poll_interval_s}"><output id="lcV"></output></div>
    <div class="meter"><i id="lcBar"></i><span></span></div>
    <div class="lab-out"><div><b id="lcA"></b><span>snapshots a day</span></div><div><b id="lcB"></b><span>credits of ${L.daily_credits.toLocaleString()}</span></div><div><b id="lcC"></b><span>until the credits run out</span></div></div>
    <p class="lab-note" id="lcN"></p>`;
  const up = () => {
    const iv = +$("lcIn").value, calls = Math.ceil(86400 / iv), cred = calls * L.credit_cost, ok = cred <= L.daily_credits, hrs = Math.min(24, ((L.daily_credits / L.credit_cost) * iv) / 3600);
    $("lcV").textContent = `${iv} s`; $("lcA").textContent = calls.toLocaleString(); $("lcB").textContent = cred.toLocaleString(); $("lcC").textContent = ok ? "never" : `${hrs.toFixed(1)} h`;
    const bar = $("lcBar"); bar.style.width = `${Math.min(100, (cred / (2 * L.daily_credits)) * 100)}%`; bar.style.background = ok ? C.text : C.cls[3];
    $("lcN").textContent = ok ? `Fits: ${L.credit_cost} credits a request. The recorder polls every ${L.poll_interval_s} s and slows itself using the credits-remaining header.` : `Over budget: the feed would go dark for ${(24 - hrs).toFixed(1)} h before the 00:00 UTC reset. This is why live data can't be every 5–10 s.`;
  };
  $("lcIn").addEventListener("input", up); up();
}

function labCadence(el) {
  const L = S.overview.lab, modes = [["trino", "OpenSky Trino · 1 s"], ["adsblol", "adsb.lol · ~20 s"], ["live", "Live feed · 90 s"]];
  el.innerHTML = `${labTop("Samples per window", "The detector measures jitter in a short window. How many points land in it?")}
    <div class="chips" role="radiogroup">${modes.map(([k, l], i) => `<button type="button" role="radio" data-m="${k}" aria-checked="${i === 1}">${l}</button>`).join("")}</div>
    <div class="lab-svg" id="cdSvg"></div>
    <div class="lab-out"><div><b id="cdA"></b><span>points in the ${L.w_stat_s} s roughness window</span></div><div><b id="cdB"></b><span>points in the ${L.w_trend_s} s trend window</span></div><div><b>${L.min_samples_stat}</b><span>needed for a roughness estimate</span></div></div>
    <p class="lab-note" id="cdN"></p>`;
  const gaps = [20, 19, 2, 20, 8, 20, 19, 20, 2, 20, 19, 8, 20, 20, 19];
  const times = (m) => { const o = []; let t = 0, k = 0; while (t <= 180) { o.push(t); t += m === "trino" ? 1 : m === "live" ? L.poll_interval_s : gaps[k++ % gaps.length]; } return o; };
  const draw = (m) => {
    const W = Math.max(300, $("cdSvg").clientWidth || 600), Hh = 64, X = (t) => 6 + (t / 180) * (W - 12), c = 90;
    const s0 = c - L.w_stat_s / 2, s1 = c + L.w_stat_s / 2, t0 = c - L.w_trend_s / 2, t1 = c + L.w_trend_s / 2, ts = times(m);
    const nS = ts.filter((t) => t >= s0 && t <= s1).length, nT = ts.filter((t) => t >= t0 && t <= t1).length;
    $("cdSvg").innerHTML = `<svg viewBox="0 0 ${W} ${Hh}" width="${W}" height="${Hh}" role="img" aria-label="Sample times over three minutes">
      <rect x="${X(t0)}" y="10" width="${X(t1) - X(t0)}" height="36" fill="rgba(236,235,232,.04)" stroke="rgba(236,235,232,.25)"/>
      <rect x="${X(s0)}" y="16" width="${X(s1) - X(s0)}" height="24" fill="rgba(236,235,232,.10)" stroke="rgba(236,235,232,.8)"/>
      <line x1="${X(0)}" x2="${X(180)}" y1="28" y2="28" stroke="rgba(255,255,255,.15)"/>
      ${ts.map((t) => `<circle cx="${X(t).toFixed(1)}" cy="28" r="${m === "trino" ? 1.4 : 3}" fill="${t >= s0 && t <= s1 ? C.text : "rgba(236,235,232,.4)"}"/>`).join("")}
      ${svgText(X(0), 62, "0 s")}${svgText(X(180) - 24, 62, "180 s")}</svg>`;
    $("cdA").textContent = nS; $("cdB").textContent = nT;
    $("cdN").textContent = nS >= L.min_samples_stat ? "Enough points: roughness can be measured every second." : `Too few points for a ${L.w_stat_s} s window${m === "adsblol" ? ` (gaps drawn from the measured spread: 10–90 % = ${L.adsblol_gap_percentiles_s.join(" / ")} s)` : ""}. Longer, rate-specific windows are part of the M3 benchmark.`;
  };
  for (const b of el.querySelectorAll(".chips button")) b.addEventListener("click", () => { for (const x of el.querySelectorAll(".chips button")) x.setAttribute("aria-checked", String(x === b)); draw(b.dataset.m); });
  draw("adsblol");
}

function labUnits(el) {
  const L = S.overview.lab, step = L.step_fpm;
  el.innerHTML = `${labTop("Rounding to the 64 ft/min grid", "Drag a true climb rate and see what the aircraft actually broadcasts.")}
    <div class="lab-row"><label for="unIn">True vertical rate</label><input id="unIn" type="range" min="-8" max="8" step="0.05" value="1.3"><output id="unV"></output></div>
    <div class="lab-svg" id="unSvg"></div>
    <div class="lab-out"><div><b id="unA"></b><span>ft/min (m/s × ${L.fpm_per_mps.toFixed(3)})</span></div><div><b id="unB"></b><span>ft/min broadcast</span></div><div><b id="unC"></b><span>ft/min rounding error</span></div></div>
    <p class="lab-note">The error is always within ±${step / 2} ft/min. Spread evenly, it adds variance Δ²/12 = ${((step * step) / 12).toFixed(1)} (ft/min)²: the amount step 4 subtracts.</p>`;
  const up = () => {
    const v = +$("unIn").value, f = v * L.fpm_per_mps, b = step * Math.round(f / step), e = f - b;
    $("unV").textContent = `${v.toFixed(2)} m/s`; $("unA").textContent = f.toFixed(1); $("unB").textContent = b.toFixed(0); $("unC").textContent = `${e >= 0 ? "+" : ""}${e.toFixed(1)}`;
    const W = Math.max(300, $("unSvg").clientWidth || 600), lo = Math.floor(f / step) * step - 3 * step, hi = lo + 7 * step, X = (x) => 14 + ((x - lo) / (hi - lo)) * (W - 28);
    let ticks = ""; for (let t = lo; t <= hi; t += step) ticks += `<line x1="${X(t)}" x2="${X(t)}" y1="14" y2="30" stroke="rgba(236,235,232,.35)"/>${svgText(X(t) - 10, 46, t)}`;
    $("unSvg").innerHTML = `<svg viewBox="0 0 ${W} 52" width="${W}" height="52" role="img" aria-label="Rounding to the 64 ft/min grid"><line x1="14" x2="${W - 14}" y1="22" y2="22" stroke="rgba(255,255,255,.12)"/>${ticks}
      <line x1="${X(f)}" x2="${X(b)}" y1="22" y2="22" stroke="${C.cls[2]}" stroke-width="2"/><circle cx="${X(f)}" cy="22" r="4" fill="${C.text}"/><circle cx="${X(b)}" cy="22" r="6" fill="none" stroke="${C.text}"/></svg>`;
  };
  $("unIn").addEventListener("input", up); up();
}

async function labChain(el, tok) {
  el.innerHTML = `${labTop("One flight through the chain", "Loading a flight…")}`;
  const D = await getScenario(DEFAULT_SCENARIO);
  if (tok !== LAB.token) return;
  const best = mostTurbulent(D), a = D.aircraft[best.k], c = a.cols, j0 = Math.max(0, best.j - 90), j1 = Math.min(c.t.length - 1, best.j + 60);
  const steps = [
    ["Raw vertical rate", `What ${a.callsign} broadcasts: 64 ft/min steps, autopilot drift and turbulence mixed together.`],
    ["Trend", "A 31-second rolling median follows the slow drift and ignores fast jitter."],
    ["Residual", "Raw minus trend: only the fast jitter is left. Calm stretches hug zero."],
    ["Roughness", "The spread of the residual over 15 s, minus the rounding variance Δ²/12. This number sets the colour."],
  ];
  el.innerHTML = `${labTop(`${esc(a.callsign)} through the chain`, "Synthetic Jet Stream flight; one point every 10 s.")}
    <div class="chips" role="tablist">${steps.map(([l], i) => `<button type="button" role="tab" data-s="${i + 1}" aria-checked="${i + 1 === LAB.chainStep}">${i + 1}. ${l}</button>`).join("")}<button type="button" class="go" id="chGo">Play</button></div>
    <div class="lab-svg" id="chSvg"></div><p class="lab-note" id="chN"></p>`;
  const draw = () => {
    const st = LAB.chainStep, W = Math.max(320, $("chSvg").clientWidth || 640), H1 = 120, G = 14, H2 = 72, X = (k) => 6 + ((k - j0) / (j1 - j0)) * (W - 56);
    let vmax = 200; for (let k = j0; k <= j1; k++) if (c.vr[k] !== null) vmax = Math.max(vmax, Math.abs(c.vr[k]));
    const Y = (v) => H1 / 2 - (v / vmax) * (H1 / 2 - 6);
    const line = (f) => { let d = "", pen = false; for (let k = j0; k <= j1; k++) { const v = f(k); if (v === null || Number.isNaN(v)) { pen = false; continue; } d += `${pen ? "L" : "M"}${X(k).toFixed(1)},${v.toFixed(1)}`; pen = true; } return d; };
    const smax = Math.max(360, ...c.sc.slice(j0, j1 + 1).filter((v) => v !== null)) * 1.05, Ys = (v) => H1 + G + H2 - 4 - (v / smax) * (H2 - 10);
    const thr = D.severity.edges.map((e, i) => (e < smax ? `<line x1="6" x2="${W - 50}" y1="${Ys(e)}" y2="${Ys(e)}" stroke="${C.cls[i + 1]}" stroke-opacity=".7" stroke-dasharray="3 3"/>${svgText(W - 44, Ys(e) + 3, e, C.cls[i + 1])}` : "")).join("");
    $("chSvg").innerHTML = `<svg viewBox="0 0 ${W} ${H1 + G + H2}" width="${W}" height="${H1 + G + H2}" role="img" aria-label="Signal processing chain">
      <line x1="6" x2="${W - 50}" y1="${Y(0)}" y2="${Y(0)}" stroke="rgba(255,255,255,.1)"/>
      <path d="${line((k) => (c.vr[k] === null ? null : Y(c.vr[k])))}" fill="none" stroke="rgba(236,235,232,${st === 1 ? 0.85 : 0.2})" stroke-width="1.1"/>
      ${st >= 2 ? `<path class="draw" pathLength="1" d="${line((k) => (c.vr[k] === null || c.res[k] === null ? null : Y(c.vr[k] - c.res[k])))}" fill="none" stroke="${C.text}" stroke-width="${st === 2 ? 2.2 : 1.2}" stroke-opacity="${st === 2 ? 1 : 0.35}"/>` : ""}
      ${st >= 3 ? `<path class="draw" pathLength="1" d="${line((k) => (c.res[k] === null ? null : Y(c.res[k])))}" fill="none" stroke="${C.cls[2]}" stroke-width="1.5" stroke-opacity="${st === 3 ? 1 : 0.5}"/>` : ""}
      ${svgText(W - 44, 12, "ft/min")}
      ${st >= 4 ? `${thr}<path class="draw" pathLength="1" d="${line((k) => (c.sc[k] === null ? null : Ys(c.sc[k])))}" fill="none" stroke="${C.text}" stroke-width="2"/>${svgText(8, H1 + G + 10, "roughness")}` : svgText(8, H1 + G + H2 / 2, "roughness appears at step 4", "rgba(236,235,232,.3)", 11)}
    </svg>`;
    $("chN").textContent = steps[st - 1][1];
    for (const b of el.querySelectorAll("[data-s]")) b.setAttribute("aria-checked", String(+b.dataset.s === st));
  };
  for (const b of el.querySelectorAll("[data-s]")) b.addEventListener("click", () => { clearInterval(LAB.timer); LAB.chainStep = +b.dataset.s; draw(); });
  $("chGo").addEventListener("click", () => { clearInterval(LAB.timer); LAB.chainStep = 1; draw(); LAB.timer = setInterval(() => { if (LAB.chainStep >= 4) return clearInterval(LAB.timer); LAB.chainStep++; draw(); }, 1700); });
  draw();
}

async function labThreshold(el, tok) {
  el.innerHTML = `${labTop("Where should the line go?", "Loading samples…")}`;
  const all = await Promise.all(["jet-cat", "convective", "pirep-replay", "calm"].map(getScenario));
  if (tok !== LAB.token) return;
  const smooth = [], rough = [];
  for (const D of all) for (const a of D.aircraft) a.cols.sc.forEach((v, k) => { if (v === null || a.cols.seg[k] !== 1) return; if (a.cols.truth[k] <= 30) smooth.push(v); else if (a.cols.truth[k] >= 150) rough.push(v); });
  el.innerHTML = `${labTop("Where should the line go?", "Drag the threshold: catch rough air without flagging smooth air.")}
    <div class="lab-svg" id="thSvg"></div>
    <div class="lab-row"><label for="thIn">Threshold</label><input id="thIn" type="range" min="20" max="400" step="5" value="90"><output id="thV"></output></div>
    <div class="lab-out"><div><b id="thA"></b><span>of rough samples caught</span></div><div><b id="thB"></b><span>of smooth samples flagged</span></div><div><b>${(smooth.length + rough.length).toLocaleString()}</b><span>synthetic level-flight samples</span></div></div>
    <p class="lab-note">Here the answer key is the injected turbulence (smooth ≤ 30, rough ≥ 150 ft/min). On real traffic, M3 picks this line on December data for a target false-alarm rate, then measures it on January.</p>`;
  const Bn = 50, max = 500, hist = (arr) => { const h = new Array(Bn).fill(0); for (const v of arr) h[Math.min(Bn - 1, Math.floor((v / max) * Bn))]++; return h.map((x) => x / arr.length); };
  const hs = hist(smooth), hr = hist(rough), top = Math.max(...hs, ...hr);
  const up = () => {
    const thr = +$("thIn").value, W = Math.max(320, $("thSvg").clientWidth || 640), Hh = 116, X = (v) => 6 + (v / max) * (W - 12), Y = (d) => Hh - 18 - (d / top) * (Hh - 30);
    const area = (h) => `M${X(0)},${Y(0)}` + h.map((d, i) => `L${X((i * max) / Bn)},${Y(d)}L${X(((i + 1) * max) / Bn)},${Y(d)}`).join("") + `L${X(max)},${Y(0)}Z`;
    const caught = rough.filter((v) => v >= thr).length / rough.length, fa = smooth.filter((v) => v >= thr).length / smooth.length;
    $("thSvg").innerHTML = `<svg viewBox="0 0 ${W} ${Hh}" width="${W}" height="${Hh}" role="img" aria-label="Roughness of smooth and rough samples">
      <path d="${area(hs)}" fill="${rgba(C.cls[0], 0.35)}" stroke="${C.cls[0]}"/><path d="${area(hr)}" fill="${rgba(C.cls[2], 0.25)}" stroke="${C.cls[2]}"/>
      <line x1="${X(thr)}" x2="${X(thr)}" y1="0" y2="${Hh - 18}" stroke="${C.text}" stroke-width="1.5"/>
      ${[0, 100, 200, 300, 400, 500].map((t) => svgText(X(t) - 8, Hh - 4, t)).join("")}${svgText(X(6), 12, "smooth air", C.cls[0], 11)}${svgText(X(250), 12, "rough air", C.cls[2], 11)}</svg>`;
    $("thV").textContent = `${thr} ft/min`; $("thA").textContent = `${(caught * 100).toFixed(1)}%`; $("thB").textContent = `${(fa * 100).toFixed(2)}%`;
  };
  $("thIn").addEventListener("input", up); up();
}

function drawThumb(cv, D) {
  const { g, w, h } = sizeCanvas(cv), P = fitter(10, 10, w - 10, h - 10);
  g.fillStyle = "#000"; g.fillRect(0, 0, w, h);
  const land = landPath(P); g.fillStyle = "rgba(255,255,255,0.03)"; g.fill(land); g.strokeStyle = "rgba(255,255,255,0.08)"; g.lineWidth = 0.5; g.stroke(land);
  g.lineCap = "round";
  for (const pass of [0, 1, 2, 3]) for (const a of D.aircraft) { const c = a.cols; for (let j = 1; j < c.t.length; j++) { const s = c.sev[j]; if ((pass === 0 && s > 0) || (pass > 0 && s !== pass)) continue; const [xa, ya] = P(c.lon[j - 1], c.lat[j - 1]), [xb, yb] = P(c.lon[j], c.lat[j]); g.strokeStyle = s <= 0 ? "rgba(236,235,232,0.22)" : rgba(C.cls[s], 0.95); g.lineWidth = s >= 2 ? 1.8 : 0.9; g.beginPath(); g.moveTo(xa, ya); g.lineTo(xb, yb); g.stroke(); } }
}

function openScenario(id) { S.pending = id; setView("radar"); }

// ---------------------------------------------------------------- scene: night sky, horizon, aircraft lights
const SCENE = {
  raf: 0, t: 1400, last: 0, D: null, stars: null, P: null, w: 0, h: 0, visible: true,
  async init() {
    this.D = await getScenario(DEFAULT_SCENARIO);
    new IntersectionObserver((es) => es.forEach((e) => { this.visible = e.isIntersecting; if (this.visible) this.start(); })).observe($("scene"));
    this.layout(); this.start();
  },
  layout() {
    const cv = $("scene"), { w, h } = sizeCanvas(cv); this.w = w; this.h = h;
    let seed = 7; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    this.stars = Array.from({ length: Math.round((w * h) / 2600) }, () => [rnd() * w, rnd() * h * 0.82, rnd() ** 3 * 1.3 + 0.2, rnd()]);
    this.P = fitter(w * (w > 900 ? 0.28 : 0.06), h * 0.18, w * 0.96, h * 0.74);
    this.land = landPath(this.P);
    for (const a of this.D.aircraft) { const n = a.cols.t.length; a.sx = new Float32Array(n); a.sy = new Float32Array(n); for (let i = 0; i < n; i++) { const [x, y] = this.P(a.cols.lon[i], a.cols.lat[i]); a.sx[i] = x; a.sy[i] = y; } }
  },
  frame() {
    const g = $("scene").getContext("2d"), { w, h } = this, D = this.D;
    g.fillStyle = "#000"; g.fillRect(0, 0, w, h);
    for (const [x, y, r, tw] of this.stars) { g.globalAlpha = 0.25 + 0.5 * tw; g.fillStyle = "#fff"; g.beginPath(); g.arc(x, y, r, 0, 2 * Math.PI); g.fill(); }
    g.globalAlpha = 1;
    // horizon: a large planet limb with a thin atmosphere
    const R = Math.max(w, h) * 1.6, cx = w * 0.5, cy = h * 0.86 + R;
    const haze = g.createRadialGradient(cx, cy, R - 4, cx, cy, R + 120);
    haze.addColorStop(0, "rgba(190,200,210,0.20)"); haze.addColorStop(0.25, "rgba(150,165,180,0.07)"); haze.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = haze; g.beginPath(); g.arc(cx, cy, R + 120, 0, 2 * Math.PI); g.fill();
    g.fillStyle = "#030405"; g.beginPath(); g.arc(cx, cy, R, 0, 2 * Math.PI); g.fill();
    g.strokeStyle = "rgba(225,232,240,0.55)"; g.lineWidth = 1; g.beginPath(); g.arc(cx, cy, R, Math.PI * 1.2, Math.PI * 1.8); g.stroke();
    // the US as a faint constellation, aircraft as lights
    g.strokeStyle = "rgba(255,255,255,0.05)"; g.lineWidth = 0.6; g.stroke(this.land);
    const i = Math.min(D.aircraft[0].cols.t.length - 1, Math.floor(this.t / D.stride_s)), trail = 30;
    g.lineCap = "round";
    for (const a of D.aircraft) {
      for (let j = Math.max(1, i - trail); j <= i; j++) { const s = a.cols.sev[j], f = 1 - (i - j) / trail; g.strokeStyle = s >= 1 ? rgba(C.cls[s], 0.9 * f) : `rgba(236,235,232,${0.35 * f})`; g.lineWidth = s >= 2 ? 1.8 : 1; g.beginPath(); g.moveTo(a.sx[j - 1], a.sy[j - 1]); g.lineTo(a.sx[j], a.sy[j]); g.stroke(); }
      const s = a.cols.sev[i], x = a.sx[i], y = a.sy[i];
      if (s >= 2) { const gl = g.createRadialGradient(x, y, 0, x, y, 16); gl.addColorStop(0, rgba(C.cls[s], 0.45)); gl.addColorStop(1, rgba(C.cls[s], 0)); g.fillStyle = gl; g.beginPath(); g.arc(x, y, 16, 0, 2 * Math.PI); g.fill(); }
      g.fillStyle = s >= 1 ? C.cls[s] : "#f2f1ee"; g.beginPath(); g.arc(x, y, s >= 2 ? 2.2 : 1.6, 0, 2 * Math.PI); g.fill();
    }
  },
  tick(ts) {
    if (!this.visible || S.view !== "overview") { this.raf = 0; return; }
    if (this.last) this.t = (this.t + ((ts - this.last) / 1000) * 90) % (this.D.duration_s - this.D.stride_s);
    this.last = ts; this.frame(); this.raf = requestAnimationFrame((t) => this.tick(t));
  },
  start() { if (!this.D || this.raf) return; this.last = 0; if (REDUCE) { this.frame(); return; } this.raf = requestAnimationFrame((t) => this.tick(t)); },
  stop() { if (this.raf) cancelAnimationFrame(this.raf); this.raf = 0; },
};

// ---------------------------------------------------------------- radar
const map = $("map"), ctx = map.getContext("2d");
const idxAt = (t) => Math.min(S.sc.aircraft[0].cols.t.length - 1, Math.max(0, Math.floor(t / S.sc.stride_s)));
const inFL = (alt) => alt !== null && alt / 100 >= S.flMin - 5 && alt / 100 <= S.flMax + 5;

async function initRadar() {
  $("scenarioSel").innerHTML = S.index.map((s) => `<option value="${esc(s.id)}">${esc(s.title)}</option>`).join("");
  $("scenarioSel").addEventListener("change", (e) => { setPlaying(false); loadScenario(e.target.value); });
  fitMap();
  const want = S.pending || H.get("scenario");
  await loadScenario(S.index.some((s) => s.id === want) ? want : DEFAULT_SCENARIO, { autoSelect: !H.get("sel") });
  if (H.has("t")) S.t = Math.max(0, Math.min(S.sc.duration_s - S.sc.stride_s, +H.get("t") || 0));
  const k = S.sc.aircraft.findIndex((a) => a.callsign === H.get("sel"));
  if (k >= 0) select(k, H.has("pin") ? idxAt(+H.get("pin")) : null);
  draw();
}
function fitMap() {
  const r = map.getBoundingClientRect(); if (r.width < 2) return;
  const dpr = window.devicePixelRatio || 1; map.width = Math.round(r.width * dpr); map.height = Math.round(r.height * dpr);
  const box = (sel) => { const el = document.querySelector(sel); if (!el || getComputedStyle(el).position !== "absolute") return null; const b = el.getBoundingClientRect(); return { l: b.left - r.left, t: b.top - r.top, r: b.right - r.left, b: b.bottom - r.top }; };
  const lp = box(".panel.left"), ly = box(".layers"), tl = box(".timeline");
  const L = lp ? lp.r + 24 : 16, R = ly ? ly.l - 24 : r.width - 16, T = 24, B = tl ? tl.t - 20 : r.height - 16;
  S.vp = { w: r.width, h: r.height, dpr, P: fitter(L, T, R, B) };
  const off = document.createElement("canvas"); off.width = map.width; off.height = map.height;
  const g = off.getContext("2d"); g.scale(dpr, dpr); g.fillStyle = "#000"; g.fillRect(0, 0, r.width, r.height);
  g.strokeStyle = "rgba(255,255,255,0.03)"; g.lineWidth = 1;
  for (let lat = 20; lat <= 55; lat += 5) { g.beginPath(); for (let lon = -135; lon <= -55; lon++) { const [x, y] = S.vp.P(lon, lat); lon === -135 ? g.moveTo(x, y) : g.lineTo(x, y); } g.stroke(); }
  for (let lon = -130; lon <= -60; lon += 5) { g.beginPath(); for (let lat = 18; lat <= 56; lat++) { const [x, y] = S.vp.P(lon, lat); lat === 18 ? g.moveTo(x, y) : g.lineTo(x, y); } g.stroke(); }
  const land = landPath(S.vp.P); g.fillStyle = "#0d0e0f"; g.fill(land); g.strokeStyle = "rgba(255,255,255,0.09)"; g.lineWidth = 0.6; g.stroke(land);
  S.base = off;
  if (S.sc) project();
}
function project() {
  for (const a of S.sc.aircraft) { const c = a.cols, n = c.t.length; a.x = new Float32Array(n); a.y = new Float32Array(n); for (let i = 0; i < n; i++) { const [x, y] = S.vp.P(c.lon[i], c.lat[i]); a.x[i] = x; a.y[i] = y; } }
  for (const p of S.sc.pireps) { const [x, y] = S.vp.P(p.lon, p.lat); p.x = x; p.y = y; }
}
async function loadScenario(id, { autoSelect = true } = {}) {
  S.sc = await getJSON(`/api/scenario/${encodeURIComponent(id)}`); // own copy: the radar adds screen coordinates
  S.sel = -1; S.pinned = null; S.lastJ = -1; S.lastListI = -1;
  $("scenarioSel").value = id; $("scenarioBlurb").textContent = S.sc.blurb;
  project(); renderLegend();
  if (autoSelect) { const m = mostTurbulent(S.sc); S.t = S.sc.aircraft[m.k].cols.t[m.j]; select(m.k); }
  else { S.t = Math.min(1200, S.sc.duration_s - S.sc.stride_s); renderInspector(); }
  $("scrub").max = String(S.sc.duration_s - S.sc.stride_s);
  draw();
}

function drawField() {
  for (const p of S.sc.patches) {
    if (p.fl_hi < S.flMin || p.fl_lo > S.flMax) continue;
    const [clat, clon] = offset(p.lat, p.lon, (p.u_kmh * S.t) / 3600, (p.v_kmh * S.t) / 3600), th = (p.angle_deg * Math.PI) / 180;
    const cls = p.sigma >= 320 ? 3 : p.sigma >= 180 ? 2 : 1;
    for (const [k, a] of [[1, 0.06], [0.6, 0.08], [0.3, 0.1]]) {
      ctx.beginPath();
      for (let j = 0; j <= 48; j++) { const ang = (j / 48) * 2 * Math.PI, al = Math.cos(ang) * p.a_km * k, ac = Math.sin(ang) * p.b_km * k; const [la, lo] = offset(clat, clon, al * Math.sin(th) + ac * Math.cos(th), al * Math.cos(th) - ac * Math.sin(th)); const [x, y] = S.vp.P(lo, la); j ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
      ctx.closePath(); ctx.fillStyle = rgba(C.cls[cls], a); ctx.fill();
      if (k === 1) { ctx.setLineDash([2, 4]); ctx.strokeStyle = rgba(C.cls[cls], 0.5); ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]); }
    }
  }
}
function caret(x, y, col, a) { // PIREP turbulence symbol (aviation caret)
  ctx.strokeStyle = rgba(col, a); ctx.lineWidth = 2; ctx.lineJoin = "miter";
  ctx.beginPath(); ctx.moveTo(x - 6, y + 4); ctx.lineTo(x, y - 5); ctx.lineTo(x + 6, y + 4); ctx.stroke();
}
function draw() {
  if (S.view !== "radar" || !S.base || !S.sc) return;
  const { dpr } = S.vp, i = idxAt(S.t);
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.drawImage(S.base, 0, 0); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (S.layers.field) drawField();
  ctx.lineCap = "round";
  S.sc.aircraft.forEach((a, k) => {
    const c = a.cols;
    if (k === S.sel) { ctx.strokeStyle = "rgba(236,235,232,0.18)"; ctx.lineWidth = 1; ctx.setLineDash([2, 4]); ctx.beginPath(); for (let j = 0; j < c.t.length; j++) j ? ctx.lineTo(a.x[j], a.y[j]) : ctx.moveTo(a.x[j], a.y[j]); ctx.stroke(); ctx.setLineDash([]); }
    if (!S.layers.trails && k !== S.sel) return;
    ctx.lineWidth = k === S.sel ? 3 : 2;
    for (let j = Math.max(1, i - 60); j <= i; j++) { if (!inFL(c.alt[j])) continue; const f = 0.2 + 0.8 * (1 - (i - j) / 60); ctx.strokeStyle = rgba(sevColor(c.sev[j]), f); ctx.beginPath(); ctx.moveTo(a.x[j - 1], a.y[j - 1]); ctx.lineTo(a.x[j], a.y[j]); ctx.stroke(); }
  });
  if (S.layers.pireps) for (const p of S.sc.pireps) {
    const age = S.t - p.t; if (age < 0 || age > 1800 || p.fl < S.flMin || p.fl > S.flMax) continue;
    const a = 1 - (age / 1800) * 0.7;
    if (p.tb === "NEG") { ctx.strokeStyle = rgba(C.cls[0], a); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(p.x, p.y, 3.5, 0, 2 * Math.PI); ctx.stroke(); }
    else { caret(p.x, p.y, C.tb[p.tb], a); if (p.tb === "SEV") caret(p.x, p.y - 6, C.tb[p.tb], a); }
    ctx.fillStyle = `rgba(236,235,232,${0.55 * a})`; ctx.font = `10px ${MONO}`; ctx.fillText(String(p.fl), p.x + 9, p.y + 4);
  }
  if (S.sel >= 0) { // PIREP matching radius around the inspected point
    const a = S.sc.aircraft[S.sel], j = S.pinned ?? i, lat = a.cols.lat[j], lon = a.cols.lon[j], [x, y] = S.vp.P(lon, lat);
    const rpx = Math.abs(S.vp.P(offset(lat, lon, S.sc.match.dist_km, 0)[1], lat)[0] - x);
    ctx.setLineDash([3, 4]); ctx.strokeStyle = "rgba(236,235,232,0.6)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x, y, rpx, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
  }
  const vis = [];
  S.sc.aircraft.forEach((a, k) => {
    const c = a.cols; if (!inFL(c.alt[i])) return; vis.push(k);
    ctx.save(); ctx.translate(a.x[i], a.y[i]); ctx.rotate((c.hdg[i] * Math.PI) / 180); if (k === S.sel) ctx.scale(1.3, 1.3);
    ctx.lineWidth = 2.4; ctx.strokeStyle = "rgba(0,0,0,0.9)"; ctx.stroke(PLANE); ctx.fillStyle = sevColor(c.sev[i]); ctx.fill(PLANE); ctx.restore();
    if (k === S.sel) { ctx.strokeStyle = C.text; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(a.x[i], a.y[i], 15, 0, 2 * Math.PI); ctx.stroke(); }
  });
  if (S.layers.labels) labels(i, vis);
  $("clock").textContent = tplus(S.t); $("scrub").value = String(Math.round(S.t));
  renderFlights();
  if (S.sel >= 0 && S.pinned === null) updateInspector();
}
function labels(i, vis) {
  const order = [];
  if (S.sel >= 0 && vis.includes(S.sel)) order.push(S.sel);
  if (S.hover >= 0 && S.hover !== S.sel && vis.includes(S.hover)) order.push(S.hover);
  vis.filter((k) => !order.includes(k) && S.sc.aircraft[k].cols.sev[i] >= 2).sort((p, q) => (S.sc.aircraft[q].cols.sc[i] ?? 0) - (S.sc.aircraft[p].cols.sc[i] ?? 0)).slice(0, 3).forEach((k) => order.push(k));
  const placed = [], ov = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  const glyphs = vis.map((k) => ({ k, x: S.sc.aircraft[k].x[i] - 9, y: S.sc.aircraft[k].y[i] - 9, w: 18, h: 18 }));
  ctx.font = `11px ${MONO}`;
  for (const k of order) {
    const a = S.sc.aircraft[k], x = a.x[i], y = a.y[i], t = `${a.callsign}  FL${Math.round(a.cols.alt[i] / 100)}`, w = ctx.measureText(t).width + 14, h = 18;
    const spot = [[14, -26], [14, 8], [-w - 14, -26], [-w - 14, 8]].map(([dx, dy]) => ({ x: x + dx, y: y + dy, w, h })).find((r) => r.x > 4 && r.y > 4 && r.x + w < S.vp.w - 4 && !placed.some((p) => ov(r, p)) && !glyphs.some((g) => g.k !== k && ov(r, g)));
    if (!spot) continue;
    placed.push(spot);
    ctx.strokeStyle = "rgba(236,235,232,0.3)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(Math.max(spot.x, Math.min(x, spot.x + w)), Math.max(spot.y, Math.min(y, spot.y + h))); ctx.stroke();
    ctx.fillStyle = "rgba(0,0,0,0.85)"; ctx.fillRect(spot.x, spot.y, w, h); ctx.strokeStyle = k === S.sel ? C.text : "rgba(255,255,255,0.18)"; ctx.strokeRect(spot.x + 0.5, spot.y + 0.5, w - 1, h - 1);
    ctx.fillStyle = C.text; ctx.textBaseline = "middle"; ctx.fillText(t, spot.x + 7, spot.y + h / 2 + 0.5); ctx.textBaseline = "alphabetic";
  }
}

function renderFlights() {
  const i = idxAt(S.t);
  if (i === S.lastListI && !renderFlights.force) return;
  renderFlights.force = false; S.lastListI = i;
  const L = S.sc.severity.labels;
  const rows = S.sc.aircraft.map((a, k) => ({ a, k, v: a.cols.sc[i], s: a.cols.sev[i], alt: a.cols.alt[i] })).sort((p, q) => (q.v ?? -1) - (p.v ?? -1));
  $("flights").innerHTML = rows.map((r) => `<li role="option" data-k="${r.k}" aria-selected="${r.k === S.sel}" class="${inFL(r.alt) ? "" : "out"}"><i class="sw" data-s="${r.s}"></i><span class="cs">${esc(r.a.callsign)}</span><span class="fl">FL${fmt(r.alt / 100)}</span><span class="w">${r.s < 0 ? "Not scored" : esc(L[r.s])}</span></li>`).join("");
  for (const d of $("flights").querySelectorAll(".sw")) d.style.background = sevColor(+d.dataset.s);
}
$("flights").addEventListener("click", (e) => { const li = e.target.closest("li"); if (li) select(+li.dataset.k); });

function select(k, j = null) { const ch = k !== S.sel; S.sel = k; S.pinned = j; S.lastJ = -1; renderFlights.force = true; if (ch) renderTicks(); renderInspector(); draw(); }
function renderInspector() { if (S.sel < 0) { $("inspector").innerHTML = '<p class="ins-empty">Select a flight from the list or the map.</p>'; return; } S.lastJ = -1; $("inspector").innerHTML = '<div id="ins"></div>'; updateInspector(); }
function matches(a, j) {
  const m = S.sc.match, c = a.cols;
  return S.sc.pireps.map((p) => ({ p, dt: p.t - c.t[j], d: haversine(c.lat[j], c.lon[j], p.lat, p.lon), da: p.fl * 100 - c.alt[j] })).filter((r) => Math.abs(r.dt) <= m.window_s && r.d <= m.dist_km && Math.abs(r.da) <= m.alt_ft).sort((x, y) => x.d - y.d);
}
function updateInspector() {
  const el = $("ins"); if (!el || S.sel < 0) return;
  const a = S.sc.aircraft[S.sel], c = a.cols, j = S.pinned ?? idxAt(S.t);
  if (j === S.lastJ) return; S.lastJ = j;
  const s = c.sev[j], L = S.sc.severity.labels, ms = matches(a, j), fl = Math.round(c.alt[j] / 100);
  const cls = s < 0 ? "Not scored" : L[s];
  const story = c.seg[j] !== 1 ? `<b>${esc(a.callsign)}</b> is climbing or descending through FL${fl}. Horus only scores level flight, so it is not scored right now.`
    : `<b>${esc(a.callsign)}</b> is level at FL${fl}. After the rounding correction its vertical rate jitters by <b>${fmt(c.sc[j])} ft/min</b>: <b>${esc(cls)}</b> on the placeholder scale.${ms.length ? ` ${ms.length} pilot report${ms.length > 1 ? "s" : ""} nearby.` : ""}`;
  const row = (k, v, tip) => `<div class="r" title="${esc(tip)}"><span>${k}</span><span>${v}</span></div>`;
  el.innerHTML = `
    <div class="ins-head"><div><div class="cs">${esc(a.callsign)}</div><div class="sub">${a.size === "medium_jet" ? "Medium jet" : "Turboprop"} · synthetic · <span class="num">${fmt(Math.abs(c.lat[j]), 2)}°N ${fmt(Math.abs(c.lon[j]), 2)}°W</span></div></div><span class="cls" id="clsPill">${esc(cls)}</span></div>
    <p class="summary">${story}</p>
    <div class="meta"><span class="num">${tplus(c.t[j])} · ${S.pinned !== null ? "pinned" : "live"}</span>${S.pinned !== null ? '<button class="text-btn" id="unpin" type="button">Follow playback</button>' : "<span>Click the chart to pin a moment</span>"}</div>
    <div class="tele">
      <div title="Flight level: hundreds of feet"><b>FL${fl}</b><span>Level</span></div>
      <div title="Ground speed"><b>${fmt(c.gs[j])} kt</b><span>Speed</span></div>
      <div title="Track, degrees true"><b>${fmt(c.hdg[j])}°</b><span>Track</span></div>
      <div title="Vertical rate as broadcast, in 64 ft/min steps"><b>${fmt(c.vr[j])}</b><span>ft/min</span></div>
    </div>
    <div class="chart-h"><span>Roughness, last 10 min</span><b>${fmt(c.sc[j])} <small>ft/min</small></b></div>
    <div class="chart" id="chart"></div>
    <div class="key"><span><i></i>σ<sub>corr</sub></span><span><i class="d"></i>injected (answer key)</span><span><i class="r"></i>residual</span></div>
    <div class="prof"><div class="ph"><span>Altitude, whole hour</span><span>shaded = scored</span></div><div id="prof"></div></div>
    <p class="label ins-sec">Detector</p>
    <div class="rows">
      ${row("Level flight", c.seg[j] === 1 ? "yes" : "no", "Scored only at FL200+, within 300 ft over 2 min, median vertical rate within ±200 ft/min")}
      ${row("σ<sub>corr</sub> <em>after Δ²/12</em>", fmt(c.sc[j], 0, " ft/min"), "Jitter over 15 s after removing the 31 s trend and the rounding variance; sets the colour")}
      ${row("σ<sub>raw</sub> <em>before</em>", fmt(c.sr[j], 0, " ft/min"), "The same jitter before the rounding correction")}
      ${row("Injected σ <em>answer key</em>", fmt(c.truth[j], 0, " ft/min"), "The turbulence the simulation put here; real data has no answer key")}
      ${row("Flicker samples <em>61 s</em>", fmt(c.flk[j]), "Single-sample ±64 ft/min toggles (encoder noise); they count half")}
      ${row("B1 vertical-accel RMS", fmt(c.b1[j], 3, " g"), "Baseline detector: RMS vertical acceleration with the rounding noise removed")}
    </div>
    <p class="label ins-sec">Pilot reports nearby · ${ms.length}</p>
    ${ms.length ? ms.map((r) => `<div class="pirep"><div class="top"><span data-tb="${esc(r.p.tb)}">${esc(r.p.tb)} · ${esc(r.p.by)}</span><span class="num">${r.d.toFixed(0)} km · ${r.dt >= 0 ? "+" : "−"}${Math.abs(r.dt / 60).toFixed(1)} min</span></div><span class="txt">${esc(r.p.text)}</span></div>`).join("") : `<p class="pirep">None within ±${S.sc.match.window_s / 60} min, ${S.sc.match.dist_km} km and ±${S.sc.match.alt_ft.toLocaleString()} ft (the dashed ring).</p>`}
    <p class="ins-foot">Research prototype · not for operational use</p>`;
  $("clsPill").style.color = sevColor(s);
  for (const t of el.querySelectorAll("[data-tb]")) t.style.color = C.tb[t.dataset.tb];
  $("chart").innerHTML = chartSVG(a, j, $("chart").clientWidth || 330);
  $("prof").innerHTML = profileSVG(a, j, $("prof").clientWidth || 330);
  $("chart").onclick = (ev) => { const r = ev.currentTarget.getBoundingClientRect(), j0 = Math.max(0, j - 59); S.pinned = Math.max(0, Math.min(j, j0 + Math.round(((ev.clientX - r.left) / (r.width - 46)) * 59))); S.lastJ = -1; updateInspector(); draw(); };
  const u = $("unpin"); if (u) u.onclick = () => { S.pinned = null; S.lastJ = -1; draw(); updateInspector(); };
}
function chartSVG(a, j, width) {
  const c = a.cols, j0 = Math.max(0, j - 59), W = Math.max(220, Math.floor(width)), pw = W - 46, H1 = 104, G = 8, H2 = 34, Ht = H1 + G + H2, X = (k) => ((k - j0) / 59) * pw;
  const vals = []; for (let k = j0; k <= j; k++) { if (c.sc[k] !== null) vals.push(c.sc[k]); if (c.truth[k] !== null) vals.push(c.truth[k]); }
  const ymax = Math.max(360, ...vals) * 1.08, Y = (v) => H1 - 4 - (v / ymax) * (H1 - 10);
  const path = (arr, f) => { let d = "", pen = false; for (let k = j0; k <= j; k++) { const v = arr[k]; if (v === null) { pen = false; continue; } d += `${pen ? "L" : "M"}${X(k).toFixed(1)},${f(v).toFixed(1)}`; pen = true; } return d; };
  const thr = S.sc.severity.edges.map((e, i) => (e < ymax ? `<line x1="0" x2="${pw}" y1="${Y(e)}" y2="${Y(e)}" stroke="${C.cls[i + 1]}" stroke-opacity=".7" stroke-dasharray="3 3"/>${svgText(pw + 6, Y(e) + 3, e, C.cls[i + 1])}` : "")).join("");
  const res = []; for (let k = j0; k <= j; k++) if (c.res[k] !== null) res.push(Math.abs(c.res[k]));
  const rmax = Math.max(200, ...res) * 1.1, Yr = (v) => H1 + G + H2 / 2 - (v / rmax) * (H2 / 2 - 2), last = c.sc[j];
  return `<svg viewBox="0 0 ${W} ${Ht}" width="${W}" height="${Ht}" role="img" aria-label="Roughness over the last 10 minutes">
    <line x1="0" x2="${pw}" y1="${H1 - 4}" y2="${H1 - 4}" stroke="rgba(255,255,255,.1)"/>${thr}
    <path d="${path(c.truth, Y)}" fill="none" stroke="${C.text2}" stroke-width="1.1" stroke-dasharray="3 3"/>
    <path d="${path(c.sc, Y)}" fill="none" stroke="${C.text}" stroke-width="1.8"/>
    ${last !== null ? `<circle cx="${X(j)}" cy="${Y(last)}" r="3" fill="${C.text}"/>` : ""}
    <path d="${path(c.res, Yr)}" fill="none" stroke="${C.text3}" stroke-width="1"/></svg>`;
}
function profileSVG(a, j, width) {
  const c = a.cols, n = c.t.length, W = Math.max(220, Math.floor(width)), Hh = 40, alts = c.alt.filter((v) => v !== null);
  const mid = (Math.min(...alts) + Math.max(...alts)) / 2, half = Math.max(2000, (Math.max(...alts) - Math.min(...alts)) / 2 + 300), lo = mid - half, hi = mid + half;
  const X = (k) => (k / (n - 1)) * W, Y = (v) => Hh - 3 - ((v - lo) / (hi - lo)) * (Hh - 8);
  let bands = ""; for (let k = 0; k < n; k++) if (c.seg[k] === 1) bands += `<rect x="${X(k).toFixed(1)}" y="0" width="${(W / n + 0.6).toFixed(2)}" height="${Hh}" fill="${sevColor(c.sev[k])}" fill-opacity=".14"/>`;
  let d = ""; c.alt.forEach((v, k) => { if (v !== null) d += `${d ? "L" : "M"}${X(k).toFixed(1)},${Y(v).toFixed(1)}`; });
  return `<svg viewBox="0 0 ${W} ${Hh}" width="${W}" height="${Hh}" role="img" aria-label="Altitude over the hour">${bands}<path d="${d}" fill="none" stroke="${C.text}" stroke-opacity=".7" stroke-width="1.2"/>
    <line x1="${X(idxAt(S.t))}" x2="${X(idxAt(S.t))}" y1="0" y2="${Hh}" stroke="${C.text3}"/><line x1="${X(j)}" x2="${X(j)}" y1="0" y2="${Hh}" stroke="${C.text}"/></svg>`;
}

function renderTicks() {
  const dur = S.sc.duration_s, el = $("ticks");
  let h = ""; for (let m = 0; m <= dur / 60; m += 10) h += `<span data-left="${(m * 60) / dur}">T+${String(m).padStart(2, "0")}</span>`;
  if (S.sel >= 0) { const a = S.sc.aircraft[S.sel], b = mostTurbulent({ aircraft: [a] }); if (b.v > 0) h += `<span class="peak" data-left="${a.cols.t[b.j] / dur}" title="Roughest moment of the selected flight">▲</span>`; }
  el.innerHTML = h;
  for (const s of el.querySelectorAll("[data-left]")) s.style.left = `${(+s.getAttribute("data-left")) * 100}%`;
}
function renderLegend() {
  const e = S.sc.severity.edges, L = S.sc.severity.labels;
  const range = [`< ${e[0]}`, `${e[0]}–${e[1]}`, `${e[1]}–${e[2]}`, `≥ ${e[2]}`];
  const line = (col) => `<svg width="22" height="8"><line x1="1" x2="21" y1="4" y2="4" stroke="${col}" stroke-width="2.5" stroke-linecap="round"/></svg>`;
  const car = (col, two) => `<svg width="22" height="16"><path d="M5 12 L11 3 L17 12" fill="none" stroke="${col}" stroke-width="2"/>${two ? `<path d="M5 7 L11 -2 L17 7" fill="none" stroke="${col}" stroke-width="2" transform="translate(0,1)"/>` : ""}</svg>`;
  $("legend").innerHTML = `<h4>Roughness (σ<sub>corr</sub>, ft/min)</h4>${L.map((l, i) => `<div class="r">${line(C.cls[i])}<span>${esc(l)}</span><span>${range[i]}</span></div>`).join("")}
    <div class="r">${line(C.unscored)}<span>Not scored (climb or descent)</span><span></span></div>
    <h4 data-gap="1">Pilot reports (synthetic)</h4>
    <div class="r">${car(C.tb.LGT)}<span>Light</span><span>LGT</span></div><div class="r">${car(C.tb.MOD)}<span>Moderate</span><span>MOD</span></div><div class="r">${car(C.tb.SEV, true)}<span>Severe</span><span>SEV</span></div>
    <p>Horus reports roughness in ft/min, not EDR, the unit official turbulence products use. The thresholds are placeholders until M3 calibrates them. Moderate and severe colours follow aviationweather.gov.</p>`;
  for (const h of $("legend").querySelectorAll("[data-gap]")) h.style.marginTop = "18px";
}
function hideLegend() { $("legend").classList.add("hidden"); $("legendBtn").setAttribute("aria-expanded", "false"); }
$("legendBtn").addEventListener("click", () => { const open = $("legend").classList.toggle("hidden") === false; $("legendBtn").setAttribute("aria-expanded", String(open)); });

// playback and controls
let last = null;
function tick(ts) { if (!S.playing) { last = null; return; } if (last !== null) { S.t += ((ts - last) / 1000) * S.speed; const end = S.sc.duration_s - S.sc.stride_s; if (S.t >= end) { S.t = end; setPlaying(false); } } last = ts; draw(); if (S.playing) requestAnimationFrame(tick); }
function setPlaying(on) { S.playing = on; $("playIcon").setAttribute("d", on ? "M4 2.5h3v11H4zM9 2.5h3v11H9z" : "M4 2.5v11l9-5.5z"); $("play").setAttribute("aria-label", on ? "Pause" : "Play"); if (on && S.sc) { if (S.t >= S.sc.duration_s - S.sc.stride_s) S.t = 0; requestAnimationFrame(tick); } }
$("play").addEventListener("click", () => setPlaying(!S.playing));
$("scrub").addEventListener("input", (e) => { S.t = +e.target.value; draw(); });
for (const b of document.querySelectorAll(".speed button")) b.addEventListener("click", () => { S.speed = +b.dataset.speed; for (const x of document.querySelectorAll(".speed button")) x.setAttribute("aria-checked", String(x === b)); });
function syncFL(which) { let lo = +$("flMin").value, hi = +$("flMax").value; if (lo > hi) { if (which === "min") hi = lo; else lo = hi; $("flMin").value = lo; $("flMax").value = hi; } S.flMin = lo; S.flMax = hi; $("flLabel").textContent = `${lo}–${hi}`; renderFlights.force = true; draw(); }
$("flMin").addEventListener("input", () => syncFL("min")); $("flMax").addEventListener("input", () => syncFL("max"));
for (const b of document.querySelectorAll(".layers button")) b.addEventListener("click", () => { const k = b.dataset.layer; S.layers[k] = !S.layers[k]; b.setAttribute("aria-pressed", String(S.layers[k])); draw(); });

function pick(mx, my) {
  if (!S.sc) return null; const i = idxAt(S.t); let best = null, bd = 13 * 13;
  S.sc.aircraft.forEach((a, k) => { if (!inFL(a.cols.alt[i])) return; const d = (a.x[i] - mx) ** 2 + (a.y[i] - my) ** 2; if (d < bd) { bd = d; best = { k, j: null }; } });
  if (best) return best; bd = 7 * 7;
  S.sc.aircraft.forEach((a, k) => { const from = k === S.sel ? 0 : Math.max(0, i - 60), to = k === S.sel ? a.x.length - 1 : i; for (let j = from; j <= to; j++) { const d = (a.x[j] - mx) ** 2 + (a.y[j] - my) ** 2; if (d < bd) { bd = d; best = { k, j }; } } });
  return best;
}
const tip = document.createElement("div"); tip.className = "tip hidden"; $("radar").appendChild(tip);
map.addEventListener("click", (e) => { const r = map.getBoundingClientRect(), h = pick(e.clientX - r.left, e.clientY - r.top); if (h) select(h.k, h.j); });
map.addEventListener("mousemove", (e) => {
  const r = map.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top, h = pick(mx, my), k = h ? h.k : -1;
  map.style.cursor = h ? "pointer" : "default";
  let p = null; if (!h && S.sc && S.layers.pireps) { let bd = 14 * 14; for (const q of S.sc.pireps) { const age = S.t - q.t; if (age < 0 || age > 1800) continue; const d = (q.x - mx) ** 2 + (q.y - my) ** 2; if (d < bd) { bd = d; p = q; } } }
  if (p) { tip.innerHTML = `${esc(p.tb)} pilot report · ${esc(p.by)} · FL${p.fl} · ${Math.round((S.t - p.t) / 60)} min ago<span class="txt">${esc(p.text)}</span>`; tip.style.left = `${Math.min(mx + 14, r.width - 300)}px`; tip.style.top = `${my + 14}px`; tip.classList.remove("hidden"); } else tip.classList.add("hidden");
  if (k !== S.hover) { S.hover = k; if (!S.playing) draw(); }
});
map.addEventListener("mouseleave", () => { tip.classList.add("hidden"); if (S.hover !== -1) { S.hover = -1; if (!S.playing) draw(); } });
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.key === "Escape") { if (!$("legend").classList.contains("hidden")) hideLegend(); return; }
  if (S.view !== "radar" || !S.sc) return;
  if (e.key === " ") { e.preventDefault(); setPlaying(!S.playing); }
  if (e.key === "ArrowRight") { S.t = Math.min(S.sc.duration_s - S.sc.stride_s, S.t + 60); draw(); }
  if (e.key === "ArrowLeft") { S.t = Math.max(0, S.t - 60); draw(); }
});

// ---------------------------------------------------------------- boot
async function boot() {
  [S.basemap, S.index, S.about, S.overview] = await Promise.all([getJSON("/api/basemap"), getJSON("/api/scenarios"), getJSON("/api/about"), getJSON("/api/overview")]);
  renderOverview();
  await setView(H.get("view") === "radar" ? "radar" : "overview");
  SCENE.init().catch(() => {});
  let rt = 0;
  new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => { if (S.view === "radar" && S.radarReady) { fitMap(); S.lastJ = -1; draw(); updateInspector(); } else if (SCENE.D) { SCENE.layout(); SCENE.frame(); } }, 120); }).observe(document.body);
}
boot().catch((err) => { document.body.insertAdjacentHTML("beforeend", `<p class="label" data-err="1">Could not load the demo: ${esc(err.message)}</p>`); });
