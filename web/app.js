// Horus map: canvas renderer, no external libraries or tiles (runs offline).
"use strict";
const $ = (id) => document.getElementById(id);
const cv = $("map"), ctx = cv.getContext("2d");
const S = {
  mode: "live", lattice: null, flBins: [], floor: 0, fl: 0, basemap: null,
  live: new Map(),            // "h3|fl" -> p   (live mode, all FL bins)
  aircraft: [], pireps: [], replay: null, frame: 0, playing: false, timer: null, ws: null,
};
const BOX = { lon0: -125, lon1: -66, lat0: 24, lat1: 50 };

function proj(lon, lat) {
  const w = cv.width, h = cv.height, k = Math.cos((37 * Math.PI) / 180);
  const sx = w / ((BOX.lon1 - BOX.lon0) * k), sy = h / (BOX.lat1 - BOX.lat0), s = Math.min(sx, sy);
  const ox = (w - (BOX.lon1 - BOX.lon0) * k * s) / 2, oy = (h - (BOX.lat1 - BOX.lat0) * s) / 2;
  return [ox + (lon - BOX.lon0) * k * s, oy + (BOX.lat1 - lat) * s];
}

// sequential single-hue ramp (orange), dim -> bright on black; log scale over [floor, 0.2]
function cellColor(p) {
  const t = Math.max(0, Math.min(1, Math.log(p / S.floor) / Math.log(0.2 / S.floor)));
  const a = 0.25 + 0.75 * t;
  return `rgba(255,${Math.round(110 + 100 * t)},${Math.round(20 + 90 * t)},${a})`;
}

function resize() {
  const r = cv.getBoundingClientRect(), d = window.devicePixelRatio || 1;
  cv.width = Math.round(r.width * d); cv.height = Math.round(r.height * d);
  draw();
}

function path(ring) {
  ctx.beginPath();
  ring.forEach(([lo, la], i) => { const [x, y] = proj(lo, la); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.closePath();
}

function currentCells() {
  if (S.mode === "replay") return S.replay ? S.replay.frames[S.frame].cells : [];
  const out = [];
  for (const [k, p] of S.live) { const [h, f] = k.split("|"); if (+f === S.fl) out.push([h, p]); }
  return out;
}

function currentAircraft() {
  if (S.mode === "replay") return S.replay ? S.replay.frames[S.frame].aircraft.map(([id, la, lo, fl, T]) => ({ icao24: id, lat: la, lon: lo, fl, T })) : [];
  const lo = S.flBins[S.fl] ?? 0;
  return S.aircraft.filter((a) => a.fl >= lo - 20 && a.fl < lo + 40);
}

function draw() {
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, cv.width, cv.height);
  const d = window.devicePixelRatio || 1;
  if (S.basemap) {
    ctx.strokeStyle = "rgba(255,255,255,0.16)"; ctx.lineWidth = d;
    for (const st of S.basemap.states) for (const r of st.rings) { path(r); ctx.stroke(); }
  }
  if ($("lyCells").checked && S.lattice) {
    for (const [h, p] of currentCells()) {
      const ring = S.lattice[h]; if (!ring) continue;
      path(ring); ctx.fillStyle = cellColor(p); ctx.fill();
    }
  }
  if ($("lyAc").checked) {
    for (const a of currentAircraft()) {
      const [x, y] = proj(a.lon, a.lat), hit = a.T != null && a.T >= S.tau;
      ctx.fillStyle = hit ? "#ff9900" : "rgba(236,236,236,0.55)";
      ctx.beginPath(); ctx.arc(x, y, (hit ? 3 : 1.6) * d, 0, 2 * Math.PI); ctx.fill();
    }
  }
  if ($("lyPirep").checked) {
    const lo = S.flBins[S.fl] ?? 0, hi = lo + 20;
    // live: only reports whose altitude range overlaps the selected bin (+-20 FL, as matching)
    const ps = S.mode === "replay" && S.replay ? [S.replay.meta]
      : S.pireps.filter((p) => p.fl_hi >= lo - 20 && p.fl_lo <= hi + 20);
    ctx.font = `${11 * d}px ui-monospace, Menlo, monospace`;
    for (const p of ps) {
      const tb = p.intensity ? (p.intensity === "SEV" ? 6 : 4) : p.tb_max;
      if (tb < 4) continue;
      const [x, y] = proj(p.lon, p.lat);
      ctx.strokeStyle = tb >= 6 ? "#ec2a2a" : "#ff9900"; ctx.lineWidth = 2 * d;
      ctx.beginPath(); ctx.moveTo(x - 6 * d, y + 4 * d); ctx.lineTo(x, y - 5 * d); ctx.lineTo(x + 6 * d, y + 4 * d); ctx.stroke();
      ctx.fillStyle = "#ececec"; ctx.fillText(`FL${p.fl_lo}${p.fl_hi !== p.fl_lo ? "-" + p.fl_hi : ""}`, x + 8 * d, y + 4 * d);
    }
  }
}

function zulu(t) { const d = new Date(t * 1000); return d.toISOString().slice(0, 16).replace("T", " ") + "Z"; }

async function getJSON(u) { const r = await fetch(u); if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json(); }

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live`);
  S.ws = ws;
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    for (const [h, f, p] of m.cells) { const k = `${h}|${f}`; if (p > S.floor) S.live.set(k, p); else S.live.delete(k); }
    S.aircraft = m.aircraft;
    if (S.mode === "live") { $("clock").textContent = zulu(m.t); $("status").textContent = `${m.aircraft.length} aircraft · grid tick ${zulu(m.tick)} · ${S.live.size} cells lit`; draw(); }
  };
  ws.onclose = () => { $("status").textContent = "feed disconnected; retrying"; setTimeout(connect, 3000); };
}

async function loadReplays() {
  const list = await getJSON("/api/replays");
  $("replaySel").innerHTML = list.map((r) => `<option value="${r.id}">${r.date} · ${r.intensity} FL${r.fl_lo}${r.fl_hi !== r.fl_lo ? "-" + r.fl_hi : ""}</option>`).join("");
  if (list.length) await openReplay(list[0].id);
}

async function openReplay(id) {
  S.replay = await getJSON(`/api/replay/${encodeURIComponent(id)}`);
  S.frame = 0;
  const m = S.replay.meta;
  $("replayMeta").textContent = `${m.intensity} report at ${zulu(m.time)}, ${m.aircraft_in_window} aircraft nearby. Selected as: ${m.selection}.`;
  $("scrub").max = S.replay.frames.length - 1; $("scrub").value = 0; $("scrub").disabled = false;
  showFrame();
}

function showFrame() {
  const f = S.replay.frames[S.frame], dt = Math.round((f.t - S.replay.meta.time) / 60);
  $("clock").textContent = zulu(f.t);
  $("tlabel").textContent = `T${dt >= 0 ? "+" : ""}${dt} min`;
  $("status").textContent = `${f.aircraft.length} aircraft · ${f.cells.length} cells lit`;
  draw();
}

function setMode(mode) {
  S.mode = mode;
  document.querySelectorAll(".modes button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === mode)));
  $("replayBox").hidden = mode !== "replay";
  $("scrub").disabled = mode !== "replay";
  $("tlabel").textContent = mode === "live" ? "live feed (archive replayed through the live loop)" : "";
  if (mode === "replay" && !S.replay) loadReplays().catch((e) => ($("status").textContent = e.message));
  else if (mode === "replay") showFrame(); else draw();
}

async function init() {
  const [lat, base] = await Promise.all([getJSON("/api/lattice"), getJSON("/api/basemap")]);
  S.lattice = lat.cells; S.flBins = lat.fl_bins; S.floor = lat.floor; S.basemap = base; S.tau = 1;
  $("fl").innerHTML = S.flBins.map((f, i) => `<option value="${i}">FL${f}–${f + 20}</option>`).join("");
  S.fl = S.flBins.indexOf(340); $("fl").value = S.fl;
  $("rampLo").textContent = `${(S.floor * 100).toFixed(2)}%`; $("rampHi").textContent = "≥20%";
  try { const th = await getJSON("/api/thresholds"); S.tau = th.tau_b3 ?? 1; } catch (_) { /* default */ }
  const pr = await getJSON("/api/pireps?since=7200"); S.pireps = pr.pireps;
  setInterval(async () => { if (S.mode === "live") S.pireps = (await getJSON("/api/pireps?since=7200")).pireps; }, 30000);
  connect(); resize();
}

$("fl").onchange = (e) => { S.fl = +e.target.value; draw(); };
["lyCells", "lyAc", "lyPirep"].forEach((id) => ($(id).onchange = draw));
document.querySelectorAll(".modes button").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
$("replaySel").onchange = (e) => openReplay(e.target.value);
$("scrub").oninput = (e) => { S.frame = +e.target.value; showFrame(); };
$("play").onclick = () => {
  if (S.mode !== "replay" || !S.replay) return;
  S.playing = !S.playing; $("play").textContent = S.playing ? "❚❚" : "▶";
  clearInterval(S.timer);
  if (S.playing) S.timer = setInterval(() => { S.frame = (S.frame + 1) % S.replay.frames.length; $("scrub").value = S.frame; showFrame(); }, 400);
};
window.addEventListener("resize", resize);
init().catch((e) => ($("status").textContent = e.message));
