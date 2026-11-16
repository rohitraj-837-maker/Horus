// Map engine: owns the background canvas and all live/case data. Renders at 60 fps
// outside React; React reads a throttled snapshot via subscribe() (4 Hz).
const COSLAT = Math.cos((37 * Math.PI) / 180);
const CONUS = { lon0: -125, lon1: -66, lat0: 24, lat1: 50 };
const STEPS = [0.002, 0.005, 0.01, 0.03, 0.1];

export const zulu = (t) => new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") + "Z";
const getJSON = async (u) => {
  const r = await fetch(u);
  if (!r.ok) throw new Error(`${u}: HTTP ${r.status}`);
  return r.json();
};

function insidePoly(r, x, y) {
  let c = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const [xi, yi] = r[i], [xj, yj] = r[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}

export function createEngine(canvas) {
  const ctx = canvas.getContext("2d");
  const DPR = () => window.devicePixelRatio || 1;
  const S = {
    mode: "live", lattice: null, flBins: [], fl: 0, floor: 0.001, tau: 1, basemap: null,
    layers: { cells: true, ac: true, pireps: true },
    frame: { x: 0, y: 0, w: 1, h: 1 },            // CSS px rect the map should fit
    view: { lon: -95.5, lat: 37, span: 62 }, user: false,
    cells: new Map(), ac: new Map(), msgGap: 1500, lastMsg: 0, simT: 0, tick: 0, pireps: [],
    hist: new Map(), wasHit: new Set(), loggedCells: new Set(), log: [],
    replay: null, rf: 0, playing: false, speed: 4, focus: null, shownAc: [], status: "Connecting…",
  };
  const subs = new Set();
  let snapAt = 0, raf = 0, anim = 0;

  // ---------- projection ----------
  const k = () => (S.frame.w * DPR()) / (S.view.span * COSLAT);
  const cx = () => (S.frame.x + S.frame.w / 2) * DPR();
  const cy = () => (S.frame.y + S.frame.h / 2) * DPR();
  const proj = (lon, lat) => [cx() + (lon - S.view.lon) * COSLAT * k(), cy() - (lat - S.view.lat) * k()];
  const unproj = (x, y) => [S.view.lon + (x - cx()) / (COSLAT * k()), S.view.lat - (y - cy()) / k()];
  const fitSpan = (f) => {
    const sw = CONUS.lon1 - CONUS.lon0, sh = (CONUS.lat1 - CONUS.lat0) / COSLAT;
    return Math.max(sw, (sh * f.w) / Math.max(1, f.h)) * 1.04;
  };
  function flyTo(lon, lat, span, ms = 900) {
    const a = { ...S.view }, t0 = performance.now();
    cancelAnimationFrame(anim); S.flying = true;
    const step = (now) => {
      const u = Math.min(1, (now - t0) / ms), e = 1 - (1 - u) ** 4;
      S.view = { lon: a.lon + (lon - a.lon) * e, lat: a.lat + (lat - a.lat) * e, span: a.span + (span - a.span) * e };
      if (u < 1) anim = requestAnimationFrame(step); else S.flying = false;
    };
    anim = requestAnimationFrame(step);
  }

  // ---------- colour: single hue, dim amber -> bright gold ----------
  const heat = (p) => {
    const t = Math.max(0, Math.min(1, Math.log(p / S.floor) / Math.log(0.1 / S.floor)));
    return [255, Math.round(110 + 100 * t), Math.round(30 + 90 * t), 0.22 + 0.6 * t];
  };
  const rgba = ([r, g, b, a], m = 1) => `rgba(${r},${g},${b},${(a * m).toFixed(3)})`;

  // ---------- description ----------
  function place(lon, lat) {
    if (!S.basemap) return "";
    for (const st of S.basemap.states) for (const r of st.rings) if (insidePoly(r, lon, lat)) return `over ${st.code}`;
    return "offshore";
  }
  function log(t, kind, text) { S.log = [{ t, kind, text, id: `${t}-${Math.random()}` }, ...S.log].slice(0, 30); }
  function noteAircraft(id, fl, T, lat, lon, t) {
    const h = S.hist.get(id) || [];
    h.push([t, T]); while (h.length && h[0][0] < t - 1800) h.shift(); S.hist.set(id, h);
    const hit = T != null && T >= S.tau;
    if (hit && !S.wasHit.has(id)) log(t, "hit", `${id} detects turbulence · FL${fl} ${place(lon, lat)}`);
    if (hit) S.wasHit.add(id); else S.wasHit.delete(id);
  }

  // ---------- live feed ----------
  function setCell(key, p) {
    const c = S.cells.get(key);
    if (p > S.floor) { if (c) { c.p = p; c.on = true; } else S.cells.set(key, { p, a: 0, on: true }); }
    else if (c) c.on = false;
  }
  const lerpAc = (o, now) => {
    const u = Math.min(1, (now - o.t1) / S.msgGap), e = u * u * (3 - 2 * u);
    return [o.x0 + (o.x1 - o.x0) * e, o.y0 + (o.y1 - o.y0) * e];
  };
  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live`);
    ws.onopen = () => (S.status = "Connected");
    ws.onclose = () => { S.status = "Feed lost · reconnecting"; setTimeout(connect, 2500); };
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data), now = performance.now();
      if (S.simT && m.t < S.simT - 60) { S.cells.clear(); S.ac.clear(); S.hist.clear(); S.log = []; }
      if (S.lastMsg) S.msgGap = 0.8 * S.msgGap + 0.2 * Math.min(now - S.lastMsg, 6000);
      S.lastMsg = now; S.simT = m.t; S.tick = m.tick;
      const seen = new Set();
      for (const a of m.aircraft) {
        seen.add(a.icao24);
        const o = S.ac.get(a.icao24);
        if (o) { const [x, y] = lerpAc(o, now); Object.assign(o, { x0: x, y0: y, x1: a.lon, y1: a.lat, fl: a.fl, T: a.T, t1: now }); }
        else S.ac.set(a.icao24, { x0: a.lon, y0: a.lat, x1: a.lon, y1: a.lat, fl: a.fl, T: a.T, t1: now });
        if (S.mode === "live") noteAircraft(a.icao24, a.fl, a.T, a.lat, a.lon, m.t);
      }
      for (const key of S.ac.keys()) if (!seen.has(key)) S.ac.delete(key);
      for (const [h, f, p] of m.cells) {
        setCell(`${h}|${f}`, p);
        const key = `${h}|${f}`;
        if (S.mode === "live" && p >= 0.03 && !S.loggedCells.has(key) && S.lattice?.[h]) {
          S.loggedCells.add(key);
          const [lo, la] = S.lattice[h][0];
          log(m.t, "cell", `Forecast ${(p * 100).toFixed(0)}% at FL${S.flBins[f]} ${place(lo, la)}`);
        } else if (p < 0.02) S.loggedCells.delete(key);
      }
      if (S.mode === "live") S.status = `Grid ${zulu(m.tick)}`;
    };
  }

  // ---------- case studies ----------
  async function openCase(id) {
    const R = await getJSON(`/api/replay/${encodeURIComponent(id)}`);
    const m = R.meta;
    R.idx = R.frames.map((f) => new Map(f.aircraft.map((a) => [a[0], a])));
    const near = (lat, lon) => Math.hypot(lat - m.lat, (lon - m.lon) * 0.8) < 0.9;
    R.tHit = R.frames.find((f) => f.aircraft.some((a) => a[4] != null && a[4] >= S.tau && near(a[1], a[2])))?.t ?? null;
    R.tCell = R.frames.find((f) => f.cells.some(([h]) => { const r = S.lattice[h]; return r && near(r[0][1], r[0][0]); }))?.t ?? null;
    R.logged = new Set();
    S.hist = new Map();
    for (const f of R.frames) for (const a of f.aircraft) { const h = S.hist.get(a[0]) || []; h.push([f.t, a[4]]); S.hist.set(a[0], h); }
    S.replay = R; S.mode = "case"; S.focus = null; S.log = [];
    S.rf = Math.max(0, R.frames.findIndex((f) => f.t >= m.time) - 10);
    const bin = S.flBins.findIndex((f) => m.fl_lo >= f && m.fl_lo < f + 20);
    if (bin >= 0) S.fl = bin;
    S.user = false; flyTo(m.lon, m.lat, 14, 1100);
    S.playing = true;
    emit(true);
  }
  function caseState() {
    const R = S.replay, n = R.frames.length, i = Math.min(Math.floor(S.rf), n - 1), j = Math.min(i + 1, n - 1), u = S.rf - i;
    const A = R.idx[i], B = R.idx[j], ac = [];
    for (const [id, a] of A) {
      const b = B.get(id) || a;
      ac.push({ id, lat: a[1] + (b[1] - a[1]) * u, lon: a[2] + (b[2] - a[2]) * u, fl: a[3], T: u < 0.5 ? a[4] : b[4] });
    }
    return { ac, cellsA: R.frames[i].cells, cellsB: R.frames[j].cells, u, t: R.frames[i].t + 60 * u };
  }
  function caption(st) {
    const R = S.replay, m = R.meta, t = st.t, mins = Math.round((t - m.time) / 60);
    const fl = `FL${m.fl_lo}${m.fl_hi !== m.fl_lo ? "–" + m.fl_hi : ""}`, word = m.intensity === "SEV" ? "severe" : "moderate";
    if (Math.abs(t - m.time) < 90) return { head: "Report time", body: `A pilot reports ${word} turbulence at ${fl}, right here.`, stage: 2 };
    if (t < m.time) return R.tHit && t >= R.tHit
      ? { head: `${-mins} min before`, body: "An aircraft crossing this patch already feels it. Its own data glows amber.", stage: 1 }
      : { head: `${-mins} min before`, body: "Traffic streams through clear-looking sky. Nothing yet.", stage: 0 };
    if (R.tCell && t >= R.tCell) return { head: `${mins} min after`, body: `The forecast blooms over the area and drifts with the jet stream, warning the aircraft behind.`, stage: 3 };
    if (R.tCell) return { head: `${mins} min after`, body: "Detections settle. The forecast appears once a minute is confirmed.", stage: 2 };
    return { head: `${mins} min after`, body: "No forecast reached the display level here. An honest miss.", stage: 3 };
  }

  // ---------- render ----------
  const ring = (r) => { ctx.beginPath(); r.forEach(([lo, la], i) => { const [x, y] = proj(lo, la); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.closePath(); };
  const inBand = (fl) => fl >= S.flBins[S.fl] - 10 && fl < S.flBins[S.fl] + 30;
  function drawCell(h, p, m) {
    const r = S.lattice[h]; if (!r) return;
    ring(r); ctx.fillStyle = rgba(heat(p), m); ctx.fill();
    ctx.strokeStyle = rgba([255, 220, 150, 0.5], m); ctx.lineWidth = DPR(); ctx.stroke();
  }
  let last = performance.now();
  function frame(now) {
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    const d = DPR(), W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    // graticule across the whole canvas: texture for the glass to refract everywhere
    ctx.strokeStyle = "rgba(140,160,255,0.07)"; ctx.lineWidth = d;
    const [lonA, latA] = unproj(0, H), [lonB, latB] = unproj(W, 0);
    for (let lo = Math.ceil(lonA / 5) * 5; lo <= lonB; lo += 5) { const [x] = proj(lo, 0); ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
    for (let la = Math.ceil(latA / 5) * 5; la <= latB; la += 5) { const [, y] = proj(0, la); ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
    if (S.basemap) {
      ctx.strokeStyle = "rgba(255,255,255,0.13)"; ctx.lineWidth = d;
      for (const st of S.basemap.states) for (const r of st.rings) { ring(r); ctx.stroke(); }
    }
    let shown = [], nCells = 0, cap = null;
    if (S.mode === "case" && S.replay) {
      if (S.playing) { S.rf += dt * S.speed; if (S.rf >= S.replay.frames.length - 1) { S.rf = S.replay.frames.length - 1; S.playing = false; } }
      const st = caseState();
      if (S.layers.cells) {
        const B = new Set(st.cellsB.map((c) => c[0])), A = new Set(st.cellsA.map((c) => c[0]));
        for (const [h, p] of st.cellsA) { drawCell(h, p, B.has(h) ? 1 : 1 - st.u); nCells++; }
        for (const [h, p] of st.cellsB) if (!A.has(h)) drawCell(h, p, st.u);
      }
      shown = st.ac.filter((a) => inBand(a.fl));
      for (const a of st.ac) if (a.T != null && a.T >= S.tau && !S.replay.logged.has(a.id)) { S.replay.logged.add(a.id); log(st.t, "hit", `${a.id} detects turbulence · FL${a.fl} ${place(a.lon, a.lat)}`); }
      if (!S.replay.logged.has("report") && st.t >= S.replay.meta.time) { S.replay.logged.add("report"); log(S.replay.meta.time, "pirep", `Pilot report: ${S.replay.meta.intensity === "SEV" ? "severe" : "moderate"} at FL${S.replay.meta.fl_lo}`); }
      S.clock = st.t; cap = caption(st);
    } else {
      for (const [key, c] of S.cells) {
        c.a += ((c.on ? 1 : 0) - c.a) * Math.min(1, dt * 2.5);
        if (!c.on && c.a < 0.02) { S.cells.delete(key); continue; }
        const [h, f] = key.split("|");
        if (+f !== S.fl || !S.layers.cells) continue;
        drawCell(h, c.p, c.a); nCells++;
      }
      for (const [id, o] of S.ac) { if (!inBand(o.fl)) continue; const [lon, lat] = lerpAc(o, now); shown.push({ id, lon, lat, fl: o.fl, T: o.T }); }
      S.clock = S.simT;
    }
    const zoomK = 62 / S.view.span, r0 = Math.min(5, 2 * Math.sqrt(zoomK)) * d;
    let hits = 0;
    if (S.layers.ac) for (const a of shown) {
      const [x, y] = proj(a.lon, a.lat); if (x < -20 || y < -20 || x > W + 20 || y > H + 20) continue;
      const hit = a.T != null && a.T >= S.tau; if (hit) hits++;
      if (hit) {
        const g = ctx.createRadialGradient(x, y, 0, x, y, r0 * 6);
        g.addColorStop(0, "rgba(255,170,60,0.55)"); g.addColorStop(1, "rgba(255,170,60,0)");
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r0 * 6, 0, 2 * Math.PI); ctx.fill();
      }
      ctx.fillStyle = hit ? "#ffb347" : "rgba(235,238,255,0.7)";
      ctx.beginPath(); ctx.arc(x, y, hit ? r0 * 1.3 : r0, 0, 2 * Math.PI); ctx.fill();
      if (a.id === S.focus) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5 * d; ctx.beginPath(); ctx.arc(x, y, 10 * d, 0, 2 * Math.PI); ctx.stroke(); }
    }
    if (S.layers.pireps) {
      const lo = S.flBins[S.fl] ?? 0;
      const ps = S.mode === "case" && S.replay ? [{ ...S.replay.meta, tb_max: S.replay.meta.intensity === "SEV" ? 6 : 4 }]
        : S.pireps.filter((p) => p.tb_max >= 4 && p.fl_hi >= lo - 20 && p.fl_lo <= lo + 40);
      for (const p of ps) {
        const [x, y] = proj(p.lon, p.lat);
        ctx.strokeStyle = p.tb_max >= 6 ? "#ff453a" : "#ff9f2e"; ctx.lineWidth = 2.2 * d; ctx.lineJoin = "round";
        ctx.beginPath(); ctx.moveTo(x - 7 * d, y + 5 * d); ctx.lineTo(x, y - 6 * d); ctx.lineTo(x + 7 * d, y + 5 * d); ctx.stroke();
      }
    }
    S.shownAc = shown; S.hits = hits; S.nCells = nCells; S.cap = cap;
    if (now - snapAt > 250) emit();
    raf = requestAnimationFrame(frame);
  }

  // ---------- snapshot for React ----------
  function bandCounts() {
    const src = S.mode === "case" && S.replay ? caseState().ac : [...S.ac.values()];
    const n = new Array(S.flBins.length).fill(0);
    for (const a of src) { const i = Math.floor((a.fl - S.flBins[0]) / 20); if (i >= 0 && i < n.length) n[i]++; }
    return n;
  }
  function snapshot() {
    const f = S.focus ? S.shownAc.find((a) => a.id === S.focus) : null;
    if (f) S.lastFocus = f;
    return {
      mode: S.mode, clock: S.clock, status: S.status, fl: S.fl, flBins: S.flBins, bands: S.flBins.length ? bandCounts() : [],
      aircraft: S.shownAc.length, hits: S.hits || 0, cells: S.nCells || 0, log: S.log, tau: S.tau, floor: S.floor,
      steps: STEPS.map((p) => { const [r, g, b, a] = heat(p), m = 0.35 + 0.65 * a; return { p, color: rgba(heat(p)), solid: `rgb(${Math.round(r * m)},${Math.round(g * m)},${Math.round(b * m)})` }; }), caption: S.cap, layers: { ...S.layers },
      replay: S.replay ? { meta: S.replay.meta, frac: S.rf / (S.replay.frames.length - 1), playing: S.playing, speed: S.speed } : null,
      focus: S.focus ? { id: S.focus, cur: f || S.lastFocus || null, hist: (S.hist.get(S.focus) || []).filter((x) => x[1] != null) } : null,
    };
  }
  function emit() { snapAt = performance.now(); const s = snapshot(); subs.forEach((fn) => fn(s)); }

  // ---------- input (canvas sits behind the glass; the map window passes events through) ----------
  let drag = null;
  canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const kk = k() / DPR();
    S.view.lon -= (e.clientX - drag.x) / (COSLAT * kk); S.view.lat += (e.clientY - drag.y) / kk;
    drag = { ...drag, x: e.clientX, y: e.clientY }; S.user = true;
  });
  canvas.addEventListener("pointerup", (e) => {
    const moved = drag && Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 4; drag = null;
    if (moved) return;
    const d = DPR(), px = e.clientX * d, py = e.clientY * d;
    let best = null, bd = (14 * d) ** 2;
    for (const a of S.shownAc) { const [x, y] = proj(a.lon, a.lat), q = (x - px) ** 2 + (y - py) ** 2; if (q < bd) { bd = q; best = a; } }
    S.focus = best ? best.id : null; S.lastFocus = best; emit();
  });
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const d = DPR(), [lon, lat] = unproj(e.clientX * d, e.clientY * d);
    S.view.span = Math.max(3, Math.min(90, S.view.span * Math.exp(e.deltaY * 0.0015)));
    const [lon2, lat2] = unproj(e.clientX * d, e.clientY * d);
    S.view.lon += lon - lon2; S.view.lat += lat - lat2; S.user = true;
  }, { passive: false });

  function resize() {
    canvas.width = Math.round(window.innerWidth * DPR()); canvas.height = Math.round(window.innerHeight * DPR());
  }
  window.addEventListener("resize", resize);

  return {
    canvas,
    async start() {
      resize();
      const [lat, base, th] = await Promise.all([getJSON("/api/lattice"), getJSON("/api/basemap"), getJSON("/api/thresholds")]);
      S.lattice = lat.cells; S.flBins = lat.fl_bins; S.floor = lat.floor; S.basemap = base; S.tau = th.tau_b3;
      S.fl = S.flBins.indexOf(340);
      S.pireps = (await getJSON("/api/pireps?since=7200")).pireps;
      setInterval(async () => { if (S.mode === "live") try { S.pireps = (await getJSON("/api/pireps?since=7200")).pireps; } catch (_) { /* keep */ } }, 20000);
      connect(); raf = requestAnimationFrame(frame);
    },
    stop() { cancelAnimationFrame(raf); },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    replays: () => getJSON("/api/replays"),
    setFrame(rect, animate = true) {
      S.frame = rect;
      if (S.mode === "live" && !S.user && !S.flying) { const span = fitSpan(rect); animate ? flyTo(-95.5, 37, span) : (S.view = { lon: -95.5, lat: 37, span }); }
    },
    setFl(i) { S.fl = i; emit(); },
    toggle(layer) { S.layers[layer] = !S.layers[layer]; emit(); },
    openCase,
    live() { S.mode = "live"; S.replay = null; S.playing = false; S.focus = null; S.log = []; S.user = false; flyTo(-95.5, 37, fitSpan(S.frame)); emit(); },
    play() { if (!S.replay) return; if (S.rf >= S.replay.frames.length - 1) S.rf = 0; S.playing = !S.playing; emit(); },
    seek(frac) { if (!S.replay) return; S.rf = frac * (S.replay.frames.length - 1); S.playing = false; emit(); },
    speed(v) { S.speed = v; emit(); },
    unfocus() { S.focus = null; emit(); },
    resetView() { S.user = false; if (S.mode === "case" && S.replay) flyTo(S.replay.meta.lon, S.replay.meta.lat, 14); else flyTo(-95.5, 37, fitSpan(S.frame)); },
  };
}
