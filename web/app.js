// Horus map: one canvas, a continuous render loop, no external libraries (offline).
"use strict";
const $ = (id) => document.getElementById(id);
const cv = $("map"), ctx = cv.getContext("2d");
const DPR = () => window.devicePixelRatio || 1;
const COSLAT = Math.cos((37 * Math.PI) / 180);
const HOME = { lon: -95.5, lat: 37.5, span: 62 }; // degrees of longitude across the view

const S = {
  mode: "live", lattice: null, flBins: [], fl: 0, floor: 0.001, tau: 1, basemap: null,
  view: { lon: HOME.lon, lat: HOME.lat, span: HOME.span },
  cells: new Map(),   // key -> {p, a (alpha now), on}
  ac: new Map(),      // icao -> {x0,y0,x1,y1, fl, T, t1}   live interpolation
  msgGap: 1500, lastMsg: 0, simT: 0, pireps: [],
  replay: null, rf: 0, playing: false, speed: 4, hover: null,
};

// ---------- projection ----------
function scale() { return cv.width / (S.view.span * COSLAT); }
function proj(lon, lat) {
  const k = scale();
  return [cv.width / 2 + (lon - S.view.lon) * COSLAT * k, cv.height / 2 - (lat - S.view.lat) * k];
}
function unproj(x, y) {
  const k = scale();
  return [S.view.lon + (x - cv.width / 2) / (COSLAT * k), S.view.lat - (y - cv.height / 2) / k];
}

// ---------- colour: one hue (orange), dim -> bright, log over [floor, 10%] ----------
const STEPS = [0.002, 0.005, 0.01, 0.03, 0.1];
function heat(p) {
  const t = Math.max(0, Math.min(1, Math.log(p / S.floor) / Math.log(0.1 / S.floor)));
  return [255, Math.round(96 + 110 * t), Math.round(10 + 80 * t), 0.28 + 0.62 * t];
}
function rgba([r, g, b, a], k = 1) { return `rgba(${r},${g},${b},${(a * k).toFixed(3)})`; }

// ---------- data ----------
async function getJSON(u) { const r = await fetch(u); if (!r.ok) throw new Error(`${u}: HTTP ${r.status}`); return r.json(); }
const zulu = (t) => new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") + "Z";
const flOf = (i) => S.flBins[i];
const inBand = (fl) => fl >= flOf(S.fl) - 10 && fl < flOf(S.fl) + 30; // band +-1,000 ft

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live`);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data), now = performance.now();
    if (S.lastMsg) S.msgGap = 0.8 * S.msgGap + 0.2 * Math.min(now - S.lastMsg, 6000);
    S.lastMsg = now; S.simT = m.t;
    const seen = new Set();
    for (const a of m.aircraft) {
      seen.add(a.icao24);
      const o = S.ac.get(a.icao24);
      if (o) { const [x, y] = lerpAc(o, now); Object.assign(o, { x0: x, y0: y, x1: a.lon, y1: a.lat, fl: a.fl, T: a.T, t1: now }); }
      else S.ac.set(a.icao24, { x0: a.lon, y0: a.lat, x1: a.lon, y1: a.lat, fl: a.fl, T: a.T, t1: now });
    }
    for (const k of S.ac.keys()) if (!seen.has(k)) S.ac.delete(k);
    for (const [h, f, p] of m.cells) setCell(`${h}|${f}`, p);
    if (S.mode === "live") { $("clock").textContent = zulu(m.t); $("status").textContent = `Grid updated to ${zulu(m.tick)}`; }
  };
  ws.onopen = () => ($("status").textContent = "Connected");
  ws.onclose = () => { $("status").textContent = "Feed lost, reconnecting…"; setTimeout(connect, 2500); };
}
function setCell(key, p) {
  const c = S.cells.get(key);
  if (p > S.floor) { if (c) { c.p = p; c.on = true; } else S.cells.set(key, { p, a: 0, on: true }); }
  else if (c) c.on = false;
}
function lerpAc(o, now) {
  const u = Math.min(1, (now - o.t1) / S.msgGap), e = u * u * (3 - 2 * u);
  return [o.x0 + (o.x1 - o.x0) * e, o.y0 + (o.y1 - o.y0) * e];
}

// ---------- replay ----------
async function loadCases() {
  const list = await getJSON("/api/replays");
  $("replayList").innerHTML = list.map((r, i) =>
    `<button data-id="${r.id}" aria-pressed="${i === 0}"><span class="t">${r.intensity === "SEV" ? "Severe" : "Moderate"} report · FL${r.fl_lo}${r.fl_hi !== r.fl_lo ? "–" + r.fl_hi : ""}</span><span class="d">${zulu(r.time)} · ${r.aircraft_in_window} aircraft nearby</span></button>`).join("");
  $("replayList").querySelectorAll("button").forEach((b) => (b.onclick = () => openCase(b.dataset.id)));
  if (list.length) await openCase(list[0].id);
}
async function openCase(id) {
  $("replayList").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.id === id)));
  S.replay = await getJSON(`/api/replay/${encodeURIComponent(id)}`);
  const m = S.replay.meta;
  S.replay.idx = S.replay.frames.map((f) => new Map(f.aircraft.map((a) => [a[0], a])));
  S.rf = Math.max(0, S.replay.frames.findIndex((f) => f.t >= m.time) - 10);
  const bin = S.flBins.findIndex((f) => m.fl_lo >= f && m.fl_lo < f + 20);
  if (bin >= 0) selectFl(bin);
  flyTo(m.lon, m.lat, 16);
  $("replayMeta").textContent = `The report is the caret at the centre. Play from 10 min before it to see aircraft detections and the forecast around it. Chosen as the clearest detections in the test days, not at random.`;
  S.playing = true; syncPlay();
}
function replayState() {
  const R = S.replay, n = R.frames.length, i = Math.min(Math.floor(S.rf), n - 1), j = Math.min(i + 1, n - 1), u = S.rf - i;
  const A = R.idx[i], B = R.idx[j], ac = [];
  for (const [id, a] of A) {
    const b = B.get(id) || a;
    ac.push({ id, lat: a[1] + (b[1] - a[1]) * u, lon: a[2] + (b[2] - a[2]) * u, fl: a[3], T: u < 0.5 ? a[4] : b[4] });
  }
  return { ac, cellsA: R.frames[i].cells, cellsB: R.frames[j].cells, u, t: R.frames[i].t + 60 * u };
}

// ---------- drawing ----------
function ring(r) { ctx.beginPath(); r.forEach(([lo, la], i) => { const [x, y] = proj(lo, la); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.closePath(); }
function drawCell(h, p, k) {
  const r = S.lattice[h]; if (!r) return;
  ring(r); const c = heat(p); ctx.fillStyle = rgba(c, k); ctx.fill();
  ctx.strokeStyle = rgba([255, 200, 120, 0.55], k); ctx.lineWidth = DPR(); ctx.stroke();
}
function caret(x, y, sev, d) {
  ctx.strokeStyle = sev ? "#ec2a2a" : "#ff9900"; ctx.lineWidth = 2.2 * d; ctx.lineJoin = "round";
  ctx.beginPath(); ctx.moveTo(x - 7 * d, y + 5 * d); ctx.lineTo(x, y - 6 * d); ctx.lineTo(x + 7 * d, y + 5 * d); ctx.stroke();
}

let lastT = performance.now();
function frame(now) {
  const dt = Math.min(0.1, (now - lastT) / 1000); lastT = now;
  const d = DPR(), W = cv.width, H = cv.height;
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H);
  if (S.basemap) {
    ctx.strokeStyle = "rgba(255,255,255,0.15)"; ctx.lineWidth = d;
    for (const st of S.basemap.states) for (const r of st.rings) { ring(r); ctx.stroke(); }
  }
  const zoomK = HOME.span / S.view.span, dotR = Math.min(4.5, 2.1 * Math.sqrt(zoomK)) * d;
  let shownAc = [], nCells = 0;

  if (S.mode === "replay" && S.replay) {
    if (S.playing) { S.rf += dt * S.speed; if (S.rf >= S.replay.frames.length - 1) { S.rf = S.replay.frames.length - 1; S.playing = false; syncPlay(); } }
    const st = replayState();
    if ($("lyCells").checked) {
      const B = new Map(st.cellsB);
      for (const [h, p] of st.cellsA) { drawCell(h, p, B.has(h) ? 1 : 1 - st.u); nCells++; }
      const A = new Set(st.cellsA.map((c) => c[0]));
      for (const [h, p] of st.cellsB) if (!A.has(h)) drawCell(h, p, st.u);
    }
    shownAc = st.ac.filter((a) => inBand(a.fl));
    $("clock").textContent = zulu(st.t);
    const mins = Math.round((st.t - S.replay.meta.time) / 60);
    $("clockNote").textContent = mins === 0 ? "report time" : `${Math.abs(mins)} min ${mins < 0 ? "before" : "after"} the report`;
    $("scrub").value = String(Math.round((1000 * S.rf) / (S.replay.frames.length - 1)));
    $("tlabel").textContent = `${mins >= 0 ? "+" : "−"}${Math.abs(mins)} min`;
  } else {
    for (const [k, c] of S.cells) {
      c.a += ((c.on ? 1 : 0) - c.a) * Math.min(1, dt * 3);
      if (!c.on && c.a < 0.02) { S.cells.delete(k); continue; }
      const [h, f] = k.split("|");
      if (+f !== S.fl || !$("lyCells").checked) continue;
      drawCell(h, c.p, c.a); nCells++;
    }
    for (const [id, o] of S.ac) {
      if (!inBand(o.fl)) continue;
      const [lon, lat] = lerpAc(o, now); shownAc.push({ id, lon, lat, fl: o.fl, T: o.T });
    }
  }

  let hits = 0;
  if ($("lyAc").checked) {
    for (const a of shownAc) {
      const [x, y] = proj(a.lon, a.lat); if (x < -10 || y < -10 || x > W + 10 || y > H + 10) continue;
      const hit = a.T != null && a.T >= S.tau; if (hit) hits++;
      ctx.fillStyle = hit ? "#ff9900" : "rgba(225,225,225,0.62)";
      ctx.beginPath(); ctx.arc(x, y, hit ? dotR * 1.35 : dotR, 0, 2 * Math.PI); ctx.fill();
      if (hit) { ctx.strokeStyle = "#ff9900"; ctx.lineWidth = 1.5 * d; ctx.beginPath(); ctx.arc(x, y, dotR * 2.8, 0, 2 * Math.PI); ctx.stroke(); }
    }
  }
  if ($("lyPirep").checked) {
    const ps = S.mode === "replay" && S.replay ? [{ ...S.replay.meta, tb_max: S.replay.meta.intensity === "SEV" ? 6 : 4 }] : S.pireps.filter((p) => p.tb_max >= 4 && p.fl_hi >= flOf(S.fl) - 20 && p.fl_lo <= flOf(S.fl) + 40);
    S.shownPireps = ps;
    for (const p of ps) { const [x, y] = proj(p.lon, p.lat); caret(x, y, p.tb_max >= 6, d); }
  }
  S.shownAc = shownAc;
  $("sAc").textContent = shownAc.length.toLocaleString(); $("sHit").textContent = hits; $("sCells").textContent = nCells;
  drawHover();
  requestAnimationFrame(frame);
}

// ---------- hover ----------
function drawHover() {
  const h = S.hover, tip = $("tip");
  if (!h) { tip.hidden = true; return; }
  const d = DPR(), px = h.x * d, py = h.y * d;
  let best = null, bd = (12 * d) ** 2;
  for (const a of S.shownAc || []) { const [x, y] = proj(a.lon, a.lat), q = (x - px) ** 2 + (y - py) ** 2; if (q < bd) { bd = q; best = { kind: "ac", a }; } }
  for (const p of S.shownPireps || []) { const [x, y] = proj(p.lon, p.lat), q = (x - px) ** 2 + (y - py) ** 2; if (q < bd) { bd = q; best = { kind: "pirep", p }; } }
  if (!best && $("lyCells").checked) {
    const [lon, lat] = unproj(px, py);
    const list = S.mode === "replay" && S.replay ? replayState().cellsA : [...S.cells].filter(([k, c]) => c.on && +k.split("|")[1] === S.fl).map(([k, c]) => [k.split("|")[0], c.p]);
    for (const [hh, p] of list) if (inside(S.lattice[hh], lon, lat)) { best = { kind: "cell", p }; break; }
  }
  if (!best) { tip.hidden = true; return; }
  let html = "";
  if (best.kind === "ac") {
    const a = best.a, hit = a.T != null && a.T >= S.tau;
    html = `<b>Aircraft ${a.id}</b><br>FL${a.fl}<br>${a.T == null ? "Not in level flight: not scored" : hit ? `<span class="hit">Turbulence detected</span> (index ${a.T.toFixed(1)}, threshold ${S.tau.toFixed(1)})` : `Smooth (index ${a.T.toFixed(1)}, threshold ${S.tau.toFixed(1)})`}`;
  } else if (best.kind === "pirep") {
    const p = best.p;
    html = `<b>Pilot report: <span class="${p.tb_max >= 6 ? "sev" : "mod"}">${p.tb_max >= 6 ? "severe" : "moderate"}</span></b><br>FL${p.fl_lo}${p.fl_hi !== p.fl_lo ? "–" + p.fl_hi : ""} · ${zulu(p.time)}`;
  } else {
    html = `<b>Forecast ${(best.p * 100).toFixed(1)}%</b><br>chance the next aircraft here in the next 15 min detects turbulence (background ${(S.floor * 50).toFixed(2)}%)`;
  }
  tip.innerHTML = html; tip.hidden = false;
  const r = cv.getBoundingClientRect();
  tip.style.left = `${Math.min(h.x + 14, r.width - 250)}px`; tip.style.top = `${Math.min(h.y + 14, r.height - 90)}px`;
}
function inside(r, x, y) {
  if (!r) return false; let c = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const [xi, yi] = r[i], [xj, yj] = r[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}

// ---------- view: pan / zoom ----------
function resize() { const r = cv.getBoundingClientRect(); cv.width = Math.round(r.width * DPR()); cv.height = Math.round(r.height * DPR()); }
function zoomAt(f, px, py) {
  const [lon, lat] = unproj(px, py);
  S.view.span = Math.max(4, Math.min(80, S.view.span / f));
  const [lon2, lat2] = unproj(px, py);
  S.view.lon += lon - lon2; S.view.lat += lat - lat2;
}
let anim = null;
function flyTo(lon, lat, span) {
  const a = { ...S.view }, t0 = performance.now();
  cancelAnimationFrame(anim);
  const step = (now) => {
    const u = Math.min(1, (now - t0) / 700), e = 1 - (1 - u) ** 3;
    S.view = { lon: a.lon + (lon - a.lon) * e, lat: a.lat + (lat - a.lat) * e, span: a.span + (span - a.span) * e };
    if (u < 1) anim = requestAnimationFrame(step);
  };
  anim = requestAnimationFrame(step);
}
let drag = null;
cv.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY }; cv.setPointerCapture(e.pointerId); cv.classList.add("drag"); });
cv.addEventListener("pointermove", (e) => {
  const r = cv.getBoundingClientRect();
  if (drag) {
    const k = scale() / DPR(); S.view.lon -= (e.clientX - drag.x) / (COSLAT * k); S.view.lat += (e.clientY - drag.y) / k;
    drag = { x: e.clientX, y: e.clientY }; S.hover = null;
  } else S.hover = { x: e.clientX - r.left, y: e.clientY - r.top };
});
cv.addEventListener("pointerup", () => { drag = null; cv.classList.remove("drag"); });
cv.addEventListener("pointerleave", () => (S.hover = null));
cv.addEventListener("wheel", (e) => { e.preventDefault(); const r = cv.getBoundingClientRect(); zoomAt(Math.exp(-e.deltaY * 0.0015), (e.clientX - r.left) * DPR(), (e.clientY - r.top) * DPR()); }, { passive: false });
$("zin").onclick = () => zoomAt(1.5, cv.width / 2, cv.height / 2);
$("zout").onclick = () => zoomAt(1 / 1.5, cv.width / 2, cv.height / 2);
$("zreset").onclick = () => flyTo(HOME.lon, HOME.lat, HOME.span);

// ---------- controls ----------
function selectFl(i) { S.fl = i; $("flList").querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(+b.dataset.i === i))); }
function syncPlay() { $("play").textContent = S.playing ? "❚❚" : "▶"; $("play").setAttribute("aria-label", S.playing ? "Pause" : "Play"); }
function setMode(mode) {
  S.mode = mode;
  document.querySelectorAll(".modes button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === mode)));
  $("replayBox").hidden = mode !== "replay";
  document.querySelector(".timeline").classList.toggle("off", mode !== "replay");
  $("clockNote").textContent = mode === "live" ? "archive day at 30× speed" : "";
  $("tlabel").textContent = "";
  if (mode === "replay") { if (!S.replay) loadCases().catch((e) => ($("status").textContent = e.message)); else { S.playing = true; syncPlay(); } }
  else { S.playing = false; syncPlay(); flyTo(HOME.lon, HOME.lat, HOME.span); }
}
document.querySelectorAll(".modes button").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
$("play").onclick = () => { if (!S.replay) return; if (S.rf >= S.replay.frames.length - 1) S.rf = 0; S.playing = !S.playing; syncPlay(); };
$("scrub").oninput = (e) => { if (!S.replay) return; S.rf = (+e.target.value / 1000) * (S.replay.frames.length - 1); S.playing = false; syncPlay(); };
document.querySelectorAll(".speeds button").forEach((b) => (b.onclick = () => {
  S.speed = +b.dataset.speed; document.querySelectorAll(".speeds button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
}));
$("introClose").onclick = () => { $("intro").hidden = true; try { localStorage.setItem("horus.intro", "1"); } catch (_) {} };
window.addEventListener("keydown", (e) => {
  if (e.key === " " && S.mode === "replay") { e.preventDefault(); $("play").click(); }
  if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); selectFl(Math.max(0, Math.min(S.flBins.length - 1, S.fl + (e.key === "ArrowUp" ? 1 : -1)))); }
});
window.addEventListener("resize", resize);

async function init() {
  try { if (localStorage.getItem("horus.intro")) $("intro").hidden = true; } catch (_) {}
  resize();
  const [lat, base, th] = await Promise.all([getJSON("/api/lattice"), getJSON("/api/basemap"), getJSON("/api/thresholds")]);
  S.lattice = lat.cells; S.flBins = lat.fl_bins; S.floor = lat.floor; S.basemap = base; S.tau = th.tau_b3;
  $("flList").innerHTML = S.flBins.map((f, i) => `<button role="radio" data-i="${i}">FL${f}</button>`).reverse().join("");
  $("flList").querySelectorAll("button").forEach((b) => (b.onclick = () => selectFl(+b.dataset.i)));
  selectFl(S.flBins.indexOf(340));
  $("legend").innerHTML = STEPS.map((p) => `<span style="background:${rgba(heat(p))}"></span>`).join("") +
    STEPS.map((p, i) => `<em>${i === STEPS.length - 1 ? "≥" : ""}${p * 100 < 1 ? (p * 100).toFixed(1) : (p * 100).toFixed(0)}%</em>`).join("");
  S.pireps = (await getJSON("/api/pireps?since=7200")).pireps;
  setInterval(async () => { if (S.mode === "live") try { S.pireps = (await getJSON("/api/pireps?since=7200")).pireps; } catch (_) {} }, 20000);
  $("loading").hidden = true;
  setMode("live"); connect(); requestAnimationFrame(frame);
}
init().catch((e) => { $("loading").textContent = `Could not load: ${e.message}`; });
