// Horus map: a living wind layer (GFS colour field + particle flow) under turbulence,
// aircraft and pilot reports. Canvas only, no external libraries (runs offline).
"use strict";
const $ = (id) => document.getElementById(id);
const DPR = () => window.devicePixelRatio || 1;
const COSLAT = Math.cos((37 * Math.PI) / 180);
const CONUS = { lon0: -125, lon1: -66, lat0: 24, lat1: 50 };

const cv = { field: $("field"), flow: $("flow"), top: $("top") };
const cx = { field: cv.field.getContext("2d"), flow: cv.flow.getContext("2d"), top: cv.top.getContext("2d") };

const S = {
  mode: "live", flBins: [], fl: 0, floor: 0.001, tau: 1, lattice: null, basemap: null,
  layers: { wind: true, cells: true, ac: true, pireps: true },
  view: { lon: -95.5, lat: 37, k: 20 }, anchor: [0, 0], user: false, viewGen: 0, fieldGen: 0,
  wind: null, windKey: "", cells: new Map(), ac: new Map(), msgGap: 1500, lastMsg: 0, simT: 0, win: null,
  pireps: [], hist: new Map(), replay: null, rf: 0, playing: false, speed: 4, popup: null,
};

// ---------- projection: view = {lon, lat at the anchor point, k = CSS px per degree latitude} ----------
function proj(lon, lat) {
  const d = DPR();
  return [(S.anchor[0] + (lon - S.view.lon) * COSLAT * S.view.k) * d, (S.anchor[1] - (lat - S.view.lat) * S.view.k) * d];
}
function unproj(x, y) {
  const d = DPR();
  return [S.view.lon + (x / d - S.anchor[0]) / (COSLAT * S.view.k), S.view.lat - (y / d - S.anchor[1]) / S.view.k];
}
// the area left free by the floating controls; CONUS is centred in it
function safeArea() {
  const W = innerWidth, H = innerHeight;
  return { x0: 24, x1: W - (W > 760 ? 150 : 70), y0: 64, y1: H - 84 };
}
function fitView() {
  const a = safeArea(), w = a.x1 - a.x0, h = a.y1 - a.y0;
  const k = Math.min(w / ((CONUS.lon1 - CONUS.lon0) * COSLAT), h / (CONUS.lat1 - CONUS.lat0)) * 0.96;
  S.anchor = [(a.x0 + a.x1) / 2, (a.y0 + a.y1) / 2];
  return { lon: (CONUS.lon0 + CONUS.lon1) / 2, lat: (CONUS.lat0 + CONUS.lat1) / 2, k };
}
let k0 = null;
const fitView0 = () => k0 || (k0 = fitView());
let flight = 0;
function flyTo(target, ms = 900) {
  const a = { ...S.view }, t0 = performance.now();
  cancelAnimationFrame(flight);
  const step = (now) => {
    const u = Math.min(1, (now - t0) / ms), e = 1 - (1 - u) ** 4;
    S.view = { lon: a.lon + (target.lon - a.lon) * e, lat: a.lat + (target.lat - a.lat) * e, k: a.k * (target.k / a.k) ** e };
    viewChanged();
    if (u < 1) flight = requestAnimationFrame(step);
  };
  flight = requestAnimationFrame(step);
}
function viewChanged() { S.viewGen++; }

// ---------- colour ----------
// wind speed: one cool ramp (navy -> cyan -> near white); turbulence: warm amber, so they never compete
const WIND_STOPS = [[0, [6, 14, 34]], [12, [12, 36, 80]], [25, [20, 72, 128]], [40, [28, 116, 166]], [55, [48, 146, 182]], [70, [96, 182, 204]], [85, [160, 214, 226]]];
function windRGB(ms) {
  for (let i = 1; i < WIND_STOPS.length; i++) {
    const [a, ca] = WIND_STOPS[i - 1], [b, cb] = WIND_STOPS[i];
    if (ms <= b) { const t = Math.max(0, (ms - a) / (b - a)); return ca.map((c, j) => c + (cb[j] - c) * t); }
  }
  return WIND_STOPS[WIND_STOPS.length - 1][1];
}
const TURB_STEPS = [0.002, 0.005, 0.01, 0.03, 0.1];
function turb(p) {
  const t = Math.max(0, Math.min(1, Math.log(p / S.floor) / Math.log(0.1 / S.floor)));
  return { t, rgb: [255, Math.round(120 + 70 * t), Math.round(30 + 40 * t)], a: 0.25 + 0.55 * t };
}

// ---------- wind field ----------
function windAt(lon, lat) {
  const w = S.wind; if (!w) return null;
  const L = w.lat, G = w.lon;
  if (lat < L[0] || lat > L[L.length - 1] || lon < G[0] || lon > G[G.length - 1]) return null;
  const fy = ((lat - L[0]) / (L[L.length - 1] - L[0])) * (L.length - 1), fx = ((lon - G[0]) / (G[G.length - 1] - G[0])) * (G.length - 1);
  const i = Math.min(L.length - 2, Math.floor(fy)), j = Math.min(G.length - 2, Math.floor(fx)), ty = fy - i, tx = fx - j;
  const bl = (A) => (A[i][j] * (1 - tx) + A[i][j + 1] * tx) * (1 - ty) + (A[i + 1][j] * (1 - tx) + A[i + 1][j + 1] * tx) * ty;
  return [bl(w.u), bl(w.v)];
}
async function loadWind(t) {
  if (!t || !S.flBins.length) return;
  const fl = S.flBins[S.fl] + 10, key = `${fl}|${Math.floor(t / 3600)}`;
  if (key === S.windKey) return;
  S.windKey = key;
  try { S.wind = await getJSON(`/api/winds?fl=${fl}&t=${Math.round(t)}`); S.fieldGen++; } catch (_) { /* keep last */ }
}
const off = document.createElement("canvas"), offx = off.getContext("2d");
let fieldDrawn = "", fieldAt = 0;
function drawField(now) {
  const c = cx.field, W = cv.field.width, H = cv.field.height;
  if (!S.layers.wind || !S.wind) { if (fieldDrawn !== "off") { c.clearRect(0, 0, W, H); fieldDrawn = "off"; } return; }
  const gen = `${S.viewGen}|${S.fieldGen}`;
  if (gen === fieldDrawn || now - fieldAt < 80) return;
  fieldDrawn = gen; fieldAt = now;
  const sc = 5, w = Math.ceil(W / sc), h = Math.ceil(H / sc);
  off.width = w; off.height = h;
  const img = offx.createImageData(w, h), px = img.data;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const [lon, lat] = unproj(x * sc, y * sc), uv = windAt(lon, lat), o = (y * w + x) * 4;
    if (!uv) continue;
    const [r, g, b] = windRGB(Math.hypot(uv[0], uv[1]));
    const e = Math.max(0, Math.min(1, (lat - CONUS.lat0) / 4, (CONUS.lat1 - lat) / 4, (lon - CONUS.lon0) / 5, (CONUS.lon1 - 1 - lon) / 5));
    px[o] = r; px[o + 1] = g; px[o + 2] = b; px[o + 3] = e * e * (3 - 2 * e) * 185;
  }
  offx.putImageData(img, 0, 0);
  c.clearRect(0, 0, W, H); c.imageSmoothingEnabled = true; c.imageSmoothingQuality = "high";
  c.drawImage(off, 0, 0, w * sc, h * sc);
}

// ---------- particle flow ----------
const parts = [];
let flowGen = -1;
function spawn(p) {
  const [lonA, latA] = unproj(0, cv.flow.height), [lonB, latB] = unproj(cv.flow.width, 0);
  const lo0 = Math.max(lonA, CONUS.lon0), lo1 = Math.min(lonB, CONUS.lon1 - 1), la0 = Math.max(latA, CONUS.lat0), la1 = Math.min(latB, CONUS.lat1);
  p.lon = lo0 + Math.random() * Math.max(0.1, lo1 - lo0); p.lat = la0 + Math.random() * Math.max(0.1, la1 - la0);
  p.age = Math.random() * 40; p.max = 70 + Math.random() * 90;
  return p;
}
function drawFlow(dt) {
  const c = cx.flow, W = cv.flow.width, H = cv.flow.height, d = DPR();
  if (!S.layers.wind || !S.wind) { c.clearRect(0, 0, W, H); return; }
  const n = Math.round(Math.min(3400, Math.max(1100, (W * H) / (d * d) / 430)));
  while (parts.length < n) parts.push(spawn({}));
  parts.length = n;
  if (flowGen !== S.viewGen) { c.clearRect(0, 0, W, H); flowGen = S.viewGen; }
  c.globalCompositeOperation = "destination-in"; c.fillStyle = "rgba(0,0,0,0.93)"; c.fillRect(0, 0, W, H);
  c.globalCompositeOperation = "source-over";
  c.lineWidth = 0.9 * d; c.lineCap = "round";
  const pxPerMs = 0.55 * d * Math.sqrt(S.view.k / fitView0().k);
  const buckets = [[], [], []];
  for (const p of parts) {
    const uv = windAt(p.lon, p.lat);
    if (!uv || ++p.age > p.max) { spawn(p); continue; }
    const [x, y] = proj(p.lon, p.lat), sp = Math.hypot(uv[0], uv[1]);
    const nx = x + uv[0] * pxPerMs * dt, ny = y - uv[1] * pxPerMs * dt;
    const [lon, lat] = unproj(nx, ny); p.lon = lon; p.lat = lat;
    buckets[sp < 25 ? 0 : sp < 50 ? 1 : 2].push(x, y, nx, ny);
  }
  ["rgba(220,236,255,0.16)", "rgba(220,236,255,0.30)", "rgba(235,245,255,0.50)"].forEach((col, b) => {
    const a = buckets[b]; if (!a.length) return;
    c.strokeStyle = col; c.beginPath();
    for (let i = 0; i < a.length; i += 4) { c.moveTo(a[i], a[i + 1]); c.lineTo(a[i + 2], a[i + 3]); }
    c.stroke();
  });
}

// ---------- data feed ----------
async function getJSON(u) { const r = await fetch(u); if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json(); }
const zulu = (t) => new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") + "Z";
function lerpAc(o, now) {
  const u = Math.min(1, (now - o.t1) / S.msgGap), e = u * u * (3 - 2 * u);
  return [o.x0 + (o.x1 - o.x0) * e, o.y0 + (o.y1 - o.y0) * e];
}
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live`);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data), now = performance.now();
    if (S.simT && m.t < S.simT - 60) { S.cells.clear(); S.ac.clear(); S.hist.clear(); }
    if (S.lastMsg) S.msgGap = 0.8 * S.msgGap + 0.2 * Math.min(now - S.lastMsg, 6000);
    S.lastMsg = now; S.simT = m.t;
    const seen = new Set();
    for (const a of m.aircraft) {
      seen.add(a.icao24);
      const o = S.ac.get(a.icao24);
      if (o) {
        const [x, y] = lerpAc(o, now), dl = a.lon - o.x1, da = a.lat - o.y1;
        if (Math.abs(dl) + Math.abs(da) > 1e-4) o.hd = Math.atan2(-da, dl * COSLAT);
        Object.assign(o, { x0: x, y0: y, x1: a.lon, y1: a.lat, fl: a.fl, T: a.T, t1: now });
      } else S.ac.set(a.icao24, { x0: a.lon, y0: a.lat, x1: a.lon, y1: a.lat, fl: a.fl, T: a.T, t1: now, hd: null });
      if (S.mode === "live") { const h = S.hist.get(a.icao24) || []; h.push([m.t, a.T]); while (h.length && h[0][0] < m.t - 1800) h.shift(); S.hist.set(a.icao24, h); }
    }
    for (const k of S.ac.keys()) if (!seen.has(k)) S.ac.delete(k);
    for (const [h, f, p] of m.cells) {
      const key = `${h}|${f}`, c = S.cells.get(key);
      if (p > S.floor) { if (c) { c.p = p; c.on = true; } else S.cells.set(key, { p, a: 0, on: true }); } else if (c) c.on = false;
    }
    if (S.mode === "live") loadWind(m.t);
  };
  ws.onclose = () => setTimeout(connect, 2500);
}

// ---------- cases ----------
let caseList = [];
async function showCases() {
  if (!caseList.length) caseList = await getJSON("/api/replays");
  $("cases").innerHTML = caseList.map((c) =>
    `<button data-id="${c.id}" aria-pressed="false">${c.intensity === "SEV" ? "Severe" : "Moderate"} · FL${c.fl_lo}${c.fl_hi !== c.fl_lo ? "–" + c.fl_hi : ""}<small>${c.date} · ${zulu(c.time).slice(11)}</small></button>`).join("");
  $("cases").querySelectorAll("button").forEach((b) => (b.onclick = () => openCase(b.dataset.id)));
  if (caseList.length) openCase(caseList[0].id);
}
async function openCase(id) {
  $("cases").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.id === id)));
  const R = await getJSON(`/api/replay/${encodeURIComponent(id)}`), m = R.meta;
  R.idx = R.frames.map((f) => new Map(f.aircraft.map((a) => [a[0], a])));
  const near = (lat, lon) => Math.hypot(lat - m.lat, (lon - m.lon) * 0.8) < 0.9;
  R.tHit = R.frames.find((f) => f.aircraft.some((a) => a[4] != null && a[4] >= S.tau && near(a[1], a[2])))?.t ?? null;
  R.tCell = R.frames.find((f) => f.cells.some(([h]) => { const r = S.lattice[h]; return r && near(r[0][1], r[0][0]); }))?.t ?? null;
  const rep = Math.max(0, R.frames.findIndex((f) => f.t >= m.time));
  S.hist = new Map();
  for (const f of R.frames) for (const a of f.aircraft) { const h = S.hist.get(a[0]) || []; h.push([f.t, a[4]]); S.hist.set(a[0], h); }
  S.replay = R; S.rf = Math.max(0, rep - 10); S.playing = true; closePopup();
  const bin = S.flBins.findIndex((f) => m.fl_lo >= f && m.fl_lo < f + 20); if (bin >= 0) selectFl(bin);
  S.windKey = ""; loadWind(m.time);
  flyTo({ lon: m.lon, lat: m.lat, k: fitView0().k * 4.2 }, 1200);
  $("mark").hidden = false; $("mark").style.left = `${(rep / (R.frames.length - 1)) * 100}%`;
  syncPlay();
}
function caseState() {
  const R = S.replay, n = R.frames.length, i = Math.min(Math.floor(S.rf), n - 1), j = Math.min(i + 1, n - 1), u = S.rf - i;
  const A = R.idx[i], B = R.idx[j], ac = [];
  for (const [id, a] of A) {
    const b = B.get(id) || a, dl = b[2] - a[2], da = b[1] - a[1];
    ac.push({ id, lat: a[1] + da * u, lon: a[2] + dl * u, fl: a[3], T: u < 0.5 ? a[4] : b[4], hd: Math.abs(dl) + Math.abs(da) > 1e-4 ? Math.atan2(-da, dl * COSLAT) : null });
  }
  return { ac, A: R.frames[i].cells, B: R.frames[j].cells, u, t: R.frames[i].t + 60 * u };
}
function caseLine(t) {
  const R = S.replay, m = R.meta, mins = Math.round((t - m.time) / 60);
  const when = mins === 0 ? "report time" : `${Math.abs(mins)} min ${mins < 0 ? "before" : "after"} report`;
  let what;
  if (Math.abs(t - m.time) < 90) what = `pilot reports ${m.intensity === "SEV" ? "severe" : "moderate"} turbulence`;
  else if (t < m.time) what = R.tHit && t >= R.tHit ? "an aircraft here already feels it" : "nothing detected yet";
  else what = R.tCell && t >= R.tCell ? "forecast marks the area, drifting with the wind" : R.tCell ? "forecast confirming" : "no forecast here (a miss)";
  return `${when} · ${what}`;
}

// ---------- drawing (top layer) ----------
function ring(c, r) { c.beginPath(); r.forEach(([lo, la], i) => { const [x, y] = proj(lo, la); i ? c.lineTo(x, y) : c.moveTo(x, y); }); c.closePath(); }
const inBand = (fl) => fl >= S.flBins[S.fl] - 10 && fl < S.flBins[S.fl] + 30;
function drawCell(c, h, p, m) {
  const r = S.lattice[h]; if (!r) return;
  const { rgb, a } = turb(p);
  ring(c, r); c.fillStyle = `rgba(${rgb.join(",")},${(a * m).toFixed(3)})`; c.fill();
  c.strokeStyle = `rgba(255,214,150,${(0.6 * m).toFixed(3)})`; c.lineWidth = DPR(); c.stroke();
}
function arrow(c, x, y, hd, s, col) {
  c.save(); c.translate(x, y); c.rotate(hd ?? -Math.PI / 2);
  c.beginPath(); c.moveTo(s * 1.4, 0); c.lineTo(-s, s * 0.85); c.lineTo(-s * 0.45, 0); c.lineTo(-s, -s * 0.85); c.closePath();
  if (s > 3.4 * DPR()) { c.lineWidth = 2 * DPR(); c.strokeStyle = "rgba(3,8,18,0.8)"; c.lineJoin = "round"; c.stroke(); }
  c.fillStyle = col; c.fill();
  c.restore();
}
let last = performance.now(), chromeAt = 0;
function frame(now) {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  drawField(now); drawFlow(dt);
  const c = cx.top, d = DPR(), W = cv.top.width, H = cv.top.height;
  c.clearRect(0, 0, W, H);
  if (S.basemap) { c.strokeStyle = "rgba(255,255,255,0.26)"; c.lineWidth = d; for (const st of S.basemap.states) for (const r of st.rings) { ring(c, r); c.stroke(); } }
  let shown = [], t = S.simT;
  if (S.mode === "case" && S.replay) {
    if (S.playing) { S.rf += dt * S.speed; if (S.rf >= S.replay.frames.length - 1) { S.rf = S.replay.frames.length - 1; S.playing = false; syncPlay(); } }
    const st = caseState(); t = st.t;
    if (S.layers.cells) {
      const Bs = new Set(st.B.map((x) => x[0])), As = new Set(st.A.map((x) => x[0]));
      for (const [h, p] of st.A) drawCell(c, h, p, Bs.has(h) ? 1 : 1 - st.u);
      for (const [h, p] of st.B) if (!As.has(h)) drawCell(c, h, p, st.u);
    }
    shown = st.ac.filter((a) => inBand(a.fl));
    loadWind(t);
  } else {
    for (const [key, cell] of S.cells) {
      cell.a += ((cell.on ? 1 : 0) - cell.a) * Math.min(1, dt * 2.5);
      if (!cell.on && cell.a < 0.02) { S.cells.delete(key); continue; }
      const [h, f] = key.split("|");
      if (S.layers.cells && +f === S.fl) drawCell(c, h, cell.p, cell.a);
    }
    for (const [id, o] of S.ac) { if (!inBand(o.fl)) continue; const [lon, lat] = lerpAc(o, now); shown.push({ id, lon, lat, fl: o.fl, T: o.T, hd: o.hd }); }
  }
  S.shownP = [];
  if (S.layers.pireps) {
    const lo = S.flBins[S.fl] ?? 0;
    S.shownP = S.mode === "case" && S.replay ? [{ ...S.replay.meta, tb_max: S.replay.meta.intensity === "SEV" ? 6 : 4 }]
      : S.pireps.filter((p) => p.tb_max >= 4 && p.fl_hi >= lo - 20 && p.fl_lo <= lo + 40);
    for (const p of S.shownP) {
      const [x, y] = proj(p.lon, p.lat);
      c.strokeStyle = p.tb_max >= 6 ? "#ff5040" : "#ffb02e"; c.lineWidth = 2.4 * d; c.lineJoin = "round";
      c.beginPath(); c.moveTo(x - 7 * d, y + 5 * d); c.lineTo(x, y - 6 * d); c.lineTo(x + 7 * d, y + 5 * d); c.stroke();
    }
  }
  const s = Math.min(7, 2.3 * Math.pow(S.view.k / fitView0().k, 0.6)) * d;
  if (S.layers.ac) for (const a of shown) {
    const [x, y] = proj(a.lon, a.lat); if (x < -20 || y < -20 || x > W + 20 || y > H + 20) continue;
    const hit = a.T != null && a.T >= S.tau;
    if (hit) { c.strokeStyle = "rgba(255,176,46,0.9)"; c.lineWidth = 1.4 * d; c.beginPath(); c.arc(x, y, s * 2.6, 0, 2 * Math.PI); c.stroke(); }
    arrow(c, x, y, a.hd, hit ? s * 1.2 : s, hit ? "#ffb02e" : "rgba(244,246,250,0.92)");
    if (S.popup?.kind === "ac" && S.popup.id === a.id) {
      c.strokeStyle = "#fff"; c.lineWidth = 1.5 * d; c.beginPath(); c.arc(x, y, s * 3.4, 0, 2 * Math.PI); c.stroke();
      S.popup.at = [x / d, y / d]; S.popup.a = a;
    }
  }
  S.shown = shown;
  if (T.i >= 0) tourPlace();
  if (now - chromeAt > 300) { chromeAt = now; updateChrome(t); }
  requestAnimationFrame(frame);
}

// ---------- chrome ----------
function updateChrome(t) {
  $("clock").textContent = t ? zulu(t) : "--:--Z";
  if (S.mode === "case" && S.replay) {
    const f = S.rf / (S.replay.frames.length - 1);
    $("fill").style.width = `${f * 100}%`; $("scrub").value = String(Math.round(1000 * f));
    $("sub").textContent = caseLine(t);
  } else if (S.win) {
    $("fill").style.width = `${Math.max(0, Math.min(1, (t - S.win[0]) / (S.win[1] - S.win[0]))) * 100}%`;
    $("sub").textContent = "archived day · 30× · live loop";
  }
  if (S.popup) renderPopup();
}
function closePopup() { S.popup = null; $("popup").hidden = true; }
function spark(h) {
  const pts = h.filter((x) => x[1] != null); if (pts.length < 2) return "";
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0], ym = Math.max(S.tau * 1.5, ...pts.map((x) => x[1]));
  const X = (tt) => ((tt - t0) / Math.max(1, t1 - t0)) * 228 + 2, Y = (v) => 38 - (v / ym) * 34;
  return `<svg viewBox="0 0 232 40" preserveAspectRatio="none"><line x1="0" x2="232" y1="${Y(S.tau)}" y2="${Y(S.tau)}" stroke="rgba(255,176,46,.55)" stroke-dasharray="3 4"/><path d="${pts.map(([tt, v], i) => `${i ? "L" : "M"}${X(tt).toFixed(1)},${Y(v).toFixed(1)}`).join("")}" fill="none" stroke="#eef1f6" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}
function renderPopup() {
  const P = S.popup, el = $("popup");
  let html = "";
  if (P.kind === "ac") {
    const a = P.a; if (!a) { closePopup(); return; }
    const hit = a.T != null && a.T >= S.tau;
    html = `<div class="h">${a.id} <span class="k">FL${a.fl}</span></div><div class="v">${a.T == null ? "Climbing, descending or turning: not scored." : hit ? `<span class="amber">Turbulence</span> · jitter ${(a.T / S.tau).toFixed(1)}× its type's threshold` : `Smooth · ${Math.round((100 * a.T) / S.tau)}% of threshold`}</div>${spark(S.hist.get(a.id) || [])}<div class="k">turbulence index, recent minutes</div>`;
  } else if (P.kind === "cell") {
    html = `<div class="h">${(P.p * 100).toFixed(1)}% chance</div><div class="v">that the next aircraft through this area in 15 min meets turbulence (background ${(S.floor * 50).toFixed(2)}%).</div>`;
  } else {
    const sev = P.p.tb_max >= 6;
    html = `<div class="h">Pilot report · <span style="color:${sev ? "var(--sev)" : "var(--amber)"}">${sev ? "severe" : "moderate"}</span></div><div class="v">FL${P.p.fl_lo}${P.p.fl_hi !== P.p.fl_lo ? "–" + P.p.fl_hi : ""} · ${zulu(P.p.time)}</div>`;
  }
  el.innerHTML = html; el.hidden = false;
  const [x, y] = P.at;
  el.style.left = `${Math.min(innerWidth - 250, Math.max(10, x + 18))}px`;
  el.style.top = `${Math.min(innerHeight - 170, Math.max(60, y - 30))}px`;
}
function inside(r, x, y) {
  if (!r) return false; let c = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) { const [xi, yi] = r[i], [xj, yj] = r[j]; if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c; }
  return c;
}
function pick(px, py) {
  const d = DPR(); let best = null, bd = (14 * d) ** 2;
  for (const a of S.shown || []) { const [x, y] = proj(a.lon, a.lat), q = (x - px) ** 2 + (y - py) ** 2; if (q < bd) { bd = q; best = { kind: "ac", id: a.id, a, at: [x / d, y / d] }; } }
  for (const p of S.shownP || []) { const [x, y] = proj(p.lon, p.lat), q = (x - px) ** 2 + (y - py) ** 2; if (q < bd) { bd = q; best = { kind: "pirep", p, at: [x / d, y / d] }; } }
  if (best || !S.layers.cells || !S.lattice) return best;
  const [lon, lat] = unproj(px, py);
  const list = S.mode === "case" && S.replay ? caseState().A : [...S.cells].filter(([k, c]) => c.on && +k.split("|")[1] === S.fl).map(([k, c]) => [k.split("|")[0], c.p]);
  for (const [h, p] of list) if (inside(S.lattice[h], lon, lat)) return { kind: "cell", p, at: [px / d, py / d] };
  return null;
}

// ---------- input ----------
let drag = null;
cv.top.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY }; cv.top.setPointerCapture(e.pointerId); });
cv.top.addEventListener("pointermove", (e) => {
  if (drag) {
    if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 3) cv.top.classList.add("drag");
    S.view.lon -= (e.clientX - drag.x) / (COSLAT * S.view.k); S.view.lat += (e.clientY - drag.y) / S.view.k;
    drag = { ...drag, x: e.clientX, y: e.clientY }; S.user = true; S.dragging = true; viewChanged(); return;
  }
  cv.top.classList.toggle("hot", !!pick(e.clientX * DPR(), e.clientY * DPR()));
});
cv.top.addEventListener("pointerup", (e) => {
  const moved = drag && Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 3; drag = null; S.dragging = false; cv.top.classList.remove("drag");
  if (moved) return;
  const hit = pick(e.clientX * DPR(), e.clientY * DPR());
  if (hit) { S.popup = hit; renderPopup(); } else closePopup();
});
cv.top.addEventListener("wheel", (e) => {
  e.preventDefault();
  const d = DPR(), [lon, lat] = unproj(e.clientX * d, e.clientY * d);
  S.view.k = Math.max(fitView0().k * 0.7, Math.min(fitView0().k * 14, S.view.k * Math.exp(-e.deltaY * 0.0016)));
  const [lon2, lat2] = unproj(e.clientX * d, e.clientY * d);
  S.view.lon += lon - lon2; S.view.lat += lat - lat2; S.user = true; viewChanged();
}, { passive: false });
function resize() {
  const d = DPR();
  for (const c of Object.values(cv)) { c.width = Math.round(innerWidth * d); c.height = Math.round(innerHeight * d); }
  k0 = null; const f = fitView0();
  if (!S.user && S.mode === "live") S.view = { ...f };
  viewChanged();
}
window.addEventListener("resize", resize);

function selectFl(i) {
  S.fl = i; S.windKey = "";
  $("alt").querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(+b.dataset.i === i)));
  loadWind(S.mode === "case" && S.replay ? caseState().t : S.simT);
}
function syncPlay() { $("play").textContent = S.playing ? "❚❚" : "▶"; $("play").setAttribute("aria-label", S.playing ? "Pause" : "Play"); }
function setMode(mode) {
  S.mode = mode; closePopup();
  document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === mode)));
  $("cases").hidden = mode !== "case"; $("speed").hidden = mode !== "case";
  $("play").style.visibility = mode === "case" ? "visible" : "hidden";
  $("scrub").disabled = mode !== "case"; $("mark").hidden = true;
  if (mode === "case") showCases().catch(() => {});
  else { S.replay = null; S.playing = false; S.user = false; S.hist.clear(); S.windKey = ""; loadWind(S.simT); flyTo(fitView0()); }
}
document.querySelectorAll(".seg button").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
document.querySelectorAll(".layers button").forEach((b) => (b.onclick = () => {
  const k = b.dataset.layer; S.layers[k] = !S.layers[k]; b.setAttribute("aria-pressed", String(S.layers[k])); viewChanged();
}));
$("helpBtn").onclick = () => { const h = $("help"); h.hidden = !h.hidden; $("helpBtn").setAttribute("aria-expanded", String(!h.hidden)); };
$("play").onclick = () => { if (!S.replay) return; if (S.rf >= S.replay.frames.length - 1) S.rf = 0; S.playing = !S.playing; syncPlay(); };
$("scrub").oninput = (e) => { if (!S.replay) return; S.rf = (+e.target.value / 1000) * (S.replay.frames.length - 1); S.playing = false; syncPlay(); };
$("speed").querySelectorAll("button").forEach((b) => (b.onclick = () => {
  S.speed = +b.dataset.s; $("speed").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
}));
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closePopup(); $("help").hidden = true; }
  if (e.key === " " && S.mode === "case") { e.preventDefault(); $("play").click(); }
  if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); selectFl(Math.max(0, Math.min(S.flBins.length - 1, S.fl + (e.key === "ArrowUp" ? 1 : -1)))); }
});

function scales() {
  const wg = [0, 20, 40, 60, 82].map((ms, i, a) => `rgb(${windRGB(ms).map(Math.round).join(",")}) ${(i / (a.length - 1)) * 100}%`).join(",");
  $("windScale").style.background = `linear-gradient(90deg, ${wg})`;
  const tg = TURB_STEPS.map((p, i) => { const { rgb, a } = turb(p), m = 0.35 + 0.65 * a; return `rgb(${rgb.map((v) => Math.round(v * m)).join(",")}) ${(i / (TURB_STEPS.length - 1)) * 100}%`; }).join(",");
  $("turbScale").style.background = `linear-gradient(90deg, ${tg})`;
  $("turbTicks").innerHTML = TURB_STEPS.map((p, i) => `<span>${i === 4 ? "≥" : ""}${p < 0.01 ? (p * 100).toFixed(1) : (p * 100).toFixed(0)}</span>`).join("");
}

// ---------- guided tour: fly into each part of the map and explain it ----------
const T = { i: -1, steps: [], at: null };
const kts = (ms) => Math.round(ms * 1.94384);
function tourTargets() {
  const fl = S.flBins[S.fl];
  const steps = [{ title: "Welcome to Horus", body: "This map shows where airliners over the US are <b>hitting turbulence right now</b>, and forecasts where the <b>next ones will</b>. Here is what each part means.", home: true }];
  if (S.wind) {
    let best = null;
    S.wind.lat.forEach((la, i) => S.wind.lon.forEach((lo, j) => {
      if (la < 28 || la > 47 || lo < -118 || lo > -74) return;
      const sp = Math.hypot(S.wind.u[i][j], S.wind.v[i][j]);
      if (!best || sp > best.sp) best = { lon: lo, lat: la, sp };
    }));
    if (best) steps.push({ title: "The wind", body: `The moving streaks are the wind at <b>FL${fl}</b> (${(fl * 100).toLocaleString()} ft). Brighter and faster means stronger. This band is the <b>jet stream</b>, about <b>${kts(best.sp)} knots</b> here. Turbulence often hides along its edges.`, point: () => [best.lon, best.lat], zoom: 2.6 });
  }
  const cand = (S.shown || []).filter((a) => a.hd != null);
  const hit = cand.find((a) => a.T != null && a.T >= S.tau);
  const mid = fitView0();
  const ac = hit || cand.sort((a, b) => Math.hypot(a.lon - mid.lon, a.lat - mid.lat) - Math.hypot(b.lon - mid.lon, b.lat - mid.lat))[0];
  if (ac) {
    const id = ac.id; let lastPos = [ac.lon, ac.lat];
    steps.push({ title: hit ? "An aircraft in turbulence" : "An aircraft", body: `Each arrow is one airliner, pointing the way it flies. This is <b>${id}</b> at FL${ac.fl}. <b>White</b> = smooth air. <b class="amber">Amber with a ring</b> = its own vertical-speed data shows turbulence right now. Click any aircraft for its details.`,
      point: () => { const a = (S.shown || []).find((x) => x.id === id); if (a) lastPos = [a.lon, a.lat]; return lastPos; }, zoom: 7, follow: true });
  }
  let cell = null;
  for (const [k, c] of S.cells) { if (!c.on) continue; const [h, f] = k.split("|"); if (!cell || c.p > cell.p) cell = { h, f: +f, p: c.p }; }
  if (cell && S.lattice[cell.h]) {
    const r = S.lattice[cell.h], c0 = [r.reduce((a, q) => a + q[0], 0) / r.length, r.reduce((a, q) => a + q[1], 0) / r.length];
    steps.push({ title: "The forecast", body: `Amber hexagons are the <b>15-minute forecast</b>: the chance that the next aircraft through this ~26 km patch meets turbulence. This one is <b>${(cell.p * 100).toFixed(1)}%</b>, against a normal <b>${(S.floor * 50).toFixed(2)}%</b>. It drifts with the wind and fades if nothing new is detected.`, point: () => c0, zoom: 6, fl: cell.f });
  }
  const pr = S.pireps.filter((p) => p.tb_max >= 4 && p.lat > 25 && p.lat < 49 && p.lon > -124 && p.lon < -67)
    .sort((a, b) => Math.hypot(a.lon - mid.lon, a.lat - mid.lat) - Math.hypot(b.lon - mid.lon, b.lat - mid.lat))[0];
  if (pr) steps.push({ title: "A pilot report", body: `A <b class="amber">^</b> marks a pilot who radioed in turbulence (<span class="amber">orange</span> = moderate, <span style="color:var(--sev)">red</span> = severe). This one: FL${pr.fl_lo}${pr.fl_hi !== pr.fl_lo ? "–" + pr.fl_hi : ""}. Horus never uses reports to detect; they are how we <b>check</b> it.`, point: () => [pr.lon, pr.lat], zoom: 3.5, fl: S.flBins.findIndex((f) => pr.fl_lo >= f && pr.fl_lo < f + 20) });
  steps.push({ title: "Altitude", body: "Turbulence depends on height. Pick a flight level here: <b>340 = 34,000 ft</b>, where most airliners cruise. The ↑ ↓ keys work too.", el: ".alt", home: true });
  steps.push({ title: "Layers", body: "Switch the wind, the forecast, aircraft and pilot reports on or off.", el: ".layers", home: true });
  steps.push({ title: "Time", body: "Live replays a <b>real archived day at 30× speed</b> through the same code that would run on a live feed. The bar shows how far through that day we are.", el: ".timeline", home: true });
  steps.push({ title: "Case studies", body: "<b>Cases</b> replays three real turbulence reports minute by minute: watch an aircraft feel it, the pilot report it, and the forecast appear. Click anything on the map for details; <b>?</b> brings back a short reminder.", el: ".seg", home: true });
  return steps;
}
function tourStart() {
  if (S.mode !== "live") setMode("live");
  closePopup(); $("help").hidden = true;
  T.steps = tourTargets(); T.home = { fl: S.fl }; tourShow(0);
}
function tourShow(i) {
  if (i < 0) return;
  if (i >= T.steps.length) { tourEnd(); return; }
  T.i = i; const st = T.steps[i];
  if (st.fl != null && st.fl >= 0 && st.fl !== S.fl) selectFl(st.fl);
  else if (st.home && S.fl !== T.home.fl) selectFl(T.home.fl);
  if (st.point) { const [lon, lat] = st.point(); S.user = true; flyTo({ lon, lat, k: fitView0().k * st.zoom }, 1300); }
  else if (st.home) { S.user = false; flyTo(fitView0(), 900); }
  $("cStep").textContent = `${i + 1} of ${T.steps.length}`;
  $("cTitle").textContent = st.title; $("cBody").innerHTML = st.body;
  $("cDots").innerHTML = T.steps.map((_, j) => `<i class="${j === i ? "on" : ""}"></i>`).join("");
  $("cBack").style.visibility = i ? "visible" : "hidden";
  $("cNext").textContent = i === T.steps.length - 1 ? "Start exploring" : "Next";
  $("callout").hidden = false; document.body.classList.add("touring");
  tourPlace();
}
function tourEnd() {
  T.i = -1; $("callout").hidden = true; $("spot").hidden = true; document.body.classList.remove("touring");
  if (T.home && S.fl !== T.home.fl) selectFl(T.home.fl);
  S.user = false; flyTo(fitView0(), 900);
  try { localStorage.setItem("horus.tour", "done"); } catch (_) { /* private mode */ }
}
function tourPlace() {
  if (T.i < 0) return;
  const st = T.steps[T.i], spot = $("spot"), co = $("callout"), cw = 320, ch = co.offsetHeight || 190;
  let box = null;
  if (st.point) {
    const d = DPR(), [lon, lat] = st.point(), [x, y] = proj(lon, lat), r = 58;
    if (st.follow && !S.dragging) { S.view.lon += (lon - S.view.lon) * 0.04; S.view.lat += (lat - S.view.lat) * 0.04; viewChanged(); }
    box = { x: x / d - r, y: y / d - r, w: 2 * r, h: 2 * r, round: true };
  } else if (st.el) {
    const rc = document.querySelector(st.el).getBoundingClientRect();
    box = { x: rc.left - 8, y: rc.top - 8, w: rc.width + 16, h: rc.height + 16 };
  }
  if (!box) { spot.hidden = true; co.style.left = `${(innerWidth - cw) / 2}px`; co.style.top = `${innerHeight / 2 - ch / 2}px`; return; }
  spot.hidden = false;
  Object.assign(spot.style, { left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px`, borderRadius: box.round ? "50%" : "16px" });
  let left = box.x + box.w + 18;
  if (left + cw > innerWidth - 12) left = box.x - cw - 18;
  if (left < 12) left = Math.min(innerWidth - cw - 12, Math.max(12, box.x));
  let top = box.y + box.h / 2 - ch / 2;
  if (box.y + box.h + ch + 18 > innerHeight && left === Math.max(12, box.x)) top = box.y - ch - 18;
  top = Math.max(60, Math.min(innerHeight - ch - 12, top));
  co.style.left = `${left}px`; co.style.top = `${top}px`;
}
$("tourBtn").onclick = tourStart;
$("cNext").onclick = () => tourShow(T.i + 1);
$("cBack").onclick = () => tourShow(T.i - 1);
$("cSkip").onclick = tourEnd;
window.addEventListener("keydown", (e) => {
  if (T.i < 0) return;
  if (e.key === "ArrowRight" || e.key === "Enter") { e.preventDefault(); tourShow(T.i + 1); }
  if (e.key === "ArrowLeft") { e.preventDefault(); tourShow(T.i - 1); }
  if (e.key === "Escape") tourEnd();
}, true);
window.addEventListener("resize", () => tourPlace());

async function init() {
  const [lat, base, th, health] = await Promise.all([getJSON("/api/lattice"), getJSON("/api/basemap"), getJSON("/api/thresholds"), getJSON("/api/health")]);
  S.lattice = lat.cells; S.flBins = lat.fl_bins; S.floor = lat.floor; S.basemap = base; S.tau = th.tau_b3; S.win = health.window; S.simT = health.time;
  $("alt").innerHTML = S.flBins.map((f, i) => `<button role="radio" data-i="${i}">${f}</button>`).reverse().join("");
  $("alt").querySelectorAll("button").forEach((b) => (b.onclick = () => selectFl(+b.dataset.i)));
  resize(); scales(); selectFl(S.flBins.indexOf(340)); setMode("live");
  S.pireps = (await getJSON("/api/pireps?since=7200")).pireps;
  setInterval(async () => { if (S.mode === "live") try { S.pireps = (await getJSON("/api/pireps?since=7200")).pireps; } catch (_) { /* keep */ } }, 20000);
  connect(); requestAnimationFrame(frame);
  let seen = null;
  try { seen = localStorage.getItem("horus.tour"); } catch (_) { /* private mode */ }
  if (!seen) setTimeout(() => { if (T.i < 0 && S.mode === "live") tourStart(); }, 9000);
}
init().catch((e) => document.body.insertAdjacentHTML("beforeend", `<p style="position:fixed;left:0;right:0;top:48%;text-align:center;color:#999">Could not load: ${e.message}</p>`));
